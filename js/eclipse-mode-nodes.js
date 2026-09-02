import {
    app
} from './comfy/index.js';
import {
    smartResize,
    notifyVue,
    batchedNotifyVue,
    batchedRefreshVueWidgetOptions,
    isVueMode
} from './eclipse-widget-performance-utils.js';
import { addCommittedTextWidget } from './eclipse-committed-text-widget.js';
import {
    findRootGraph,
    getGraphAncestors,
    getGraphDescendants,
    subgraphOpState,
    patchSubgraphOps
} from './eclipse-set-get-utils.js';
const MODE_ALWAYS = 0,
    MODE_MUTE = 2,
    MODE_BYPASS = 4;
let _collapseStyle = null;
const _collapsedNodeIds = new Set();

function _updateCollapseStyleSheet() {
    if (!_collapseStyle) {
        _collapseStyle = document.createElement('style');
        _collapseStyle.id = 'eclipse-collapse-connections';
        document.head.appendChild(_collapseStyle);
    }
    if (0 === _collapsedNodeIds.size) {
        _collapseStyle.textContent = '';
        return;
    }
    const selector = [..._collapsedNodeIds].map((id) => `[data-node-id="${id}"] .lg-slot--input`).join(',\n');
    _collapseStyle.textContent = `${selector} {\n        height: 0 !important;\n        min-height: 0 !important;\n        overflow: hidden !important;\n        margin: 0 !important;\n        padding: 0 !important;\n        pointer-events: none !important;\n    }`;
}

function setCollapseCSS(node, collapsed) {
    if (null == node.id) return;
    const idStr = String(node.id);
    if (collapsed) {
        if (_collapsedNodeIds.has(idStr)) return;
        _collapsedNodeIds.add(idStr);
    } else {
        if (!_collapsedNodeIds.has(idStr)) return;
        _collapsedNodeIds.delete(idStr);
    }
    _updateCollapseStyleSheet();
}
const NODE_NAMES = {
    FAST_MODE_TOGGLE: 'Fast Mode Toggle [Eclipse]',
    FAST_MODE_TOGGLE_NATIVE: 'Fast Mode Toggle Native [Eclipse]',
    FAST_MODE_SWITCHER: 'Fast Mode Switcher [Eclipse]',
    NODE_MODE_REPEATER: 'Mute / Bypass Repeater [Eclipse]',
    NODE_COLLECTOR: 'Node Collector [Eclipse]',
    MODE_RELAY: 'Mode Relay [Eclipse]',
    MODE_BRIDGE_SET: 'Mode Bridge Set [Eclipse]',
    MODE_BRIDGE_GET: 'Mode Bridge Get [Eclipse]',
},
    ECLIPSE_MODE_TYPES = Object.values(NODE_NAMES),
    REROUTE_TYPES = ['Reroute'],
    RELAY_TYPES = [NODE_NAMES.MODE_RELAY],
    BRIDGE_TYPES = [],
    BRIDGE_SET_TYPES = [NODE_NAMES.MODE_BRIDGE_SET],
    BRIDGE_GET_TYPES = [NODE_NAMES.MODE_BRIDGE_GET],
    COLLECTOR_TYPES = [NODE_NAMES.NODE_COLLECTOR],
    TOGGLER_TYPES = [NODE_NAMES.FAST_MODE_TOGGLE, NODE_NAMES.FAST_MODE_TOGGLE_NATIVE, NODE_NAMES.FAST_MODE_SWITCHER];

function propagateToInnerNodes(node, mode) {
    if (!node.isSubgraphNode?.() || !node.subgraph) return;
    const innerNodes = node.subgraph._nodes || node.subgraph.nodes;
    if (!innerNodes) return;
    for (const inner of innerNodes) {
        if (!inner || inner === node) continue;
        if (inner.mode !== mode) {
            inner.mode = mode;
            // Vue Nodes 2.0: direct `.mode=` assignment doesn't trigger the
            // per-node reactive proxy. Poke widget array so Vue re-renders.
            if (isVueMode()) batchedNotifyVue(inner);
        }
        if (inner.isSubgraphNode?.() && inner.subgraph) propagateToInnerNodes(inner, mode);
    }
}

function changeModeOfNodes(nodes, mode) {
    const list = Array.isArray(nodes) ? nodes : [nodes];
    for (const node of list) {
        if (node && undefined !== node.mode && node.mode !== mode) {
            node.mode = mode;
            propagateToInnerNodes(node, mode);
            notifyDownstreamModeChange(node);
            // Vue Nodes 2.0: notify the node whose mode just changed so the
            // muted/bypassed visual state updates without needing collapse/expand.
            if (isVueMode()) batchedNotifyVue(node);
        }
    }
}

function isReroute(node) {
    if (!node) return false;
    const type = node.type || '';
    return REROUTE_TYPES.includes(type);
}

function isCollector(node) {
    if (!node) return false;
    const type = node.type || '';
    return COLLECTOR_TYPES.includes(type);
}

function isPassThrough(node, isCollectorCheck) {
    return !!isReroute(node) || (!isCollectorCheck && isCollector(node));
}

function getLink(graph, linkId) {
    if (!graph || null == linkId) return null;
    return graph.links && typeof graph.links.get === 'function' ? graph.links.get(linkId) || null : graph.links?.[linkId] || null;
}

function getConnectedInputNodes(node, slotIndex) {
    const result = [];
    if (!node || !node.inputs || !node.graph) return result;
    const inputs = slotIndex >= 0 ? [node.inputs[slotIndex]] : node.inputs;
    for (const inp of inputs) {
        if (!inp || null == inp.link) continue;
        const link = getLink(node.graph, inp.link);
        if (!link) continue;
        const sourceNode = node.graph.getNodeById(link.origin_id);
        if (sourceNode) result.push(sourceNode);
    }
    return result;
}

function getConnectedInputNodesFiltered(node, slotIndex, followCollectors) {
    const result = [];
    if (!node || !node.inputs || !node.graph) return result;
    const inputs = slotIndex >= 0 ? [node.inputs[slotIndex]] : node.inputs;
    for (const inp of inputs) {
        if (!inp || null == inp.link) continue;
        const link = getLink(node.graph, inp.link);
        if (!link) continue;
        const sourceNode = node.graph.getNodeById(link.origin_id);
        if (sourceNode) {
            if (isPassThrough(sourceNode, followCollectors)) {
                const deeper = getConnectedInputNodesFiltered(sourceNode, -1, followCollectors);
                result.push(...deeper);
            } else {
                result.push(sourceNode);
            }
        }
    }
    return result;
}

function getConnectedOutputNodes(node, followPassThrough, targetFilter) {
    const result = [];
    if (!node || !node.graph) return result;
    const outputs = node.outputs || [];
    for (const output of outputs) {
        if (!output.links) continue;
        for (const linkId of output.links) {
            const link = getLink(node.graph, linkId);
            if (!link) continue;
            const target = node.graph.getNodeById(link.target_id);
            if (target && (!targetFilter || target === targetFilter)) {
                if (followPassThrough && isPassThrough(target, false)) {
                    const deeper = getConnectedOutputNodes(target, true);
                    result.push(...deeper);
                } else {
                    result.push(target);
                }
            }
        }
    }
    return result;
}

function getGroupNodes(group, recompute) {
    if (!group) return [];
    if (false !== recompute && typeof group.recomputeInsideNodes === 'function') group.recomputeInsideNodes();
    const nodes = group._nodes || [];
    return Array.from(nodes).filter((n) => n && undefined !== n.mode);
}

function syncModeWidgets(node) {
    if (!node.graph || undefined === node._eclipse_modeOn) return;
    const modeOn = node._eclipse_modeOn,
        connectedNodes = getConnectedInputNodesFiltered(node, -1, false);
    let changed = false;
    for (let idx = 0; idx < connectedNodes.length; idx++) {
        const widget = node.widgets?.[idx];
        if (!widget) continue;
        const isOn = connectedNodes[idx].mode === modeOn;
        if (widget.value !== isOn) {
            widget.value = isOn;
            changed = true;
        }
    }
    if (changed) {
        // Batched: during workflow load many Fast Muters sync simultaneously;
        // the microtask Set dedupes and flushes all in one pass.
        if (isVueMode()) batchedNotifyVue(node);
        node.setDirtyCanvas(true, false);
    }
}

function requestSync(node) {
    if (node._eclipse_syncQueued) return;
    node._eclipse_syncQueued = true;
    requestAnimationFrame(() => {
        node._eclipse_syncQueued = false;
        syncModeWidgets(node);
    });
}

function notifyDownstreamModeChange(node) {
    if (!node?.graph) return;
    const outputs = node.outputs || [];
    for (const output of outputs) {
        if (!output.links) continue;
        for (const linkId of output.links) {
            const link = getLink(node.graph, linkId);
            if (!link) continue;
            const target = node.graph.getNodeById(link.target_id);
            if (target && target._eclipse_onUpstreamModeChange) target._eclipse_onUpstreamModeChange();
        }
    }
}

function hookModeProperty(node, callback) {
    if (!node) return () => { };
    if (node._eclipse_modeHooks) {
        node._eclipse_modeHooks.push(callback);
        return () => {
            const idx = node._eclipse_modeHooks?.indexOf(callback);
            if (idx >= 0) node._eclipse_modeHooks.splice(idx, 1);
        };
    }
    node._eclipse_modeHooks = [callback];
    const fireHooks = (target, oldMode, newMode) => {
        const hooks = target._eclipse_modeHooks?.slice() || [];
        for (const hook of hooks) hook(target, oldMode, newMode);
    },
        descriptor = Object.getOwnPropertyDescriptor(node, 'mode');
    if (descriptor && (descriptor.get || descriptor.set)) {
        const origSet = descriptor.set,
            origGet = descriptor.get;
        Object.defineProperty(node, 'mode', {
            get() {
                return origGet ? origGet.call(this) : descriptor.value;
            },
            set(val) {
                const old = origGet ? origGet.call(this) : descriptor.value;
                origSet ? origSet.call(this, val) : (descriptor.value = val);
                if (val !== old) fireHooks(this, old, val);
            },
            configurable: true,
            enumerable: false !== descriptor.enumerable,
        });
    } else {
        let current = descriptor ? descriptor.value : node.mode;
        Object.defineProperty(node, 'mode', {
            get: () => current,
            set(val) {
                const old = current;
                current = val;
                if (val !== old) fireHooks(this, old, val);
            },
            configurable: true,
            enumerable: true,
        });
    }
    return () => {
        const idx = node._eclipse_modeHooks?.indexOf(callback);
        if (idx >= 0) node._eclipse_modeHooks.splice(idx, 1);
    };
}

function hookTitleProperty(node, callback) {
    if (!node) return () => { };
    if (node._eclipse_titleHooks) {
        node._eclipse_titleHooks.push(callback);
        return () => {
            const idx = node._eclipse_titleHooks?.indexOf(callback);
            if (idx >= 0) node._eclipse_titleHooks.splice(idx, 1);
        };
    }
    node._eclipse_titleHooks = [callback];
    const fireHooks = (target) => {
        const hooks = target._eclipse_titleHooks?.slice() || [];
        for (const hook of hooks) hook(target);
    },
        descriptor = Object.getOwnPropertyDescriptor(node, 'title');
    if (descriptor && (descriptor.get || descriptor.set)) {
        const origSet = descriptor.set,
            origGet = descriptor.get;
        Object.defineProperty(node, 'title', {
            get() {
                return origGet ? origGet.call(this) : descriptor.value;
            },
            set(val) {
                const old = origGet ? origGet.call(this) : descriptor.value;
                origSet ? origSet.call(this, val) : (descriptor.value = val);
                if (val !== old) fireHooks(this);
            },
            configurable: true,
            enumerable: false !== descriptor.enumerable,
        });
    } else {
        let current = descriptor ? descriptor.value : node.title;
        Object.defineProperty(node, 'title', {
            get: () => current,
            set(val) {
                const old = current;
                current = val;
                if (val !== old) fireHooks(this);
            },
            configurable: true,
            enumerable: true,
        });
    }
    return () => {
        const idx = node._eclipse_titleHooks?.indexOf(callback);
        if (idx >= 0) node._eclipse_titleHooks.splice(idx, 1);
    };
}

function syncTitleHooks(node, targetNodes, callback) {
    const hookMap = node._eclipse_hookedTitles || (node._eclipse_hookedTitles = new Map()),
        activeIds = new Set(targetNodes.map((n) => n.id));
    for (const [id, unhook] of hookMap) {
        if (!activeIds.has(id)) {
            unhook();
            hookMap.delete(id);
        }
    }
    for (const target of targetNodes) {
        if (!hookMap.has(target.id)) {
            const unhook = hookTitleProperty(target, callback);
            hookMap.set(target.id, unhook);
        }
    }
}

function stabilizeInputs(node, showTitles, labelMode) {
    let changed = false;
    if (!node.inputs) node.inputs = [];
    for (const inp of node.inputs) {
        if (inp && /^input_\d+$/i.test(inp.name)) {
            inp.name = ' ';
            changed = true;
        }
    }
    const collapse = !!node.properties?.collapse_connections,
        lastInput = node.inputs[node.inputs.length - 1];
    if (lastInput) {
        if (null != lastInput.link) {
            if (!collapse) {
                node.addInput(' ', '*');
                changed = true;
            }
        } else if (collapse && node.inputs.length > 1 && node.inputs.slice(0, -1).some((inp) => null != inp?.link)) {
            node.removeInput(node.inputs.length - 1);
            changed = true;
        } else if (' ' !== lastInput.name) {
            lastInput.name = ' ';
            changed = true;
        }
    } else {
        node.addInput(' ', '*');
        changed = true;
    }
    for (let idx = collapse ? node.inputs.length - 1 : node.inputs.length - 2; idx >= 0; idx--) {
        const inp = node.inputs[idx];
        if (!inp) continue;
        if (null == inp.link) {
            if (node.inputs.length > 1) {
                node.removeInput(idx);
                changed = true;
            } else if (' ' !== inp.name) {
                inp.name = ' ';
                changed = true;
            }
        } else if ('hide' === labelMode) {
            if (' ' !== inp.name) {
                inp.name = ' ';
                changed = true;
            }
        } else if (showTitles) {
            const connected = getConnectedInputNodesFiltered(node, idx, false),
                title = connected[0]?.title || ' ';
            if (inp.name !== title) {
                inp.name = title;
                changed = true;
            }
        } else {
            const connected = getConnectedInputNodes(node, idx),
                title = connected[0]?.title || ' ';
            if (inp.name !== title) {
                inp.name = title;
                changed = true;
            }
        }
    }
    if (setCollapseCSS(node, collapse), collapse) {
        const slotH = 0.7 * (LiteGraph.NODE_SLOT_HEIGHT ?? 20);
        for (const inp of node.inputs) {
            if (!inp.pos || inp.pos[1] !== slotH) {
                inp.pos = [10, slotH];
                changed = true;
            }
        }
    } else {
        for (const inp of node.inputs) {
            if (inp.pos) {
                delete inp.pos;
                changed = true;
            }
        }
    }
    for (const inp of node.inputs) {
        if ('_eclipseHide' === inp.widget?.name) {
            delete inp.widget;
            changed = true;
        }
    }
    return changed;
}

function getNativeModeToggleTargets(node) {
    return getNativeModeToggleTargetRecords(node).map((record) => record.targetNode);
}

function getNativeModeToggleTargetRecords(node) {
    const result = [];
    for (const input of _nativeModeTargetInputs(node)) {
        const link = getNativeModeInputLink(node, input);
        if (!link) continue;
        const sourceNode = node.graph?.getNodeById?.(link.origin_id)
            || (node.graph?._nodes || []).find((candidate) => String(candidate.id) === String(link.origin_id));
        if (!sourceNode) continue;
        if (isPassThrough(sourceNode, false)) {
            for (const targetNode of getNativeModeTogglePassThroughTargets(sourceNode)) {
                result.push({ input, targetNode });
            }
        } else {
            result.push({ input, targetNode: sourceNode });
        }
    }
    return result;
}

function getNativeModeTogglePassThroughTargets(node, visited) {
    if (!node?.graph) return [];
    const seen = visited || new Set();
    if (seen.has(node)) return [];
    seen.add(node);
    const result = [];
    for (let slot = 0; slot < (node.inputs?.length || 0); slot++) {
        const link = getNativeModeInputLink(node, node.inputs[slot]);
        if (!link) continue;
        const source = node.graph.getNodeById?.(link.origin_id)
            || (node.graph._nodes || []).find((candidate) => String(candidate.id) === String(link.origin_id));
        if (!source) continue;
        if (isPassThrough(source, false)) {
            result.push(...getNativeModeTogglePassThroughTargets(source, seen));
        } else {
            result.push(source);
        }
    }
    return result;
}

function getNativeModeInputLink(node, input) {
    const slot = node.inputs?.indexOf(input) ?? -1;
    if (slot < 0 || !node.graph) return null;
    const direct = node.getInputLink?.(slot) || getLink(node.graph, input.link);
    if (direct) return direct;
    const links = node.graph.links instanceof Map
        ? node.graph.links.values()
        : Object.values(node.graph.links || {});
    for (const link of links) {
        if (String(link?.target_id) === String(node.id) && link?.target_slot === slot) return link;
    }
    return null;
}

function isNativeModeTargetConnected(node, input) {
    return !!getNativeModeInputLink(node, input);
}

function stabilizeNativeModeToggleInputs(node) {
    let changed = false;
    if (!node.inputs) node.inputs = [];
    for (const input of node.inputs) {
        if (_nativeModeBackingInput(input)) input._eclipse_modeToggleBacking = true;
    }
    let targets = _nativeModeTargetInputs(node);
    for (const input of targets) {
        if (/^input_\d+$/i.test(input.name) || input.name !== ' ') {
            input.name = ' ';
            changed = true;
        }
        input._eclipse_modeToggleTarget = true;
    }
    const collapse = !!node.properties?.collapse_connections;
    const lastTarget = targets[targets.length - 1];
    if (!lastTarget) {
        node.addInput(' ', '*');
        changed = true;
    } else if (isNativeModeTargetConnected(node, lastTarget) && !collapse) {
        node.addInput(' ', '*');
        changed = true;
    } else if (!isNativeModeTargetConnected(node, lastTarget) && collapse && targets.length > 1
        && targets.slice(0, -1).some((input) => isNativeModeTargetConnected(node, input))) {
        node.removeInput(node.inputs.indexOf(lastTarget));
        changed = true;
    }
    targets = _nativeModeTargetInputs(node);
    for (let idx = (collapse ? targets.length : targets.length - 1) - 1; idx >= 0; idx--) {
        const input = targets[idx];
        if (isNativeModeTargetConnected(node, input)) continue;
        if (targets.length > 1) {
            node.removeInput(node.inputs.indexOf(input));
            targets.splice(idx, 1);
            changed = true;
        }
    }
    targets = _nativeModeTargetInputs(node);
    if (!targets.length) {
        node.addInput(' ', '*');
        targets = _nativeModeTargetInputs(node);
        changed = true;
    }
    if (!collapse && isNativeModeTargetConnected(node, targets[targets.length - 1])) {
        node.addInput(' ', '*');
        targets = _nativeModeTargetInputs(node);
        changed = true;
    }
    const slotY = 0.7 * (LiteGraph.NODE_SLOT_HEIGHT ?? 20);
    for (const input of targets) {
        if (collapse) {
            if (!input.pos || input.pos[1] !== slotY) {
                input.pos = [10, slotY];
                changed = true;
            }
        } else if (input.pos) {
            delete input.pos;
            changed = true;
        }
    }
    setCollapseCSS(node, collapse);
    return changed;
}

function ensureNativeModeToggleBacking(node, widget, name, pairKey) {
    let input = (node.inputs || []).find((candidate) => (
        _nativeModeBackingInput(candidate)
        && (_nativeModePairKey(candidate) === pairKey || candidate === widget._eclipse_backingInput)
    ));
    if (!input) {
        const before = node.inputs?.length || 0;
        input = node.addInput(name, 'BOOLEAN', {
            widget: { name, type: 'BOOLEAN' },
            _eclipse_modeToggleBacking: true,
            _eclipse_modeToggleKey: pairKey,
        });
        input ||= node.inputs?.[before] || node.inputs?.[node.inputs.length - 1];
    }
    if (!input) return null;
    input.name = name;
    input.type = 'BOOLEAN';
    input.widget = { ...(input.widget || {}), name, type: 'BOOLEAN' };
    input.label = name;
    input._eclipse_modeToggleBacking = true;
    input._eclipse_modeToggleKey = pairKey;
    input._eclipse_modeToggleAutoName = name;
    input._eclipse_modeToggleTargetId = widget._eclipse_targetId;
    widget._eclipse_backingInput = input;
    widget._eclipse_modeToggleKey = pairKey;
    widget._eclipse_modeToggleAutoName = name;
    return input;
}

