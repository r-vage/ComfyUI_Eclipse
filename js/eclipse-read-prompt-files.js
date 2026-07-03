import {
    app,
    api
} from './comfy/index.js';
import {
    notifyVue,
    isVueMode
} from './eclipse-widget-performance-utils.js';
import { getResolvedSeedFromGraph as _getResolvedSeedFromGraph } from './eclipse-seed-utils.js';
const NODE_NAME = 'Read Prompt Files [Eclipse]',
    SPECIAL_SEED_RANDOM = -1,
    SPECIAL_SEED_INCREMENT = -2,
    SPECIAL_SEED_DECREMENT = -3,
    SPECIAL_SEED_SHUFFLE = -4,
    SPECIAL_SEEDS = [-1, -2, -3, -4],
    nodePromptCounts = new Map(),
    nodeFilePaths = new Map();

app.registerExtension({
    name: 'Eclipse.ReadPromptFiles',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            this._Eclipse_lastIndex = undefined;
            this._Eclipse_lastResolvedIndex = undefined;
            this._Eclipse_manualIndex = undefined;
            this._Eclipse_baseIndexForNavigation = undefined;
            this._Eclipse_cachedInputIndex = null;
            this._Eclipse_cachedResolvedIndex = null;
            this._Eclipse_lastSeedInput = undefined;
            this._Eclipse_usedIndices = new Set();
            let indexWidget = null;
            for (const [idx, widget] of this.widgets.entries()) {
                const name = (widget.name || '').toString().toLowerCase();
                const label = (widget.label || widget.options?.label || widget.options?.name || '').toString().toLowerCase();
                if (name === 'index' || label === 'index') {
                    indexWidget = widget;
                } else if (name === 'control_after_generate') {
                    this.widgets.splice(idx, 1);
                }
            }
            if (!indexWidget) {
                console.warn('[Eclipse-ReadPromptFiles] Could not find Index widget');
                return ret;
            }
            this._Eclipse_indexWidget = indexWidget;
            const nodeId = this.id;
            const filePathsWidget = this.widgets?.find((w) => (w.name || '').toLowerCase().includes('file_paths') || (w.name || '').toLowerCase().includes('filepaths'), );
            if (filePathsWidget) {
                nodeFilePaths.set(nodeId, filePathsWidget.value);
                const origFilePathsCb = filePathsWidget.callback;
                filePathsWidget.callback = function (newValue) {
                    const prevPaths = nodeFilePaths.get(nodeId);
                    if (origFilePathsCb) origFilePathsCb.apply(this, arguments);
                    if (!nodeId || nodeId < 0) {
                        nodeFilePaths.set(nodeId, newValue);
                        return;
                    }
                    if (newValue === prevPaths || (prevPaths === '' && newValue === '')) return;
                    nodeFilePaths.set(nodeId, newValue);
                    node._Eclipse_lastResolvedIndex = undefined;
                    node._Eclipse_cachedInputIndex = null;
                    node._Eclipse_cachedResolvedIndex = null;
                    if (prevPaths && prevPaths.trim()) {
                        fetch('/eclipse/read_prompt_files/invalidate_cache', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({
                                file_paths: prevPaths
                            }),
                        }).catch(() => {});
                    }
                    if (newValue && newValue.trim()) {
                        node.getMaxIndex().then((maxIdx) => {
                            if (indexWidget.options) {
                                const currentVal = indexWidget.value || 0;
                                indexWidget.options.max = Math.max(0, maxIdx);
                                if (currentVal > indexWidget.options.max) {
                                    indexWidget.value = 0;
                                    if (indexWidget.callback) indexWidget.callback(0);
                                }
                            }
                        }).catch((err) => {
                            console.warn('[ReadPromptFiles] Error updating max index:', err);
                            indexWidget.value = 0;
                            if (indexWidget.callback) indexWidget.callback(0);
                        });
                    }
                    if (node._Eclipse_lastIndexButton) {
                        node._Eclipse_lastIndexButton.disabled = true;
                        if (isVueMode()) notifyVue(node);
                    }
                };
            }
            const origIndexCb = indexWidget.callback;
            indexWidget.callback = (val) => {
                this._Eclipse_cachedInputIndex = null;
                this._Eclipse_cachedResolvedIndex = null;
                if (SPECIAL_SEEDS.includes(Number(val))) {
                    this._Eclipse_manualIndex = undefined;
                    if (Number(val) === -4) this._Eclipse_usedIndices = new Set();
                    if (this._Eclipse_lastIndexButton && this._Eclipse_lastResolvedIndex !== undefined) {
                        this._Eclipse_lastIndexButton.name = `♻️ ${this._Eclipse_lastResolvedIndex}`;
                        this._Eclipse_lastIndexButton.disabled = false;
                        if (isVueMode()) notifyVue(this);
                    }
                } else {
                    this._Eclipse_manualIndex = val;
                    this._Eclipse_baseIndexForNavigation = val;
                    if (this._Eclipse_lastIndexButton) {
                        this._Eclipse_lastIndexButton.name = '♻️ (Use Last Queued Index)';
                        this._Eclipse_lastIndexButton.disabled = true;
                        if (isVueMode()) notifyVue(this);
                    }
                }
                if (origIndexCb) return origIndexCb.call(indexWidget, val);
            };
            const randomBtn = this.addWidget('button', '🎲 Randomize Each Time', '', () => {
                indexWidget.value = -1;
                this._Eclipse_manualIndex = undefined;
                if (indexWidget.callback) indexWidget.callback(-1);
            }, {
                serialize: false
            }, );
            const lastIndexBtn = this.addWidget('button', '♻️ (Use Last Queued Index)', '', () => {
                if (this._Eclipse_lastResolvedIndex != null) {
                    indexWidget.value = this._Eclipse_lastResolvedIndex;
                    this._Eclipse_manualIndex = this._Eclipse_lastResolvedIndex;
                    lastIndexBtn.name = '♻️ (Use Last Queued Index)';
                    lastIndexBtn.disabled = true;
                    if (isVueMode()) notifyVue(this);
                    if (indexWidget.callback) indexWidget.callback(this._Eclipse_lastResolvedIndex);
                }
            }, {
                serialize: false
            }, );
            lastIndexBtn.disabled = true;
            this._Eclipse_lastIndexButton = lastIndexBtn;
            this.generateRandomIndex = async function () {
                const maxIdx = await this.getMaxIndex();
                if (maxIdx >= 0) {
                    let idx = Math.floor(Math.random() * (maxIdx + 1));
                    if (SPECIAL_SEEDS.includes(idx)) idx = 0;
                    return idx;
                }
                return 0;
            };
            this.getIndexToUse = function () {
                const inputVal = Number(this._Eclipse_indexWidget.value);
                if (this._Eclipse_cachedInputIndex === inputVal && this._Eclipse_cachedResolvedIndex != null) {
                    return this._Eclipse_cachedResolvedIndex;
                }
                let resolved = null;
                if (SPECIAL_SEEDS.includes(inputVal)) resolved = 0;
                const result = resolved != null ? resolved : inputVal;
                this._Eclipse_cachedInputIndex = inputVal;
                this._Eclipse_cachedResolvedIndex = result;
                return result;
            };
            this.createSeededRNG = function (seed) {
                let state = seed;
                return function () {
                    state = (9301 * state + 49297) % 233280;
                    return state / 233280;
                };
            };
            this.getMaxIndex = async function () {
                try {
                    const filePaths = this._Eclipse_getFilePathsValue();
                    if (!filePaths || !filePaths.trim()) return 0;
                    const resp = await api.fetchApi('/eclipse/read_prompt_files_count', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            file_paths: filePaths
                        }),
                    });
                    if (resp.ok) {
                        const count = (await resp.json()).count || 0;
                        const maxIdx = Math.max(0, count - 1);
                        nodePromptCounts.set(this.id, count);
                        return maxIdx;
                    }
                    console.warn(`[ReadPromptFiles] Server error getting count: ${resp.status} ${resp.statusText}`);
                } catch (err) {
                    console.warn('[ReadPromptFiles] Error getting max index:', err);
                }
                return 0;
            };
            this._Eclipse_getFilePathsValue = function () {
                const fpWidget = this.widgets?.find((w) => (w.name || '').toLowerCase().includes('file_paths') || (w.name || '').toLowerCase().includes('filepaths'), );
                return fpWidget?.value || '';
            };
            this._Eclipse_navigationButtons = [randomBtn, lastIndexBtn];
            return ret;
        };
    },
    async setup() {
        const prevGraphToPrompt = app.graphToPrompt;
        app.graphToPrompt = async function () {
            const t = await prevGraphToPrompt.apply(this, arguments),
                allNodes = app.graph._nodes;
            for (const node of allNodes) {
                if (node.type !== NODE_NAME || !node._Eclipse_indexWidget) continue;
                if (2 === node.mode || 4 === node.mode) continue;
                const nodeId = String(node.id);
                if (!t.output || !t.output[nodeId]) continue;
                if (void 0 !== node._Eclipse_manualIndex) {
                    ((t.output[nodeId].inputs.index = node._Eclipse_manualIndex), (node._Eclipse_lastResolvedIndex = node._Eclipse_manualIndex));
                    continue;
                }
                const seedInputIdx = node.inputs?.findIndex((e) => 'seed_input' === e.name),
                    hasSeedLink = seedInputIdx >= 0 && null != node.inputs[seedInputIdx]?.link,
                    indexIsSpecial = SPECIAL_SEEDS.includes(Number(node._Eclipse_indexWidget?.value));
                if (hasSeedLink && indexIsSpecial) {
                    const currentSeed = _getResolvedSeedFromGraph(node);
                    if (void 0 !== currentSeed && null !== currentSeed && void 0 !== node._Eclipse_lastResolvedIndex && void 0 !== node._Eclipse_lastSeedInput && String(currentSeed) === String(node._Eclipse_lastSeedInput)) {
                        t.output[nodeId].inputs.index = node._Eclipse_lastResolvedIndex;
                        node._Eclipse_lastIndexButton && ((node._Eclipse_lastIndexButton.name = `♻️ ${node._Eclipse_lastResolvedIndex}`), (node._Eclipse_lastIndexButton.disabled = !1), isVueMode() && notifyVue(node));
                        if (t.output[nodeId]?.inputs?.seed_input !== void 0) delete t.output[nodeId].inputs.seed_input;
                        continue;
                    }
                    node._Eclipse_lastSeedInput = void 0 !== currentSeed && null !== currentSeed ? String(currentSeed) : void 0;
                }
                if (t.output[nodeId]?.inputs?.seed_input !== void 0) delete t.output[nodeId].inputs.seed_input;
                let resolvedIndex = null,
                    indexChanged = !1;
                if (node._Eclipse_indexWidget) {
                    const mode = Number(node._Eclipse_indexWidget.value);
                    if (SPECIAL_SEEDS.includes(mode))
                        switch (mode) {
                            case -1: {
                                const maxIdx = await node.getMaxIndex();
                                resolvedIndex = maxIdx >= 0 ? Math.floor(Math.random() * (maxIdx + 1)) : 0;
                                break;
                            }
                            case -2: {
                                const maxIdx = await node.getMaxIndex();
                                if (maxIdx >= 0) {
                                    const stopAtEnd = !1 !== t.output[nodeId].inputs.stop_at_end;
                                    if (void 0 === node._Eclipse_baseIndexForNavigation || SPECIAL_SEEDS.includes(node._Eclipse_baseIndexForNavigation))
                                        if (void 0 === node._Eclipse_lastResolvedIndex || SPECIAL_SEEDS.includes(node._Eclipse_lastResolvedIndex))
                                            resolvedIndex = 0;
                                        else {
                                            const prev = node._Eclipse_lastResolvedIndex;
                                            resolvedIndex = !stopAtEnd && prev + 1 > maxIdx ? 0 : (prev + 1) % (maxIdx + 1);
                                        }
                                    else {
                                        const base = node._Eclipse_baseIndexForNavigation;
                                        ((node._Eclipse_baseIndexForNavigation = void 0), (resolvedIndex = !stopAtEnd && base + 1 > maxIdx ? 0 : (base + 1) % (maxIdx + 1)));
                                    }
                                } else resolvedIndex = 0;
                                break;
                            }
                            case -4: {
                                const maxIdx = await node.getMaxIndex();
                                if (maxIdx >= 0) {
                                    const total = maxIdx + 1,
                                        used = node._Eclipse_usedIndices || new Set(),
                                        available = [];
                                    for (let j = 0; j <= maxIdx; j++) used.has(j) || available.push(j);
                                    if (available.length > 0) {
                                        ((resolvedIndex = available[Math.floor(Math.random() * available.length)]), used.add(resolvedIndex), (node._Eclipse_usedIndices = used));
                                    } else {
                                        !1 !== t.output[nodeId].inputs.stop_at_end ? (resolvedIndex = maxIdx + 1) : ((node._Eclipse_usedIndices = new Set()), (resolvedIndex = Math.floor(Math.random() * total)), node._Eclipse_usedIndices.add(resolvedIndex));
                                    }
                                } else resolvedIndex = 0;
                                break;
                            }
                            case -3: {
                                const maxIdx = await node.getMaxIndex();
                                if (maxIdx >= 0) {
                                    const stopAtEnd = !1 !== t.output[nodeId].inputs.stop_at_end;
                                    if (void 0 === node._Eclipse_baseIndexForNavigation || SPECIAL_SEEDS.includes(node._Eclipse_baseIndexForNavigation))
                                        if (void 0 === node._Eclipse_lastResolvedIndex || SPECIAL_SEEDS.includes(node._Eclipse_lastResolvedIndex))
                                            resolvedIndex = maxIdx;
                                        else {
                                            const prev = node._Eclipse_lastResolvedIndex;
                                            resolvedIndex = !stopAtEnd && prev - 1 < 0 ? maxIdx : prev > 0 ? prev - 1 : maxIdx;
                                        }
                                    else {
                                        const base = node._Eclipse_baseIndexForNavigation;
                                        ((node._Eclipse_baseIndexForNavigation = void 0), (resolvedIndex = !stopAtEnd && base - 1 < 0 ? maxIdx : base > 0 ? base - 1 : maxIdx));
                                    }
                                } else resolvedIndex = 0;
                                break;
                            }
                        }
                    else resolvedIndex = node.getIndexToUse();
                    null !== resolvedIndex && ((t.output[nodeId].inputs.index = resolvedIndex), (indexChanged = void 0 === node._Eclipse_lastResolvedIndex || String(node._Eclipse_lastResolvedIndex) !== String(resolvedIndex)));
                }
                if ((indexChanged && null !== resolvedIndex && (node._Eclipse_lastResolvedIndex = resolvedIndex), node._Eclipse_lastIndexButton)) {
                    const mode = node._Eclipse_indexWidget?.value;
                    if (SPECIAL_SEEDS.includes(Number(mode)))
                        if (void 0 !== node._Eclipse_lastResolvedIndex) {
                            if (-4 === Number(mode)) {
                                const count = nodePromptCounts.get(node.id) || 0,
                                    usedCount = node._Eclipse_usedIndices?.size || 0;
                                node._Eclipse_lastIndexButton.name = `♻️ ${node._Eclipse_lastResolvedIndex} (${usedCount}/${count})`;
                            } else node._Eclipse_lastIndexButton.name = `♻️ ${node._Eclipse_lastResolvedIndex}`;
                            node._Eclipse_lastIndexButton.disabled = !1;
                        } else
                            ((node._Eclipse_lastIndexButton.name = '♻️ (Use Last Queued Index)'), (node._Eclipse_lastIndexButton.disabled = !0));
                    else
                        ((node._Eclipse_lastIndexButton.name = '♻️ (Use Last Queued Index)'), (node._Eclipse_lastIndexButton.disabled = !0));
                    if (isVueMode()) notifyVue(node);
                }
            }
            return t;
        };
    },
    async refreshComboInNodes() {
        for (const node of app.graph?._nodes || []) {
            if (node.type !== NODE_NAME) continue;
            const filePaths = node._Eclipse_getFilePathsValue?.();
            if (!filePaths?.trim()) continue;
            await fetch('/eclipse/read_prompt_files/invalidate_cache', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    file_paths: filePaths
                }),
            }).catch(() => {});
            if (node.getMaxIndex) {
                const maxIdx = await node.getMaxIndex();
                const indexW = node._Eclipse_indexWidget;
                if (indexW?.options) indexW.options.max = Math.max(0, maxIdx);
                node.setDirtyCanvas?.(true, true);
            }
        }
    },
});
