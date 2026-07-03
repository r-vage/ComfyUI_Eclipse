import {
    app,
    api
} from './comfy/index.js';
import {
    notifyVue,
    createWidgetVisibilityManager,
    isVueMode
} from './eclipse-widget-performance-utils.js';
import {
    injectComboChipCSS,
    createComboChipWidget as _createComboChipWidget
} from './eclipse-combo-chip.js';
import { getResolvedSeedFromGraph as _getResolvedSeedFromGraph } from './eclipse-seed-utils.js';
const NODE_CONFIGS = {
    'Load Image From Folder [Eclipse]': {
        extName: 'Eclipse.LoadImageFromFolder',
        cssPrefix: 'lif',
        chipWidgetName: '_lif_features',
        logPrefix: 'LoadImageFromFolder',
        chipOptions: ['read_subfolders', 'stop_at_end', 'refresh_list', 'preview', '🎲 random', '⏫ increment', '⏬ decrement', '🔀 shuffle', ],
        chipToBacking: {
            'read_subfolders': 'include_subfolders',
            'stop_at_end': 'stop_at_end',
            'refresh_list': 'refresh_list',
        },
    },
    'Load Image From Folder (Pipe) [Eclipse]': {
        extName: 'Eclipse.LoadImageFromFolderPipe',
        cssPrefix: 'liff',
        chipWidgetName: '_liff_features',
        logPrefix: 'LoadImageFromFolder Pipe',
        chipOptions: ['read_subfolders', 'stop_at_end', 'extract_metadata', 'refresh_list', 'preview', '🎲 random', '⏫ increment', '⏬ decrement', '🔀 shuffle', ],
        chipToBacking: {
            'read_subfolders': 'include_subfolders',
            'stop_at_end': 'stop_at_end',
            'extract_metadata': 'extract_metadata',
            'refresh_list': 'refresh_list',
        },
    },
};
const ALL_NODES = new Set(Object.keys(NODE_CONFIGS));
const MODE_RANDOM = -1,
    MODE_INCREMENT = -2,
    MODE_DECREMENT = -3,
    MODE_RANDOM_NO_REPEAT = -4;
const SPECIAL_MODES = [MODE_RANDOM, MODE_INCREMENT, MODE_DECREMENT, MODE_RANDOM_NO_REPEAT];
const DEFAULT_CHIPS = ['read_subfolders', 'stop_at_end', 'preview'];
const MODE_CHIPS = ['🎲 random', '⏫ increment', '⏬ decrement', '🔀 shuffle'];
const MODE_CHIP_TO_INDEX = {
    '🎲 random': MODE_RANDOM,
    '⏫ increment': MODE_INCREMENT,
    '⏬ decrement': MODE_DECREMENT,
    '🔀 shuffle': MODE_RANDOM_NO_REPEAT,
};
const INDEX_TO_MODE_CHIP = Object.fromEntries(Object.entries(MODE_CHIP_TO_INDEX).map(([k, v]) => [v, k]));
for (const cfg of Object.values(NODE_CONFIGS)) injectComboChipCSS(cfg.cssPrefix);
const nodeFolderPaths = new Map();
const nodeStopTriggered = new Map();
const nodeImageCounts = new Map();
const fetchDebounceTimers = new Map();


