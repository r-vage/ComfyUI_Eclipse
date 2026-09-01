import {
    app
} from './comfy/index.js';
import {
    createWidgetVisibilityManager,
    isConfiguringGraph,
    notifyVue,
    isVueMode
} from './eclipse-widget-performance-utils.js';
import { getResolvedSeedFromGraph as _getResolvedSeedFromGraph, storeQueuedSeed, enterGraphToPromptHook, exitGraphToPromptHook, getGraphNodeList, clearNodeQueuedSeed, findWorkflowNode } from './eclipse-seed-utils.js';
import { migrateWildcardProcessorWorkflow } from './eclipse-wildcard-workflow-migration.js';
const NODE_NAME = 'Wildcard Processor [Eclipse]';
const LAST_SEED_BUTTON_LABEL = '🌘 (Use Last Queued Seed)';
const RANDOMIZE_BUTTON_LABEL = '🌑 Randomize Each Time';
const NEW_RANDOM_BUTTON_LABEL = '🌕 New Fixed Random';
const SPECIAL_SEED_RANDOM = -1;
const SPECIAL_SEED_INCREMENT = -2;
const SPECIAL_SEED_DECREMENT = -3;
const SPECIAL_SEEDS = [-1, -2, -3];

let wildcardList = [];
let wildcardListLoading = false;
async function loadWildcardList() {
    if (wildcardListLoading) return;
    wildcardListLoading = true;
    try {
        const resp = await fetch('/eclipse/wildcards/list');
        if (resp.ok) wildcardList = await resp.json();
    } catch (err) {
        console.warn('[Eclipse Wildcard] Failed to load wildcard list:', err);
        wildcardList = [];
    } finally {
        wildcardListLoading = false;
    }
}

function updateWildcardCombo(widget) {
    if (!widget) return;
    const newOptions = ['Select a Wildcard', ...wildcardList];
    if (widget.options) {
        if (typeof widget.options === 'object' && !Array.isArray(widget.options)) {
            widget.options.values = newOptions;
        } else if (Array.isArray(widget.options)) {
            Object.defineProperty(widget, 'options', {
                value: newOptions,
                writable: true
            });
        }
    } else {
        Object.defineProperty(widget, 'options', {
            value: newOptions,
            writable: true
        });
    }
    if (widget.element) widget.element.style.setProperty('--changed', 'true', 'important');
}

function cleanProcessedText(text) {
    if (!text) return text;
    text = text.replace(/__[\w.\-+/*\\]+?__/g, '');
    text = text.replace(/[,\s]*,[,\s]*,/g, ',');
    text = text.replace(/\.,\s*/g, ', ');
    text = text.replace(/,\s*\./g, '.');
    text = text.replace(/\s*,\s*,/g, ',');
    text = text.replace(/^\s*,\s*/g, '');
    text = text.replace(/\s*,\s*$/g, '');
    text = text.replace(/\s+/g, ' ').trim();
    return text;
}
async function updatePopulatedText(widget, text, seed) {
    if (!widget || !text) return;
    try {
        const resp = await fetch('/eclipse/wildcards/process', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                text,
                seed
            }),
        });
        if (resp.ok) {
            const data = await resp.json();
            if (data.success) {
                const cleaned = cleanProcessedText(data.output);
                widget.value = cleaned;
                if (widget.callback) widget.callback(cleaned);
            } else {
                console.warn('[Eclipse Wildcard] Server error - success=false');
            }
        } else {
            console.warn('[Eclipse Wildcard] Server returned status:', resp.status);
        }
    } catch (err) {
        console.error('[Eclipse Wildcard] Error updating preview:', err);
    }
}

