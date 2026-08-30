import {
    app,
    ComfyWidgets
} from './comfy/index.js';
import {
    notifyVue,
    isVueMode,
    debounce,
    canvasDirtyBatcher
} from './eclipse-widget-performance-utils.js';
import { getResolvedSeedFromGraph, storeQueuedSeed, enterGraphToPromptHook, exitGraphToPromptHook, getGraphNodeList, clearNodeQueuedSeed, findWorkflowNode } from './eclipse-seed-utils.js';
const LAST_SEED_BUTTON_LABEL = '🌘 (Use Last Queued Seed)';
const SPECIAL_SEED_RANDOM = -1;
const SPECIAL_SEED_INCREMENT = -2;
const SPECIAL_SEED_DECREMENT = -3;
const SPECIAL_SEEDS = [-1, -2, -3];
const nodeLastSeeds = {};
const BOTTOM_SEED_CONTROL_NODE_TYPES = new Set([
    'Danbooru Prompt Forge [Eclipse]',
]);
const SEED_NODE_TYPES = [
    'Seed [Eclipse]',
    'Eclipse KSampler (Kargim) [Eclipse]',
    'Detailer (SEGS/pipe) [Eclipse]',
    'Danbooru Prompt Forge [Eclipse]',
];
app.registerExtension({
    name: 'Eclipse.SamplerSettingsSeed',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (!SEED_NODE_TYPES.includes(nodeData.name)) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const origResult = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            let seedWidget = node.widgets.find(w => {
                const n = (w.name || '').toLowerCase();
                const l = (w.label || w.options?.label || w.options?.name || '').toLowerCase();
                return n === 'seed' || l === 'seed';
            });
            let bitDepthWidget = node.widgets.find(w => (w.name || '').toLowerCase() === 'bit_depth');
            const cagIdx = node.widgets.findIndex(w => (w.name || '').toLowerCase() === 'control_after_generate');
            if (cagIdx !== -1) {
                node.widgets.splice(cagIdx, 1);
            }
            if (!seedWidget) {
                console.warn(`Eclipse: Could not find Seed widget in ${nodeData.name}. Widgets:`, node.widgets.map((w) => ({
                    name: w.name,
                    label: w.label,
                    options: w.options
                })), );
                return origResult;
            }
            node._Eclipse_seedWidget = seedWidget;
            node._Eclipse_lastSeed = undefined;
            node._Eclipse_randomMin = 0;
            node._Eclipse_randomMax = Number.MAX_SAFE_INTEGER;
            if (bitDepthWidget) {
                const updateBitDepth = () => {
                    const is32 = bitDepthWidget.value === '32-bit';
                    node._Eclipse_randomMax = is32 ? 0xffffffff : Number.MAX_SAFE_INTEGER;
                    if (seedWidget.options) {
                        seedWidget.options.max = node._Eclipse_randomMax;
                        if (seedWidget.value > seedWidget.options.max) {
                            seedWidget.value = seedWidget.options.max;
                            if (seedWidget.callback) seedWidget.callback(seedWidget.value);
                        }
                    }
                };
                node._Eclipse_updateBitDepth = updateBitDepth;
                updateBitDepth();
                const origBitDepthCallback = bitDepthWidget.callback;
                bitDepthWidget.callback = function (val) {
                    const ret = origBitDepthCallback ? origBitDepthCallback.apply(this, arguments) : val;
                    updateBitDepth();
                    return ret;
                };
            }
            node._Eclipse_cachedInputSeed = null;
            node._Eclipse_cachedResolvedSeed = null;
            const origCallback = seedWidget.callback;
            seedWidget.callback = (val) => {
                node._Eclipse_cachedInputSeed = null;
                node._Eclipse_cachedResolvedSeed = null;
                if (origCallback) return origCallback.call(seedWidget, val);
            };
            const randomizeBtn = node.addWidget('button', '🌑 Randomize Each Time', '', () => {
                seedWidget.value = -1;
                if (seedWidget.callback) seedWidget.callback(-1);
            }, {
                serialize: false
            }, );
            const newFixedBtn = node.addWidget('button', '🌕 New Fixed Random', '', () => {
                const newSeed = node.generateRandomSeed();
                seedWidget.value = newSeed;
                if (seedWidget.callback) seedWidget.callback(newSeed);
            }, {
                serialize: false
            }, );
            const lastSeedBtn = node.addWidget('button', LAST_SEED_BUTTON_LABEL, '', () => {
                if (node._Eclipse_lastSeed != null) {
                    seedWidget.value = node._Eclipse_lastSeed;
                    lastSeedBtn.name = LAST_SEED_BUTTON_LABEL;
                    lastSeedBtn.disabled = true;
                    if (isVueMode()) notifyVue(node);
                }
            }, {
                serialize: false
            }, );
            lastSeedBtn.disabled = true;
            node._Eclipse_lastSeedButton = lastSeedBtn;
            const buttons = [randomizeBtn, newFixedBtn, lastSeedBtn];
            if (BOTTOM_SEED_CONTROL_NODE_TYPES.has(nodeData.name)) {
                for (const widget of [seedWidget, ...buttons]) {
                    const widgetIndex = node.widgets.indexOf(widget);
                    if (widgetIndex !== -1) node.widgets.splice(widgetIndex, 1);
                }
                node.widgets.push(seedWidget, ...buttons);
            } else {
                const seedIndex = node.widgets.indexOf(seedWidget);
                for (let idx = buttons.length - 1; idx >= 0; idx--) {
                    const btn = buttons[idx];
                    const btnIndex = node.widgets.indexOf(btn);
                    if (btnIndex !== seedIndex + 1) {
                        node.widgets.splice(btnIndex, 1);
                        node.widgets.splice(seedIndex + 1, 0, btn);
                    }
                }
            }

            const updateSeedInputState = () => {
                if (node.id === -1) return;
                const seedInput = node.inputs?.find((inp) => inp.name === seedWidget.name);
                const isConnected = seedInput && seedInput.link != null;
                if (node._Eclipse_lastSeedInputConnected === isConnected) return;
                node._Eclipse_lastSeedInputConnected = isConnected;
                const hidden = isConnected;
                randomizeBtn.hidden = hidden;
                if (randomizeBtn.options) randomizeBtn.options.hidden = hidden;
                newFixedBtn.hidden = hidden;
                if (newFixedBtn.options) newFixedBtn.options.hidden = hidden;
                lastSeedBtn.hidden = hidden;
                if (lastSeedBtn.options) lastSeedBtn.options.hidden = hidden;
                if (isVueMode()) notifyVue(node);
                canvasDirtyBatcher.markDirty(node, true, true);
            };

            updateSeedInputState();
            node._Eclipse_updateSeedInputState = updateSeedInputState;

            const debouncedUpdate = debounce(() => {
                node._Eclipse_updateSeedInputState?.();
            }, 150);
            const origOnConnectionsChange = node.onConnectionsChange;
            node.onConnectionsChange = function (ioType, slotIndex, isConnected, linkInfo) {
                if (origOnConnectionsChange) origOnConnectionsChange.apply(this, arguments);
                const seedInput = this.inputs?.find((inp) => inp.name === seedWidget.name);
                if (seedInput) debouncedUpdate();
            };

            const origOnConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                if (origOnConfigure) origOnConfigure.apply(this, arguments);
                node._Eclipse_lastSeedInputConnected = undefined;
                if (node._Eclipse_updateSeedInputState) node._Eclipse_updateSeedInputState();
                if (node._Eclipse_updateBitDepth) node._Eclipse_updateBitDepth();
            };

            return origResult;
        };
        nodeType.prototype.generateRandomSeed = function () {
            const step = this._Eclipse_seedWidget?.options?.step || 1;
            const minVal = this._Eclipse_randomMin || 0;
            const bitDepthWidget = this.widgets.find(w => (w.name || '').toLowerCase() === 'bit_depth');
            const is32 = bitDepthWidget && bitDepthWidget.value === '32-bit';
            const maxVal = is32 ? 0xffffffff : (this._Eclipse_randomMax || Number.MAX_SAFE_INTEGER);
            const range = (maxVal - minVal) / (step / 10);
            let result = Math.floor(Math.random() * range) * (step / 10) + minVal;
            if (SPECIAL_SEEDS.includes(result)) result = 0;
            return result;
        };
        nodeType.prototype.getSeedToUse = function () {
            const seedInput = this.inputs?.find((inp) => inp.name === this._Eclipse_seedWidget?.name);
            if (seedInput && seedInput.link != null) return null;

            const seedValue = Number(this._Eclipse_seedWidget.value);
            if (this._Eclipse_cachedInputSeed === seedValue && this._Eclipse_cachedResolvedSeed != null) {
                return this._Eclipse_cachedResolvedSeed;
            }

            const bitDepthWidget = this.widgets.find(w => (w.name || '').toLowerCase() === 'bit_depth');
            const is32 = bitDepthWidget && bitDepthWidget.value === '32-bit';
            const maxVal = is32 ? 0xffffffff : (this._Eclipse_randomMax || Number.MAX_SAFE_INTEGER);

            let resolved = null;
            if (SPECIAL_SEEDS.includes(seedValue)) {
                if (typeof this._Eclipse_lastSeed === 'number' && !SPECIAL_SEEDS.includes(this._Eclipse_lastSeed)) {
                    if (seedValue === -2) {
                        resolved = this._Eclipse_lastSeed + 1;
                    } else if (seedValue === -3) {
                        resolved = this._Eclipse_lastSeed - 1;
                    }
                }
                if (resolved != null) {
                    resolved = resolved % (maxVal + 1);
                    if (resolved < 0) resolved = maxVal;
                }
                if (resolved == null || SPECIAL_SEEDS.includes(resolved)) {
                    resolved = this.generateRandomSeed();
                }
            }
            const result = resolved != null ? resolved : seedValue;
            this._Eclipse_cachedInputSeed = seedValue;
            this._Eclipse_cachedResolvedSeed = result;
            return result;
        };
        const origOnExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (outputData) {
            const origResult = origOnExecuted ? origOnExecuted.apply(this, arguments) : undefined;
            if (outputData && outputData.seed !== undefined) {
                this._Eclipse_lastSeed = outputData.seed;
                nodeLastSeeds[this.id] = outputData.seed;
            }
            return origResult;
        };
    },
    async setup() {
        const origGraphToPrompt = app.graphToPrompt;
        app.graphToPrompt = async function () {
            // Shared node list across all chained hooks — one graph walk per queue call
            const seedFilter = n => SEED_NODE_TYPES.includes(n.type) && n._Eclipse_seedWidget;
            enterGraphToPromptHook();
            try {
                for (const { node } of getGraphNodeList(app.graph)) {
                    if (seedFilter(node)) clearNodeQueuedSeed(node);
                }
                const promptData = await origGraphToPrompt.apply(this, arguments);
                for (const { node, outputKey } of getGraphNodeList(app.graph)) {
                    if (!seedFilter(node)) continue;
                    if (node.mode === 2 || node.mode === 4) continue;
                    if (!promptData.output || !promptData.output[outputKey]) continue;

                    const seedInput = node.inputs?.find((inp) => inp.name === node._Eclipse_seedWidget?.name);
                    const isConnected = seedInput && seedInput.link != null;

                    const resolvedSeed = node.getSeedToUse() ?? getResolvedSeedFromGraph(node, node._Eclipse_seedWidget?.name);
                    if (resolvedSeed != null) {
                        storeQueuedSeed(node, resolvedSeed);
                        if (!isConnected) {
                            if (promptData.output[outputKey].inputs && promptData.output[outputKey].inputs.seed !== undefined) {
                                const currentSeed = promptData.output[outputKey].inputs.seed;
                                if (Number(currentSeed) !== Number(resolvedSeed)) {
                                    promptData.output[outputKey].inputs.seed = resolvedSeed;
                                }
                            }
                        }
                        if (Number(node._Eclipse_lastSeed) !== Number(resolvedSeed)) {
                            node._Eclipse_lastSeed = resolvedSeed;
                            nodeLastSeeds[node.id] = resolvedSeed;
                        }
                        node._Eclipse_cachedInputSeed = null;
                        node._Eclipse_cachedResolvedSeed = null;
                        if (node._Eclipse_lastSeedButton) {
                            const currentSeedValue = node._Eclipse_seedWidget.value;
                            if (isConnected || SPECIAL_SEEDS.includes(currentSeedValue)) {
                                node._Eclipse_lastSeedButton.name = `🌘 ${resolvedSeed}`;
                                node._Eclipse_lastSeedButton.disabled = false;
                            } else {
                                node._Eclipse_lastSeedButton.name = LAST_SEED_BUTTON_LABEL;
                                node._Eclipse_lastSeedButton.disabled = true;
                            }
                            if (isVueMode()) notifyVue(node);
                        }
                        if (!isConnected && promptData.workflow) {
                            const workflowNode = findWorkflowNode(promptData.workflow, outputKey);
                            if (workflowNode?.widgets_values) {
                                const seedWidgetIndex = node.widgets.indexOf(node._Eclipse_seedWidget);
                                if (seedWidgetIndex >= 0 && workflowNode.widgets_values[seedWidgetIndex] !== resolvedSeed) {
                                    workflowNode.widgets_values[seedWidgetIndex] = resolvedSeed;
                                }
                            }
                        }
                    }
                }
                return promptData;
            } finally {
                exitGraphToPromptHook();
            }
        };
    },
});