async function updateImageCount(node) {
    const id = node.id;
    const folderW = node.widgets?.find(w => w.name === 'folder_path');
    const indexW = node.widgets?.find(w => w.name === 'index');
    if (!folderW || !indexW) return;
    const folderPath = folderW.value;
    const includeSubW = node.widgets?.find(w => w.name === 'include_subfolders');
    const includeSub = includeSubW?.value ?? false;
    if (!folderPath || !folderPath.trim()) {
        indexW.options.max = 999999;
        nodeImageCounts.set(id, 0);
        return;
    }
    try {
        const resp = await fetch('/eclipse/load_image_folder/count', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                folder_path: folderPath,
                include_subfolders: includeSub
            }),
        });
        if (resp.ok) {
            const count = (await resp.json()).total_count || 0;
            nodeImageCounts.set(id, count);
            if (count > 0) {
                indexW.options.max = Math.max(0, count - 1);
                if (indexW.value > indexW.options.max) {
                    indexW.value = indexW.options.max;
                    indexW.callback?.(indexW.value);
                }
            } else {
                indexW.options.max = 0;
            }
            const btn = node._Eclipse_lastIndexButton;
            if (btn && indexW.value === -4 && node._Eclipse_lastResolvedIndex !== null) {
                const used = node._Eclipse_usedIndices?.size || 0;
                btn.name = `♻️ ${node._Eclipse_lastResolvedIndex} (${used}/${count})`;
                if (isVueMode()) notifyVue(node);
            }
            node.setDirtyCanvas(true, true);
        }
    } catch (e) {
        console.warn('[Eclipse LoadImageFromFolder] Failed to fetch image count:', e);
    }
}

function updateImageCountDebounced(node, delay = 300) {
    const id = node.id;
    if (fetchDebounceTimers.has(id)) clearTimeout(fetchDebounceTimers.get(id));
    fetchDebounceTimers.set(id, setTimeout(() => {
        updateImageCount(node);
        fetchDebounceTimers.delete(id);
    }, delay));
}

function syncChipsToBacking(selectedSet, node, chipToBacking) {
    for (const [chip, backing] of Object.entries(chipToBacking)) {
        const w = node.widgets?.find(w => w.name === backing);
        if (w && w.value !== selectedSet.has(chip)) w.value = selectedSet.has(chip);
    }
}