function scheduleStabilize(node, fn, delay, cancelPending) {
    if (cancelPending && node._eclipse_stabilizeTimer) {
        clearTimeout(node._eclipse_stabilizeTimer);
        node._eclipse_stabilizeTimer = null;
    }
    if (!node._eclipse_stabilizeTimer) {
        node._eclipse_stabilizeTimer = setTimeout(() => {
            node._eclipse_stabilizeTimer = null;
            if (node.graph) fn.call(node);
        }, delay || 100);
    }
}

const MAX_SUBGRAPH_HOST_REFRESH_FRAMES = 5;
const _subgraphHostRefreshJobs = new WeakMap();

function findSubgraphHosts(innerGraph) {
    const root = findRootGraph(innerGraph);
    if (!root) return [];
    const hosts = [];
    for (const graph of [root, ...getGraphDescendants(root)]) {
        for (const node of graph?._nodes || []) {
            if (node?.subgraph === innerGraph) hosts.push(node);
        }
    }
    return hosts;
}

function scheduleSubgraphHostRefresh(innerGraph) {
    if (!innerGraph || !isVueMode()) return;
    const job = {};
    _subgraphHostRefreshJobs.set(innerGraph, job);
    let framesLeft = MAX_SUBGRAPH_HOST_REFRESH_FRAMES;
    const refresh = () => {
        if (_subgraphHostRefreshJobs.get(innerGraph) !== job || !isVueMode()) return;
        for (const host of findSubgraphHosts(innerGraph)) notifyVue(host);
        if (--framesLeft > 0) {
            requestAnimationFrame(refresh);
        } else if (_subgraphHostRefreshJobs.get(innerGraph) === job) {
            _subgraphHostRefreshJobs.delete(innerGraph);
        }
    };
    requestAnimationFrame(refresh);
}

// Promoted widgets are host-owned projections in current ComfyUI versions.
// Some post-proxy frontends update only that host state, while newer releases
// also call the live host widget callback. Native mode toggles are a narrow
// exception that need the concrete interior callback to change target modes, so
// bridge only projections that resolve to this Eclipse node and keep every
// other promoted widget untouched.
const _nativeModePromotionJobs = new WeakMap();
const _nativeModePromotionInteractionJobs = new WeakMap();
const _nativeModePromotionForwarding = new WeakSet();
const _nativeModePromotionRoots = new Set();
const _nativeModeIdentitySyncing = new WeakSet();
let _nativeModePromotionInteractionInstalled = false;
let _nativeModeRekeySequence = 0;

function _nativeModePairKey(value) {
    const key = value?._eclipse_modeToggleKey;
    if (/^target_\d+$/.test(key || '')) return key;
    const legacyName = value?.widget?.name || value?.name;
    return /^target_\d+$/.test(legacyName || '') ? legacyName : null;
}

function _nativeModeKeyIndex(key) {
    const match = /^target_(\d+)$/.exec(key || '');
    return match ? Number.parseInt(match[1], 10) : Number.MAX_SAFE_INTEGER;
}

function _nextNativeModePairKey(node, reserved) {
    const used = reserved || new Set();
    for (const input of node.inputs || []) {
        const key = _nativeModePairKey(input);
        if (key) used.add(key);
    }
    for (const widget of node.widgets || []) {
        const key = _nativeModePairKey(widget);
        if (key) used.add(key);
    }
    let index = 0;
    while (used.has(`target_${index}`)) index++;
    const key = `target_${index}`;
    used.add(key);
    return key;
}

function _nativeModeBackingInput(input) {
    if (!input) return false;
    if (input._eclipse_modeToggleBacking) return true;
    if (/^target_\d+$/.test(input._eclipse_modeToggleKey || '')
        && String(input.type || '').toUpperCase() === 'BOOLEAN'
        && input.widget) return true;
    const name = input.widget?.name;
    return /^target_\d+$/.test(name || '') && String(input.type || '').toUpperCase() === 'BOOLEAN';
}

function _nativeModeTargetInputs(node) {
    return (node.inputs || []).filter((input) => !_nativeModeBackingInput(input));
}

function _subgraphBoundaryInput(host, hostInput) {
    if (hostInput?._subgraphSlot) return hostInput._subgraphSlot;
    const name = hostInput?.name;
    return host.subgraph?.inputs?.find?.((input) => input?.name === name)
        || host.subgraph?.inputNode?.slots?.find?.((input) => input?.name === name)
        || null;
}

function _resolveSubgraphLinkTarget(graph, link) {
    if (!graph || !link) return null;
    if (typeof link.resolve === 'function') {
        try {
            const resolved = link.resolve(graph);
            const inputNode = resolved?.inputNode;
            const targetInput = resolved?.input || resolved?.targetInput;
            if (inputNode && targetInput) return { inputNode, targetInput };
        } catch { /* fall through to legacy link fields */ }
    }
    const inputNode = graph.getNodeById?.(link.target_id);
    const targetInput = inputNode?.inputs?.[link.target_slot];
    return inputNode && targetInput ? { inputNode, targetInput } : null;
}

function _nativeModeSameInput(node, input, otherNode, otherInput) {
    if (node !== otherNode) return false;
    if (input === otherInput) return true;
    const leftIndex = node?.inputs?.indexOf(input) ?? -1;
    const rightIndex = otherNode?.inputs?.indexOf(otherInput) ?? -1;
    return leftIndex >= 0 && leftIndex === rightIndex;
}

function _nativeModePromotionChain(sourceNode, sourceInput) {
    const entries = [];
    const seenSources = new Set();
    const seenBoundaries = new Set();
    const visit = (node, input, depth) => {
        if (!node || !input || seenSources.has(input)) return;
        seenSources.add(input);
        const graph = node.graph;
        const boundaries = graph?.inputs || graph?.inputNode?.slots || [];
        for (const boundary of boundaries) {
            let linked = false;
            for (const linkId of boundary?.linkIds || []) {
                const link = graph.getLink?.(linkId) || getLink(graph, linkId);
                const resolved = _resolveSubgraphLinkTarget(graph, link);
                if (resolved && _nativeModeSameInput(resolved.inputNode, resolved.targetInput, node, input)) {
                    linked = true;
                    break;
                }
            }
            if (!linked) continue;
            const hosts = [];
            for (const host of findSubgraphHosts(graph)) {
                const hostInput = (host.inputs || []).find((candidate) => (
                    candidate._subgraphSlot === boundary
                    || (candidate._subgraphSlot?.id && candidate._subgraphSlot.id === boundary.id)
                ));
                if (!hostInput) continue;
                hosts.push({ host, input: hostInput });
                visit(host, hostInput, depth + 1);
            }
            if (!seenBoundaries.has(boundary)) {
                seenBoundaries.add(boundary);
                entries.push({ boundary, depth, graph, hosts });
            }
        }
    };
    visit(sourceNode, sourceInput, 0);
    return entries;
}

function _nativeModeHostInputConnected(host, input) {
    const index = host?.inputs?.indexOf(input) ?? -1;
    if (index < 0) return false;
    if (typeof host.isInputConnected === 'function') return !!host.isInputConnected(index);
    return !!(host.getInputLink?.(index) || input.link != null);
}

function _removeEmptyLegacyNativeModeBoundaries(pairKey, chain) {
    const linkedByGraph = new Map();
    for (const entry of chain) {
        const linked = linkedByGraph.get(entry.graph) || new Set();
        linked.add(entry.boundary);
        linkedByGraph.set(entry.graph, linked);
    }
    const legacyPattern = new RegExp(`^${pairKey}(?:_\\d+)?$`);
    for (const [graph, linked] of linkedByGraph) {
        const boundaries = graph?.inputs || graph?.inputNode?.slots || [];
        for (const boundary of [...boundaries]) {
            if (linked.has(boundary) || !legacyPattern.test(boundary?.name || '')) continue;
            if ((boundary.linkIds || []).length) continue;
            const externallyLinked = findSubgraphHosts(graph).some((host) => {
                const hostInput = (host.inputs || []).find((candidate) => (
                    candidate._subgraphSlot === boundary
                    || (candidate._subgraphSlot?.id && candidate._subgraphSlot.id === boundary.id)
                ));
                return hostInput && _nativeModeHostInputConnected(host, hostInput);
            });
            if (externallyLinked) continue;
            if (typeof graph.removeInput === 'function') graph.removeInput(boundary);
        }
    }
}

function _nativeModeUniqueName(baseName, reservedNames) {
    const base = String(baseName || 'Target').trim() || 'Target';
    let name = base;
    let suffix = 1;
    while (reservedNames.has(name)) name = `${base}_${suffix++}`;
    reservedNames.add(name);
    return name;
}

function _stageNativeModeWidgetRenames(node, namedPairs, reservedNames) {
    let changed = false;
    for (const { name, pair } of namedPairs) {
        const { widget } = pair;
        if (widget.name === name) continue;
        let temporaryName;
        do {
            temporaryName = `__eclipse_native_mode_rekey_${node.id ?? 'node'}_${pair.key}_${_nativeModeRekeySequence++}`;
        } while (reservedNames.has(temporaryName));
        reservedNames.add(temporaryName);
        widget.name = temporaryName;
        changed ||= widget.name === temporaryName;
    }
    return changed;
}

function _rekeyNativeModePromotionPair(node, pair, name) {
    const { backing, chain, record, widget } = pair;
    let changed = widget.name !== name || widget.label !== name;
    const previousAutoName = widget._eclipse_modeToggleAutoName
        || backing?._eclipse_modeToggleAutoName
        || widget.name;
    const legacyAutoLabel = widget.label || backing?.label;
    widget.name = name;
    if (widget.name !== name) return false;
    widget.label = name;
    widget._eclipse_modeToggleAutoName = name;
    widget._eclipse_modeToggleKey = pair.key;
    if (record?.input) {
        changed ||= record.input._eclipse_modeToggleKey !== pair.key
            || record.input._eclipse_modeToggleAutoName !== name
            || record.input._eclipse_modeToggleTargetId !== record.targetNode?.id;
        record.input._eclipse_modeToggleKey = pair.key;
        record.input._eclipse_modeToggleAutoName = name;
        record.input._eclipse_modeToggleTarget = true;
        record.input._eclipse_modeToggleTargetId = record.targetNode?.id;
    }
    if (backing) {
        changed ||= backing.name !== name || backing.label !== name
            || backing.widget?.name !== name || backing._eclipse_modeToggleKey !== pair.key
            || backing._eclipse_modeToggleTargetId !== record?.targetNode?.id;
        backing.name = name;
        backing.label = name;
        backing.type = 'BOOLEAN';
        backing.widget = { ...(backing.widget || {}), name, type: 'BOOLEAN' };
        backing._eclipse_modeToggleBacking = true;
        backing._eclipse_modeToggleKey = pair.key;
        backing._eclipse_modeToggleAutoName = name;
        backing._eclipse_modeToggleTargetId = record?.targetNode?.id;
    }

    const rebuildDepth = new Map();
    for (const entry of chain) {
        const { boundary } = entry;
        const oldBoundaryName = boundary.name;
        const oldAutomaticName = boundary._eclipse_modeToggleAutoName || oldBoundaryName;
        const automaticLabel = boundary.label == null
            || boundary.label === oldAutomaticName
            || boundary.label === previousAutoName
            || boundary.label === legacyAutoLabel;
        const boundaryChanged = oldBoundaryName !== name
            || (automaticLabel && boundary.label !== name);
        changed ||= boundaryChanged;
        boundary.name = name;
        boundary._eclipse_modeToggleAutoName = name;
        if (automaticLabel) boundary.label = name;
        for (const { host, input } of entry.hosts) {
            const hostChanged = input.name !== name
                || input.label !== (boundary.label ?? name)
                || (input.widget && input.widget.name !== name);
            changed ||= hostChanged;
            input.name = name;
            input.label = boundary.label ?? name;
            if (input.widget) input.widget.name = name;
            if (boundaryChanged || hostChanged) {
                const currentDepth = rebuildDepth.get(host);
                if (currentDepth == null || entry.depth < currentDepth) rebuildDepth.set(host, entry.depth);
            }
        }
    }
    for (const [host] of [...rebuildDepth].sort((left, right) => left[1] - right[1])) {
        host.rebuildInputWidgetBindings?.();
    }
    return changed;
}

function _rehydrateNativeModeToggleWidgets(node, data) {
    const backings = (node.inputs || [])
        .filter(_nativeModeBackingInput)
        .sort((left, right) => _nativeModeKeyIndex(_nativeModePairKey(left)) - _nativeModeKeyIndex(_nativeModePairKey(right)));
    if (!backings.length) return;
    const values = data?.widgets_values || [];
    for (let index = 0; index < backings.length; index++) {
        const backing = backings[index];
        const pairKey = _nativeModePairKey(backing) || `target_${index}`;
        backing._eclipse_modeToggleBacking = true;
        backing._eclipse_modeToggleKey = pairKey;
        let widget = (node.widgets || []).find((candidate) => _nativeModePairKey(candidate) === pairKey);
        const targetId = backing._eclipse_modeToggleTargetId;
        const targetNode = targetId == null ? null : node.graph?.getNodeById?.(targetId);
        const name = backing._eclipse_modeToggleAutoName || backing.name || backing.widget?.name || pairKey;
        if (!widget) {
            widget = createModeToggleNativeWidget(node, targetNode, name, pairKey, name);
        }
        widget._eclipse_backingInput = backing;
        widget._eclipse_targetId = targetNode?.id ?? targetId;
        const restored = values[index];
        if (restored !== undefined) widget.value = _mtNormalizeValue(restored, targetNode?.mode);
        ensureNativeModeToggleBacking(node, widget, name, pairKey);
    }
}

function _installNativeModeToggleConfigureHydration(node) {
    if (!node || node._eclipse_nativeModeConfigureHydration) return;
    node._eclipse_nativeModeConfigureHydration = true;
    const originalConfigure = node.configure;
    node.configure = function (data) {
        const result = originalConfigure?.apply(this, arguments);
        _rehydrateNativeModeToggleWidgets(this, data);
        normalizeModeToggleWidgetValues(this);
        return result;
    };
}

function _installNativeModeToggleLoadHydration() {
    const nodeType = LiteGraph.registered_node_types?.[NODE_NAMES.FAST_MODE_TOGGLE_NATIVE];
    const prototype = nodeType?.prototype;
    if (!prototype || prototype._eclipse_nativeModeLoadHydration) return;
    prototype._eclipse_nativeModeLoadHydration = true;
    const originalOnGraphConfigured = prototype.onGraphConfigured;
    prototype.onGraphConfigured = function () {
        _rehydrateNativeModeToggleWidgets(this);
        return originalOnGraphConfigured?.apply(this, arguments);
    };
}

function _resolveNativeModePromotion(host, hostInput, visited) {
    if (!host?.isSubgraphNode?.() || !host.subgraph || !hostInput?.widgetId) return null;
    const seen = visited || new Set();
    if (seen.has(hostInput)) return null;
    seen.add(hostInput);
    const boundary = _subgraphBoundaryInput(host, hostInput);
    for (const linkId of boundary?.linkIds || []) {
        const link = host.subgraph.getLink?.(linkId) || getLink(host.subgraph, linkId);
        const resolved = _resolveSubgraphLinkTarget(host.subgraph, link);
        if (!resolved) continue;
        const { inputNode, targetInput } = resolved;
        if (inputNode.isSubgraphNode?.()) {
            const nestedInput = (inputNode.inputs || []).find((input) => (
                input === targetInput || input.name === targetInput.name
            ));
            const nested = _resolveNativeModePromotion(inputNode, nestedInput, seen);
            if (nested) return nested;
            continue;
        }
        const widget = inputNode.getWidgetFromSlot?.(targetInput)
            || (inputNode.widgets || []).find((item) => item.name === targetInput.widget?.name);
        const type = inputNode.comfyClass || inputNode.type || '';
        if (type === NODE_NAMES.FAST_MODE_TOGGLE_NATIVE
            && inputNode._eclipse_isModeToggleNative
            && widget?._eclipse_isModeToggleNative) {
            return { sourceNode: inputNode, sourceWidget: widget };
        }
    }
    return null;
}

function _projectedHostWidget(host, input) {
    if (input?._widget) return input._widget;
    return (host.widgets || []).find((widget) => (
        (input.widgetId && widget.widgetId === input.widgetId)
        || widget.name === input.name
    )) || null;
}

function _unbindNativeModePromotion(binding) {
    if (binding?.widget?.callback === binding.wrapper) {
        binding.widget.callback = binding.original;
    }
}

function _forwardNativeModePromotionValue(binding, value, root) {
    const normalized = !!value;
    binding.lastValue = normalized;
    const sourceWidget = binding.sourceWidget;
    if (_nativeModePromotionForwarding.has(sourceWidget)) return;
    _nativeModePromotionForwarding.add(sourceWidget);
    try {
        sourceWidget.callback?.(normalized);
    } finally {
        _nativeModePromotionForwarding.delete(sourceWidget);
    }
    reconcileNativeModePromotions(root || binding.sourceNode?.graph);
}

function _checkNativeModePromotionInteraction(graph) {
    const root = findRootGraph(graph || app.graph);
    if (!root) return;
    const changed = [];
    let bindingCount = 0;
    for (const currentGraph of [root, ...getGraphDescendants(root)]) {
        for (const host of currentGraph?._nodes || []) {
            for (const binding of host?._eclipse_nativeModePromotionBindings?.values?.() || []) {
                bindingCount++;
                const value = !!binding.widget?.value;
                if (value !== binding.lastValue) changed.push({ binding, value });
            }
        }
    }
    for (const { binding, value } of changed) {
        if (!!binding.widget?.value !== value || binding.lastValue === value) continue;
        _forwardNativeModePromotionValue(binding, value, root);
    }
    if (!bindingCount) _nativeModePromotionRoots.delete(root);
}

function _queueNativeModePromotionInteractionCheck(graph) {
    const root = findRootGraph(graph || app.graph);
    if (!root || _nativeModePromotionInteractionJobs.has(root)) return;
    const job = {};
    _nativeModePromotionInteractionJobs.set(root, job);
    queueMicrotask(() => {
        if (_nativeModePromotionInteractionJobs.get(root) !== job) return;
        _nativeModePromotionInteractionJobs.delete(root);
        _checkNativeModePromotionInteraction(root);
    });
}

function _installNativeModePromotionInteractionBridge() {
    if (_nativeModePromotionInteractionInstalled || !document?.addEventListener) return;
    _nativeModePromotionInteractionInstalled = true;
    document.addEventListener('click', (event) => {
        if (!event.target?.closest?.('[role="switch"]')) return;
        for (const root of _nativeModePromotionRoots) {
            _queueNativeModePromotionInteractionCheck(root);
        }
    });
}

function _disposeNativeModePromotionHost(host) {
    for (const binding of host?._eclipse_nativeModePromotionBindings?.values?.() || []) {
        _unbindNativeModePromotion(binding);
    }
    host?._eclipse_nativeModePromotionBindings?.clear?.();
    for (const remove of host?._eclipse_nativeModePromotionListeners || []) remove();
    if (host) host._eclipse_nativeModePromotionListeners = [];
}

function _watchNativeModePromotionHost(host) {
    if (!host?.isSubgraphNode?.() || !host.subgraph || host._eclipse_nativeModePromotionWatching) return;
    host._eclipse_nativeModePromotionWatching = true;
    const listeners = host._eclipse_nativeModePromotionListeners = [];
    const events = host.subgraph.events;
    if (events?.addEventListener) {
        const refresh = () => queueNativeModePromotionReconcile(host.graph || host.subgraph);
        for (const name of ['input-added', 'removing-input', 'widget-promoted', 'widget-demoted', 'configured']) {
            events.addEventListener(name, refresh);
            listeners.push(() => events.removeEventListener?.(name, refresh));
        }
    }
    const originalRemoved = host.onRemoved;
    host.onRemoved = function () {
        _disposeNativeModePromotionHost(this);
        return originalRemoved?.apply(this, arguments);
    };
}

