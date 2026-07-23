import {
    app
} from './comfy/index.js';
import {
    patchNodeCSSSize
} from './eclipse-widget-performance-utils.js';
const MIN_SLOTS = 2;
const MAX_SLOTS = 64;
const MATCHTYPE_PLACEHOLDER = 'COMFY_MATCHTYPE_V3';

function isPendingType(type) {
    if (!type) return true;
    const value = String(type);
    return value === '*' || value.includes(MATCHTYPE_PLACEHOLDER);
}

function getCommonConnectionType(types) {
    const concreteTypes = types.filter(type => !isPendingType(type)).map(type => String(type));
    if (!concreteTypes.length) return null;
    let common = [...new Set(concreteTypes[0].split(','))];
    for (const type of concreteTypes.slice(1)) {
        const candidates = type.split(',');
        common = common.filter(current =>
            candidates.some(candidate => LiteGraph.isValidConnection(current, candidate))
        );
        if (!common.length) return null;
    }
    return common.join(',');
}

function scheduleResize(node) {
    setTimeout(() => {
        node.setDirtyCanvas(true, false);
        const computed = node.computeSize();
        const cur = node.size;
        const w = Math.max(cur[0], 200);
        const h = Math.max(computed[1] + 5, 50);
        if (h > cur[1] || Math.abs(cur[1] - h) > 10) {
            node.setSize([w, h]);
            patchNodeCSSSize(node);
        }
        node.setDirtyCanvas(true, true);
    }, 50);
}

function getHighestSlotNum(node, prefix) {
    let max = 0;
    if (!node.inputs) return max;
    const re = new RegExp('^' + prefix + '_(\\d+)$');
    for (const inp of node.inputs) {
        const m = inp.name?.match(re);
        if (m) {
            const num = parseInt(m[1], 10);
            if (num > max) max = num;
        }
    }
    return max;
}

function inferSlotType(node, prefix, defaultType) {
    if (defaultType !== '*') return defaultType;
    if (!node.inputs) return '*';
    const re = new RegExp('^' + prefix + '_(\\d+)$');
    const typed = node.inputs.find(inp =>
        inp.name && re.test(inp.name) && !isPendingType(inp.type)
    );
    if (typed) return typed.type;
    const linked = node.inputs.find(inp => inp.name && re.test(inp.name) && inp.link != null);
    if (linked) {
        const g = node.graph || app.graph;
        const link = g?.links?.[linked.link] ?? g?.links?.get?.(linked.link);
        if (!isPendingType(link?.type)) return link.type;
    }
    return '*';
}

function getLink(id, graph) {
    if (id == null) return null;
    const g = graph || app.graph;
    return g?.links?.[id] ?? g?.links?.get?.(id) ?? null;
}