function updateUIForMode(node, mode) {
    const seedWidget = node.widgets?.find((w) => w.name === 'seed');
    if (!seedWidget) return;
    switch (mode) {
        case 'populate':
            if (seedWidget.element) {
                seedWidget.element.style.opacity = '1.0';
                seedWidget.element.title = 'Change seed to generate new output, fix seed to keep same output';
            }
            break;
        case 'fixed':
            if (seedWidget.element) {
                seedWidget.element.style.opacity = '0.5';
                seedWidget.element.title = "Seed is ignored in 'fixed' mode";
            }
            break;
    }
}
(app.registerExtension({
    name: 'Eclipse.WildcardProcessor',
    beforeConfigureGraph(graphData) {
        migrateWildcardProcessorWorkflow(graphData);
    },
    async setup() {
        await loadWildcardList();
        const origGraphToPrompt = app.graphToPrompt;
        app.graphToPrompt = async function () {
            // Shared node list across all chained hooks — one graph walk per queue call
            const seedFilter = n => n.type === NODE_NAME && n._Eclipse_seedWidget;
            const resolvedSeeds = new Map();
            enterGraphToPromptHook();
            try {
                for (const { node } of getGraphNodeList(app.graph)) {
                    if (seedFilter(node)) clearNodeQueuedSeed(node);
                }
                // Pre-pass: populate wildcard text before prompt is built
                for (const { node } of getGraphNodeList(app.graph)) {
                    if (node.type !== NODE_NAME) continue;
                    const seedW = node._Eclipse_seedWidget
                        ?? node.widgets?.find((w) => w.name === 'seed');
                    if (seedW) {
                        const seedInput = node.inputs?.find((input) => input.name === 'seed_input');
                        const hasSeedLink = seedInput?.link != null;
                        const resolvedSeed = hasSeedLink
                            ? (_getResolvedSeedFromGraph(node, 'seed_input') ?? seedW.value)
                            : (node.getSeedToUse?.() ?? seedW.value);
                        resolvedSeeds.set(node, { value: resolvedSeed, external: hasSeedLink });
                    }
                    const wildcardTextW = node.widgets?.find((w) => w.name === 'wildcard_text');
                    const populatedTextW = node.widgets?.find((w) => w.name === 'populated_text');
                    const modeW = node.widgets?.find((w) => w.name === 'mode');
                    if (!modeW || !wildcardTextW || !populatedTextW) continue;
                    const mode = modeW.value;
                    const rawText = wildcardTextW.value;
                    if (mode === 'fixed') continue;
                    if (mode === 'populate' && rawText) {
                        const resolvedSeed = resolvedSeeds.get(node)?.value ?? 0;
                        try {
                            const resp = await fetch('/eclipse/wildcards/process', {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/json'
                                },
                                body: JSON.stringify({
                                    text: rawText,
                                    seed: resolvedSeed
                                }),
                            });
                            if (resp.ok) {
                                const data = await resp.json();
                                if (data.success) populatedTextW.value = data.output;
                            }
                        } catch (err) {
                            console.error('[Eclipse Wildcard] graphToPrompt wildcard processing error:', err);
                        }
                    }
                }
                const promptData = await origGraphToPrompt.apply(this, arguments);
                for (const { node, outputKey } of getGraphNodeList(app.graph)) {
                    if (node.type !== NODE_NAME || !node._Eclipse_seedWidget) continue;
                    if (node.mode === 2 || node.mode === 4) continue;
                    if (!promptData.output || !promptData.output[outputKey]) continue;
                    const seedWidget = node._Eclipse_seedWidget;
                    const seedState = resolvedSeeds.get(node);
                    const hasSeedLink = seedState?.external ?? false;
                    const resolvedSeed = seedState?.value ?? seedWidget.value;
                    if (promptData.output[outputKey].inputs) {
                        promptData.output[outputKey].inputs.seed = resolvedSeed;
                        delete promptData.output[outputKey].inputs.seed_input;
                    }
                    storeQueuedSeed(node, resolvedSeed);
                    node._Eclipse_lastSeed = resolvedSeed;
                    node._Eclipse_cachedInputSeed = null;
                    node._Eclipse_cachedResolvedSeed = null;
                    if (node._Eclipse_lastSeedButton) {
                        const seedVal = node._Eclipse_seedWidget.value;
                        const showResolved = hasSeedLink || SPECIAL_SEEDS.includes(seedVal);
                        if (showResolved) {
                            node._Eclipse_lastSeedButton.label = `🌘 ${resolvedSeed}`;
                            node._Eclipse_lastSeedButton.disabled = false;
                        } else {
                            node._Eclipse_lastSeedButton.label = LAST_SEED_BUTTON_LABEL;
                            node._Eclipse_lastSeedButton.disabled = true;
                        }
                        if (isVueMode()) notifyVue(node);
                    }
                    if (promptData.workflow) {
                        const wfNode = findWorkflowNode(promptData.workflow, outputKey);
                        if (wfNode && wfNode.widgets_values) {
                            node.widgets?.find((w) => w.name === 'mode');
                            const seedW = node.widgets?.find((w) => w.name === 'seed');
                            const populatedW = node.widgets?.find((w) => w.name === 'populated_text');
                            const seedIdx = node.widgets.indexOf(seedW);
                            const popIdx = node.widgets.indexOf(populatedW);
                            if (seedIdx >= 0) wfNode.widgets_values[seedIdx] = resolvedSeed;
                            if (popIdx >= 0 && populatedW) wfNode.widgets_values[popIdx] = populatedW.value;
                        }
                    }
                }
                return promptData;
            } finally {
                exitGraphToPromptHook();
            }
        };
    },
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME && nodeData.class_type !== NODE_NAME) return;
        nodeType.prototype.generateRandomSeed = function () {
            const step = this._Eclipse_seedWidget?.options?.step || 1;
            const minVal = this._Eclipse_randomMin || 0;
            const range = ((this._Eclipse_randomMax || 0xFFFFFFFF) - minVal) / (step / 10);
            let seed = Math.floor(Math.random() * range) * (step / 10) + minVal;
            if (SPECIAL_SEEDS.includes(seed)) seed = 0;
            return seed;
        };
        nodeType.prototype.getSeedToUse = function () {
            const seedVal = Number(this._Eclipse_seedWidget.value);
            if (this._Eclipse_cachedInputSeed === seedVal && this._Eclipse_cachedResolvedSeed != null) {
                return this._Eclipse_cachedResolvedSeed;
            }
            let resolved = null;
            if (SPECIAL_SEEDS.includes(seedVal)) {
                if (typeof this._Eclipse_lastSeed === 'number' && !SPECIAL_SEEDS.includes(this._Eclipse_lastSeed)) {
                    if (seedVal === -2) resolved = this._Eclipse_lastSeed + 1;
                    else if (seedVal === -3) resolved = this._Eclipse_lastSeed - 1;
                }
                if (resolved == null || SPECIAL_SEEDS.includes(resolved)) {
                    resolved = this.generateRandomSeed();
                }
            }
            const result = resolved != null ? resolved : seedVal;
            this._Eclipse_cachedInputSeed = seedVal;
            this._Eclipse_cachedResolvedSeed = result;
            return result;
        };
        const origOnExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (data) {
            const ret = origOnExecuted ? origOnExecuted.apply(this, arguments) : undefined;
            if (data && data.text && data.text.length > 0) {
                const modeW = this.widgets?.find((w) => w.name === 'mode');
                const mode = modeW?.value || 'populate';
                const populatedW = this.widgets?.find((w) => w.name === 'populated_text');
                if (populatedW && mode === 'populate') populatedW.value = data.text[0];
            }
            if (data && data.seed !== undefined) {
                this._Eclipse_lastSeed = Array.isArray(data.seed) ? data.seed[0] : data.seed;
            }
            return ret;
        };
        nodeType.prototype.isSeedConnected = function () {
            const seedInput = this.inputs?.find((input) => input.name === 'seed_input');
            return seedInput && seedInput.link != null;
        };
        nodeType.prototype.updateSeedButtonStates = function () {
            const modeW = this.widgets?.find((w) => w.name === 'mode');
            const mode = modeW?.value || 'populate';
            const isConnected = this.isSeedConnected();
            if (mode !== 'populate' || isConnected) {
                if (this._Eclipse_randomizeButton) this._Eclipse_randomizeButton.disabled = true;
                if (this._Eclipse_newRandomButton) this._Eclipse_newRandomButton.disabled = true;
                if (this._Eclipse_lastSeedButton) this._Eclipse_lastSeedButton.disabled = true;
            } else {
                if (this._Eclipse_randomizeButton) this._Eclipse_randomizeButton.disabled = false;
                if (this._Eclipse_newRandomButton) this._Eclipse_newRandomButton.disabled = false;
                if (this._Eclipse_lastSeedButton && this._Eclipse_lastSeed != null) {
                    const seedVal = this._Eclipse_seedWidget?.value;
                    this._Eclipse_lastSeedButton.disabled = !SPECIAL_SEEDS.includes(seedVal);
                }
            }
            if (isVueMode()) notifyVue(this);
        };
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            this._isInitializing = true;
            if (origOnNodeCreated) origOnNodeCreated.call(this);
            const node = this;
            const vis = createWidgetVisibilityManager(node);
            this._Eclipse_seedVisibility = vis;
            let seedWidget = null;
            let ctrlIdx = -1;
            for (let idx = 0; idx < this.widgets.length; idx++) {
                const widget = this.widgets[idx];
                const name = (widget.name || '').toString().toLowerCase();
                if (name === 'seed') {
                    seedWidget = widget;
                } else if (name === 'control_after_generate') {
                    ctrlIdx = idx;
                }
            }
            if (ctrlIdx >= 0) this.widgets.splice(ctrlIdx, 1);
            if (!seedWidget) {
                console.warn('[Eclipse Wildcard] Seed widget not found! Available widgets:', this.widgets.map((w) => w.name), );
            }
            if (seedWidget) {
                this._Eclipse_seedWidget = seedWidget;
                this._Eclipse_lastSeed = undefined;
                this._Eclipse_randomMin = 0;
                this._Eclipse_randomMax = Number.MAX_SAFE_INTEGER;
                this._Eclipse_cachedInputSeed = null;
                this._Eclipse_cachedResolvedSeed = null;
                if (seedWidget.type) seedWidget.type = 'number';
                const randomizeBtn = this.addWidget('button', '_btn_randomize', '', () => {
                    seedWidget.value = -1;
                    if (seedWidget.callback) seedWidget.callback(-1);
                }, {
                    serialize: false
                }, );
                randomizeBtn.label = RANDOMIZE_BUTTON_LABEL;
                randomizeBtn.serialize = false;
                const newRandomBtn = this.addWidget('button', '_btn_new_fixed', '', () => {
                    const newSeed = this.generateRandomSeed();
                    seedWidget.value = newSeed;
                    if (seedWidget.callback) seedWidget.callback(newSeed);
                }, {
                    serialize: false
                }, );
                newRandomBtn.label = NEW_RANDOM_BUTTON_LABEL;
                newRandomBtn.serialize = false;
                const lastSeedBtn = this.addWidget('button', '_btn_last_seed', '', () => {
                    if (this._Eclipse_lastSeed != null) {
                        seedWidget.value = this._Eclipse_lastSeed;
                        lastSeedBtn.label = LAST_SEED_BUTTON_LABEL;
                        lastSeedBtn.disabled = true;
                        if (isVueMode()) notifyVue(this);
                    }
                }, {
                    serialize: false
                }, );
                lastSeedBtn.label = LAST_SEED_BUTTON_LABEL;
                lastSeedBtn.serialize = false;
                lastSeedBtn.disabled = true;
                this._Eclipse_lastSeedButton = lastSeedBtn;
                this._Eclipse_randomizeButton = randomizeBtn;
                this._Eclipse_newRandomButton = newRandomBtn;
                const wildcardsWidget = this.widgets?.find((w) => w.name === 'wildcards');
                const modeWidget = this.widgets?.find((w) => w.name === 'mode');
                const modeIdx = modeWidget ? this.widgets.indexOf(modeWidget) : -1;
                if (wildcardsWidget && modeIdx >= 0) {
                    const wcIdx = this.widgets.indexOf(wildcardsWidget);
                    if (wcIdx !== modeIdx + 1) {
                        this.widgets.splice(wcIdx, 1);
                        this.widgets.splice(modeIdx + 1, 0, wildcardsWidget);
                    }
                }
                const buttonList = [randomizeBtn, newRandomBtn, lastSeedBtn];
                for (let b = buttonList.length - 1; b >= 0; b--) {
                    const btn = buttonList[b];
                    const btnIdx = this.widgets.indexOf(btn);
                    const seedIdx = this.widgets.indexOf(seedWidget);
                    if (btnIdx !== seedIdx + 1) {
                        this.widgets.splice(btnIdx, 1);
                        this.widgets.splice(seedIdx + 1, 0, btn);
                    }
                }
                const spacer = {
                    type: 'SPACER',
                    name: 'spacer',
                    computeSize: () => [0, 8],
                    draw: () => {},
                    mouse: () => {},
                    serialize: false,
                };
                this.widgets.push(spacer);
            }
            vis.hideInitially(['seed', '_btn_randomize', '_btn_new_fixed', '_btn_last_seed']);
            this.updateSeedControlVisibility = function () {
                const visible = !this.isSeedConnected();
                vis.setVisible('seed', visible);
                vis.setVisible('_btn_randomize', visible);
                vis.setVisible('_btn_new_fixed', visible);
                vis.setVisible('_btn_last_seed', visible);
            };
            if (!isConfiguringGraph()) this.updateSeedControlVisibility();
            const origConfigure = this.onConfigure;
            this.onConfigure = function () {
                const ret = origConfigure?.apply(this, arguments);
                vis.clearCache();
                this.updateSeedControlVisibility();
                this.updateSeedButtonStates();
                return ret;
            };
            const origOnResize = this.onResize;
            this.onResize = function (size) {
                size[0] = Math.max(size[0], 200);
                size[1] = Math.max(size[1], 100);
                if (origOnResize) return origOnResize.apply(this, [size]);
            };
            const currentSize = this.size;
            if (currentSize[0] >= 259) this.size = [200, currentSize[1]];
            const wildcardTextW = this.widgets?.find((w) => w.name === 'wildcard_text');
            const populatedTextW = this.widgets?.find((w) => w.name === 'populated_text');
            const modeW = this.widgets?.find((w) => w.name === 'mode');
            const wildcardsComboW = this.widgets?.find((w) => w.name === 'wildcards');
            if (seedWidget) {
                const origSeedCb = seedWidget.callback;
                seedWidget.callback = (val) => {
                    node._Eclipse_cachedInputSeed = null;
                    node._Eclipse_cachedResolvedSeed = null;
                    if (origSeedCb) origSeedCb.call(seedWidget, val);
                    if (!node._isInitializing && modeW?.value === 'populate' && wildcardTextW?.value && populatedTextW) {
                        const seed = node.getSeedToUse();
                        updatePopulatedText(populatedTextW, wildcardTextW.value, seed);
                    }
                };
            }
            if (wildcardTextW && populatedTextW) {
                const origWildcardCb = wildcardTextW.callback;
                wildcardTextW.callback = function (val) {
                    try {
                        if (origWildcardCb) origWildcardCb.call(this, val);
                        if (node._isInitializing) return;
                        const stack = new Error().stack;
                        if (stack && stack.includes('serializeValue')) return;
                        if (modeW?.value === 'populate' && val && seedWidget) {
                            const seed = node.getSeedToUse();
                            updatePopulatedText(populatedTextW, val, seed);
                        }
                    } catch (err) {
                        console.error('[Eclipse Wildcard] Error in wildcard_text callback:', err);
                    }
                };
            }
            if (modeW) {
                const origModeCb = modeW.callback;
                modeW.callback = function (val) {
                    try {
                        if (origModeCb) origModeCb.call(this, val);
                        if (node._isInitializing) return;
                        if (val === 'populate') {
                            if (wildcardTextW && populatedTextW && seedWidget) {
                                populatedTextW.disabled = true;
                                if (populatedTextW.element) {
                                    populatedTextW.element.style.opacity = '0.85';
                                    populatedTextW.element.style.cursor = 'not-allowed';
                                    populatedTextW.element.title = 'Auto-generated in populate mode. Change seed to generate new output, fix seed to keep same output.';
                                }
                            }
                            node.updateSeedButtonStates();
                        } else if (val === 'fixed') {
                            if (populatedTextW) {
                                node._Eclipse_cachedInputSeed = undefined;
                                node._Eclipse_cachedResolvedSeed = undefined;
                                populatedTextW.disabled = false;
                                if (populatedTextW.element) {
                                    populatedTextW.element.style.opacity = '1.0';
                                    populatedTextW.element.style.cursor = 'text';
                                    populatedTextW.element.title = 'Edit to customize the output';
                                }
                            }
                            node.updateSeedButtonStates();
                        }
                        updateUIForMode(node, val);
                    } catch (err) {
                        console.error('[Eclipse Wildcard] Error in mode callback:', err);
                    }
                };
            }
            if (wildcardsComboW) {
                const origDraw = wildcardsComboW.draw;
                if (origDraw) {
                    wildcardsComboW.draw = function (ctx, x, y, w, h) {
                        updateWildcardCombo(this);
                        return origDraw.call(this, ctx, x, y, w, h);
                    };
                }
                const origWcCb = wildcardsComboW.callback;
                wildcardsComboW.callback = function (val) {
                    if (origWcCb) origWcCb.call(this, val);
                    if (val && val !== 'Select a Wildcard') {
                        const wtWidget = node.widgets?.find((w) => w.name === 'wildcard_text');
                        if (wtWidget) {
                            let text = wtWidget.value || '';
                            let separator = '';
                            if (text) {
                                const trimmed = text.trimEnd();
                                if (trimmed && !trimmed.endsWith(',')) separator = ', ';
                                else if (trimmed.endsWith(',')) separator = ' ';
                            }
                            wtWidget.value = text + separator + val;
                            wtWidget.value = wtWidget.value.replace(/\.,\s+/g, ', ');
                            wtWidget.value = wtWidget.value.replace(/\s+/g, ' ').trim();
                            if (wtWidget.callback) wtWidget.callback(wtWidget.value);
                        }
                        setTimeout(() => {
                            wildcardsComboW.value = 'Select a Wildcard';
                        }, 10);
                    }
                };
                updateWildcardCombo(wildcardsComboW);
            }
            setTimeout(() => {
                this._isInitializing = false;
                if (modeW && populatedTextW) {
                    const mode = modeW.value;
                    if (mode === 'populate') {
                        populatedTextW.disabled = true;
                        if (populatedTextW.element) {
                            populatedTextW.element.style.opacity = '0.85';
                            populatedTextW.element.style.cursor = 'not-allowed';
                            populatedTextW.element.title = 'Auto-generated in populate mode. Change seed to generate new output, fix seed to keep same output.';
                        }
                        node.updateSeedButtonStates();
                    }
                    updateUIForMode(node, mode);
                }
            }, 0);
            const origOnConnectionsChange = this.onConnectionsChange;
            this.onConnectionsChange = function (side, slotIdx, connected, linkInfo) {
                if (origOnConnectionsChange) origOnConnectionsChange.apply(this, arguments);
                if (side === 1) {
                    const input = this.inputs?.[slotIdx];
                    if (input?.name === 'seed_input') {
                        this.updateSeedControlVisibility();
                        this.updateSeedButtonStates();
                    }
                }
            };
        };
    },
    async nodeCreated(node, _app) {
        if (node.type !== NODE_NAME) return;
        const wildcardsW = node.widgets?.find((w) => w.name === 'wildcards');
        if (wildcardsW) updateWildcardCombo(wildcardsW);
    },
    async loadedGraphNode(node, _app) {
        if (node.type !== NODE_NAME) return;
        node.widgets?.find((w) => w.name === 'mode');
        const populatedW = node.widgets?.find((w) => w.name === 'populated_text');
        node.widgets?.find((w) => w.name === 'wildcard_text');
        const wildcardsW = node.widgets?.find((w) => w.name === 'wildcards');
        if (wildcardsW) updateWildcardCombo(wildcardsW);
        setTimeout(() => {
            node._isInitializing = false;
            if (populatedW) {
                const val = populatedW.value;
                if (populatedW.callback) populatedW.callback(val);
                if (populatedW.element) {
                    populatedW.element.value = val;
                } else if (node.onResize) {
                    node.onResize(node.size);
                }
                if (populatedW.options) populatedW.options.property = 'populated_text';
            }
            if (node.updateSeedButtonStates) node.updateSeedButtonStates();
            if (node.updateSeedControlVisibility) node.updateSeedControlVisibility();
            if (node.setDirtyCanvas) node.setDirtyCanvas(true, true);
        }, 100);
    },
    async refreshComboInNodes() {
        // Invalidate server-side caches first so /eclipse/wildcards/list returns fresh data.
        try { await fetch('/eclipse/reload_all'); } catch (_) {}
        try {
            const resp = await fetch('/eclipse/wildcards/list');
            if (resp.ok) {
                const newList = await resp.json();
                if (JSON.stringify(newList) !== JSON.stringify(wildcardList)) {
                    wildcardList = newList;
                    for (const key in app.graph._nodes) {
                        const node = app.graph._nodes[key];
                        if (node.type === NODE_NAME) {
                            const wc = node.widgets?.find((w) => w.name === 'wildcards');
                            if (wc) updateWildcardCombo(wc);
                        }
                    }
                }
            }
        } catch (_) {}
    },
}));