function reconcileNativeModePromotions(graph) {
    _installNativeModePromotionInteractionBridge();
    const root = findRootGraph(graph || app.graph);
    if (!root) return;
    _nativeModePromotionRoots.add(root);
    const synchronizedSources = new Set();
    for (const currentGraph of [root, ...getGraphDescendants(root)]) {
        for (const host of currentGraph?._nodes || []) {
            if (!host?.isSubgraphNode?.() || !host.subgraph) continue;
            _watchNativeModePromotionHost(host);
            const bindings = host._eclipse_nativeModePromotionBindings
                || (host._eclipse_nativeModePromotionBindings = new Map());
            const active = new Set();
            for (const input of host.inputs || []) {
                let resolved = _resolveNativeModePromotion(host, input);
                if (resolved && !synchronizedSources.has(resolved.sourceNode)) {
                    synchronizedSources.add(resolved.sourceNode);
                    const backing = resolved.sourceWidget._eclipse_backingInput;
                    const boundary = _subgraphBoundaryInput(host, input);
                    if (backing && boundary?.name !== resolved.sourceWidget.name
                        && _nativeModePromotionChain(resolved.sourceNode, backing).length) {
                        modeToggleNativeStabilize.call(resolved.sourceNode);
                        resolved = _resolveNativeModePromotion(host, input);
                    }
                }
                const hostWidget = resolved && _projectedHostWidget(host, input);
                if (!resolved || !hostWidget) continue;
                active.add(input);
                const previous = bindings.get(input);
                if (previous?.widget === hostWidget
                    && previous.sourceNode === resolved.sourceNode
                    && previous.sourceWidget === resolved.sourceWidget
                    && hostWidget.callback === previous.wrapper) {
                    const sourceValue = !!resolved.sourceWidget.value;
                    hostWidget.value = sourceValue;
                    previous.lastValue = sourceValue;
                    hostWidget.label = input.label
                        || _subgraphBoundaryInput(host, input)?.label
                        || input.name;
                    continue;
                }
                if (previous) _unbindNativeModePromotion(previous);
                const original = hostWidget.callback;
                let binding;
                const wrapper = function (value) {
                    original?.apply(this, arguments);
                    _forwardNativeModePromotionValue(binding, value, resolved.sourceNode.graph);
                };
                binding = {
                    host,
                    inputName: input.name,
                    lastValue: !!resolved.sourceWidget.value,
                    original,
                    sourceNode: resolved.sourceNode,
                    sourceWidget: resolved.sourceWidget,
                    widget: hostWidget,
                    wrapper,
                };
                hostWidget.callback = wrapper;
                hostWidget.value = !!resolved.sourceWidget.value;
                hostWidget.label = input.label
                    || _subgraphBoundaryInput(host, input)?.label
                    || input.name;
                bindings.set(input, binding);
            }
            for (const [input, binding] of bindings) {
                if (active.has(input)) continue;
                _unbindNativeModePromotion(binding);
                bindings.delete(input);
            }
        }
    }
}

function queueNativeModePromotionReconcile(graph) {
    const root = findRootGraph(graph || app.graph);
    if (!root || _nativeModePromotionJobs.has(root)) return;
    const job = {};
    _nativeModePromotionJobs.set(root, job);
    requestAnimationFrame(() => {
        if (_nativeModePromotionJobs.get(root) !== job) return;
        _nativeModePromotionJobs.delete(root);
        reconcileNativeModePromotions(root);
    });
}

function preserveWidth(node) {
    node._eclipse_tempWidth = node.size[0];
}

function blankInputNames(node) {
    if (node.inputs) {
        for (const inp of node.inputs) {
            if (inp && (/^input_\d+$/i.test(inp.name) || '' === inp.name)) inp.name = ' ';
        }
    }
}

function fitString(ctx, text, maxWidth) {
    if (!text) return '';
    let width = ctx.measureText(text).width;
    if (width <= maxWidth) return text;
    const ellipsisW = ctx.measureText('…').width;
    let len = text.length;
    while (len > 0) {
        len--;
        width = ctx.measureText(text.substring(0, len)).width;
        if (width + ellipsisW <= maxWidth) return text.substring(0, len) + '…';
    }
    return '…';
}

function setupNodeModeRepeater(nodeType) {
    nodeType.prototype.isVirtualNode = true;
    const origOnNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = origOnNodeCreated?.apply(this, arguments);
        this.properties = this.properties || {};
        if (this.outputs?.length) {
            if (this.outputs[0]) {
                this.outputs[0].color_on = '#Fc0';
                this.outputs[0].color_off = '#a80';
            }
        } else {
            this.addOutput('oc', '*', {
                color_on: '#Fc0',
                color_off: '#a80'
            });
        }
        blankInputNames(this);
        const self = this;
        this._eclipse_unhookMode = hookModeProperty(this, (_node, oldMode, newMode) => {
            if (!self._eclipse_configuring) repeaterOnModeChange.call(self, oldMode, newMode);
        });
        scheduleStabilize(this, repeaterStabilize, 100);
        return result;
    };
    const origConfigure = nodeType.prototype.configure;
    nodeType.prototype.configure = function (data) {
        this._eclipse_configuring = true;
        this._eclipse_loading = true;
        const result = origConfigure?.apply(this, arguments);
        this._eclipse_configuring = false;
        scheduleStabilize(this, repeaterStabilize, 300, true);
        return result;
    };
    const origOnRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
        origOnRemoved?.apply(this, arguments);
        if (this._eclipse_unhookMode) {
            this._eclipse_unhookMode();
            this._eclipse_unhookMode = null;
        }
        if (this._eclipse_hookedNodes) {
            for (const unhook of this._eclipse_hookedNodes.values()) unhook();
            this._eclipse_hookedNodes.clear();
        }
        if (this._eclipse_hookedTitles) {
            for (const unhook of this._eclipse_hookedTitles.values()) unhook();
            this._eclipse_hookedTitles.clear();
        }
        if (this._eclipse_stabilizeTimer) {
            clearTimeout(this._eclipse_stabilizeTimer);
            this._eclipse_stabilizeTimer = null;
        }
    };
    nodeType.prototype.onConnectionsChange = function (_dir, _slot, connected, linkInfo) {
        if (!linkInfo) return;
        if (connected) {
            if (this._eclipse_loading) {
                scheduleStabilize(this, repeaterStabilize, 300, true);
            } else {
                if (this._eclipse_stabilizeTimer) {
                    clearTimeout(this._eclipse_stabilizeTimer);
                    this._eclipse_stabilizeTimer = null;
                }
                repeaterStabilize.call(this);
                scheduleStabilize(this, repeaterStabilize, 200, true);
            }
        } else {
            scheduleStabilize(this, repeaterStabilize, 500, true);
        }
    };
    nodeType.prototype.onConnectOutput = function (_slot, _type, _output, targetNode, _targetSlot) {
        if (!targetNode) return false;
        const downstream = (getConnectedOutputNodes(this, true, targetNode)[0] || targetNode).type || '';
        return TOGGLER_TYPES.includes(downstream) || COLLECTOR_TYPES.includes(downstream) || REROUTE_TYPES.includes(downstream);
    };
    nodeType.prototype.onConnectInput = function (slot, _type, _output, sourceNode, _sourceSlot) {
        if (!sourceNode) return false;
        if (getConnectedOutputNodes(this, false).includes(sourceNode)) return false;
        if (getConnectedInputNodes(this).includes(sourceNode)) {
            if (!getConnectedInputNodes(this, slot).includes(sourceNode)) return false;
        }
        return true;
    };
    nodeType.prototype.computeSize = function (out) {
        let size = LGraphNode.prototype.computeSize.call(this, out);
        if (this._eclipse_tempWidth) {
            size[0] = Math.max(this._eclipse_tempWidth, size[0]);
            clearTimeout(this._eclipse_widthTimer);
            this._eclipse_widthTimer = setTimeout(() => {
                this._eclipse_tempWidth = null;
            }, 32);
        }
        return size;
    };
}

function repeaterStabilize() {
    if (!this.graph) return;
    this._eclipse_loading = false;
    preserveWidth(this);
    let changed = stabilizeInputs(this, false);
    const connectedNodes = getConnectedInputNodes(this),
        self = this;
    syncTitleHooks(this, connectedNodes, () => {
        scheduleStabilize(self, repeaterStabilize, 50, true);
    });
    const filtered = getConnectedInputNodesFiltered(this, -1, false),
        hookedNodes = this._eclipse_hookedNodes || (this._eclipse_hookedNodes = new Map()),
        activeIds = new Set(filtered.map((n) => n.id));
    for (const [id, unhook] of hookedNodes) {
        if (!activeIds.has(id)) {
            unhook();
            hookedNodes.delete(id);
        }
    }
    for (const node of filtered) {
        if (!hookedNodes.has(node.id)) {
            const unhook = hookModeProperty(node, (_n, _oldMode, newMode) => {
                if (self._eclipse_propagating) return;
                if (_n._eclipse_repeaterDriven) {
                    if (self.mode === 0 && newMode !== self.mode) {
                        self._eclipse_propagating = true;
                        changeModeOfNodes(_n, self.mode);
                        self._eclipse_propagating = false;
                    }
                    return;
                }
                const allInputs = getConnectedInputNodesFiltered(self, -1, false);
                if (allInputs.length > 1) {
                    if (!allInputs.every((n) => n.mode === newMode)) return;
                }
                if (self.mode !== newMode) {
                    self._eclipse_propagating = true;
                    self.mode = newMode;
                    self._eclipse_propagating = false;
                }
            });
            hookedNodes.set(node.id, unhook);
        }
    }
    if (changed) {
        this.inputs = this.inputs.map((inp) => ({
            ...inp,
            boundingRect: inp.boundingRect || [0, 0, 0, 0]
        }));
        smartResize(this, {
            minWidth: 0,
            minHeight: 0,
            padding: 0
        });
    }
}

function repeaterOnModeChange(oldMode, newMode) {
    if (!this.graph) return;
    if (this._eclipse_propagating) return;
    this._eclipse_propagating = true;
    const connected = getConnectedInputNodesFiltered(this, -1, false);
    if (connected.length) {
        for (const node of connected) {
            node._eclipse_repeaterDriven = true;
            changeModeOfNodes(node, newMode);
            node._eclipse_repeaterDriven = false;
        }
    }
    notifyDownstreamModeChange(this);
    this._eclipse_propagating = false;
}

function setupNodeCollector(nodeType) {
    nodeType.prototype.isVirtualNode = true;
    const origOnNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = origOnNodeCreated?.apply(this, arguments);
        this.properties = this.properties || {};
        if (!this.outputs?.length) this.addOutput('Output', '*');
        blankInputNames(this);
        const self = this;
        this._eclipse_unhookMode = hookModeProperty(this, (_node, oldMode, newMode) => {
            if (!self._eclipse_configuring) collectorOnModeChange.call(self, oldMode, newMode);
        });
        this._eclipse_onUpstreamModeChange = function () {
            if (!self._eclipse_upstreamChangeQueued) {
                self._eclipse_upstreamChangeQueued = true;
                requestAnimationFrame(() => {
                    self._eclipse_upstreamChangeQueued = false;
                    if (self.graph) notifyDownstreamModeChange(self);
                });
            }
        };
        scheduleStabilize(this, collectorStabilize, 100);
        return result;
    };
    const origConfigure = nodeType.prototype.configure;
    nodeType.prototype.configure = function (data) {
        this._eclipse_configuring = true;
        this._eclipse_loading = true;
        const result = origConfigure?.apply(this, arguments);
        this._eclipse_configuring = false;
        scheduleStabilize(this, collectorStabilize, 300, true);
        return result;
    };
    nodeType.prototype.onConnectionsChange = function (_dir, _slot, connected, linkInfo) {
        if (!linkInfo) return;
        const downstream = getConnectedOutputNodes(this, true);
        for (const node of downstream) {
            if (node._eclipse_onChainChange) node._eclipse_onChainChange();
        }
        if (connected) {
            if (this._eclipse_loading) {
                scheduleStabilize(this, collectorStabilize, 300, true);
            } else {
                if (this._eclipse_stabilizeTimer) {
                    clearTimeout(this._eclipse_stabilizeTimer);
                    this._eclipse_stabilizeTimer = null;
                }
                collectorStabilize.call(this);
                scheduleStabilize(this, collectorStabilize, 200, true);
            }
        } else {
            scheduleStabilize(this, collectorStabilize, 500, true);
        }
    };
    nodeType.prototype.onConnectInput = function (slot, _type, _output, sourceNode, _sourceSlot) {
        if (!sourceNode) return false;
        if (getConnectedOutputNodes(this, false).includes(sourceNode)) return false;
        const existing = getConnectedInputNodes(this);
        if (existing.includes(sourceNode)) {
            if (!getConnectedInputNodes(this, slot).includes(sourceNode)) return false;
        }
        if (isReroute(sourceNode)) {
            const upstream = getConnectedInputNodesFiltered(sourceNode, -1, true)[0];
            if (upstream && existing.includes(upstream)) {
                if (!getConnectedInputNodes(this, slot).some((n) => n === sourceNode)) return false;
            }
        }
        return true;
    };
    nodeType.prototype.onConnectOutput = function (_slot, _type, _output, targetNode, _targetSlot) {
        if (!targetNode) return false;
        return !getConnectedInputNodes(this).includes(targetNode);
    };
    const origOnRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
        origOnRemoved?.apply(this, arguments);
        if (this._eclipse_unhookMode) {
            this._eclipse_unhookMode();
            this._eclipse_unhookMode = null;
        }
        if (this._eclipse_hookedTitles) {
            for (const unhook of this._eclipse_hookedTitles.values()) unhook();
            this._eclipse_hookedTitles.clear();
        }
        if (this._eclipse_stabilizeTimer) {
            clearTimeout(this._eclipse_stabilizeTimer);
            this._eclipse_stabilizeTimer = null;
        }
    };
    nodeType.prototype._eclipse_onChainChange = function () {
        if (this._eclipse_loading || this._eclipse_configuring) {
            scheduleStabilize(this, collectorStabilize, 300, true);
        } else {
            if (this._eclipse_stabilizeTimer) {
                clearTimeout(this._eclipse_stabilizeTimer);
                this._eclipse_stabilizeTimer = null;
            }
            collectorStabilize.call(this);
        }
    };
    nodeType.prototype.computeSize = function (out) {
        let size = LGraphNode.prototype.computeSize.call(this, out);
        if (this._eclipse_tempWidth) {
            size[0] = Math.max(this._eclipse_tempWidth, size[0]);
            clearTimeout(this._eclipse_widthTimer);
            this._eclipse_widthTimer = setTimeout(() => {
                this._eclipse_tempWidth = null;
            }, 32);
        }
        return size;
    };
}

function collectorOnModeChange(oldMode, newMode) {
    if (!this.graph) return;
    const connected = getConnectedInputNodesFiltered(this, -1, false);
    for (const node of connected) changeModeOfNodes(node, newMode);
    notifyDownstreamModeChange(this);
}

function collectorStabilize() {
    if (!this.graph) return;
    this._eclipse_loading = false;
    preserveWidth(this);
    let changed = stabilizeInputs(this, true);
    const connected = getConnectedInputNodesFiltered(this, -1, false),
        self = this;
    syncTitleHooks(this, connected, () => {
        scheduleStabilize(self, collectorStabilize, 50, true);
    });
    if (changed) {
        this.inputs = this.inputs.map((inp) => ({
            ...inp,
            boundingRect: inp.boundingRect || [0, 0, 0, 0]
        }));
        smartResize(this, {
            minWidth: 0,
            minHeight: 0,
            padding: 0
        });
        this.setDirtyCanvas(true, false);
    }
}

function setupModeRelay(nodeType) {
    nodeType.prototype.isVirtualNode = true;
    const origOnNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = origOnNodeCreated?.apply(this, arguments);
        this.properties = this.properties || {};
        if (this.outputs?.length) {
            if (this.outputs[0]) {
                this.outputs[0].color_on = '#Fc0';
                this.outputs[0].color_off = '#a80';
            }
        } else {
            this.addOutput('oc', '*', {
                color_on: '#Fc0',
                color_off: '#a80'
            });
        }
        blankInputNames(this);
        const self = this;
        this._eclipse_unhookMode = hookModeProperty(this, (_node, _oldMode, newMode) => {
            if (self._eclipse_propagating) return;
            self._eclipse_propagating = true;
            const connected = getConnectedInputNodesFiltered(self, -1, false);
            if (connected.length) {
                for (const node of connected) changeModeOfNodes(node, newMode);
            }
            if (self.graph?._groups?.length) {
                const deepScope = self.properties?.group_scope === 'deep';
                for (const group of self.graph._groups) {
                    const nodes = getGroupNodes(group);
                    if (nodes.includes(self)) {
                        for (const n of nodes) {
                            if (n !== self && n.mode !== newMode) {
                                if (deepScope) {
                                    // Deep mode: recurse into subgraph internal nodes.
                                    changeModeOfNodes(n, newMode);
                                } else {
                                    // Root mode (default): match native ComfyUI
                                    // "set group to never/bypass" behavior — change
                                    // the subgraph node's mode but do NOT recurse.
                                    n.mode = newMode;
                                    notifyDownstreamModeChange(n);
                                    if (isVueMode()) batchedNotifyVue(n);
                                }
                            }
                        }
                    }
                }
            }
            notifyDownstreamModeChange(self);
            self._eclipse_propagating = false;
        });
        this._eclipse_hookUpstream = () => {
            if (self._eclipse_unhookUpstreamNode) {
                self._eclipse_unhookUpstreamNode();
                self._eclipse_unhookUpstreamNode = null;
            }
            const upstream = getConnectedInputNodesFiltered(self, 0, false);
            const sourceNode = upstream[0];
            if (!sourceNode) return;
            self._eclipse_unhookUpstreamNode = hookModeProperty(sourceNode, (_n, _old, newMode) => {
                if (self._eclipse_propagating) return;
                if (self.mode !== newMode) {
                    self._eclipse_propagating = true;
                    self.mode = newMode;
                    self._eclipse_propagating = false;
                }
            });
        };
        this._eclipse_hookUpstream();
        return result;
    };
    const origConfigure = nodeType.prototype.configure;
    nodeType.prototype.configure = function (data) {
        const result = origConfigure?.apply(this, arguments);
        const self = this;
        setTimeout(() => self._eclipse_hookUpstream?.(), 300);
        return result;
    };
    nodeType.prototype.onConnectionsChange = function () {
        this._eclipse_hookUpstream?.();
    };
    nodeType.prototype.onConnectOutput = function (_slot, _type, _output, targetNode, _targetSlot) {
        if (!targetNode) return false;
        const downstream = (getConnectedOutputNodes(this, true, targetNode)[0] || targetNode).type || '';
        return TOGGLER_TYPES.includes(downstream) || COLLECTOR_TYPES.includes(downstream) || BRIDGE_TYPES.includes(downstream) || BRIDGE_SET_TYPES.includes(downstream) || BRIDGE_GET_TYPES.includes(downstream) || REROUTE_TYPES.includes(downstream) || downstream === NODE_NAMES.NODE_MODE_REPEATER;
    };
    nodeType.prototype.onConnectInput = function (_slot, _type, _output, sourceNode, _sourceSlot) {
        if (!sourceNode) return false;
        if (getConnectedOutputNodes(this, false).includes(sourceNode)) return false;
        return true;
    };
    const origOnRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
        origOnRemoved?.apply(this, arguments);
        if (this._eclipse_unhookMode) {
            this._eclipse_unhookMode();
            this._eclipse_unhookMode = null;
        }
        if (this._eclipse_unhookUpstreamNode) {
            this._eclipse_unhookUpstreamNode();
            this._eclipse_unhookUpstreamNode = null;
        }
    };
    nodeType.prototype.computeSize = function () {
        return [140, 26];
    };
    nodeType.prototype.getExtraMenuOptions = function (_canvas, options) {
        options.push(null);
        const current = this.properties?.group_scope || 'root';
        for (const [val, label] of [
            ['root', 'Group scope: Root nodes only'],
            ['deep', 'Group scope: All nodes incl. subgraphs'],
        ]) {
            options.push({
                content: `${val === current ? '✓ ' : '\u2003'}${label}`,
                callback: () => { this.properties.group_scope = val; },
            });
        }
        return options;
    };
}