function getSourceType(inp, graph) {
    const link = getLink(inp?.link, graph);
    if (!link) return null;
    const g = graph || app.graph;
    const srcNode = g?.getNodeById(link.origin_id);
    return srcNode?.outputs?.[link.origin_slot]?.type ?? link.type ?? null;
}
const NODE_CONFIGS = {
    RvConversion_ConcatMulti: {
        type: 'PIPE',
        prefix: 'pipe',
        max: MAX_SLOTS
    },
    'Concat Pipe Multi [Eclipse]': {
        type: 'PIPE',
        prefix: 'pipe',
        max: MAX_SLOTS
    },
    RvRouter_Any_MultiSwitch: {
        type: '*',
        prefix: 'any',
        max: MAX_SLOTS
    },
    'Any Multi-Switch [Eclipse]': {
        type: '*',
        prefix: 'any',
        max: MAX_SLOTS
    },
    RvRouter_Any_MultiSwitch_purge: {
        type: '*',
        prefix: 'any',
        max: MAX_SLOTS
    },
    'Any Multi-Switch Purge [Eclipse]': {
        type: '*',
        prefix: 'any',
        max: MAX_SLOTS
    },
    RvRouter_Any_MultiSwitch_lazy: {
        type: '*',
        prefix: 'any',
        max: MAX_SLOTS
    },
    'Any Multi-Switch Lazy [Eclipse]': {
        type: '*',
        prefix: 'any',
        max: MAX_SLOTS
    },
    RvRouter_Any_MultiSwitch_lazy_purge: {
        type: '*',
        prefix: 'any',
        max: MAX_SLOTS
    },
    'Any Multi-Switch Lazy Purge [Eclipse]': {
        type: '*',
        prefix: 'any',
        max: MAX_SLOTS
    },
    RvConversion_MergeStrings: {
        type: 'STRING',
        prefix: 'string',
        max: MAX_SLOTS
    },
    'Merge Strings [Eclipse]': {
        type: 'STRING',
        prefix: 'string',
        max: MAX_SLOTS
    },
    RvConversion_Join: {
        type: '*',
        prefix: 'input',
        max: MAX_SLOTS
    },
    'Join [Eclipse]': {
        type: '*',
        prefix: 'input',
        max: MAX_SLOTS
    },
    RvText_DeDuplicate: {
        type: 'STRING',
        prefix: 'string',
        max: 20
    },
    'String DeDuplicate [Eclipse]': {
        type: 'STRING',
        prefix: 'string',
        max: 20
    },
    RvImage_LoopImageSelector: {
        type: 'IMAGE',
        prefix: 'image',
        max: MAX_SLOTS
    },
    'Loop Image Selector [Eclipse]': {
        type: 'IMAGE',
        prefix: 'image',
        max: MAX_SLOTS
    },
};
app.registerExtension({
    name: 'Eclipse.DynamicInputs',
    async beforeRegisterNodeDef(nodeType, nodeData, appRef) {
        if (!nodeData?.name) return;
        const name = nodeData.name?.includes('/') ? nodeData.name.split('/').pop() : nodeData.name;
        const cfg = NODE_CONFIGS[name];
        if (!cfg) return;
        const origOnCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origOnCreated?.apply(this, arguments);
            const node = this;
            const isAnyType = cfg.type === '*';
            const isAnyMultiSwitch = isAnyType && cfg.prefix === 'any';
            const prefix = cfg.prefix;
            const maxSlots = cfg.max || MAX_SLOTS;
            const slotName = (num) => `${prefix}_${num}`;
            const slotRegex = new RegExp('^' + prefix + '_(\\d+)$');
            const countWidget = node.widgets?.find(w => w.name === 'inputcount');
            if (countWidget) {
                countWidget.hidden = true;
                countWidget.options = countWidget.options || {};
                countWidget.options.hidden = true;
                countWidget.computeSize = () => [0, -4];
            }
            let _localCount = MIN_SLOTS;

            function getInputCount() {
                return countWidget ? Math.max(MIN_SLOTS, countWidget.value) : _localCount;
            }

            function setInputCount(val) {
                val = Math.max(MIN_SLOTS, Math.min(maxSlots, val));
                if (countWidget) countWidget.value = val;
                _localCount = val;
            }

            function syncInputs() {
                node.inputs || (node.inputs = []);
                const desired = getInputCount();
                const slotType = inferSlotType(node, prefix, cfg.type);
                const existing = new Set();
                const widgetNames = new Set();
                for (const inp of node.inputs) {
                    if (inp.name && slotRegex.test(inp.name)) existing.add(inp.name);
                }
                for (const w of (node.widgets || [])) {
                    if (w.name && slotRegex.test(w.name)) widgetNames.add(w.name);
                }
                for (let i = 1; i <= desired; i++) {
                    const name = slotName(i);
                    if (widgetNames.has(name) && !existing.has(name)) {
                        node.addInput(name, slotType, cfg.shape != null ? {
                            shape: cfg.shape
                        } : undefined);
                        existing.add(name);
                    }
                }
                for (let i = 1; i <= desired; i++) {
                    const name = slotName(i);
                    if (!existing.has(name)) {
                        node.addInput(name, slotType, cfg.shape != null ? {
                            shape: cfg.shape
                        } : undefined);
                        existing.add(name);
                    }
                }
                const nums = [];
                for (const name of existing) {
                    const m = name.match(slotRegex);
                    if (m) nums.push(parseInt(m[1], 10));
                }
                nums.sort((a, b) => b - a);
                for (const num of nums) {
                    if (existing.size <= desired) break;
                    const name = slotName(num);
                    const idx = node.inputs.findIndex(inp => inp.name === name);
                    if (idx !== -1) node.removeInput(idx);
                    if (node.widgets) {
                        const wIdx = node.widgets.findIndex(w => w.name === name);
                        if (wIdx !== -1) node.widgets.splice(wIdx, 1);
                    }
                    existing.delete(name);
                }
                scheduleResize(node);
            }

            function autoGrow() {
                const highestNum = getHighestSlotNum(node, prefix);
                if (highestNum <= 0 || highestNum >= maxSlots) return;
                const lastSlot = node.inputs?.find(inp => inp.name === slotName(highestNum));
                if (!lastSlot || lastSlot.link == null) return;
                const newCount = highestNum + 1;
                setInputCount(newCount);
                syncInputs();
            }

            function autoShrink() {
                const highestNum = getHighestSlotNum(node, prefix);
                if (highestNum <= MIN_SLOTS) return;
                let lastConnected = 0;
                for (let i = 1; i <= highestNum; i++) {
                    const inp = node.inputs?.find(inp => inp.name === slotName(i));
                    if (inp?.link != null) lastConnected = i;
                }
                const keep = Math.max(MIN_SLOTS, lastConnected + 1);
                if (keep < highestNum) {
                    setInputCount(keep);
                    syncInputs();
                }
            }

            function propagateType(connectedType) {
                if (!isAnyType || isPendingType(connectedType) || app.configuringGraph) return;
                for (const inp of node.inputs || []) {
                    if (!inp.name || !slotRegex.test(inp.name)) continue;
                    inp.type = connectedType;
                    delete inp.color_on;
                    delete inp.color_off;
                }
                if (node.outputs?.[0]) {
                    node.outputs[0].type = connectedType;
                    node.outputs[0].name = connectedType;
                    delete node.outputs[0].color_on;
                    delete node.outputs[0].color_off;
                }
                const color = LGraphCanvas.link_type_colors?.[connectedType];
                const nodeGraph = node.graph || app.graph;
                for (const inp of node.inputs || []) {
                    if (!inp.name || !slotRegex.test(inp.name) || inp.link == null) continue;
                    const lnk = getLink(inp.link, nodeGraph);
                    if (lnk) {
                        lnk.type = connectedType;
                        if (color) lnk.color = color;
                    }
                }
                for (const linkId of node.outputs?.[0]?.links || []) {
                    const lnk = getLink(linkId, nodeGraph);
                    if (lnk) {
                        lnk.type = connectedType;
                        if (color) lnk.color = color;
                    }
                }
                node.setDirtyCanvas(true, true);
            }

            function resetType() {
                if (!isAnyType) return;
                const connected = (node.inputs || []).filter(inp => inp.name && slotRegex.test(inp.name) && inp.link != null);
                if (connected.length > 0) {
                    const srcType = getSourceType(connected[0], node.graph || app.graph);
                    if (!isPendingType(srcType)) {
                        propagateType(srcType);
                        return;
                    }
                }
                for (const inp of node.inputs || []) {
                    if (!inp.name || !slotRegex.test(inp.name)) continue;
                    inp.type = '*';
                    delete inp.color_on;
                    delete inp.color_off;
                }
                if (node.outputs?.[0]) {
                    node.outputs[0].type = '*';
                    node.outputs[0].name = '';
                    delete node.outputs[0].color_on;
                    delete node.outputs[0].color_off;
                }
                node.setDirtyCanvas(true, true);
            }

            function validateRestoredConnections() {
                if (!node.inputs) return;
                let resolvedType = null;
                const incompatible = [];
                for (let slotIdx = 0; slotIdx < node.inputs.length; slotIdx++) {
                    const inp = node.inputs[slotIdx];
                    if (!inp.name || !slotRegex.test(inp.name) || inp.link == null) continue;
                    const srcType = getSourceType(inp, node.graph || app.graph);
                    if (isPendingType(srcType)) continue;
                    if (!resolvedType) {
                        resolvedType = srcType;
                        continue;
                    }
                    const commonType = getCommonConnectionType([resolvedType, srcType]);
                    if (commonType) {
                        resolvedType = commonType;
                    } else if (isAnyMultiSwitch) {
                        incompatible.push({
                            slotIdx,
                            linkId: inp.link
                        });
                    }
                }
                if (!resolvedType) {
                    resetType();
                    return;
                }
                propagateType(resolvedType);
                for (const {
                    slotIdx,
                    linkId
                } of incompatible) {
                    const currentInput = node.inputs?.[slotIdx];
                    if (!currentInput || currentInput.link !== linkId) continue;
                    const currentSourceType = getSourceType(currentInput, node.graph || app.graph);
                    if (isPendingType(currentSourceType)) continue;
                    if (LiteGraph.isValidConnection(currentSourceType, resolvedType)) continue;
                    node.disconnectInput(slotIdx);
                }
            }
            const origOnConnectInput = node.onConnectInput;
            node.onConnectInput = function (slotIdx, inputType, outputInfo, sourceNode, sourceSlot) {
                const originalResult = origOnConnectInput
                    ? origOnConnectInput.apply(this, arguments)
                    : undefined;
                if (originalResult === false || !isAnyMultiSwitch || app.configuringGraph) {
                    return originalResult;
                }
                const inp = this.inputs?.[slotIdx];
                if (!inp?.name || !slotRegex.test(inp.name)) return originalResult;
                const sourceType = outputInfo?.type
                    ?? sourceNode?.outputs?.[sourceSlot]?.type
                    ?? inputType;
                const existingType = inferSlotType(this, prefix, '*');
                if (isPendingType(sourceType) || isPendingType(existingType)) return originalResult;
                if (!LiteGraph.isValidConnection(sourceType, existingType)) return false;
                return originalResult;
            };
            const origOnConns = node.onConnectionsChange;
            node.onConnectionsChange = function (direction, slotIdx, connected, linkData) {
                const originalResult = origOnConns
                    ? origOnConns.apply(this, arguments)
                    : undefined;
                if (direction !== LiteGraph.INPUT || !this.inputs || app.configuringGraph) {
                    return originalResult;
                }
                const inp = this.inputs[slotIdx];
                if (!inp?.name || !slotRegex.test(inp.name)) return originalResult;
                if (connected && linkData) {
                    if (isAnyType) {
                        const connGraph = node.graph || app.graph;
                        const srcNode = connGraph?.getNodeById(linkData.origin_id);
                        const srcType = srcNode?.outputs?.[linkData.origin_slot]?.type;
                        if (!isPendingType(srcType)) propagateType(srcType);
                    }
                    autoGrow();
                } else if (!connected) {
                    resetType();
                    requestAnimationFrame(() => autoShrink());
                }
                this.setDirtyCanvas?.(true, true);
                return originalResult;
            };
            const origOnAdded = node.onAdded;
            node.onAdded = function () {
                const originalResult = origOnAdded
                    ? origOnAdded.apply(this, arguments)
                    : undefined;
                syncInputs();
                if (isAnyType && !app.configuringGraph) validateRestoredConnections();
                return originalResult;
            };
            const origOnConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                const originalResult = origOnConfigure
                    ? origOnConfigure.apply(this, arguments)
                    : undefined;
                if (!countWidget) {
                    const highest = getHighestSlotNum(node, prefix);
                    if (highest > _localCount) _localCount = highest;
                }
                const configureGeneration = (node._eclipseDynamicConfigureGeneration || 0) + 1;
                node._eclipseDynamicConfigureGeneration = configureGeneration;
                const synchronizeAfterConfigure = () => {
                    if (node._eclipseDynamicConfigureGeneration !== configureGeneration) return;
                    if (app.configuringGraph) {
                        requestAnimationFrame(synchronizeAfterConfigure);
                        return;
                    }
                    try {
                        syncInputs();
                        if (isAnyType) validateRestoredConnections();
                        autoShrink();
                    } catch (_) {}
                };
                requestAnimationFrame(synchronizeAfterConfigure);
                return originalResult;
            };
            if (countWidget) {
                let lastVal = countWidget.value;
                const origCb = countWidget.callback;
                countWidget.callback = function () {
                    origCb?.apply(this, arguments);
                    if (countWidget.value !== lastVal) {
                        lastVal = countWidget.value;
                        syncInputs();
                    }
                };
            }
        };
    },
});