function readChipsFromBacking(node, chipToBacking) {
    const chips = new Set();
    for (const [chip, backing] of Object.entries(chipToBacking)) {
        const w = node.widgets?.find(w => w.name === backing);
        if (w && w.value) chips.add(chip);
    }
    return chips;
}
app.registerExtension({
    name: 'Eclipse.LoadImageFromFolder',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        const cfg = NODE_CONFIGS[nodeData.name];
        if (!cfg) return;
        const backingWidgets = Object.values(cfg.chipToBacking);
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated?.apply(this, arguments);
            const node = this;
            const id = node.id;
            const vis = createWidgetVisibilityManager(node);
            // Pre-hide backing widgets so Vue's first render paints without them.
            // These are always hidden (the combo-chip is the visible UI).
            vis.hideInitially(backingWidgets);
            const findW = (name) => node.widgets?.find(w => w.name === name);
            const folderW = findW('folder_path');
            const indexW = findW('index');
            if (!folderW) {
                console.warn(`[${cfg.logPrefix}] folder_path widget not found`);
                return ret;
            }
            if (folderW.element) {
                const textarea = folderW.element.tagName === 'TEXTAREA' ? folderW.element : folderW.element.querySelector('textarea');
                const styledEl = textarea || folderW.element;
                const s = styledEl.style;
                s.background = '#1a1a1a';
                s.color = '#ccc';
                s.font = '12px monospace';
                s.borderRadius = '4px';
                s.border = 'none';
                s.outline = 'none';
                s.padding = '6px';
            }
            node._Eclipse_cfg = cfg;
            node._Eclipse_indexWidget = indexW;
            node._Eclipse_lastIndex = null;
            node._Eclipse_updatingIndex = false;
            node._Eclipse_lastResolvedIndex = null;
            node._Eclipse_lastIndexButton = null;
            node._Eclipse_lastSeedInput = undefined;
            node._Eclipse_usedIndices = new Set();
            node._Eclipse_pausedShuffle = false;
            const parseFolders = (v) => (v || '').split('\n').map(s => s.trim()).filter(s => s.length > 0);
            nodeFolderPaths.set(id, parseFolders(folderW.value));
            nodeStopTriggered.set(id, false);
            const initialSet = readChipsFromBacking(node, cfg.chipToBacking);
            const hasAnyBacking = backingWidgets.some(name => {
                const w = findW(name);
                return w && w.value === true;
            });
            const chipSet = hasAnyBacking ? initialSet : new Set(DEFAULT_CHIPS);
            if (indexW && SPECIAL_MODES.includes(indexW.value)) {
                const modeChip = INDEX_TO_MODE_CHIP[indexW.value];
                if (modeChip) chipSet.add(modeChip);
            }
            for (const name of backingWidgets) vis.setVisible(name, false);
            const origIdx = folderW ? node.widgets.indexOf(folderW) + 1 : 0;
            const featWidget = _createComboChipWidget({
                node,
                options: cfg.chipOptions,
                savedValue: chipSet,
                origIdx,
                widgetName: cfg.chipWidgetName,
                cssPrefix: cfg.cssPrefix,
                serialize: false,
                radioGroups: [MODE_CHIPS],
                radioToggle: true,
            });
            node._Eclipse_chipWidget = featWidget;
            featWidget.callback = () => {
                const selected = new Set(featWidget.value);
                syncChipsToBacking(selected, node, cfg.chipToBacking);
                const preview = node._eclipseDomPreview;
                if (preview) {
                    const wantPreview = selected.has('preview');
                    const {
                        container,
                        state
                    } = preview;
                    if (wantPreview && state.hidden) {
                        state.hidden = false;
                        container.style.display = '';
                        node.setDirtyCanvas(true, true);
                    } else if (!wantPreview && !state.hidden) {
                        state.hidden = true;
                        container.style.display = 'none';
                        node.setDirtyCanvas(true, true);
                    }
                }
                if (indexW) {
                    const activeMode = MODE_CHIPS.find(m => selected.has(m));
                    if (activeMode) {
                        const modeVal = MODE_CHIP_TO_INDEX[activeMode];
                        if (indexW.value !== modeVal) {
                            if (modeVal === MODE_RANDOM_NO_REPEAT && node._Eclipse_pausedShuffle) {
                                node._Eclipse_pausedShuffle = false;
                            } else if (modeVal === MODE_RANDOM_NO_REPEAT && indexW.value !== MODE_RANDOM_NO_REPEAT) {
                                node._Eclipse_usedIndices = new Set();
                            }
                            node._Eclipse_updatingIndex = true;
                            indexW.value = modeVal;
                            indexW.callback?.(modeVal);
                            node._Eclipse_updatingIndex = false;
                        }
                    } else if (SPECIAL_MODES.includes(indexW.value)) {
                        const pinVal = node._Eclipse_lastResolvedIndex ?? 0;
                        node._Eclipse_updatingIndex = true;
                        indexW.value = pinVal;
                        indexW.callback?.(pinVal);
                        node._Eclipse_updatingIndex = false;
                    }
                    node.setDirtyCanvas(true, true);
                }
                updateImageCountDebounced(node);
            };
            syncChipsToBacking(chipSet, node, cfg.chipToBacking);
            const preview = node._eclipseDomPreview;
            if (preview && !chipSet.has('preview')) {
                preview.state.hidden = true;
                preview.container.style.display = 'none';
            }
            const origFolderCb = folderW.callback;
            folderW.callback = function (val) {
                const oldPaths = nodeFolderPaths.get(id) || [];
                const newPaths = parseFolders(val);
                origFolderCb?.apply(this, arguments);
                const firstChanged = oldPaths[0] !== newPaths[0];
                const removed = oldPaths.filter(p => !newPaths.includes(p));
                const added = newPaths.filter(p => !oldPaths.includes(p));
                const needReset = removed.length > 0 || firstChanged;
                if (firstChanged || removed.length > 0 || added.length > 0) {
                    nodeFolderPaths.set(id, newPaths);
                    if (needReset) nodeStopTriggered.set(id, false);
                    if (firstChanged) {
                        node._Eclipse_lastIndex = null;
                        node._Eclipse_usedIndices = new Set();
                    }
                    for (const p of removed) {
                        fetch('/eclipse/load_image_folder/invalidate_cache', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({
                                folder_path: p
                            }),
                        }).catch(() => {});
                    }
                    if (firstChanged && indexW && indexW.value !== 0) {
                        node._Eclipse_updatingIndex = true;
                        indexW.value = 0;
                        indexW.callback?.(0);
                        node._Eclipse_updatingIndex = false;
                    }
                    if (removed.length > 0) {
                        const refreshW = findW('refresh_list');
                        if (refreshW) refreshW.value = true;
                    }
                    updateImageCountDebounced(node);
                    node.setDirtyCanvas(true, true);
                }
            };
            if (indexW) {
                const origIndexCb = indexW.callback;
                indexW.callback = function (val) {
                    origIndexCb?.apply(this, arguments);
                    if (node._Eclipse_updatingIndex) return;
                    const isSpecial = SPECIAL_MODES.includes(val);
                    const wasShuffle = node._Eclipse_indexWidget && node._Eclipse_lastIndex === -4;
                    if (node._Eclipse_chipWidget) {
                        const chips = new Set(node._Eclipse_chipWidget.value);
                        for (const m of MODE_CHIPS) chips.delete(m);
                        if (isSpecial) {
                            const modeChip = INDEX_TO_MODE_CHIP[val];
                            if (modeChip) chips.add(modeChip);
                        }
                        node._Eclipse_chipWidget.value = [...chips];
                    }
                    if (isSpecial) {
                        const btn = node._Eclipse_lastIndexButton;
                        if (btn && node._Eclipse_lastResolvedIndex !== null) {
                            btn.disabled = false;
                            const ic = nodeImageCounts.get(id) || 0;
                            btn.name = val === -4 && ic > 0 ? `♻️ ${node._Eclipse_lastResolvedIndex} (${node._Eclipse_usedIndices?.size || 0}/${ic})` : `♻️ ${node._Eclipse_lastResolvedIndex}`;
                            if (isVueMode()) notifyVue(node);
                        }
                        if (val === -4 && !wasShuffle) {
                            if (node._Eclipse_pausedShuffle) node._Eclipse_pausedShuffle = false;
                            else node._Eclipse_usedIndices = new Set();
                        }
                    } else {
                        node._Eclipse_lastResolvedIndex = null;
                        node._Eclipse_lastIndex = null;
                        const btn = node._Eclipse_lastIndexButton;
                        if (btn) {
                            btn.disabled = true;
                            btn.name = '♻️ (Use Last Queued Index)';
                            if (isVueMode()) notifyVue(node);
                        }
                    }
                    if (nodeStopTriggered.get(id)) nodeStopTriggered.set(id, false);
                };
            }
            if (indexW) {
                const lastBtn = node.addWidget('button', '♻️ (Use Last Queued Index)', null, () => {
                    if (node._Eclipse_lastResolvedIndex !== null) {
                        if (indexW.value === -4) node._Eclipse_pausedShuffle = true;
                        node._Eclipse_updatingIndex = true;
                        indexW.value = node._Eclipse_lastResolvedIndex;
                        indexW.callback?.(indexW.value);
                        node._Eclipse_updatingIndex = false;
                        if (node._Eclipse_chipWidget) {
                            const chips = new Set(node._Eclipse_chipWidget.value);
                            for (const m of MODE_CHIPS) chips.delete(m);
                            node._Eclipse_chipWidget.value = [...chips];
                        }
                        node.setDirtyCanvas(true, true);
                    }
                });
                lastBtn.serialize = false;
                lastBtn.disabled = true;
                node._Eclipse_lastIndexButton = lastBtn;
            }
            const origOnRemoved = node.onRemoved;
            node.onRemoved = function () {
                nodeFolderPaths.delete(id);
                nodeStopTriggered.delete(id);
                nodeImageCounts.delete(id);
                if (fetchDebounceTimers.has(id)) {
                    clearTimeout(fetchDebounceTimers.get(id));
                    fetchDebounceTimers.delete(id);
                }
                origOnRemoved?.apply(this, arguments);
            };
            if (folderW.value && folderW.value.trim()) {
                setTimeout(() => updateImageCount(node), 100);
            }
            const origOnConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                if (origOnConfigure) origOnConfigure.apply(this, arguments);
                requestAnimationFrame(() => {
                    if (node._Eclipse_chipWidget && indexW) {
                        const idxVal = Number(indexW.value);
                        if (!SPECIAL_MODES.includes(idxVal)) {
                            const chips = new Set(node._Eclipse_chipWidget.value);
                            let changed = false;
                            for (const m of MODE_CHIPS) {
                                if (chips.has(m)) {
                                    chips.delete(m);
                                    changed = true;
                                }
                            }
                            if (changed) {
                                node._Eclipse_chipWidget.value = [...chips];
                            }
                        }
                    }
                    if (folderW?.value?.trim()) updateImageCountDebounced(node);
                });
            };
            return ret;
        };
        nodeType.prototype.getIndexToUse = function (stopAtEnd = true) {
            const indexW = this._Eclipse_indexWidget;
            if (!indexW) return 0;
            const val = indexW.value;
            const lastIdx = this._Eclipse_lastIndex;
            const maxIdx = indexW.options?.max ?? 999999;
            const totalCount = nodeImageCounts.get(this.id) || maxIdx + 1;
            let resolved = val;
            if (val === MODE_RANDOM) {
                if (totalCount > 1) {
                    let attempts = 0;
                    do {
                        resolved = Math.floor(Math.random() * totalCount);
                        attempts++;
                    }
                    while (resolved === lastIdx && attempts < 10);
                } else resolved = 0;
            } else if (val === MODE_INCREMENT) {
                if (lastIdx === null) resolved = 0;
                else {
                    resolved = lastIdx + 1;
                    if (!stopAtEnd && resolved > maxIdx) resolved = 0;
                    else if (resolved > maxIdx) resolved = maxIdx;
                }
            } else if (val === MODE_DECREMENT) {
                if (lastIdx === null) resolved = maxIdx;
                else {
                    resolved = lastIdx - 1;
                    if (!stopAtEnd && resolved < 0) resolved = maxIdx;
                    else if (resolved < 0) resolved = 0;
                }
            } else if (val === MODE_RANDOM_NO_REPEAT) {
                const used = this._Eclipse_usedIndices || new Set();
                const available = [];
                for (let i = 0; i <= maxIdx; i++) {
                    if (!used.has(i)) available.push(i);
                }
                if (available.length > 0) {
                    resolved = available[Math.floor(Math.random() * available.length)];
                    used.add(resolved);
                    this._Eclipse_usedIndices = used;
                } else if (stopAtEnd) {
                    resolved = maxIdx + 1;
                } else {
                    this._Eclipse_usedIndices = new Set();
                    resolved = Math.floor(Math.random() * totalCount);
                    this._Eclipse_usedIndices.add(resolved);
                }
            }
            return resolved;
        };
    },
    async setup() {
        api.addEventListener('stop-iteration', () => {
            const cb = document.getElementById('autoQueueCheckbox');
            if (cb?.checked) {
                cb.checked = false;
                cb.dispatchEvent(new Event('change', {
                    bubbles: true
                }));
            }
            if (app.ui?.autoQueueEnabled !== undefined) app.ui.autoQueueEnabled = false;
            try {
                const autoCb = document.querySelector('input[type="checkbox"][id*="auto"], input[type="checkbox"][class*="auto"]');
                if (autoCb?.checked) {
                    autoCb.checked = false;
                    autoCb.dispatchEvent(new Event('change', {
                        bubbles: true
                    }));
                }
            } catch (_) {}
            for (const node of app.graph?._nodes || []) {
                if (!ALL_NODES.has(node.type)) continue;
                nodeStopTriggered.set(node.id, true);
                const indexW = node.widgets?.find(w => w.name === 'index');
                if (indexW) {
                    node._Eclipse_updatingIndex = true;
                    indexW.value = 0;
                    indexW.callback?.(0);
                    node._Eclipse_updatingIndex = false;
                    node.setDirtyCanvas(true, true);
                }
                node._Eclipse_lastIndex = null;
            }
        });
        api.addEventListener('execution_start', () => {
            for (const node of app.graph?._nodes || []) {
                if (!ALL_NODES.has(node.type)) continue;
                const refreshW = node.widgets?.find(w => w.name === 'refresh_list');
                if (refreshW?.value === true) {
                    node._Eclipse_refreshPending = true;
                    setTimeout(() => {
                        refreshW.value = false;
                        const chipWidgetName = node._Eclipse_cfg?.chipWidgetName;
                        if (chipWidgetName) {
                            const chipW = node.widgets?.find(w => w.name === chipWidgetName);
                            if (chipW) {
                                const sel = new Set(chipW.value);
                                sel.delete('refresh_list');
                                chipW.value = [...sel];
                            }
                        }
                        if (isVueMode()) notifyVue(node);
                        node.setDirtyCanvas(true, true);
                    }, 500);
                }
            }
        });
        api.addEventListener('executed', (e) => {
            const detail = e.detail;
            if (!detail) return;
            const nodeId = detail.node || detail.display_node;
            if (!nodeId) return;
            const node = app.graph?.getNodeById(Number(nodeId));
            if (!node || !ALL_NODES.has(node.type)) return;
            if (node._Eclipse_refreshPending) {
                node._Eclipse_refreshPending = false;
                node._Eclipse_usedIndices = new Set();
                updateImageCount(node);
            } else {
                updateImageCountDebounced(node, 500);
            }
        });
        const origConfigure = app.graph?.configure?.bind(app.graph);
        if (app.graph && origConfigure) {
            app.graph.configure = function (data) {
                const result = origConfigure(data);
                setTimeout(() => {
                    for (const node of app.graph?._nodes || []) {
                        if (!ALL_NODES.has(node.type)) continue;
                        const folderW = node.widgets?.find(w => w.name === 'folder_path');
                        if (folderW?.value?.trim()) updateImageCount(node);
                    }
                }, 200);
                return result;
            };
        }
        const origGraphToPrompt = app.graphToPrompt;
        app.graphToPrompt = async function () {
            const result = await origGraphToPrompt.apply(this, arguments);
            if (!result?.output) return result;
            for (const node of app.graph._nodes) {
                if (!ALL_NODES.has(node.type) || !node._Eclipse_indexWidget) continue;
                if (node.mode === 2 || node.mode === 4) continue;
                const nodeId = String(node.id);
                if (!result.output[nodeId]) continue;
                if (result.output[nodeId].inputs) {
                    for (const w of node.widgets || []) {
                        if (w.type === 'button' && w.name in result.output[nodeId].inputs) {
                            delete result.output[nodeId].inputs[w.name];
                        }
                    }
                    const chipWidgetName = node._Eclipse_cfg?.chipWidgetName;
                    if (chipWidgetName) delete result.output[nodeId].inputs[chipWidgetName];
                }
                const stopAtEnd = result.output[nodeId].inputs?.stop_at_end !== false;
                const seedInputIdx = node.inputs?.findIndex(e => 'seed_input' === e.name);
                const hasSeedLink = seedInputIdx >= 0 && node.inputs[seedInputIdx]?.link != null;
                const indexVal = Number(node._Eclipse_indexWidget?.value);
                const isSpecial = SPECIAL_MODES.includes(indexVal);
                if (hasSeedLink && isSpecial) {
                    const currentSeed = _getResolvedSeedFromGraph(node);
                    if (currentSeed != null && node._Eclipse_lastResolvedIndex != null && node._Eclipse_lastSeedInput !== undefined && String(currentSeed) === String(node._Eclipse_lastSeedInput)) {
                        if (result.output[nodeId].inputs?.index !== undefined) {
                            result.output[nodeId].inputs.index = node._Eclipse_lastResolvedIndex;
                        }
                        const btn = node._Eclipse_lastIndexButton;
                        if (btn) {
                            const ic = nodeImageCounts.get(node.id) || 0;
                            btn.disabled = false;
                            btn.name = indexVal === -4 ? `♻️ ${node._Eclipse_lastResolvedIndex} (${node._Eclipse_usedIndices?.size || 0}/${ic})` : `♻️ ${node._Eclipse_lastResolvedIndex}`;
                            if (isVueMode()) notifyVue(node);
                        }
                        if (result.workflow?.nodes) {
                            const wn = result.workflow.nodes.find(x => x.id === node.id);
                            if (wn?.widgets_values) {
                                const wi = node.widgets.indexOf(node._Eclipse_indexWidget);
                                if (wi >= 0) wn.widgets_values[wi] = node._Eclipse_lastResolvedIndex;
                            }
                        }
                        node._Eclipse_lastIndex = node._Eclipse_lastResolvedIndex;
                        if (result.output[nodeId]?.inputs?.seed_input !== undefined) delete result.output[nodeId].inputs.seed_input;
                        continue;
                    }
                    node._Eclipse_lastSeedInput = currentSeed != null ? String(currentSeed) : undefined;
                }
                if (result.output[nodeId]?.inputs?.seed_input !== undefined) delete result.output[nodeId].inputs.seed_input;
                const resolved = node.getIndexToUse(stopAtEnd);
                const indexW = node._Eclipse_indexWidget;
                const rawVal = indexW.value;
                const rawIsSpecial = SPECIAL_MODES.includes(rawVal);
                if (result.output[nodeId].inputs?.index !== undefined) result.output[nodeId].inputs.index = resolved;
                if (rawIsSpecial) {
                    node._Eclipse_lastResolvedIndex = resolved;
                    const btn = node._Eclipse_lastIndexButton;
                    if (btn) {
                        btn.disabled = false;
                        if (rawVal === -4) {
                            const ic = nodeImageCounts.get(node.id) || 0;
                            btn.name = `♻️ ${resolved} (${node._Eclipse_usedIndices?.size || 0}/${ic})`;
                        } else {
                            btn.name = `♻️ ${resolved}`;
                        }
                        if (isVueMode()) notifyVue(node);
                    }
                } else {
                    if (rawVal !== resolved) {
                        node._Eclipse_updatingIndex = true;
                        indexW.value = resolved;
                        indexW.callback?.(resolved);
                        node._Eclipse_updatingIndex = false;
                        node.setDirtyCanvas(true, true);
                    }
                    const btn = node._Eclipse_lastIndexButton;
                    if (btn) {
                        btn.disabled = true;
                        btn.name = '♻️ (Use Last Queued Index)';
                        if (isVueMode()) notifyVue(node);
                    }
                }
                node._Eclipse_lastIndex = resolved;
                if (result.workflow?.nodes) {
                    const wn = result.workflow.nodes.find(x => x.id === node.id);
                    if (wn?.widgets_values) {
                        const wi = node.widgets.indexOf(indexW);
                        if (wi >= 0) wn.widgets_values[wi] = resolved;
                    }
                }
            }
            return result;
        };
    },
    async refreshComboInNodes() {
        for (const node of app.graph?._nodes || []) {
            if (!ALL_NODES.has(node.type)) continue;
            const folderW = node.widgets?.find(w => w.name === 'folder_path');
            if (folderW?.value?.trim()) {
                await fetch('/eclipse/load_image_folder/invalidate_cache', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        folder_path: folderW.value
                    }),
                }).catch(() => {});
                updateImageCount(node);
            }
        }
    },
});