function _collectAllBridgeNames(graph, excludeNode) {
    const names = new Set();
    const allGraphs = _collectAllGraphs(graph);
    for (const g of allGraphs) {
        if (!g?._nodes) continue;
        for (const n of g._nodes) {
            if (n === excludeNode) continue;
            if (!BRIDGE_TYPES.includes(n.type)) continue;
            const bname = n.properties?.bridgeName;
            if (bname) names.add(bname);
        }
    }
    return names;
}

// Count pre-existing (non-just-pasted) bridges with the given name, excluding excludeNode.
function _countEstablishedBridges(graph, name, excludeNode) {
    let count = 0;
    const allGraphs = _collectAllGraphs(graph);
    for (const g of allGraphs) {
        if (!g?._nodes) continue;
        for (const n of g._nodes) {
            if (n === excludeNode) continue;
            if (n._eclipse_justAdded) continue;
            if (!BRIDGE_TYPES.includes(n.type)) continue;
            if (n.properties?.bridgeName === name) count++;
        }
    }
    return count;
}

// Maps original bridge name → new name during paste so paired bridges get the same name.
const _bridgePasteRenameMap = new Map();
// Maps old Set name → new name during paste so late-arriving Bridge Gets (inside subgraphs)
// can self-rename when _renameMatchingGets couldn't reach them (subgraph didn't exist yet).
const _bridgeSetPasteRenameMap = new Map();
let _bridgePasteRenameLastRootGraphId = null;
let _bridgePasteRenameMapClearTimer = null;
let _bridgePasteRenamePassTimer = null;

function _clearBridgePasteRenameMaps() {
    _bridgePasteRenameMap.clear();
    _bridgeSetPasteRenameMap.clear();
}

function scheduleBridgePasteRenameMapClear() {
    clearTimeout(_bridgePasteRenameMapClearTimer);
    _bridgePasteRenameMapClearTimer = setTimeout(() => {
        _clearBridgePasteRenameMaps();
        _bridgePasteRenameMapClearTimer = null;
    }, 500);
}

function _collectBridgeRenameCandidates(root) {
    const allGraphs = [root, ...getGraphDescendants(root)];
    const setterNodes = [];
    const getterNodes = [];
    for (const g of allGraphs) {
        if (!g?._nodes) continue;
        for (const n of g._nodes) {
            if (!n?._eclipse_justAdded) continue;
            if (BRIDGE_TYPES.includes(n.type) || BRIDGE_SET_TYPES.includes(n.type)) {
                setterNodes.push(n);
            } else if (BRIDGE_GET_TYPES.includes(n.type)) {
                getterNodes.push(n);
            }
        }
    }
    return { setterNodes, getterNodes };
}

function _clearJustAddedBridgeFlags(root) {
    const allGraphs = [root, ...getGraphDescendants(root)];
    for (const g of allGraphs) {
        if (!g?._nodes) continue;
        for (const n of g._nodes) {
            if (n?._eclipse_justAdded && (BRIDGE_TYPES.includes(n.type) || BRIDGE_SET_TYPES.includes(n.type) || BRIDGE_GET_TYPES.includes(n.type))) {
                n._eclipse_justAdded = false;
            }
        }
    }
}

function runBridgePasteRenamePass() {
    const root = findRootGraph(app.graph);
    if (!root) return;
    if (subgraphOpState.active) {
        // convertToSubgraph/unpackSubgraph path: do not rename, clear pending flags.
        _clearJustAddedBridgeFlags(root);
        return;
    }
    if (app.configuringGraph) {
        scheduleBridgePasteRenamePass(0);
        return;
    }

    const rootId = root.id;
    if (rootId && _bridgePasteRenameLastRootGraphId !== rootId) {
        _clearBridgePasteRenameMaps();
        _bridgePasteRenameLastRootGraphId = rootId;
    }

    const { setterNodes, getterNodes } = _collectBridgeRenameCandidates(root);
    if (setterNodes.length === 0 && getterNodes.length === 0) return;

    // Phase 1: setters/legacy-bridges rename first and populate maps.
    for (const n of setterNodes) {
        if (BRIDGE_TYPES.includes(n.type)) {
            _validateBridgeName(n, true);
        } else if (BRIDGE_SET_TYPES.includes(n.type)) {
            _validateBridgeSetName(n);
        }
        n._eclipse_justAdded = false;
    }

    // Phase 2: gets read the now-populated set-rename map.
    for (const n of getterNodes) {
        const mapped = _bridgeSetPasteRenameMap.get(n.properties?.bridgeName);
        if (mapped !== undefined) {
            _applyBridgeGetRename(n, mapped);
        }
        n._eclipse_justAdded = false;
    }

    // Refresh dynamic bridge-name options only after the new graph attachment
    // and paste-name coordination have settled. Classic canvas reads the live
    // provider directly and does not need a reactivity notification.
    if (isVueMode()) {
        for (const n of getterNodes) batchedRefreshVueWidgetOptions(n);
    }

    scheduleBridgePasteRenameMapClear();
}

function scheduleBridgePasteRenamePass(delay = 0) {
    clearTimeout(_bridgePasteRenamePassTimer);
    _bridgePasteRenamePassTimer = setTimeout(runBridgePasteRenamePass, delay);
}

function _validateBridgeName(node, usePasteMap) {
    const name = node.properties?.bridgeName;
    if (!name) return false;
    // If a paired bridge was already renamed during this paste, reuse its new name.
    if (usePasteMap) {
        const mapped = _bridgePasteRenameMap.get(name);
        if (mapped !== undefined) {
            node.properties.bridgeName = mapped;
            return mapped !== name;
        }
        // Only rename if a complete pair (2+) already exists among non-pasted bridges.
        // If 0-1 pre-existing bridges have this name, the pasted node is creating/joining a pair.
        if (_countEstablishedBridges(node.graph, name, node) < 2) return false;
    }
    const existing = _collectAllBridgeNames(node.graph, node);
    if (!existing.has(name)) return false;
    const baseName = name.replace(/_\d+$/, '');
    let newName = name;
    let tries = 1;
    while (existing.has(newName)) {
        newName = baseName + '_' + tries;
        tries++;
    }
    node.properties.bridgeName = newName;
    if (usePasteMap) {
        _bridgePasteRenameMap.set(name, newName);
    }
    return true;
}

function _collectAllGraphs(graph) {
    const root = findRootGraph(graph) || graph;
    return [root, ...getGraphDescendants(root)];
}

function _syncNamedBridges(sourceBridge, newMode) {
    const name = sourceBridge.properties?.bridgeName;
    if (!name) return;
    const allGraphs = _collectAllGraphs(sourceBridge.graph);
    for (const g of allGraphs) {
        if (!g?._nodes) continue;
        for (const n of g._nodes) {
            if (n === sourceBridge) continue;
            if (!BRIDGE_TYPES.includes(n.type)) continue;
            if (n.properties?.bridgeName !== name) continue;
            if (n.mode !== newMode) {
                n._eclipse_bridgeSyncing = true;
                n.mode = newMode;
                n._eclipse_bridgeSyncing = false;
            }
        }
    }
}

// ──────────────────────────────────────────────────────────
// Mode Bridge Set / Get — new split architecture
// ──────────────────────────────────────────────────────────

function _collectAllBridgeSetNames(graph) {
    const names = new Set();
    for (const g of _collectAllGraphs(graph)) {
        if (!g?._nodes) continue;
        for (const n of g._nodes) {
            if (n.type !== NODE_NAMES.MODE_BRIDGE_SET) continue;
            const bname = n.properties?.bridgeName;
            if (bname) names.add(bname);
        }
    }
    return names;
}

function _syncNamedBridgeGets(sourceSet, newMode) {
    const name = sourceSet.properties?.bridgeName;
    if (!name) return;
    for (const g of _collectAllGraphs(sourceSet.graph)) {
        if (!g?._nodes) continue;
        for (const n of g._nodes) {
            if (n === sourceSet) continue;
            if (n.type !== NODE_NAMES.MODE_BRIDGE_GET) continue;
            if (n.properties?.bridgeName !== name) continue;
            if (n.mode !== newMode) {
                n._eclipse_bridgeSyncing = true;
                n.mode = newMode;
                n._eclipse_bridgeSyncing = false;
            }
        }
    }
}

function _collectMatchingGets(setNode) {
    const name = setNode.properties?.bridgeName;
    if (!name) return [];
    const gets = [];
    for (const g of _collectAllGraphs(setNode.graph)) {
        if (!g?._nodes) continue;
        for (const n of g._nodes) {
            if (n.type !== NODE_NAMES.MODE_BRIDGE_GET) continue;
            if (n.properties?.bridgeName !== name) continue;
            gets.push(n);
        }
    }
    return gets;
}

function _notifyMatchingSets(getNode, newMode, isRepeaterDriven) {
    const name = getNode.properties?.bridgeName;
    if (!name) return;
    for (const g of _collectAllGraphs(getNode.graph)) {
        if (!g?._nodes) continue;
        for (const n of g._nodes) {
            if (n === getNode) continue;
            if (n.type !== NODE_NAMES.MODE_BRIDGE_SET) continue;
            if (n.properties?.bridgeName !== name) continue;
            if (n._eclipse_onGetModeChanged) {
                n._eclipse_onGetModeChanged(getNode, newMode, isRepeaterDriven);
            }
        }
    }
}

function _renameMatchingGets(setNode, oldName, newName, onlyJustAdded = false) {
    if (!oldName || !newName || oldName === newName) return;
    for (const g of _collectAllGraphs(setNode.graph)) {
        if (!g?._nodes) continue;
        for (const n of g._nodes) {
            if (n.type !== NODE_NAMES.MODE_BRIDGE_GET) continue;
            if (n.properties?.bridgeName !== oldName) continue;
            if (onlyJustAdded && !n._eclipse_justAdded) continue;
            _applyBridgeGetRename(n, newName);
        }
    }
}

// Apply a paste rename to a single Bridge Get node (name, combo widget, title).
function _applyBridgeGetRename(node, newName) {
    const oldName = node.properties.bridgeName;
    node.properties.bridgeName = newName;
    const comboW = node.widgets?.find(w => w.name === 'bridge name');
    if (comboW) comboW.value = newName;
    if (node.title === oldName || node.title === 'Get: ' + oldName || node.title === 'Mode Bridge Get') {
        node.title = 'Get: ' + newName;
    }
}

function _validateBridgeSetName(node) {
    const name = node.properties?.bridgeName;
    if (!name) return false;
    let conflict = false;
    for (const g of _collectAllGraphs(node.graph)) {
        if (!g?._nodes) continue;
        for (const n of g._nodes) {
            if (n === node) continue;
            if (n._eclipse_justAdded) continue;
            if (n.type !== NODE_NAMES.MODE_BRIDGE_SET) continue;
            if (n.properties?.bridgeName === name) { conflict = true; break; }
        }
        if (conflict) break;
    }
    if (!conflict) return false;
    const existingNames = _collectAllBridgeSetNames(node.graph);
    const baseName = name.replace(/_\d+$/, '');
    let newName = name;
    let tries = 1;
    while (existingNames.has(newName)) {
        newName = baseName + '_' + tries;
        tries++;
    }
    const oldName = node.properties.bridgeName;
    node.properties.bridgeName = newName;
    node.properties.previousBridgeName = newName;
    _renameMatchingGets(node, oldName, newName, true);
    // Store mapping for late-arriving Bridge Gets (subgraph nodes created after this Set)
    _bridgeSetPasteRenameMap.set(oldName, newName);
    const nameW = node.widgets?.find(w => w.name === 'bridge name');
    if (nameW) nameW.value = newName;
    _updateAutomaticBridgeSetTitle(node, newName);
    nameW?._eclipseCommittedText?.syncCommittedValue(newName, 'paste');
    return true;
}

function _automaticBridgeSetTitle(name) {
    return name || 'Mode Bridge Set';
}

function _syncAutomaticBridgeSetTitle(node, name) {
    node._eclipseAutomaticBridgeSetTitle = _automaticBridgeSetTitle(name);
}

function _updateAutomaticBridgeSetTitle(node, name) {
    const previousTitle = node._eclipseAutomaticBridgeSetTitle || 'Mode Bridge Set';
    const nextTitle = _automaticBridgeSetTitle(name);
    if (node.title === previousTitle) node.title = nextTitle;
    node._eclipseAutomaticBridgeSetTitle = nextTitle;
}

function _commitBridgeSetName(node, value) {
    if (!node.graph || app.configuringGraph) return value;
    const trimmed = String(value ?? '').trim();
    const requestedName = trimmed === '(new)' ? '' : trimmed;
    const existing = new Set();
    for (const graph of _collectAllGraphs(node.graph)) {
        if (!graph?._nodes) continue;
        for (const other of graph._nodes) {
            if (other === node || other.type !== NODE_NAMES.MODE_BRIDGE_SET) continue;
            const otherName = other.properties?.bridgeName;
            if (otherName) existing.add(otherName);
        }
    }

    let finalName = requestedName;
    if (existing.has(finalName)) {
        const baseName = finalName.replace(/_\d+$/, '');
        let index = 1;
        while (existing.has(finalName)) {
            finalName = baseName + '_' + index;
            index++;
        }
    }

    const oldName = node.properties.bridgeName || '';
    node.properties.bridgeName = finalName;
    node.properties.previousBridgeName = finalName;
    if (oldName && oldName !== finalName) {
        _renameMatchingGets(node, oldName, finalName);
    }
    _updateAutomaticBridgeSetTitle(node, finalName);
    node.setDirtyCanvas(true, false);
    return finalName;
}

function bridgeSetStabilize() {
    if (!this.graph) return;
    this._eclipse_loading = false;
    preserveWidth(this);
    let changed = stabilizeInputs(this, true);
    const connectedNodes = getConnectedInputNodes(this),
        self = this;
    syncTitleHooks(this, connectedNodes, () => {
        scheduleStabilize(self, bridgeSetStabilize, 50, true);
    });
    const filtered = getConnectedInputNodesFiltered(this, -1, false),
        hookedNodes = this._eclipse_hookedNodes || (this._eclipse_hookedNodes = new Map()),
        activeIds = new Set(filtered.map((n) => n.id));
    for (const [id, unhook] of hookedNodes) {
        if (!activeIds.has(id)) { unhook(); hookedNodes.delete(id); }
    }
    for (const node of filtered) {
        if (!hookedNodes.has(node.id)) {
            const unhook = hookModeProperty(node, (_n, _oldMode, newMode) => {
                if (self._eclipse_propagating) return;
                if (_n._eclipse_repeaterDriven) {
                    if (self.mode === 0 && newMode !== self.mode) {
                        self._eclipse_propagating = true;
                        changeModeOfNodes(_n, self.mode);
                        self._eclipse_propagating = false;
                    }
                    return;
                }
                if (self.mode !== 0) return;
                const allInputs = getConnectedInputNodesFiltered(self, -1, false);
                if (allInputs.length > 1) {
                    if (!allInputs.every((n) => n.mode === newMode)) return;
                }
                if (self.mode !== newMode) {
                    self._eclipse_propagating = true;
                    self.mode = newMode;
                    self._eclipse_propagating = false;
                }
            });
            hookedNodes.set(node.id, unhook);
        }
    }
    if (changed) {
        this.inputs = this.inputs.map((inp) => ({
            ...inp,
            boundingRect: inp.boundingRect || [0, 0, 0, 0]
        }));
        smartResize(this, { minWidth: 0, minHeight: 0, padding: 0 });
    }
}

function bridgeGetStabilize() {
    if (!this.graph) return;
    this._eclipse_loading = false;
    preserveWidth(this);
    let changed = stabilizeInputs(this, true);
    const connectedNodes = getConnectedInputNodes(this),
        self = this;
    syncTitleHooks(this, connectedNodes, () => {
        scheduleStabilize(self, bridgeGetStabilize, 50, true);
    });
    const filtered = getConnectedInputNodesFiltered(this, -1, false),
        hookedNodes = this._eclipse_hookedNodes || (this._eclipse_hookedNodes = new Map()),
        activeIds = new Set(filtered.map((n) => n.id));
    for (const [id, unhook] of hookedNodes) {
        if (!activeIds.has(id)) { unhook(); hookedNodes.delete(id); }
    }
    for (const node of filtered) {
        if (!hookedNodes.has(node.id)) {
            const unhook = hookModeProperty(node, (_n, _oldMode, newMode) => {
                if (self._eclipse_propagating) return;
                if (_n._eclipse_repeaterDriven) {
                    if (self.mode === 0 && newMode !== self.mode) {
                        self._eclipse_propagating = true;
                        changeModeOfNodes(_n, self.mode);
                        self._eclipse_propagating = false;
                    }
                    return;
                }
                if (self.mode !== 0) return;
                const allInputs = getConnectedInputNodesFiltered(self, -1, false);
                if (allInputs.length > 1) {
                    if (!allInputs.every((n) => n.mode === newMode)) return;
                }
                if (self.mode !== newMode) {
                    self._eclipse_propagating = true;
                    self.mode = newMode;
                    self._eclipse_propagating = false;
                }
            });
            hookedNodes.set(node.id, unhook);
        }
    }
    const name = this.properties?.bridgeName;
    if (name && this.title === 'Mode Bridge Get') {
        this.title = 'Get: ' + name;
        changed = true;
    }
    if (changed) {
        this.inputs = this.inputs.map((inp) => ({
            ...inp,
            boundingRect: inp.boundingRect || [0, 0, 0, 0]
        }));
        smartResize(this, { minWidth: 0, minHeight: 0, padding: 0 });
    }
}

