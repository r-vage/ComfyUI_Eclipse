import {
    app
} from './comfy/index.js';
import {
    smartResize,
    notifyVue,
    batchedNotifyVue,
    isVueMode
} from './eclipse-widget-performance-utils.js';
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
    TOGGLER_TYPES = [NODE_NAMES.FAST_MODE_TOGGLE, NODE_NAMES.FAST_MODE_SWITCHER];

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
    return true;
}

function _bridgeSetUpdateTitle(node, name, defaultTitle) {
    if (!name) {
        if (node.title !== defaultTitle) node.title = defaultTitle;
    } else {
        if (node.title === defaultTitle || node.title === node.properties?.previousBridgeName) {
            node.title = name;
        }
    }
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
        const nameWidget = this.addWidget('text', 'bridge name', this.properties.bridgeName || '', (value) => {
            if (!self.graph || app.configuringGraph) return;
            const trimmed = value.trim();
            if (trimmed === '(new)' || trimmed === '') {
                self.properties.bridgeName = '';
                nameWidget.value = '';
                return;
            }
            // Auto-increment if duplicate: another Set already owns this name
            const existing = _collectAllBridgeSetNames(self.graph);
            existing.delete(self.properties.bridgeName); // exclude our own current name
            let finalName = trimmed;
            if (existing.has(finalName)) {
                const baseName = finalName.replace(/_\d+$/, '');
                let i = 1;
                while (existing.has(finalName)) {
                    finalName = baseName + '_' + i;
                    i++;
                }
            }
            nameWidget.value = finalName;
            const oldName = self.properties.previousBridgeName || self.properties.bridgeName || '';
            self.properties.bridgeName = finalName;
            // Only auto-set title once when it's still the default
            if (self.title === 'Mode Bridge Set') self.title = finalName;
            if (oldName && oldName !== finalName) {
                _renameMatchingGets(self, oldName, finalName);
            }
            self.properties.previousBridgeName = finalName;
            self.setDirtyCanvas(true, false);
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
        if (nameW) nameW.value = this.properties.bridgeName || '';
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
    const connectedNodes = getConnectedInputNodesFiltered(node, -1, false);
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

function setupModeToggle(nodeType) {
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
        // Default off-mode = BYPASS. Migrated workflows missing this property
        // get bypass behavior (matches the most common Fast Bypasser use case).
        if (this.properties.modeOff !== MODE_MUTE && this.properties.modeOff !== MODE_BYPASS) {
            this.properties.modeOff = MODE_BYPASS;
        }
        if (!this.outputs?.length) this.addOutput('oc', '*');
        blankInputNames(this);
        this._eclipse_isModeToggle = true;
        const self = this;
        this._eclipse_onUpstreamModeChange = function () {
            requestToggleSync(self);
        };
        this._eclipse_hookedNodes = new Map();
        scheduleStabilize(this, modeToggleStabilize, 100);
        return result;
    };
    const origConfigure = nodeType.prototype.configure;
    nodeType.prototype.configure = function (data) {
        this._eclipse_configuring = true;
        this._eclipse_loading = true;
        const result = origConfigure?.apply(this, arguments);
        normalizeModeToggleWidgetValues(this);
        this._eclipse_configuring = false;
        this._eclipse_isModeToggle = true;
        scheduleStabilize(this, modeToggleStabilize, 300, true);
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
                scheduleStabilize(this, modeToggleStabilize, 300, true);
            } else {
                if (this._eclipse_stabilizeTimer) {
                    clearTimeout(this._eclipse_stabilizeTimer);
                    this._eclipse_stabilizeTimer = null;
                }
                modeToggleStabilize.call(this);
                scheduleStabilize(this, modeToggleStabilize, 200, true);
            }
        } else {
            scheduleStabilize(this, modeToggleStabilize, 500, true);
        }
    };
    nodeType.prototype._eclipse_onChainChange = function () {
        if (this._eclipse_loading || this._eclipse_configuring) {
            scheduleStabilize(this, modeToggleStabilize, 300, true);
        } else {
            if (this._eclipse_stabilizeTimer) {
                clearTimeout(this._eclipse_stabilizeTimer);
                this._eclipse_stabilizeTimer = null;
            }
            modeToggleStabilize.call(this);
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
        const self = this;
        const isBypass = (this.properties?.modeOff ?? MODE_BYPASS) === MODE_BYPASS;
        options.push(null);
        options.push({
            content: this.properties?.collapse_connections ? 'Show Connections' : 'Collapse Connections',
            callback: () => {
                this.properties.collapse_connections = !this.properties.collapse_connections;
                scheduleStabilize(this, modeToggleStabilize, 0, true);
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
    },
    async nodeCreated(node) {
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
});
