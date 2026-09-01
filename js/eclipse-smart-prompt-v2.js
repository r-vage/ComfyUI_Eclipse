import {
    app
} from './comfy/index.js';
import {
    debounce,
    canvasDirtyBatcher,
    notifyVue,
    createWidgetVisibilityManager,
    smartResize,
    isConfiguringGraph,
    isVueMode,
} from './eclipse-widget-performance-utils.js';
import {
    injectComboChipCSS,
    createComboChipWidget as _createComboChipWidget
} from './eclipse-combo-chip.js';
import { getResolvedSeedFromGraph as _getResolvedSeedFromGraph, storeQueuedSeed, enterGraphToPromptHook, exitGraphToPromptHook, getGraphNodeList, clearNodeQueuedSeed, findWorkflowNode } from './eclipse-seed-utils.js';
const NODE_NAME = 'Smart Prompt v2 [Eclipse]';
const LAST_SEED_BUTTON_LABEL = '🌘 (Use Last Queued Seed)';
const RESET_SELECTIONS_BUTTON_LABEL = '↺ Reset Visible Selections to None';
const SPECIAL_SEEDS = [-1, -2, -3];
injectComboChipCSS('spv2');

function createFolderChipWidget(node, options, savedValue, origIdx) {
    return _createComboChipWidget({
        node,
        options,
        savedValue,
        origIdx,
        widgetName: 'folders',
        cssPrefix: 'spv2',
    });
}
app.registerExtension({
    name: 'Eclipse.SmartPromptV2',
    async setup() {
        const origGraphToPrompt = app.graphToPrompt;
        app.graphToPrompt = async function () {
            // Shared node list across all chained hooks — one graph walk per queue call
            const seedFilter = n => n.type === NODE_NAME && n._Eclipse_seedWidget;
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
                    const resolvedSeed = node.getSeedToUse() ?? _getResolvedSeedFromGraph(node);
                    if (resolvedSeed == null) continue;
                    storeQueuedSeed(node, resolvedSeed);
                    if (promptData.output[outputKey].inputs && promptData.output[outputKey].inputs.seed !== undefined) {
                        const currentSeed = promptData.output[outputKey].inputs.seed;
                        if (Number(currentSeed) !== Number(resolvedSeed)) {
                            promptData.output[outputKey].inputs.seed = resolvedSeed;
                        }
                    }
                    if (promptData.output[outputKey].inputs?.seed_input !== undefined) {
                        delete promptData.output[outputKey].inputs.seed_input;
                    }
                    if (Number(node._Eclipse_lastSeed) !== Number(resolvedSeed)) {
                        node._Eclipse_lastSeed = resolvedSeed;
                    }
                    node._Eclipse_cachedInputSeed = null;
                    node._Eclipse_cachedResolvedSeed = null;
                    if (node._Eclipse_lastSeedButton) {
                        const seedInput = node.inputs?.find((inp) => inp.name === 'seed_input');
                        const hasSeedLink = seedInput && seedInput.link != null;
                        const widgetValue = node._Eclipse_seedWidget.value;
                        if (hasSeedLink || SPECIAL_SEEDS.includes(widgetValue)) {
                            node._Eclipse_lastSeedButton.name = `🌘 ${resolvedSeed}`;
                            node._Eclipse_lastSeedButton.disabled = false;
                        } else {
                            node._Eclipse_lastSeedButton.name = LAST_SEED_BUTTON_LABEL;
                            node._Eclipse_lastSeedButton.disabled = true;
                        }
                        if (isVueMode()) notifyVue(node);
                    }
                    if (promptData.workflow) {
                        const wfNode = findWorkflowNode(promptData.workflow, outputKey);
                        if (wfNode && wfNode.widgets_values) {
                            const seedIdx = node.widgets.indexOf(node._Eclipse_seedWidget);
                            if (seedIdx >= 0 && wfNode.widgets_values[seedIdx] !== resolvedSeed) {
                                wfNode.widgets_values[seedIdx] = resolvedSeed;
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
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;
        nodeType.prototype.generateRandomSeed = function () {
            const step = this._Eclipse_seedWidget?.options?.step || 1;
            const minVal = this._Eclipse_randomMin || 0;
            const range = ((this._Eclipse_randomMax || 0xFFFFFFFF) - minVal) / (step / 10);
            let result = Math.floor(Math.random() * range) * (step / 10) + minVal;
            if (SPECIAL_SEEDS.includes(result)) result = 0;
            return result;
        };
        nodeType.prototype.getSeedToUse = function () {
            const seedInput = this.inputs?.find((inp) => inp.name === 'seed_input');
            if (seedInput && seedInput.link != null) return null;
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
            }
            return ret;
        };
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            const autoFoldersW = node.widgets?.find((w) => w.name === 'folders');
            const SKIP_NAMES = new Set(['folders', 'seed', 'seed_input']);
            const folderSet = new Set();
            for (const w of node.widgets || []) {
                if (SKIP_NAMES.has(w.name) || w.type === 'button') continue;
                const prefix = w.name.split(' ')[0];
                if (prefix && w.name.includes(' ')) folderSet.add(prefix);
            }
            const folderOptions = [...folderSet].sort();
            // Fallback when autoFoldersW has no saved value: start with nothing
            // selected.  Matches Python default="" and keeps freshly added
            // nodes compact (no toggles shown until user selects a folder).
            let savedFolders = [];
            let chipWidget;
            const origIdx = autoFoldersW ? node.widgets.indexOf(autoFoldersW) : 0;
            if (autoFoldersW) {
                const v = autoFoldersW.value;
                if (typeof v === 'string' && v.trim()) {
                    savedFolders = v.split(',').map(s => s.trim()).filter(Boolean);
                } else if (Array.isArray(v) && v.length > 0) {
                    savedFolders = v.slice();
                }
                autoFoldersW.onRemove?.();
                node.widgets.splice(node.widgets.indexOf(autoFoldersW), 1);
            }
            chipWidget = createFolderChipWidget(node, folderOptions, savedFolders, origIdx);
            node._Eclipse_chipWidget = chipWidget;
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
            if (seedWidget) {
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
                node._Eclipse_randomizeButton = randomizeBtn;
                node._Eclipse_newRandomButton = newFixedBtn;
                const buttons = [randomizeBtn, newFixedBtn, lastSeedBtn];
                for (let b = buttons.length - 1; b >= 0; b--) {
                    const btn = buttons[b];
                    btn.serialize = false;
                    const btnIdx = node.widgets.indexOf(btn);
                    if (btnIdx !== seedIndex + 1) {
                        node.widgets.splice(btnIdx, 1);
                        node.widgets.splice(seedIndex + 1, 0, btn);
                    }
                }
                const updateSeedInputState = () => {
                    if (node.id === -1) return;
                    const seedInput = node.inputs?.find((inp) => inp.name === 'seed_input');
                    const isConnected = seedInput && seedInput.link != null;
                    if (node._Eclipse_lastSeedInputConnected === isConnected) return;
                    node._Eclipse_lastSeedInputConnected = isConnected;
                    const hidden = isConnected;
                    seedWidget.hidden = hidden;
                    if (seedWidget.options) seedWidget.options.hidden = hidden;
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
            }
            const vis = createWidgetVisibilityManager(node);
            // Pre-hide all file-toggle widgets (those with "folder name" prefix
            // in their name, i.e. names containing a space).  Chip, seed,
            // folder selector, and buttons are excluded.  This prevents the
            // show-then-hide flash on cold workflow load: Vue's first render
            // sees them already hidden; refreshFolderVisibility below only
            // flips the selected subset to visible.
            const _toggleNames = [];
            for (const w of node.widgets || []) {
                if (w.name === 'folders' || w.name === 'seed' || w.type === 'button') continue;
                if (w === chipWidget) continue;
                if (w === node._Eclipse_randomizeButton || w === node._Eclipse_newRandomButton || w === node._Eclipse_lastSeedButton) continue;
                if (w.name && w.name.includes(' ')) _toggleNames.push(w.name);
            }
            vis.hideInitially(_toggleNames);
            const resetSelectionsBtn = node.addWidget('button', RESET_SELECTIONS_BUTTON_LABEL, '', () => {
                let changed = false;
                for (const widgetName of _toggleNames) {
                    const widget = node.widgets?.find((candidate) => candidate.name === widgetName);
                    if (!widget || widget.hidden || widget.options?.hidden || widget.value === 'None') continue;
                    widget.value = 'None';
                    widget.callback?.call(widget, 'None');
                    changed = true;
                }
                if (!changed) return;
                if (isVueMode()) notifyVue(node);
                canvasDirtyBatcher.markDirty(node, true, true);
            }, {
                serialize: false
            });
            resetSelectionsBtn.serialize = false;
            node._Eclipse_resetSelectionsButton = resetSelectionsBtn;
            const refreshFolderVisibility = () => {
                if (node.id === -1) return;
                const selectedFolders = new Set(Array.isArray(chipWidget.value) ? chipWidget.value : []);
                const key = [...selectedFolders].sort().join(',');
                if (node._Eclipse_lastFolderKey === key) return;
                node._Eclipse_lastFolderKey = key;
                node.widgets?.forEach((w) => {
                    if (w.name === 'folders' || w.name === 'seed') return;
                    if (w.type === 'button') return;
                    if (w === chipWidget) return;
                    if (w === node._Eclipse_randomizeButton || w === node._Eclipse_newRandomButton || w === node._Eclipse_lastSeedButton) return;
                    const prefix = w.name.split(' ')[0];
                    const show = selectedFolders.has(prefix);
                    vis.setVisible(w.name, show);
                });
                smartResize(node, {
                    minWidth: 0,
                    minHeight: 50,
                    padding: 0
                });
            };
            const debouncedFolderRefresh = debounce(refreshFolderVisibility, 200);
            chipWidget.callback = () => {
                node._Eclipse_lastFolderKey = undefined;
                vis.markUserDriven();
                debouncedFolderRefresh();
            };
            if (node._Eclipse_updateSeedInputState) {
                const updateSeedState = node._Eclipse_updateSeedInputState;
                const debouncedBoth = debounce(() => {
                    updateSeedState();
                    refreshFolderVisibility();
                }, 150);
                const origOnConnectionsChange = node.onConnectionsChange;
                node.onConnectionsChange = function (ioType, slotIndex, isConnected, linkInfo) {
                    if (origOnConnectionsChange) origOnConnectionsChange.apply(this, arguments);
                    const seedInput = this.inputs?.find((inp) => inp.name === 'seed_input');
                    if (seedInput) debouncedBoth();
                };
            }
            node.widgets?.forEach((w) => {
                if (w.name !== 'folders' && w.name !== 'seed' && w.type !== 'button' && w !== chipWidget) {
                    const parts = w.name.split(' ');
                    if (parts.length > 1) w.label = parts.slice(1).join(' ');
                }
            });
            if (_app.canvas) {
                const maxWidth = 250;
                const origComputeSize = node.computeSize.bind(node);
                node.computeSize = function () {
                    const size = origComputeSize();
                    if (size[0] > maxWidth) size[0] = maxWidth;
                    return size;
                };
            }
            // Call synchronously (no setTimeout 50ms) so Vue's first render
            // sees the final visibility state.  hideInitially above already
            // hid the full toggle set; this pass unhides the selected subset.
            // Skip during workflow load — onConfigure runs refreshFolderVisibility
            // next with the correct folder values, and node.size is restored
            // from serialized data (no need for the sync size-shrink).
            if (!node._Eclipse_initialized && !isConfiguringGraph()) {
                node._Eclipse_initialized = true;
                if (node._Eclipse_updateSeedInputState) node._Eclipse_updateSeedInputState();
                refreshFolderVisibility();
                // Synchronous size-shrink.  On fresh add the frontend's
                // initial computeSize() pass happened before hideInitially()
                // ran, so node.size[1] is still sized for every widget.  The
                // async rAF pass inside smartResize() eventually corrects this
                // but leaves a visible tall-node gap.  Force a sync re-size
                // now so the node renders at its correct compact height on
                // the very first paint.
                const _oldH = node.size[1];
                node.size[1] = 0;
                const _c = node.computeSize();
                if (_c[1] !== _oldH) node.setSize?.([node.size[0], _c[1]]);
                else node.size[1] = _oldH;
            }
            const origOnConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                if (origOnConfigure) origOnConfigure.apply(this, arguments);
                node._Eclipse_initialized = true;
                // Synchronous refresh during onConfigure — still inside
                // configuringGraph window, so Phase 1.7 skips Vue notifies.
                node._Eclipse_lastSeedInputConnected = undefined;
                node._Eclipse_lastFolderKey = undefined;
                if (node._Eclipse_updateSeedInputState) node._Eclipse_updateSeedInputState();
                refreshFolderVisibility();
            };
            return ret;
        };
    },
});