function setupModeBridgeSet(nodeType) {
    nodeType.prototype.isVirtualNode = true;
    const origOnNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = origOnNodeCreated?.apply(this, arguments);
        this.properties = this.properties || {};
        if (undefined === this.properties.bridgeName) this.properties.bridgeName = '';
        if (undefined === this.properties.previousBridgeName) this.properties.previousBridgeName = '';
        this._eclipseAutomaticBridgeSetTitle = 'Mode Bridge Set';
        if (this.outputs?.length) {
            if (this.outputs[0]) {
                this.outputs[0].color_on = '#0Cf';
                this.outputs[0].color_off = '#08a';
            }
        } else {
            this.addOutput('oc', '*', { color_on: '#0Cf', color_off: '#08a' });
        }
        blankInputNames(this);
        const self = this;
        // Text widget for bridge name
        const nameWidget = addCommittedTextWidget(this, 'bridge name', this.properties.bridgeName || '', (value) => {
            return _commitBridgeSetName(self, value);
        }, {
            onSync: (value, { reason }) => {
                if (reason === 'configure') _syncAutomaticBridgeSetTitle(self, value);
            },
        });
        // Mode hook
        this._eclipse_unhookMode = hookModeProperty(this, (_node, _oldMode, newMode) => {
            if (self._eclipse_configuring) return;
            if (self._eclipse_propagating) return;
            self._eclipse_propagating = true;
            bridgeLocalPropagate.call(self, newMode);
            _syncNamedBridgeGets(self, newMode);
            notifyDownstreamModeChange(self);
            self._eclipse_propagating = false;
        });
        // Handler for wireless reverse notifications from Gets
        this._eclipse_onGetModeChanged = function (getNode, newMode, isRepeaterDriven) {
            if (self._eclipse_propagating) return;
            if (self.mode === newMode) return;
            if (isRepeaterDriven) {
                const allGets = _collectMatchingGets(self);
                if (allGets.length > 1 && !allGets.every(g => g.mode === newMode)) return;
            }
            self.mode = newMode;
        };
        this._eclipse_hookedNodes = new Map();
        scheduleStabilize(this, bridgeSetStabilize, 100);
        return result;
    };
    nodeType.prototype.onAdded = function () {
        this._eclipse_justAdded = true;
        scheduleBridgePasteRenamePass();
    };
    const origConfigure = nodeType.prototype.configure;
    nodeType.prototype.configure = function (data) {
        this._eclipse_configuring = true;
        this._eclipse_loading = true;
        const result = origConfigure?.apply(this, arguments);
        this._eclipse_configuring = false;
        // Restore widget from properties
        const nameW = this.widgets?.find(w => w.name === 'bridge name');
        if (nameW) {
            nameW.value = this.properties.bridgeName || '';
            nameW._eclipseCommittedText?.syncCommittedValue(nameW.value, 'configure');
        }
        scheduleStabilize(this, bridgeSetStabilize, 300, true);
        return result;
    };
    nodeType.prototype.onConnectionsChange = function (_dir, _slot, connected, linkInfo) {
        if (!linkInfo) return;
        if (connected) {
            if (this._eclipse_loading) {
                scheduleStabilize(this, bridgeSetStabilize, 300, true);
            } else {
                if (this._eclipse_stabilizeTimer) { clearTimeout(this._eclipse_stabilizeTimer); this._eclipse_stabilizeTimer = null; }
                bridgeSetStabilize.call(this);
                scheduleStabilize(this, bridgeSetStabilize, 200, true);
            }
        } else {
            scheduleStabilize(this, bridgeSetStabilize, 500, true);
        }
    };
    nodeType.prototype.onConnectOutput = function (_slot, _type, _output, targetNode, _targetSlot) {
        if (!targetNode) return false;
        const downstream = (getConnectedOutputNodes(this, true, targetNode)[0] || targetNode).type || '';
        return TOGGLER_TYPES.includes(downstream) || COLLECTOR_TYPES.includes(downstream) || BRIDGE_TYPES.includes(downstream) || BRIDGE_SET_TYPES.includes(downstream) || BRIDGE_GET_TYPES.includes(downstream) || REROUTE_TYPES.includes(downstream) || downstream === NODE_NAMES.NODE_MODE_REPEATER;
    };
    nodeType.prototype.onConnectInput = function (slot, _type, _output, sourceNode, _sourceSlot) {
        if (!sourceNode) return false;
        if (getConnectedOutputNodes(this, false).includes(sourceNode)) return false;
        if (getConnectedInputNodes(this).includes(sourceNode)) {
            if (!getConnectedInputNodes(this, slot).includes(sourceNode)) return false;
        }
        return true;
    };
    const origOnRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
        origOnRemoved?.apply(this, arguments);
        if (this._eclipse_unhookMode) { this._eclipse_unhookMode(); this._eclipse_unhookMode = null; }
        if (this._eclipse_hookedNodes) { for (const unhook of this._eclipse_hookedNodes.values()) unhook(); this._eclipse_hookedNodes.clear(); }
        if (this._eclipse_hookedTitles) { for (const unhook of this._eclipse_hookedTitles.values()) unhook(); this._eclipse_hookedTitles.clear(); }
        if (this._eclipse_stabilizeTimer) { clearTimeout(this._eclipse_stabilizeTimer); this._eclipse_stabilizeTimer = null; }
    };
    nodeType.prototype.computeSize = function (out) {
        let size = LGraphNode.prototype.computeSize.call(this, out);
        if (this._eclipse_tempWidth) {
            size[0] = Math.max(this._eclipse_tempWidth, size[0]);
            clearTimeout(this._eclipse_widthTimer);
            this._eclipse_widthTimer = setTimeout(() => { this._eclipse_tempWidth = null; }, 32);
        }
        return size;
    };
}

function setupModeBridgeGet(nodeType) {
    nodeType.prototype.isVirtualNode = true;
    const origOnNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = origOnNodeCreated?.apply(this, arguments);
        this.properties = this.properties || {};
        if (undefined === this.properties.bridgeName) this.properties.bridgeName = '';
        blankInputNames(this);
        const self = this;
        // Combo widget with dynamic values getter (must be a plain object, not an array)
        const comboOptions = {};
        Object.defineProperty(comboOptions, 'values', {
            get: () => {
                const names = _collectAllBridgeSetNames(self.graph);
                const sorted = [...names].sort();
                const current = self.properties.bridgeName;
                if (current && !names.has(current)) {
                    sorted.unshift(current + ' \u26A0');
                }
                if (!sorted.length) return ['(none)'];
                return sorted;
            },
            enumerable: true,
            configurable: true,
        });
        const comboW = this.addWidget('combo', 'bridge name',
            this.properties.bridgeName || '(none)',
            (value) => {
                if (!self.graph || app.configuringGraph) return;
                let selected = value;
                // Strip orphan marker if present
                if (typeof selected === 'string' && selected.endsWith(' \u26A0')) {
                    selected = selected.slice(0, -2);
                }
                if (selected === '(none)') {
                    self.properties.bridgeName = '';
                    if (self.title !== 'Mode Bridge Get') self.title = 'Mode Bridge Get';
                    self.setDirtyCanvas(true, false);
                    return;
                }
                const prev = self.properties.bridgeName;
                self.properties.bridgeName = selected;
                if (self.title === 'Mode Bridge Get' || self.title === 'Get: ' + prev) {
                    self.title = 'Get: ' + selected;
                }
                self.setDirtyCanvas(true, false);
            },
            comboOptions
        );
        // Mode hook
        this._eclipse_unhookMode = hookModeProperty(this, (_node, _oldMode, newMode) => {
            if (self._eclipse_configuring) return;
            // Wireless sync from Set → local propagation only
            if (self._eclipse_bridgeSyncing) {
                bridgeLocalPropagate.call(self, newMode);
                return;
            }
            // Bubble-up (bridgeStabilize set _eclipse_propagating before self.mode = newMode)
            if (self._eclipse_propagating) {
                _notifyMatchingSets(self, newMode, false);
                return;
            }
            // Direct change (user toggle, group mute, or repeater-driven)
            self._eclipse_propagating = true;
            bridgeLocalPropagate.call(self, newMode);
            _notifyMatchingSets(self, newMode, !!self._eclipse_repeaterDriven);
            self._eclipse_propagating = false;
        });
        this._eclipse_hookedNodes = new Map();
        scheduleStabilize(this, bridgeGetStabilize, 100);
        return result;
    };
    nodeType.prototype.onAdded = function () {
        this._eclipse_justAdded = true;
        scheduleBridgePasteRenamePass();
    };
    const origConfigure = nodeType.prototype.configure;
    nodeType.prototype.configure = function (data) {
        this._eclipse_configuring = true;
        this._eclipse_loading = true;
        const result = origConfigure?.apply(this, arguments);
        this._eclipse_configuring = false;
        // Restore combo from properties
        const comboW = this.widgets?.find(w => w.name === 'bridge name');
        if (comboW) {
            const saved = this.properties.bridgeName || '';
            comboW.value = saved || '(none)';
        }
        scheduleStabilize(this, bridgeGetStabilize, 300, true);
        return result;
    };
    nodeType.prototype.onConnectionsChange = function (_dir, _slot, connected, linkInfo) {
        if (!linkInfo) return;
        if (connected) {
            if (this._eclipse_loading) {
                scheduleStabilize(this, bridgeGetStabilize, 300, true);
            } else {
                if (this._eclipse_stabilizeTimer) { clearTimeout(this._eclipse_stabilizeTimer); this._eclipse_stabilizeTimer = null; }
                bridgeGetStabilize.call(this);
                scheduleStabilize(this, bridgeGetStabilize, 200, true);
            }
        } else {
            scheduleStabilize(this, bridgeGetStabilize, 500, true);
        }
    };
    // Get has no output → no onConnectOutput
    nodeType.prototype.onConnectInput = function (slot, _type, _output, sourceNode, _sourceSlot) {
        if (!sourceNode) return false;
        // Prevent loop: reject if source is a Bridge Set (Get never receives from Set via wire)
        if (BRIDGE_SET_TYPES.includes(sourceNode.type)) return false;
        if (getConnectedInputNodes(this).includes(sourceNode)) {
            if (!getConnectedInputNodes(this, slot).includes(sourceNode)) return false;
        }
        return true;
    };
    const origOnRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
        origOnRemoved?.apply(this, arguments);
        if (this._eclipse_unhookMode) { this._eclipse_unhookMode(); this._eclipse_unhookMode = null; }
        if (this._eclipse_hookedNodes) { for (const unhook of this._eclipse_hookedNodes.values()) unhook(); this._eclipse_hookedNodes.clear(); }
        if (this._eclipse_hookedTitles) { for (const unhook of this._eclipse_hookedTitles.values()) unhook(); this._eclipse_hookedTitles.clear(); }
        if (this._eclipse_stabilizeTimer) { clearTimeout(this._eclipse_stabilizeTimer); this._eclipse_stabilizeTimer = null; }
    };
    nodeType.prototype.computeSize = function (out) {
        let size = LGraphNode.prototype.computeSize.call(this, out);
        if (this._eclipse_tempWidth) {
            size[0] = Math.max(this._eclipse_tempWidth, size[0]);
            clearTimeout(this._eclipse_widthTimer);
            this._eclipse_widthTimer = setTimeout(() => { this._eclipse_tempWidth = null; }, 32);
        }
        return size;
    };
}

function bridgeLocalPropagate(newMode) {
    const connected = getConnectedInputNodesFiltered(this, -1, false);
    if (connected.length) {
        for (const node of connected) {
            node._eclipse_repeaterDriven = true;
            changeModeOfNodes(node, newMode);
            node._eclipse_repeaterDriven = false;
        }
    }
}

// --- Fast Mode Switcher: 3-state cycle (Active / Mute / Bypass) per connected node ---

const MODE_SWITCHER_STATES = [
    { mode: MODE_ALWAYS, label: 'active', color: '#6a6' },
    { mode: MODE_BYPASS, label: 'bypass', color: '#aa6' },
    { mode: MODE_MUTE, label: 'muted', color: '#a44' },
];

function modeToStateIndex(mode) {
    for (let i = 0; i < MODE_SWITCHER_STATES.length; i++) {
        if (MODE_SWITCHER_STATES[i].mode === mode) return i;
    }
    return 0;
}

function syncModeSwitcherWidgets(node) {
    if (!node.graph) return;
    const connectedNodes = getConnectedInputNodesFiltered(node, -1, false);
    let changed = false;
    for (let idx = 0; idx < connectedNodes.length; idx++) {
        const widget = node.widgets?.[idx];
        if (!widget) continue;
        const expectedLabel = MODE_SWITCHER_STATES[modeToStateIndex(connectedNodes[idx].mode)].label;
        if (widget.value !== expectedLabel) {
            widget.value = expectedLabel;
            changed = true;
        }
    }
    if (changed) {
        if (isVueMode()) batchedNotifyVue(node);
        node.setDirtyCanvas(true, false);
    }
}

function requestSwitcherSync(node) {
    if (node._eclipse_syncQueued) return;
    node._eclipse_syncQueued = true;
    requestAnimationFrame(() => {
        node._eclipse_syncQueued = false;
        syncModeSwitcherWidgets(node);
    });
}

function setupModeSwitcher(nodeType, menuActions) {
    nodeType.prototype.isVirtualNode = true;
    nodeType['@toggleRestriction'] = {
        type: 'combo',
        values: ['default', 'max one', 'always one']
    };
    nodeType['@showNav'] = {
        type: 'boolean'
    };
    const origOnNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = origOnNodeCreated?.apply(this, arguments);
        this.serialize_widgets = true;
        this.properties = this.properties || {};
        if (undefined === this.properties.toggleRestriction) this.properties.toggleRestriction = 'default';
        if (undefined === this.properties.collapse_connections) this.properties.collapse_connections = false;
        if (undefined === this.properties.showNav) this.properties.showNav = false;
        if (!this.outputs?.length) this.addOutput('oc', '*');
        blankInputNames(this);
        this._eclipse_isModeSwitcher = true;
        const self = this;
        this._eclipse_onUpstreamModeChange = function () {
            requestSwitcherSync(self);
        };
        this._eclipse_hookedNodes = new Map();
        scheduleStabilize(this, modeSwitcherStabilize, 100);
        return result;
    };
    const origConfigure = nodeType.prototype.configure;
    nodeType.prototype.configure = function (data) {
        this._eclipse_configuring = true;
        this._eclipse_loading = true;
        const result = origConfigure?.apply(this, arguments);
        this._eclipse_configuring = false;
        this._eclipse_isModeSwitcher = true;
        scheduleStabilize(this, modeSwitcherStabilize, 300, true);
        return result;
    };
    nodeType.prototype.onConnectionsChange = function (_dir, _slot, connected, linkInfo) {
        if (!linkInfo) return;
        const downstream = getConnectedOutputNodes(this, true);
        for (const dn of downstream) {
            if (dn._eclipse_onChainChange) dn._eclipse_onChainChange();
        }
        if (connected) {
            if (this._eclipse_loading) {
                scheduleStabilize(this, modeSwitcherStabilize, 300, true);
            } else {
                if (this._eclipse_stabilizeTimer) {
                    clearTimeout(this._eclipse_stabilizeTimer);
                    this._eclipse_stabilizeTimer = null;
                }
                modeSwitcherStabilize.call(this);
                scheduleStabilize(this, modeSwitcherStabilize, 200, true);
            }
        } else {
            scheduleStabilize(this, modeSwitcherStabilize, 500, true);
        }
    };
    nodeType.prototype._eclipse_onChainChange = function () {
        if (this._eclipse_loading || this._eclipse_configuring) {
            scheduleStabilize(this, modeSwitcherStabilize, 300, true);
        } else {
            if (this._eclipse_stabilizeTimer) {
                clearTimeout(this._eclipse_stabilizeTimer);
                this._eclipse_stabilizeTimer = null;
            }
            modeSwitcherStabilize.call(this);
        }
    };
    nodeType.prototype.onConnectOutput = function (_slotIdx, _type, _inputInfo, targetNode, _targetSlot) {
        return !getConnectedInputNodes(this).includes(targetNode);
    };
    nodeType.prototype.onConnectInput = function (_slotIdx, _type, _outputInfo, sourceNode, _sourceSlot) {
        return !getConnectedOutputNodes(this, false).includes(sourceNode);
    };
    const origOnRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
        origOnRemoved?.apply(this, arguments);
        if (this._eclipse_hookedNodes) {
            for (const unhook of this._eclipse_hookedNodes.values()) unhook();
            this._eclipse_hookedNodes.clear();
        }
        if (this._eclipse_hookedTitles) {
            for (const unhook of this._eclipse_hookedTitles.values()) unhook();
            this._eclipse_hookedTitles.clear();
        }
        if (this._eclipse_stabilizeTimer) {
            clearTimeout(this._eclipse_stabilizeTimer);
            this._eclipse_stabilizeTimer = null;
        }
        setCollapseCSS(this, false);
    };
    const origOnSerialize = nodeType.prototype.onSerialize;
    nodeType.prototype.onSerialize = function (data) {
        origOnSerialize?.call(this, data);
        if (data?.inputs) {
            for (const inp of data.inputs) {
                if ('_eclipseHide' === inp?.widget?.name) delete inp.widget;
                delete inp.pos;
            }
        }
    };
    nodeType.prototype.computeSize = function (out) {
        let size = LGraphNode.prototype.computeSize.call(this, out);
        if (this._eclipse_tempWidth) {
            size[0] = Math.max(this._eclipse_tempWidth, size[0]);
            clearTimeout(this._eclipse_widthTimer);
            this._eclipse_widthTimer = setTimeout(() => {
                this._eclipse_tempWidth = null;
            }, 32);
        }
        if (this.properties?.collapse_connections) {
            const slotH = LiteGraph.NODE_SLOT_HEIGHT ?? 20,
                hiddenCount = Math.max((this.inputs?.length || 0) - 1, 0);
            if (hiddenCount > 0) size[1] = size[1] - hiddenCount * slotH;
        }
        return size;
    };
    nodeType.prototype.getExtraMenuOptions = function (_canvas, options) {
        options.push(null);
        options.push({
            content: this.properties?.collapse_connections ? 'Show Connections' : 'Collapse Connections',
            callback: () => {
                this.properties.collapse_connections = !this.properties.collapse_connections;
                scheduleStabilize(this, modeSwitcherStabilize, 0, true);
            },
        });
        options.push({
            content: this.properties?.showNav ? 'Hide Nav Arrows' : 'Show Nav Arrows',
            callback: () => {
                this.properties.showNav = !this.properties.showNav;
                this.setDirtyCanvas(true, false);
            },
        });
        options.push(null);
        for (const action of menuActions) {
            options.push({
                content: action,
                callback: () => this._eclipse_handleAction(action)
            });
        }
        options.push(null);
        const currentRestriction = this.properties?.toggleRestriction || 'default';
        for (const restriction of ['default', 'max one', 'always one']) {
            options.push({
                content: `${restriction === currentRestriction ? '✓ ' : '  '}Restriction: ${restriction}`,
                callback: () => {
                    this.properties.toggleRestriction = restriction;
                    const self = this;
                    requestAnimationFrame(() => {
                        requestAnimationFrame(() => self.setDirtyCanvas(true, false));
                    });
                },
            });
        }
        return options;
    };
    nodeType.prototype._eclipse_handleAction = function (action) {
        const alwaysOne = 'always one' === this.properties?.toggleRestriction,
            widgets = this.widgets || [];
        if (action === 'Enable all') {
            const restrictOne = (this.properties?.toggleRestriction || '').includes(' one');
            for (let idx = 0; idx < widgets.length; idx++) widgets[idx]._eclipse_setMode?.(restrictOne && idx > 0 ? MODE_MUTE : MODE_ALWAYS, true);
        } else if (action === 'Mute all') {
            for (let idx = 0; idx < widgets.length; idx++) widgets[idx]._eclipse_setMode?.(alwaysOne && 0 === idx ? MODE_ALWAYS : MODE_MUTE, true);
        } else if (action === 'Bypass all') {
            for (let idx = 0; idx < widgets.length; idx++) widgets[idx]._eclipse_setMode?.(alwaysOne && 0 === idx ? MODE_ALWAYS : MODE_BYPASS, true);
        } else if (action === 'Toggle all') {
            for (const w of widgets) {
                if (!w._eclipse_cycleMode) continue;
                w._eclipse_cycleMode(true);
            }
        }
        // Redraw each custom widget's own canvas (Vue mode renders them per-widget).
        for (const w of widgets) w.triggerDraw?.();
        // Defer notify+redraw so it lands after the menu overlay unmounts.
        // In Vue/Nodes 2.0 the synchronous notifyVue + setDirtyCanvas fired
        // inside a menu callback is clobbered by Vue's reflow on menu close,
        // leaving the custom-drawn switches visually stale until collapse/expand.
        if (isVueMode()) batchedNotifyVue(this);
        const self = this;
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                if (isVueMode()) notifyVue(self);
                self.setDirtyCanvas(true, false);
            });
        });
    };
}

// Resolve the best nav target for a Mode Switcher widget.
// Bridge Set → find matching Gets in a different group → group (with zoom)
// Bridge (legacy) → find sibling bridge by bridgeName in a different group → group (with zoom)
// Relay  → find its containing group → group (with zoom)
// Other  → the node itself
function _resolveNavTarget(node, graph, ownerNode) {
    if (!node || !graph) return null;
    // Mode Bridge Set: find matching Gets with same name, pick one in a different group
    if (BRIDGE_SET_TYPES.includes(node.type)) {
        const name = node.properties?.bridgeName;
        if (name) {
            const switcherGroup = ownerNode ? _findContainingGroup(ownerNode, graph) : null;
            const allGraphs = _collectAllGraphs(graph);
            let fallbackGet = null;
            for (const g of allGraphs) {
                if (!g?._nodes) continue;
                for (const n of g._nodes) {
                    if (n.type !== NODE_NAMES.MODE_BRIDGE_GET) continue;
                    if (n.properties?.bridgeName !== name) continue;
                    const grp = _findContainingGroup(n, g);
                    if (grp && grp !== switcherGroup) return { target: grp, isGroup: true };
                    if (!fallbackGet && (!grp || grp !== switcherGroup)) fallbackGet = { node: n, group: grp };
                }
            }
            if (fallbackGet) {
                if (fallbackGet.group) return { target: fallbackGet.group, isGroup: true };
                return { target: fallbackGet.node, isGroup: false };
            }
        }
        return { target: node, isGroup: false };
    }
    // Legacy Mode Bridge: find sibling bridges with same name, pick one in a different group
    if (BRIDGE_TYPES.includes(node.type)) {
        const name = node.properties?.bridgeName;
        if (name) {
            const switcherGroup = ownerNode ? _findContainingGroup(ownerNode, graph) : null;
            const allGraphs = _collectAllGraphs(graph);
            let fallbackSibling = null;
            for (const g of allGraphs) {
                if (!g?._nodes) continue;
                for (const n of g._nodes) {
                    if (n === node || !BRIDGE_TYPES.includes(n.type)) continue;
                    if (n.properties?.bridgeName !== name) continue;
                    const grp = _findContainingGroup(n, g);
                    if (grp && grp !== switcherGroup) return { target: grp, isGroup: true };
                    if (!fallbackSibling && (!grp || grp !== switcherGroup)) fallbackSibling = { node: n, group: grp };
                }
            }
            if (fallbackSibling) {
                if (fallbackSibling.group) return { target: fallbackSibling.group, isGroup: true };
                return { target: fallbackSibling.node, isGroup: false };
            }
        }
        return { target: node, isGroup: false };
    }
    // Mode Relay: find its containing group
    if (RELAY_TYPES.includes(node.type)) {
        const grp = _findContainingGroup(node, graph);
        if (grp) return { target: grp, isGroup: true };
        return { target: node, isGroup: false };
    }
    // Anything else: jump to the node directly
    return { target: node, isGroup: false };
}

