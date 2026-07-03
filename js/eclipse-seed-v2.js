import {
    app
} from './comfy/index.js';
import {
    notifyVue,
    isVueMode
} from './eclipse-widget-performance-utils.js';
import { storeQueuedSeed, enterGraphToPromptHook, exitGraphToPromptHook, getGraphNodeList, clearNodeQueuedSeed, findWorkflowNode } from './eclipse-seed-utils.js';
const LAST_SEED_BUTTON_LABEL = '♻️ (Use Last Queued Seed)';
const SPECIAL_SEEDS = [-1, -2, -3];
const nodeLastSeeds = {};
const SEED_NODE_TYPES_V2 = ['Sampler Settings NI+Seed v2 [Eclipse]', 'Sampler Settings+Seed v2 [Eclipse]', ];
app.registerExtension({
    name: 'Eclipse.SamplerSettingsSeedV2',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (!SEED_NODE_TYPES_V2.includes(nodeData.name)) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            let seedWidget = null;
            for (const [idx, w] of node.widgets.entries()) {
                const wName = (w.name || '').toString().toLowerCase();
                const wLabel = (w.label || w.options?.label || w.options?.name || '').toString().toLowerCase();
                const wLocalized = (w.localized_name || '').toString().toLowerCase();
                if (wName === 'seed' || wLabel === 'seed' || wLocalized === 'seed') {
                    seedWidget = w;
                } else if (wName === 'control_after_generate') {
                    node.widgets.splice(idx, 1);
                }
            }
            if (!seedWidget) {
                console.warn(`Eclipse: Could not find Seed widget in ${nodeData.name}. Widgets:`, node.widgets.map((w) => ({
                    name: w.name,
                    label: w.label,
                    options: w.options
                })), );
                return ret;
            }
            node._Eclipse_seedWidget = seedWidget;
            node._Eclipse_lastSeed = undefined;
            node._Eclipse_randomMin = 0;
            node._Eclipse_randomMax = Number.MAX_SAFE_INTEGER;
            node._Eclipse_cachedInputSeed = null;
            node._Eclipse_cachedResolvedSeed = null;
            const origCallback = seedWidget.callback;
            seedWidget.callback = (val) => {
                node._Eclipse_cachedInputSeed = null;
                node._Eclipse_cachedResolvedSeed = null;
                if (origCallback) return origCallback.call(seedWidget, val);
            };
            const seedIndex = node.widgets.indexOf(seedWidget);
            const newFixedBtn = node.addWidget('button', '🎲 New Fixed Random', '', () => {
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
            const buttons = [newFixedBtn, lastSeedBtn];
            for (let b = buttons.length - 1; b >= 0; b--) {
                const btn = buttons[b];
                const btnIdx = node.widgets.indexOf(btn);
                if (btnIdx !== seedIndex + 1) {
                    node.widgets.splice(btnIdx, 1);
                    node.widgets.splice(seedIndex + 1, 0, btn);
                }
            }
            return ret;
        };
        nodeType.prototype.generateRandomSeed = function () {
            const step = this._Eclipse_seedWidget?.options?.step || 1;
            const minVal = this._Eclipse_randomMin || 0;
            const range = ((this._Eclipse_randomMax || 0xFFFFFFFF) - minVal) / (step / 10);
            let result = Math.floor(Math.random() * range) * (step / 10) + minVal;
            if (SPECIAL_SEEDS.includes(result)) result = 0;
            return result;
        };
        nodeType.prototype.getSeedToUse = function () {
            const seedValue = Number(this._Eclipse_seedWidget.value);
            if (this._Eclipse_cachedInputSeed === seedValue && this._Eclipse_cachedResolvedSeed != null) {
                return this._Eclipse_cachedResolvedSeed;
            }
            let resolved = null;
            if (SPECIAL_SEEDS.includes(seedValue)) {
                if (typeof this._Eclipse_lastSeed === 'number' && !SPECIAL_SEEDS.includes(this._Eclipse_lastSeed)) {
                    if (seedValue === -2) resolved = this._Eclipse_lastSeed + 1;
                    else if (seedValue === -3) resolved = this._Eclipse_lastSeed - 1;
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
            const ret = origOnExecuted ? origOnExecuted.apply(this, arguments) : undefined;
            if (outputData && outputData.seed !== undefined) {
                this._Eclipse_lastSeed = outputData.seed;
                nodeLastSeeds[this.id] = outputData.seed;
            }
            return ret;
        };
    },
    async setup() {
        const origGraphToPrompt = app.graphToPrompt;
        app.graphToPrompt = async function () {
            // Shared node list across all chained hooks — one graph walk per queue call
            const seedFilter = n => SEED_NODE_TYPES_V2.includes(n.type) && n._Eclipse_seedWidget;
            enterGraphToPromptHook();
            for (const { node } of getGraphNodeList(app.graph)) {
                if (seedFilter(node)) clearNodeQueuedSeed(node);
            }
            let promptData;
            try {
                promptData = await origGraphToPrompt.apply(this, arguments);
            } catch (e) {
                exitGraphToPromptHook();
                throw e;
            }
            for (const { node, outputKey } of getGraphNodeList(app.graph)) {
                if (!seedFilter(node)) continue;
                if (node.mode === 2 || node.mode === 4) continue;
                if (!promptData.output || !promptData.output[outputKey]) continue;
                const resolvedSeed = node.getSeedToUse();
                storeQueuedSeed(node, resolvedSeed);
                if (promptData.output[outputKey].inputs && promptData.output[outputKey].inputs.seed !== undefined) {
                    const currentSeed = promptData.output[outputKey].inputs.seed;
                    if (Number(currentSeed) !== Number(resolvedSeed)) {
                        promptData.output[outputKey].inputs.seed = resolvedSeed;
                    }
                }
                if (Number(node._Eclipse_lastSeed) !== Number(resolvedSeed)) {
                    node._Eclipse_lastSeed = resolvedSeed;
                    nodeLastSeeds[node.id] = resolvedSeed;
                }
                node._Eclipse_cachedInputSeed = null;
                node._Eclipse_cachedResolvedSeed = null;
                if (node._Eclipse_lastSeedButton) {
                    const widgetValue = node._Eclipse_seedWidget.value;
                    if (SPECIAL_SEEDS.includes(widgetValue)) {
                        node._Eclipse_lastSeedButton.name = `♻️ ${resolvedSeed}`;
                        node._Eclipse_lastSeedButton.disabled = false;
                    } else {
                        node._Eclipse_lastSeedButton.name = LAST_SEED_BUTTON_LABEL;
                        node._Eclipse_lastSeedButton.disabled = true;
                    }
                    if (isVueMode()) notifyVue(node);
                }
                if (promptData.workflow) {
                    const wfNode = findWorkflowNode(promptData.workflow, outputKey);
                    if (wfNode?.widgets_values) {
                        const seedIdx = node.widgets.indexOf(node._Eclipse_seedWidget);
                        if (seedIdx >= 0 && wfNode.widgets_values[seedIdx] !== resolvedSeed) {
                            wfNode.widgets_values[seedIdx] = resolvedSeed;
                        }
                    }
                }
            }
            exitGraphToPromptHook();
            return promptData;
        };
    },
});