function _findContainingGroup(node, graph) {
    if (!graph?._groups?.length) return null;
    for (const group of graph._groups) {
        const nodes = getGroupNodes(group);
        if (nodes.includes(node)) return group;
    }
    return null;
}

// Map BaseWidget combo value (label string) → mode int. Tolerates legacy
// numeric indices left over from workflows saved by the pre-3.5.8 plain
// 'custom' widget.
function _msValueToStateIndex(v) {
    if (typeof v === 'string') {
        for (let i = 0; i < MODE_SWITCHER_STATES.length; i++) {
            if (MODE_SWITCHER_STATES[i].label === v) return i;
        }
        return 0;
    }
    const n = Number(v);
    if (Number.isFinite(n) && n >= 0 && n < MODE_SWITCHER_STATES.length) return n | 0;
    return 0;
}

function _msValueToMode(v) {
    return MODE_SWITCHER_STATES[_msValueToStateIndex(v)].mode;
}

const _MS_LABELS = MODE_SWITCHER_STATES.map((s) => s.label);

function createModeSwitcherWidget(ownerNode, targetNode, title, slotIdx) {
    const initialLabel = MODE_SWITCHER_STATES[modeToStateIndex(targetNode.mode)].label;
    // Stable, unique widget name keyed off the input SLOT INDEX (not target
    // node id). Slot index survives copy/paste and subgraph duplication
    // (target node ids change on paste, but slot positions stay the same).
    // Side-panel + subgraph promotion bind to widget.name, so the name must
    // be stable across paste / re-id for promoted bindings to survive.
    const stableName = `target_${slotIdx}`;
    // Use a real BaseWidget (combo type) so:
    //   1) the Vue side panel renders it (combo has a registered Vue component)
    //   2) it's eligible for "Convert widget to input" / subgraph promotion
    //   3) widget value is registered in widgetValueStore (Pinia reactive)
    // Canvas appearance + click handling are restored by overriding draw/mouse.
    const widget = ownerNode.addWidget('combo', stableName, initialLabel, null, {
        values: _MS_LABELS,
    });
    // Display name in side panel + custom canvas paint. widget.name is the
    // stable slot key (target_<idx>); widget.label is the human-readable title.
    widget.label = title;
    widget._eclipse_targetId = targetNode.id;
    widget._eclipse_isModeSwitcher = true;

    // Resolve the *current* target node from the graph by id. The widget's
    // _eclipse_targetId is updated by modeSwitcherStabilize whenever the slot
    // gets a different upstream node (e.g. user disconnects A and connects B
    // into the same slot). Using a closure-captured targetNode would keep
    // toggling the old node.
    const resolveTarget = () => ownerNode.graph?.getNodeById(widget._eclipse_targetId) || null;

    // Programmatic mode set — assigns widget.value directly (does NOT fire
    // callback). Used by menu actions and restriction enforcement.
    widget._eclipse_setMode = function (mode, apply) {
        if (false !== apply) {
            const t = resolveTarget();
            if (t) changeModeOfNodes(t, mode);
        }
        widget.value = MODE_SWITCHER_STATES[modeToStateIndex(mode)].label;
    };

    widget._eclipse_cycleMode = function (force) {
        const restriction = ownerNode.properties?.toggleRestriction || 'default';
        const restrictOne = true !== force && restriction.includes(' one');
        const alwaysOne = true !== force && 'always one' === restriction;
        const curIdx = _msValueToStateIndex(widget.value);
        const next = (curIdx + 1) % MODE_SWITCHER_STATES.length;
        const curMode = MODE_SWITCHER_STATES[curIdx].mode;
        const nextMode = MODE_SWITCHER_STATES[next].mode;
        // "always one": refuse to cycle away from the last active widget.
        if (alwaysOne && curMode === MODE_ALWAYS && nextMode !== MODE_ALWAYS) {
            const otherActive = (ownerNode.widgets || []).some(
                (w) => w !== widget && _msValueToMode(w.value) === MODE_ALWAYS
            );
            if (!otherActive) return;
        }
        widget.value = MODE_SWITCHER_STATES[next].label;
        const t = resolveTarget();
        if (t) changeModeOfNodes(t, nextMode);
        // "max one" / "always one": when becoming active, mute any other active widgets.
        if (restrictOne && nextMode === MODE_ALWAYS) {
            for (const w of ownerNode.widgets || []) {
                if (w === widget || !w._eclipse_setMode) continue;
                if (_msValueToMode(w.value) === MODE_ALWAYS) {
                    w._eclipse_setMode(MODE_MUTE, true);
                }
            }
        }
    };

    // callback fires when widget.value changes via BaseWidget.setValue, which
    // is triggered by the side-panel combo dropdown (and by canvas-side
    // BaseWidget click handling — but we override mouse to bypass that path).
    widget.callback = function (newValue) {
        const newMode = _msValueToMode(newValue);
        const t = resolveTarget();
        if (t) changeModeOfNodes(t, newMode);
        // Enforce restriction when user picks a value from the side panel.
        const restriction = ownerNode.properties?.toggleRestriction || 'default';
        const restrictOne = restriction.includes(' one');
        if (restrictOne && newMode === MODE_ALWAYS) {
            for (const w of ownerNode.widgets || []) {
                if (w === widget || !w._eclipse_setMode) continue;
                if (_msValueToMode(w.value) === MODE_ALWAYS) {
                    w._eclipse_setMode(MODE_MUTE, true);
                }
            }
        }
        if (isVueMode()) notifyVue(ownerNode);
        ownerNode.setDirtyCanvas(true, false);
    };

    // Override BaseWidget's combo draw with the existing tri-state pill paint.
    // BaseWidget signature: drawWidget(ctx, { width, showText, ... }) — y/height
    // come from the widget instance (this.y, this.height). Stock combo would
    // paint ◀ ▶ arrows + value text, so we replace it entirely.
    const _paintTriState = function (ctx, width, y, height) {
        const state = MODE_SWITCHER_STATES[_msValueToStateIndex(widget.value)] || MODE_SWITCHER_STATES[0];
        const showNav = false !== ownerNode.properties?.showNav;
        // Background
        ctx.fillStyle = '#2a2a2a';
        ctx.beginPath();
        ctx.roundRect(15, y, width - 30, height, 4);
        ctx.fill();
        ctx.strokeStyle = '#444';
        ctx.stroke();
        let xCursor = width - 15;
        // Nav arrow (jump-to target node)
        if (showNav) {
            xCursor -= 7;
            const centerY = y + 0.5 * height;
            ctx.fillStyle = ctx.strokeStyle = '#89A';
            ctx.lineJoin = 'round';
            ctx.lineCap = 'round';
            ctx.beginPath();
            ctx.moveTo(xCursor, centerY);
            ctx.lineTo(xCursor - 7, centerY + 6);
            ctx.lineTo(xCursor - 7, centerY + 3);
            ctx.lineTo(xCursor - 14, centerY + 3);
            ctx.lineTo(xCursor - 14, centerY - 3);
            ctx.lineTo(xCursor - 7, centerY - 3);
            ctx.lineTo(xCursor - 7, centerY - 6);
            ctx.closePath();
            ctx.fill();
            ctx.stroke();
            xCursor -= 21;
            xCursor -= 4;
            ctx.strokeStyle = '#444';
            ctx.beginPath();
            ctx.moveTo(xCursor, y + 2);
            ctx.lineTo(xCursor, y + height - 2);
            ctx.stroke();
        }
        // State indicator (colored circle)
        xCursor -= 7;
        const radius = 0.32 * height;
        const circleX = xCursor - radius;
        ctx.fillStyle = state.color;
        ctx.beginPath();
        ctx.arc(circleX, y + 0.5 * height, radius, 0, 2 * Math.PI);
        ctx.fill();
        xCursor = circleX - radius;
        // State label text
        xCursor -= 6;
        ctx.textAlign = 'right';
        ctx.fillStyle = state.color;
        ctx.font = `${Math.max(10, 0.5 * height)}px Arial`;
        ctx.fillText(state.label, xCursor, y + 0.7 * height);
        const stateTextWidth = Math.max(
            ctx.measureText('active').width,
            ctx.measureText('muted').width,
            ctx.measureText('bypass').width
        );
        // Node title
        const titleX = xCursor - stateTextWidth - 10;
        const maxTitleWidth = titleX - 25;
        ctx.textAlign = 'left';
        ctx.fillStyle = state.mode === MODE_ALWAYS ? '#ddd' : '#999';
        ctx.font = `${Math.max(10, 0.55 * height)}px Arial`;
        if (maxTitleWidth > 0) ctx.fillText(fitString(ctx, (widget.label || widget.name || ''), maxTitleWidth), 25, y + 0.7 * height);
    };
    // New BaseWidget API path (ComfyUI ≥ 1.42 / Vue Nodes 2.0).
    widget.drawWidget = function (ctx, options) {
        const width = options?.width ?? 0;
        const y = this.y ?? 0;
        const height = this.height ?? (LiteGraph.NODE_WIDGET_HEIGHT || 20);
        _paintTriState(ctx, width, y, height);
    };
    // Legacy plain-object widget API (older builds + some fallback paths).
    widget.draw = function (ctx, _node, width, y, height) {
        _paintTriState(ctx, width, y, height);
    };

    // Override BaseWidget's combo click handler. Concrete BaseWidget instances
    // route canvas clicks through `widget.onClick({e, node, canvas})` (called
    // from LGraphCanvas.processWidgetClick). The default for combo would
    // increment / open a context menu — we replace it with cycle + nav-arrow
    // hit testing.
    widget.onClick = function (info) {
        const node = info?.node ?? ownerNode;
        const canvas = info?.canvas ?? app.canvas;
        const mouse = canvas?.graph_mouse || [0, 0];
        const localX = mouse[0] - (node.pos?.[0] ?? 0);
        const nodeWidth = node.size?.[0] ?? 0;
        if (false !== ownerNode.properties?.showNav && localX >= nodeWidth - 15 - 32) {
            const directTarget = ownerNode.graph?.getNodeById(widget._eclipse_targetId);
            const resolved = _resolveNavTarget(directTarget, ownerNode.graph, ownerNode);
            if (canvas && resolved?.target) {
                canvas.centerOnNode?.(resolved.target);
                if (resolved.isGroup && resolved.target._size) {
                    const scale = canvas.ds?.scale || 1;
                    const zoomW = canvas.canvas.width / resolved.target._size[0] - 0.02,
                        zoomH = canvas.canvas.height / resolved.target._size[1] - 0.02;
                    canvas.setZoom?.(Math.min(scale, zoomW, zoomH), [canvas.canvas.width / 2, canvas.canvas.height / 2]);
                }
                canvas.setDirty?.(true, true);
            }
            return;
        }
        widget._eclipse_cycleMode();
        if (isVueMode()) notifyVue(ownerNode);
        ownerNode.setDirtyCanvas(true, false);
    };
    // Plain-object fallback path (older canvas / pre-concrete-widget builds).
    widget.mouse = function (event, pos, nodeInfo) {
        if ('pointerdown' !== event.type) return true;
        if (false !== ownerNode.properties?.showNav && pos[0] >= (nodeInfo?.size?.[0] ?? 0) - 15 - 32) {
            const directTarget = ownerNode.graph?.getNodeById(widget._eclipse_targetId);
            const resolved = _resolveNavTarget(directTarget, ownerNode.graph, ownerNode);
            const canvas = app.canvas;
            if (canvas && resolved?.target) {
                canvas.centerOnNode?.(resolved.target);
                if (resolved.isGroup && resolved.target._size) {
                    const scale = canvas.ds?.scale || 1;
                    const zoomW = canvas.canvas.width / resolved.target._size[0] - 0.02,
                        zoomH = canvas.canvas.height / resolved.target._size[1] - 0.02;
                    canvas.setZoom?.(Math.min(scale, zoomW, zoomH), [canvas.canvas.width / 2, canvas.canvas.height / 2]);
                }
                canvas.setDirty?.(true, true);
            }
            return true;
        }
        widget._eclipse_cycleMode();
        if (isVueMode()) notifyVue(ownerNode);
        ownerNode.setDirtyCanvas(true, false);
        return true;
    };
    // Disable drag-scrubbing (BaseSteppedWidget defaults).
    widget.onDrag = function () { /* no-op */ };
    widget.computeSize = function (w) { return [w, LiteGraph.NODE_WIDGET_HEIGHT || 20]; };
    widget.serializeValue = function () { return widget.value; };
    return widget;
}

function modeSwitcherStabilize() {
    if (!this.graph) return;
    this._eclipse_loading = false;
    preserveWidth(this);
    let changed = stabilizeInputs(this, true, 'hide');
    const connectedNodes = getConnectedInputNodesFiltered(this, -1, false);
    // Helper: drop a widget from this node's widget list at index, calling
    // onRemove so BaseWidget unregisters itself from the Vue widget store.
    const removeAt = (idx) => {
        const w = this.widgets?.[idx];
        if (!w) return;
        try { w.onRemove?.(); } catch { /* ignore */ }
        this.widgets.splice(idx, 1);
    };
    for (let idx = 0; idx < connectedNodes.length; idx++) {
        const targetNode = connectedNodes[idx];
        if (!targetNode) continue;
        let widget = this.widgets?.[idx];
        const title = targetNode.title;
        const expectedName = `target_${idx}`;
        if (widget) {
            // Update runtime target reference + label. Widget identity (name)
            // is keyed off slot index, so the same widget is reused across
            // target swaps / renames / paste-with-new-ids. This preserves
            // subgraph-promoted bindings (which key off widget.name).
            if (widget._eclipse_targetId !== targetNode.id) {
                widget._eclipse_targetId = targetNode.id;
                changed = true;
            }
            if (widget.label !== title) {
                widget.label = title;
                changed = true;
            }
        } else {
            preserveWidth(this);
            widget = createModeSwitcherWidget(this, targetNode, title, idx);
            // addWidget already pushed it; if it didn't land at idx (e.g. an
            // older widget existed past idx), splice into place.
            const lastIdx = this.widgets.length - 1;
            if (lastIdx !== idx) {
                this.widgets.pop();
                this.widgets.splice(idx, 0, widget);
            }
            changed = true;
        }
        // Sync widget value to current target mode without firing callback.
        const expectedLabel = MODE_SWITCHER_STATES[modeToStateIndex(targetNode.mode)].label;
        if (widget.value !== expectedLabel) {
            widget.value = expectedLabel;
            changed = true;
        }
        // Belt & suspenders: ensure stable name (slot-index keyed).
        if (widget.name !== expectedName) {
            widget.name = expectedName;
            changed = true;
        }
    }
    while (this.widgets && this.widgets.length > connectedNodes.length) {
        removeAt(this.widgets.length - 1);
        changed = true;
    }
    const hookedNodes = this._eclipse_hookedNodes || (this._eclipse_hookedNodes = new Map()),
        activeIds = new Set(connectedNodes.map((n) => n.id));
    for (const [id, unhook] of hookedNodes) {
        if (!activeIds.has(id)) {
            unhook();
            hookedNodes.delete(id);
        }
    }
    const self = this;
    for (const target of connectedNodes) {
        if (!hookedNodes.has(target.id)) {
            const unhook = hookModeProperty(target, () => {
                requestSwitcherSync(self);
            });
            hookedNodes.set(target.id, unhook);
        }
    }
    syncTitleHooks(this, connectedNodes, () => {
        scheduleStabilize(self, modeSwitcherStabilize, 50, true);
    });
    if (changed) {
        if (isVueMode()) batchedNotifyVue(this);
        smartResize(this, {
            minWidth: 0,
            minHeight: 0,
            padding: 0
        });
        scheduleSubgraphHostRefresh(this.graph);
    }
}

// --- Fast Mode Toggle: 2-state toggle (Active / Mute-or-Bypass) per connected node ---
// Uses the same pill-paint look as Fast Mode Switcher, but cycles between Active
// and one of {Mute, Bypass} chosen via right-click context menu.
// Default modeOff = MODE_BYPASS (4). Property: node.properties.modeOff ∈ {2, 4}.

const _MT_STATE_ACTIVE = { mode: MODE_ALWAYS, label: 'active', color: '#6a6' };
const _MT_STATE_MUTE = { mode: MODE_MUTE, label: 'muted', color: '#a44' };
const _MT_STATE_BYPASS = { mode: MODE_BYPASS, label: 'bypass', color: '#aa6' };

function _mtStates(modeOff) {
    return [_MT_STATE_ACTIVE, modeOff === MODE_MUTE ? _MT_STATE_MUTE : _MT_STATE_BYPASS];
}

function _mtNormalizeValue(value, targetMode) {
    if (typeof value === 'boolean') return value;
    if (typeof value === 'string') {
        const normalized = value.trim().toLowerCase();
        if (['active', 'on', 'true'].includes(normalized)) return true;
        if (['muted', 'bypass', 'off', 'false'].includes(normalized)) return false;
        if (normalized === '0') return true;
        if (normalized === '1') return false;
    }
    if (value === 0) return true;
    if (value === 1) return false;
    return targetMode === MODE_ALWAYS;
}

function _mtValueToMode(states, value, targetMode) {
    return _mtNormalizeValue(value, targetMode) ? MODE_ALWAYS : states[1].mode;
}

function _mtWidgetTarget(ownerNode, widget) {
    return ownerNode.graph?.getNodeById(widget._eclipse_targetId) || null;
}

function _mtWidgetMode(ownerNode, widget, states) {
    const target = _mtWidgetTarget(ownerNode, widget);
    return _mtValueToMode(states, widget.value, target?.mode);
}

function normalizeModeToggleWidgetValues(node) {
    let changed = false;
    for (const widget of node.widgets || []) {
        if (!widget._eclipse_isModeToggle) continue;
        const target = _mtWidgetTarget(node, widget);
        const normalized = target
            ? target.mode === MODE_ALWAYS
            : _mtNormalizeValue(widget.value, target?.mode);
        if (widget.value !== normalized) {
            widget.value = normalized;
            changed = true;
        }
    }
    return changed;
}

function syncModeToggleWidgets(node) {
    if (!node.graph) return;
    const connectedNodes = node._eclipse_isModeToggleNative
        ? getNativeModeToggleTargets(node)
        : getConnectedInputNodesFiltered(node, -1, false);
    let changed = false;
    for (let idx = 0; idx < connectedNodes.length; idx++) {
        const widget = node.widgets?.[idx];
        if (!widget) continue;
        const expectedValue = connectedNodes[idx].mode === MODE_ALWAYS;
        if (widget.value !== expectedValue) {
            widget.value = expectedValue;
            changed = true;
        }
    }
    if (changed) {
        if (isVueMode()) batchedNotifyVue(node);
        node.setDirtyCanvas(true, false);
    }
    if (node._eclipse_isModeToggleNative) queueNativeModePromotionReconcile(node.graph);
}

function requestToggleSync(node) {
    if (node._eclipse_syncQueued) return;
    node._eclipse_syncQueued = true;
    requestAnimationFrame(() => {
        node._eclipse_syncQueued = false;
        syncModeToggleWidgets(node);
    });
}

function createModeToggleWidget(ownerNode, targetNode, title, slotIdx) {
    const initialValue = targetNode.mode === MODE_ALWAYS;
    const stableName = `target_${slotIdx}`;
    const widget = ownerNode.addWidget('toggle', stableName, initialValue, () => { });
    widget.label = title;
    widget._eclipse_targetId = targetNode.id;
    widget._eclipse_isModeToggle = true;

    const currentStates = () => _mtStates(ownerNode.properties?.modeOff ?? MODE_BYPASS);
    const resolveTarget = () => _mtWidgetTarget(ownerNode, widget);

    widget._eclipse_setMode = function (mode, apply) {
        if (false !== apply) {
            const t = resolveTarget();
            if (t) changeModeOfNodes(t, mode);
        }
        widget.value = mode === MODE_ALWAYS;
    };

    widget._eclipse_cycleMode = function (force) {
        const st = currentStates();
        const restriction = ownerNode.properties?.toggleRestriction || 'default';
        const restrictOne = true !== force && restriction.includes(' one');
        const alwaysOne = true !== force && 'always one' === restriction;
        const target = resolveTarget();
        const curMode = _mtValueToMode(st, widget.value, target?.mode);
        const nextMode = curMode === MODE_ALWAYS ? st[1].mode : MODE_ALWAYS;
        if (alwaysOne && curMode === MODE_ALWAYS && nextMode !== MODE_ALWAYS) {
            const otherActive = (ownerNode.widgets || []).some(
                (w) => w !== widget && _mtWidgetMode(ownerNode, w, st) === MODE_ALWAYS
            );
            if (!otherActive) return;
        }
        widget.value = nextMode === MODE_ALWAYS;
        if (target) changeModeOfNodes(target, nextMode);
        if (restrictOne && nextMode === MODE_ALWAYS) {
            for (const w of ownerNode.widgets || []) {
                if (w === widget || !w._eclipse_setMode) continue;
                if (_mtWidgetMode(ownerNode, w, st) === MODE_ALWAYS) {
                    w._eclipse_setMode(st[1].mode, true);
                }
            }
        }
    };

    widget.callback = function (newValue) {
        const st = currentStates();
        const t = resolveTarget();
        const normalized = _mtNormalizeValue(newValue, t?.mode);
        widget.value = normalized;
        const newMode = normalized ? MODE_ALWAYS : st[1].mode;
        if (t) changeModeOfNodes(t, newMode);
        const restriction = ownerNode.properties?.toggleRestriction || 'default';
        const restrictOne = restriction.includes(' one');
        if (restrictOne && newMode === MODE_ALWAYS) {
            for (const w of ownerNode.widgets || []) {
                if (w === widget || !w._eclipse_setMode) continue;
                if (_mtWidgetMode(ownerNode, w, st) === MODE_ALWAYS) {
                    w._eclipse_setMode(st[1].mode, true);
                }
            }
        }
        if (isVueMode()) notifyVue(ownerNode);
        ownerNode.setDirtyCanvas(true, false);
    };

    const _paintPill = function (ctx, width, y, height) {
        const st = currentStates();
        const state = st[_mtNormalizeValue(widget.value, resolveTarget()?.mode) ? 0 : 1];
        const showNav = false !== ownerNode.properties?.showNav;
        ctx.fillStyle = '#2a2a2a';
        ctx.beginPath();
        ctx.roundRect(15, y, width - 30, height, 4);
        ctx.fill();
        ctx.strokeStyle = '#444';
        ctx.stroke();
        let xCursor = width - 15;
        if (showNav) {
            xCursor -= 7;
            const centerY = y + 0.5 * height;
            ctx.fillStyle = ctx.strokeStyle = '#89A';
            ctx.lineJoin = 'round';
            ctx.lineCap = 'round';
            ctx.beginPath();
            ctx.moveTo(xCursor, centerY);
            ctx.lineTo(xCursor - 7, centerY + 6);
            ctx.lineTo(xCursor - 7, centerY + 3);
            ctx.lineTo(xCursor - 14, centerY + 3);
            ctx.lineTo(xCursor - 14, centerY - 3);
            ctx.lineTo(xCursor - 7, centerY - 3);
            ctx.lineTo(xCursor - 7, centerY - 6);
            ctx.closePath();
            ctx.fill();
            ctx.stroke();
            xCursor -= 21;
            xCursor -= 4;
            ctx.strokeStyle = '#444';
            ctx.beginPath();
            ctx.moveTo(xCursor, y + 2);
            ctx.lineTo(xCursor, y + height - 2);
            ctx.stroke();
        }
        xCursor -= 7;
        const radius = 0.32 * height;
        const circleX = xCursor - radius;
        ctx.fillStyle = state.color;
        ctx.beginPath();
        ctx.arc(circleX, y + 0.5 * height, radius, 0, 2 * Math.PI);
        ctx.fill();
        xCursor = circleX - radius;
        xCursor -= 6;
        ctx.textAlign = 'right';
        ctx.fillStyle = state.color;
        ctx.font = `${Math.max(10, 0.5 * height)}px Arial`;
        ctx.fillText(state.label, xCursor, y + 0.7 * height);
        const stateTextWidth = Math.max(
            ctx.measureText('active').width,
            ctx.measureText('muted').width,
            ctx.measureText('bypass').width
        );
        const titleX = xCursor - stateTextWidth - 10;
        const maxTitleWidth = titleX - 25;
        ctx.textAlign = 'left';
        ctx.fillStyle = state.mode === MODE_ALWAYS ? '#ddd' : '#999';
        ctx.font = `${Math.max(10, 0.55 * height)}px Arial`;
        if (maxTitleWidth > 0) ctx.fillText(fitString(ctx, (widget.label || widget.name || ''), maxTitleWidth), 25, y + 0.7 * height);
    };
    widget.drawWidget = function (ctx, options) {
        const width = options?.width ?? 0;
        const y = this.y ?? 0;
        const height = this.height ?? (LiteGraph.NODE_WIDGET_HEIGHT || 20);
        _paintPill(ctx, width, y, height);
    };
    widget.draw = function (ctx, _node, width, y, height) {
        _paintPill(ctx, width, y, height);
    };

    widget.onClick = function (info) {
        const node = info?.node ?? ownerNode;
        const canvas = info?.canvas ?? app.canvas;
        const mouse = canvas?.graph_mouse || [0, 0];
        const localX = mouse[0] - (node.pos?.[0] ?? 0);
        const nodeWidth = node.size?.[0] ?? 0;
        if (false !== ownerNode.properties?.showNav && localX >= nodeWidth - 15 - 32) {
            const directTarget = ownerNode.graph?.getNodeById(widget._eclipse_targetId);
            const resolved = _resolveNavTarget(directTarget, ownerNode.graph, ownerNode);
            if (canvas && resolved?.target) {
                canvas.centerOnNode?.(resolved.target);
                if (resolved.isGroup && resolved.target._size) {
                    const scale = canvas.ds?.scale || 1;
                    const zoomW = canvas.canvas.width / resolved.target._size[0] - 0.02,
                        zoomH = canvas.canvas.height / resolved.target._size[1] - 0.02;
                    canvas.setZoom?.(Math.min(scale, zoomW, zoomH), [canvas.canvas.width / 2, canvas.canvas.height / 2]);
                }
                canvas.setDirty?.(true, true);
            }
            return;
        }
        widget._eclipse_cycleMode();
        if (isVueMode()) notifyVue(ownerNode);
        ownerNode.setDirtyCanvas(true, false);
    };
    widget.mouse = function (event, pos, nodeInfo) {
        if ('pointerdown' !== event.type) return true;
        if (false !== ownerNode.properties?.showNav && pos[0] >= (nodeInfo?.size?.[0] ?? 0) - 15 - 32) {
            const directTarget = ownerNode.graph?.getNodeById(widget._eclipse_targetId);
            const resolved = _resolveNavTarget(directTarget, ownerNode.graph, ownerNode);
            const canvas = app.canvas;
            if (canvas && resolved?.target) {
                canvas.centerOnNode?.(resolved.target);
                if (resolved.isGroup && resolved.target._size) {
                    const scale = canvas.ds?.scale || 1;
                    const zoomW = canvas.canvas.width / resolved.target._size[0] - 0.02,
                        zoomH = canvas.canvas.height / resolved.target._size[1] - 0.02;
                    canvas.setZoom?.(Math.min(scale, zoomW, zoomH), [canvas.canvas.width / 2, canvas.canvas.height / 2]);
                }
                canvas.setDirty?.(true, true);
            }
            return true;
        }
        widget._eclipse_cycleMode();
        if (isVueMode()) notifyVue(ownerNode);
        ownerNode.setDirtyCanvas(true, false);
        return true;
    };
    widget.onDrag = function () { /* no-op */ };
    widget.computeSize = function (w) { return [w, LiteGraph.NODE_WIDGET_HEIGHT || 20]; };
    widget.serializeValue = function () {
        const normalized = _mtNormalizeValue(widget.value, resolveTarget()?.mode);
        widget.value = normalized;
        return normalized;
    };
    return widget;
}

function createModeToggleNativeWidget(ownerNode, targetNode, title, pairKey, widgetName) {
    const stableKey = /^target_\d+$/.test(pairKey || '') ? pairKey : `target_${pairKey}`;
    const name = widgetName || title || stableKey;
    const widget = ownerNode.addWidget(
        'toggle',
        name,
        targetNode?.mode === MODE_ALWAYS,
        () => { }
    );
    widget.label = name;
    widget._eclipse_targetId = targetNode?.id;
    widget._eclipse_modeToggleKey = stableKey;
    widget._eclipse_modeToggleAutoName = name;
    widget._eclipse_isModeToggle = true;
    widget._eclipse_isModeToggleNative = true;
    ensureNativeModeToggleBacking(ownerNode, widget, name, stableKey);

    const currentStates = () => _mtStates(ownerNode.properties?.modeOff ?? MODE_BYPASS);
    const resolveTarget = () => _mtWidgetTarget(ownerNode, widget);

    widget._eclipse_setMode = function (mode, apply) {
        if (false !== apply) {
            const target = resolveTarget();
            if (target) changeModeOfNodes(target, mode);
        }
        widget.value = mode === MODE_ALWAYS;
    };

    widget._eclipse_cycleMode = function (force) {
        const states = currentStates();
        const restriction = ownerNode.properties?.toggleRestriction || 'default';
        const restrictOne = true !== force && restriction.includes(' one');
        const alwaysOne = true !== force && restriction === 'always one';
        const target = resolveTarget();
        const currentMode = _mtValueToMode(states, widget.value, target?.mode);
        const nextMode = currentMode === MODE_ALWAYS ? states[1].mode : MODE_ALWAYS;
        if (alwaysOne && currentMode === MODE_ALWAYS && nextMode !== MODE_ALWAYS) {
            const otherActive = (ownerNode.widgets || []).some((candidate) => (
                candidate !== widget && _mtWidgetMode(ownerNode, candidate, states) === MODE_ALWAYS
            ));
            if (!otherActive) return;
        }
        widget.value = nextMode === MODE_ALWAYS;
        if (target) changeModeOfNodes(target, nextMode);
        if (restrictOne && nextMode === MODE_ALWAYS) {
            for (const candidate of ownerNode.widgets || []) {
                if (candidate === widget || !candidate._eclipse_setMode) continue;
                if (_mtWidgetMode(ownerNode, candidate, states) === MODE_ALWAYS) {
                    candidate._eclipse_setMode(states[1].mode, true);
                }
            }
        }
        queueNativeModePromotionReconcile(ownerNode.graph);
    };

    widget.callback = function (newValue) {
        const states = currentStates();
        const target = resolveTarget();
        const normalized = _mtNormalizeValue(newValue, target?.mode);
        const restriction = ownerNode.properties?.toggleRestriction || 'default';
        if (!normalized && restriction === 'always one' && target?.mode === MODE_ALWAYS) {
            const otherActive = (ownerNode.widgets || []).some((candidate) => (
                candidate !== widget && _mtWidgetMode(ownerNode, candidate, states) === MODE_ALWAYS
            ));
            if (!otherActive) {
                widget.value = true;
                queueNativeModePromotionReconcile(ownerNode.graph);
                return;
            }
        }
        widget.value = normalized;
        const newMode = normalized ? MODE_ALWAYS : states[1].mode;
        if (target) changeModeOfNodes(target, newMode);
        if (restriction.includes(' one') && newMode === MODE_ALWAYS) {
            for (const candidate of ownerNode.widgets || []) {
                if (candidate === widget || !candidate._eclipse_setMode) continue;
                if (_mtWidgetMode(ownerNode, candidate, states) === MODE_ALWAYS) {
                    candidate._eclipse_setMode(states[1].mode, true);
                }
            }
        }
        if (isVueMode()) notifyVue(ownerNode);
        ownerNode.setDirtyCanvas(true, false);
        queueNativeModePromotionReconcile(ownerNode.graph);
    };
    return widget;
}

function setupModeToggle(nodeType, nativeWidgets) {
    const stabilize = nativeWidgets ? modeToggleNativeStabilize : modeToggleStabilize;
    nodeType.prototype.isVirtualNode = true;
    nodeType['@toggleRestriction'] = {
        type: 'combo',
        values: ['default', 'max one', 'always one']
    };
    if (!nativeWidgets) {
        nodeType['@showNav'] = {
            type: 'boolean'
        };
    }
    const origOnNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = origOnNodeCreated?.apply(this, arguments);
        this.serialize_widgets = true;
        this.properties = this.properties || {};
        if (undefined === this.properties.toggleRestriction) this.properties.toggleRestriction = 'default';
        if (undefined === this.properties.collapse_connections) this.properties.collapse_connections = false;
        if (!nativeWidgets && undefined === this.properties.showNav) this.properties.showNav = false;
        // Default off-mode = BYPASS. Migrated workflows missing this property
        // get bypass behavior (matches the most common Fast Bypasser use case).
        if (this.properties.modeOff !== MODE_MUTE && this.properties.modeOff !== MODE_BYPASS) {
            this.properties.modeOff = MODE_BYPASS;
        }
        if (!this.outputs?.length) this.addOutput('oc', '*');
        blankInputNames(this);
        this._eclipse_isModeToggle = true;
        if (nativeWidgets) {
            this._eclipse_isModeToggleNative = true;
            _installNativeModeToggleConfigureHydration(this);
            if (!this._eclipse_nativeModeGraphConfiguredHydration) {
                this._eclipse_nativeModeGraphConfiguredHydration = true;
                const instanceOnGraphConfigured = this.onGraphConfigured;
                this.onGraphConfigured = function () {
                    _rehydrateNativeModeToggleWidgets(this);
                    return instanceOnGraphConfigured?.apply(this, arguments);
                };
            }
        }
        const self = this;
        this._eclipse_onUpstreamModeChange = function () {
            requestToggleSync(self);
        };
        this._eclipse_hookedNodes = new Map();
        scheduleStabilize(this, stabilize, 100);
        return result;
    };
    const origConfigure = nodeType.prototype.configure;
    nodeType.prototype.configure = function (data) {
        this._eclipse_configuring = true;
        this._eclipse_loading = true;
        const result = origConfigure?.apply(this, arguments);
        if (nativeWidgets) _rehydrateNativeModeToggleWidgets(this, data);
        normalizeModeToggleWidgetValues(this);
        this._eclipse_configuring = false;
        this._eclipse_isModeToggle = true;
        if (nativeWidgets) this._eclipse_isModeToggleNative = true;
        scheduleStabilize(this, stabilize, 300, true);
        return result;
    };
    if (nativeWidgets) {
        const origOnConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (data) {
            const result = origOnConfigure?.apply(this, arguments);
            _rehydrateNativeModeToggleWidgets(this, data);
            normalizeModeToggleWidgetValues(this);
            return result;
        };
        const origOnGraphConfigured = nodeType.prototype.onGraphConfigured;
        nodeType.prototype.onGraphConfigured = function () {
            // ComfyUI removes widget-backed inputs whose widget has not been
            // reconstructed yet. Hydrate first so linked promotions survive
            // the core widget-input cleanup on workflow refresh.
            _rehydrateNativeModeToggleWidgets(this);
            return origOnGraphConfigured?.apply(this, arguments);
        };
    }
    nodeType.prototype.onConnectionsChange = function (_dir, _slot, connected, linkInfo) {
        if (!linkInfo) return;
        const downstream = getConnectedOutputNodes(this, true);
        for (const dn of downstream) {
            if (dn._eclipse_onChainChange) dn._eclipse_onChainChange();
        }
        if (connected) {
            if (this._eclipse_loading) {
                scheduleStabilize(this, stabilize, 300, true);
            } else {
                if (this._eclipse_stabilizeTimer) {
                    clearTimeout(this._eclipse_stabilizeTimer);
                    this._eclipse_stabilizeTimer = null;
                }
                stabilize.call(this);
                scheduleStabilize(this, stabilize, 200, true);
            }
        } else {
            scheduleStabilize(this, stabilize, 500, true);
        }
    };
    nodeType.prototype._eclipse_onChainChange = function () {
        if (this._eclipse_loading || this._eclipse_configuring) {
            scheduleStabilize(this, stabilize, 300, true);
        } else {
            if (this._eclipse_stabilizeTimer) {
                clearTimeout(this._eclipse_stabilizeTimer);
                this._eclipse_stabilizeTimer = null;
            }
            stabilize.call(this);
        }
    };
    nodeType.prototype.onConnectOutput = function (_slotIdx, _type, _inputInfo, targetNode, _targetSlot) {
        return !getConnectedInputNodes(this).includes(targetNode);
    };
    nodeType.prototype.onConnectInput = function (_slotIdx, _type, _outputInfo, sourceNode, _sourceSlot) {
        return !getConnectedOutputNodes(this, false).includes(sourceNode);
    };
    const origOnRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
        origOnRemoved?.apply(this, arguments);
        if (this._eclipse_hookedNodes) {
            for (const unhook of this._eclipse_hookedNodes.values()) unhook();
            this._eclipse_hookedNodes.clear();
        }
        if (this._eclipse_hookedTitles) {
            for (const unhook of this._eclipse_hookedTitles.values()) unhook();
            this._eclipse_hookedTitles.clear();
        }
        if (this._eclipse_stabilizeTimer) {
            clearTimeout(this._eclipse_stabilizeTimer);
            this._eclipse_stabilizeTimer = null;
        }
        setCollapseCSS(this, false);
    };
    const origOnSerialize = nodeType.prototype.onSerialize;
    nodeType.prototype.onSerialize = function (data) {
        origOnSerialize?.call(this, data);
        if (data?.inputs) {
            for (let index = 0; index < data.inputs.length; index++) {
                const inp = data.inputs[index];
                const runtimeInput = this.inputs?.[index];
                if ('_eclipseHide' === inp?.widget?.name) delete inp.widget;
                delete inp.pos;
                if (nativeWidgets && runtimeInput) {
                    const pairKey = _nativeModePairKey(runtimeInput);
                    if (pairKey) inp._eclipse_modeToggleKey = pairKey;
                    if (runtimeInput._eclipse_modeToggleAutoName) {
                        inp._eclipse_modeToggleAutoName = runtimeInput._eclipse_modeToggleAutoName;
                    }
                    if (runtimeInput._eclipse_modeToggleTargetId != null) {
                        inp._eclipse_modeToggleTargetId = runtimeInput._eclipse_modeToggleTargetId;
                    }
                    if (_nativeModeBackingInput(runtimeInput)) {
                        inp._eclipse_modeToggleBacking = true;
                        inp.widget = {
                            ...(inp.widget || {}),
                            name: runtimeInput.name,
                            type: 'BOOLEAN',
                        };
                    } else if (runtimeInput._eclipse_modeToggleTarget) {
                        inp._eclipse_modeToggleTarget = true;
                    }
                }
            }
        }
    };
    nodeType.prototype.computeSize = function (out) {
        let size = LGraphNode.prototype.computeSize.call(this, out);
        if (this._eclipse_tempWidth) {
            size[0] = Math.max(this._eclipse_tempWidth, size[0]);
            clearTimeout(this._eclipse_widthTimer);
            this._eclipse_widthTimer = setTimeout(() => {
                this._eclipse_tempWidth = null;
            }, 32);
        }
        if (this.properties?.collapse_connections) {
            const slotH = LiteGraph.NODE_SLOT_HEIGHT ?? 20,
                inputCount = nativeWidgets ? _nativeModeTargetInputs(this).length : (this.inputs?.length || 0),
                hiddenCount = Math.max(inputCount - 1, 0);
            if (hiddenCount > 0) size[1] = size[1] - hiddenCount * slotH;
        }
        return size;
    };
    nodeType.prototype.getExtraMenuOptions = function (_canvas, options) {
        const self = this;
        const isBypass = (this.properties?.modeOff ?? MODE_BYPASS) === MODE_BYPASS;
        options.push(null);
        options.push({
            content: this.properties?.collapse_connections ? 'Show Connections' : 'Collapse Connections',
            callback: () => {
                this.properties.collapse_connections = !this.properties.collapse_connections;
                scheduleStabilize(this, stabilize, 0, true);
            },
        });
        if (!nativeWidgets) {
            options.push({
                content: this.properties?.showNav ? 'Hide Nav Arrows' : 'Show Nav Arrows',
                callback: () => {
                    this.properties.showNav = !this.properties.showNav;
                    this.setDirtyCanvas(true, false);
                },
            });
        }
        options.push(null);
        // Mode selector — switch between Bypass and Mute as the off-state.
        options.push({
            content: `${isBypass ? '✓ ' : '  '}Mode: Bypass`,
            callback: () => self._eclipse_setOffMode(MODE_BYPASS),
        });
        options.push({
            content: `${!isBypass ? '✓ ' : '  '}Mode: Mute`,
            callback: () => self._eclipse_setOffMode(MODE_MUTE),
        });
        options.push(null);
        const enableAction = isBypass ? 'Bypass all' : 'Mute all';
        for (const action of ['Enable all', enableAction, 'Toggle all']) {
            options.push({
                content: action,
                callback: () => this._eclipse_handleAction(action)
            });
        }
        options.push(null);
        const currentRestriction = this.properties?.toggleRestriction || 'default';
        for (const restriction of ['default', 'max one', 'always one']) {
            options.push({
                content: `${restriction === currentRestriction ? '✓ ' : '  '}Restriction: ${restriction}`,
                callback: () => {
                    this.properties.toggleRestriction = restriction;
                    requestAnimationFrame(() => {
                        requestAnimationFrame(() => self.setDirtyCanvas(true, false));
                    });
                },
            });
        }
        return options;
    };
    // Switch the off-mode (mute ↔ bypass). Re-applies the new off-mode to any
    // currently-off targets, refreshes widget labels, and notifies Vue.
    nodeType.prototype._eclipse_setOffMode = function (newOff) {
        if (newOff !== MODE_MUTE && newOff !== MODE_BYPASS) return;
        const prev = this.properties.modeOff ?? MODE_BYPASS;
        this.properties.modeOff = newOff;
        if (prev !== newOff) {
            const widgets = this.widgets || [];
            for (const w of widgets) {
                if (!w._eclipse_isModeToggle) continue;
                const targetNode = this.graph?.getNodeById(w._eclipse_targetId);
                if (!targetNode) continue;
                // If the target was in the *old* off mode, re-apply the new one.
                if (targetNode.mode === prev) {
                    changeModeOfNodes(targetNode, newOff);
                }
            }
            requestToggleSync(this);
        }
        if (isVueMode()) batchedNotifyVue(this);
        this.setDirtyCanvas(true, false);
        if (nativeWidgets) queueNativeModePromotionReconcile(this.graph);
    };
    nodeType.prototype._eclipse_handleAction = function (action) {
        const states = _mtStates(this.properties?.modeOff ?? MODE_BYPASS);
        const offMode = states[1].mode;
        const alwaysOne = 'always one' === this.properties?.toggleRestriction;
        const widgets = this.widgets || [];
        if (action === 'Enable all') {
            const restrictOne = (this.properties?.toggleRestriction || '').includes(' one');
            for (let idx = 0; idx < widgets.length; idx++) widgets[idx]._eclipse_setMode?.(restrictOne && idx > 0 ? offMode : MODE_ALWAYS, true);
        } else if (action === 'Mute all' || action === 'Bypass all') {
            for (let idx = 0; idx < widgets.length; idx++) widgets[idx]._eclipse_setMode?.(alwaysOne && 0 === idx ? MODE_ALWAYS : offMode, true);
        } else if (action === 'Toggle all') {
            for (const w of widgets) {
                if (!w._eclipse_cycleMode) continue;
                w._eclipse_cycleMode(true);
            }
        }
        for (const w of widgets) w.triggerDraw?.();
        if (isVueMode()) batchedNotifyVue(this);
        const self = this;
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                if (isVueMode()) notifyVue(self);
                self.setDirtyCanvas(true, false);
            });
        });
        if (nativeWidgets) queueNativeModePromotionReconcile(this.graph);
    };
}

function modeToggleStabilize() {
    if (!this.graph) return;
    this._eclipse_loading = false;
    preserveWidth(this);
    let changed = stabilizeInputs(this, true, 'hide');
    const connectedNodes = getConnectedInputNodesFiltered(this, -1, false);
    const removeAt = (idx) => {
        const w = this.widgets?.[idx];
        if (!w) return;
        try { w.onRemove?.(); } catch { /* ignore */ }
        this.widgets.splice(idx, 1);
    };
    for (let idx = 0; idx < connectedNodes.length; idx++) {
        const targetNode = connectedNodes[idx];
        if (!targetNode) continue;
        let widget = this.widgets?.[idx];
        const title = targetNode.title;
        const expectedName = `target_${idx}`;
        if (widget) {
            if (widget._eclipse_targetId !== targetNode.id) {
                widget._eclipse_targetId = targetNode.id;
                changed = true;
            }
            if (widget.label !== title) {
                widget.label = title;
                changed = true;
            }
        } else {
            preserveWidth(this);
            widget = createModeToggleWidget(this, targetNode, title, idx);
            const lastIdx = this.widgets.length - 1;
            if (lastIdx !== idx) {
                this.widgets.pop();
                this.widgets.splice(idx, 0, widget);
            }
            changed = true;
        }
        const expectedValue = targetNode.mode === MODE_ALWAYS;
        if (widget.value !== expectedValue) {
            widget.value = expectedValue;
            changed = true;
        }
        if (widget.name !== expectedName) {
            widget.name = expectedName;
            changed = true;
        }
    }
    while (this.widgets && this.widgets.length > connectedNodes.length) {
        removeAt(this.widgets.length - 1);
        changed = true;
    }
    const hookedNodes = this._eclipse_hookedNodes || (this._eclipse_hookedNodes = new Map()),
        activeIds = new Set(connectedNodes.map((n) => n.id));
    for (const [id, unhook] of hookedNodes) {
        if (!activeIds.has(id)) {
            unhook();
            hookedNodes.delete(id);
        }
    }
    const self = this;
    for (const target of connectedNodes) {
        if (!hookedNodes.has(target.id)) {
            const unhook = hookModeProperty(target, () => {
                requestToggleSync(self);
            });
            hookedNodes.set(target.id, unhook);
        }
    }
    syncTitleHooks(this, connectedNodes, () => {
        scheduleStabilize(self, modeToggleStabilize, 50, true);
    });
    if (changed) {
        if (isVueMode()) batchedNotifyVue(this);
        smartResize(this, {
            minWidth: 0,
            minHeight: 0,
            padding: 0
        });
        scheduleSubgraphHostRefresh(this.graph);
    }
}

function modeToggleNativeStabilize() {
    if (!this.graph) return;
    if (_nativeModeIdentitySyncing.has(this)) return;
    _nativeModeIdentitySyncing.add(this);
    try {
    this._eclipse_loading = false;
    this._eclipse_isModeToggleNative = true;
    preserveWidth(this);
    let changed = stabilizeNativeModeToggleInputs(this);
    const records = getNativeModeToggleTargetRecords(this);
    const existingWidgets = [...(this.widgets || [])];
    const existingBackings = (this.inputs || []).filter(_nativeModeBackingInput);
    const reservedKeys = new Set();
    const pairs = [];

    for (let index = 0; index < records.length; index++) {
        const record = records[index];
        const targetNode = record.targetNode;
        let widget = existingWidgets.find((candidate) => (
            !reservedKeys.has(_nativeModePairKey(candidate))
            && candidate._eclipse_targetId != null
            && String(candidate._eclipse_targetId) === String(targetNode.id)
        ));
        let key = _nativeModePairKey(record.input);
        if (!key || reservedKeys.has(key)) key = _nativeModePairKey(widget);
        if (!key || reservedKeys.has(key)) {
            const indexedWidget = existingWidgets[index];
            const indexedKey = _nativeModePairKey(indexedWidget);
            if (indexedKey && !reservedKeys.has(indexedKey)) {
                key = indexedKey;
                widget ||= indexedWidget;
            }
        }
        if (!key || reservedKeys.has(key)) key = _nextNativeModePairKey(this, reservedKeys);
        reservedKeys.add(key);
        widget ||= existingWidgets.find((candidate) => _nativeModePairKey(candidate) === key);
        const backing = existingBackings.find((candidate) => _nativeModePairKey(candidate) === key);
        const initialName = backing?._eclipse_modeToggleAutoName
            || backing?.name
            || widget?._eclipse_modeToggleAutoName
            || widget?.name
            || targetNode.title
            || key;
        if (!widget || !widget._eclipse_isModeToggleNative) {
            preserveWidth(this);
            widget = createModeToggleNativeWidget(this, targetNode, initialName, key, initialName);
            changed = true;
        }
        if (widget._eclipse_targetId !== targetNode.id) {
            widget._eclipse_targetId = targetNode.id;
            changed = true;
        }
        const ensuredBacking = ensureNativeModeToggleBacking(this, widget, initialName, key);
        const pair = { backing: ensuredBacking, key, record, widget };
        pair.chain = ensuredBacking ? _nativeModePromotionChain(this, ensuredBacking) : [];
        _removeEmptyLegacyNativeModeBoundaries(key, pair.chain);
        pairs.push(pair);
    }

    const activeWidgets = new Set(pairs.map((pair) => pair.widget));
    const activeBackings = new Set(pairs.map((pair) => pair.backing).filter(Boolean));
    const ownedBoundaries = new Set(pairs.flatMap((pair) => pair.chain.map((entry) => entry.boundary)));
    const reservedNames = new Set();
    for (const widget of existingWidgets) {
        if (!activeWidgets.has(widget)) reservedNames.add(widget.name);
    }
    for (const pair of pairs) {
        for (const entry of pair.chain) {
            const boundaries = entry.graph?.inputs || entry.graph?.inputNode?.slots || [];
            for (const boundary of boundaries) {
                if (!ownedBoundaries.has(boundary)) reservedNames.add(boundary.name);
            }
        }
    }
    const namedPairs = pairs.map((pair) => ({
        name: _nativeModeUniqueName(pair.record.targetNode.title, reservedNames),
        pair,
    }));
    changed = _stageNativeModeWidgetRenames(this, namedPairs, reservedNames) || changed;
    for (const { name, pair } of namedPairs) {
        changed = _rekeyNativeModePromotionPair(this, pair, name) || changed;
        const expectedValue = pair.record.targetNode.mode === MODE_ALWAYS;
        if (pair.widget.value !== expectedValue) {
            pair.widget.value = expectedValue;
            changed = true;
        }
    }

    const orderedWidgets = pairs.map((pair) => pair.widget);
    if ((this.widgets || []).length !== orderedWidgets.length
        || orderedWidgets.some((widget, index) => this.widgets[index] !== widget)) {
        for (const widget of existingWidgets) {
            if (activeWidgets.has(widget)) continue;
            try { widget.onRemove?.(); } catch { /* ignore */ }
        }
        this.widgets.splice(0, this.widgets.length, ...orderedWidgets);
        changed = true;
    }
    for (const input of [...existingBackings]) {
        if (activeBackings.has(input)) continue;
        this.removeInput(this.inputs.indexOf(input));
        changed = true;
    }
    const connectedNodes = records.map((record) => record.targetNode);
    const hookedNodes = this._eclipse_hookedNodes || (this._eclipse_hookedNodes = new Map());
    const activeIds = new Set(connectedNodes.map((node) => node.id));
    for (const [id, unhook] of hookedNodes) {
        if (activeIds.has(id)) continue;
        unhook();
        hookedNodes.delete(id);
    }
    const self = this;
    for (const target of connectedNodes) {
        if (hookedNodes.has(target.id)) continue;
        hookedNodes.set(target.id, hookModeProperty(target, () => requestToggleSync(self)));
    }
    syncTitleHooks(this, connectedNodes, () => {
        scheduleStabilize(self, modeToggleNativeStabilize, 50, true);
    });
    if (changed) {
        if (isVueMode()) batchedNotifyVue(this);
        smartResize(this, { minWidth: 0, minHeight: 0, padding: 0 });
        scheduleSubgraphHostRefresh(this.graph);
    }
    queueNativeModePromotionReconcile(this.graph);
    } finally {
        _nativeModeIdentitySyncing.delete(this);
    }
}

// Provide Bridge Set/Get menu items via shared Eclipse submenu collector
(window._eclipseMenuProviders ??= []).push((node) => {
    const items = [];
    const type = node.type || node.comfyClass || '';
    // Don't show on Bridge Set/Get nodes themselves
    if (BRIDGE_SET_TYPES.includes(type) || BRIDGE_GET_TYPES.includes(type)) return items;
    items.push(null);
    items.push({
        content: 'Add Bridge Set',
        callback: () => {
            const setNode = LiteGraph.createNode(NODE_NAMES.MODE_BRIDGE_SET);
            if (!setNode) return;
            setNode.pos = [node.pos[0] + node.size[0] + 30, node.pos[1]];
            node.graph.add(setNode);
            app.canvas?.selectNode(setNode, false);
            app.canvas?.setDirty(true, true);
        },
    });
    items.push({
        content: 'Add Bridge Get',
        callback: () => {
            const getNode = LiteGraph.createNode(NODE_NAMES.MODE_BRIDGE_GET);
            if (!getNode) return;
            getNode.pos = [node.pos[0] - (getNode.size?.[0] || 200) - 30, node.pos[1]];
            node.graph.add(getNode);
            app.canvas?.selectNode(getNode, false);
            app.canvas?.setDirty(true, true);
        },
    });
    return items;
});

// Provide Bridge Set/Get items in canvas right-click Eclipse submenu
(window._eclipseCanvasMenuProviders ??= []).push(() => {
    return [
        {
            content: 'Add Bridge Set',
            callback: () => {
                const pos = app.canvas.graph_mouse;
                const n = LiteGraph.createNode(NODE_NAMES.MODE_BRIDGE_SET);
                if (!n) return;
                n.pos = [pos[0], pos[1]];
                (app.canvas.graph || app.graph).add(n);
                app.canvas?.selectNode(n, false);
                app.canvas?.setDirty(true, true);
            },
        },
        {
            content: 'Add Bridge Get',
            callback: () => {
                const pos = app.canvas.graph_mouse;
                const n = LiteGraph.createNode(NODE_NAMES.MODE_BRIDGE_GET);
                if (!n) return;
                n.pos = [pos[0], pos[1]];
                (app.canvas.graph || app.graph).add(n);
                app.canvas?.selectNode(n, false);
                app.canvas?.setDirty(true, true);
            },
        },
    ];
});

app.registerExtension({
    name: 'Eclipse.ModeNodes',
    async setup() {
        patchSubgraphOps();
        _installNativeModePromotionInteractionBridge();
    },
    async beforeLoadGraph() {
        // Subgraph definitions configure before loadedGraphNode callbacks.
        // Install this after every extension has registered its core cleanup
        // so native widgets are present when onGraphConfigured validates slots.
        _installNativeModeToggleLoadHydration();
    },
    async nodeCreated(node) {
        if (node.isSubgraphNode?.()) {
            _watchNativeModePromotionHost(node);
            queueNativeModePromotionReconcile(node.graph);
        }
        const comfyClass = node.comfyClass || node.type || '';
        if (!ECLIPSE_MODE_TYPES.includes(comfyClass)) return;
        blankInputNames(node);
        if (comfyClass === NODE_NAMES.FAST_MODE_TOGGLE) {
            if (!node.outputs?.length) node.addOutput('oc', '*');
            node.properties = node.properties || {};
            if (node.properties.modeOff !== MODE_MUTE && node.properties.modeOff !== MODE_BYPASS) {
                node.properties.modeOff = MODE_BYPASS;
            }
            node._eclipse_isModeToggle = true;
            scheduleStabilize(node, modeToggleStabilize, 100, true);
        } else if (comfyClass === NODE_NAMES.FAST_MODE_TOGGLE_NATIVE) {
            _installNativeModeToggleConfigureHydration(node);
            if (!node.outputs?.length) node.addOutput('oc', '*');
            node.properties = node.properties || {};
            if (node.properties.modeOff !== MODE_MUTE && node.properties.modeOff !== MODE_BYPASS) {
                node.properties.modeOff = MODE_BYPASS;
            }
            node._eclipse_isModeToggle = true;
            node._eclipse_isModeToggleNative = true;
            scheduleStabilize(node, modeToggleNativeStabilize, 100, true);
        } else if (comfyClass === NODE_NAMES.FAST_MODE_SWITCHER) {
            if (!node.outputs?.length) node.addOutput('oc', '*');
            node._eclipse_isModeSwitcher = true;
            scheduleStabilize(node, modeSwitcherStabilize, 100, true);
        } else if (comfyClass === NODE_NAMES.NODE_MODE_REPEATER) {
            if (node.outputs?.length) {
                if (node.outputs[0]) {
                    node.outputs[0].color_on = '#Fc0';
                    node.outputs[0].color_off = '#a80';
                }
            } else {
                node.addOutput('oc', '*', {
                    color_on: '#Fc0',
                    color_off: '#a80'
                });
            }
            scheduleStabilize(node, repeaterStabilize, 100, true);
        } else if (comfyClass === NODE_NAMES.NODE_COLLECTOR) {
            if (!node.outputs?.length) node.addOutput('Output', '*');
            scheduleStabilize(node, collectorStabilize, 100, true);
        } else if (comfyClass === NODE_NAMES.MODE_RELAY) {
        } else if (comfyClass === NODE_NAMES.MODE_BRIDGE_SET) {
            if (node.outputs?.length) {
                if (node.outputs[0]) {
                    node.outputs[0].color_on = '#0Cf';
                    node.outputs[0].color_off = '#08a';
                }
            } else {
                node.addOutput('oc', '*', {
                    color_on: '#0Cf',
                    color_off: '#08a'
                });
            }
            scheduleStabilize(node, bridgeSetStabilize, 100, true);
        } else if (comfyClass === NODE_NAMES.MODE_BRIDGE_GET) {
            const name = node.properties?.bridgeName;
            if (name && node.title === 'Mode Bridge Get') node.title = 'Get: ' + name;
            scheduleStabilize(node, bridgeGetStabilize, 100, true);
        }
    },
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (!nodeData?.name) return;
        switch (nodeData.name) {
            case NODE_NAMES.FAST_MODE_TOGGLE:
                setupModeToggle(nodeType);
                break;
            case NODE_NAMES.FAST_MODE_TOGGLE_NATIVE:
                setupModeToggle(nodeType, true);
                break;
            case NODE_NAMES.FAST_MODE_SWITCHER:
                setupModeSwitcher(nodeType, ['Enable all', 'Mute all', 'Bypass all', 'Toggle all']);
                break;
            case NODE_NAMES.NODE_MODE_REPEATER:
                setupNodeModeRepeater(nodeType);
                break;
            case NODE_NAMES.NODE_COLLECTOR:
                setupNodeCollector(nodeType);
                break;
            case NODE_NAMES.MODE_RELAY:
                setupModeRelay(nodeType);
                break;
            case NODE_NAMES.MODE_BRIDGE_SET:
                setupModeBridgeSet(nodeType);
                break;
            case NODE_NAMES.MODE_BRIDGE_GET:
                setupModeBridgeGet(nodeType);
                break;
        }
    },
    loadedGraphNode(node) {
        if (node?.isSubgraphNode?.()) _watchNativeModePromotionHost(node);
        const comfyClass = node?.comfyClass || node?.type || '';
        if (comfyClass === NODE_NAMES.FAST_MODE_TOGGLE_NATIVE) {
            _rehydrateNativeModeToggleWidgets(node);
        }
        queueNativeModePromotionReconcile(node?.graph);
    },
    async afterConfigureGraph() {
        reconcileNativeModePromotions(app.graph);
    },
});
