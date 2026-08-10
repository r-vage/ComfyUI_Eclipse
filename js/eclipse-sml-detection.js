import {
    app,
    api
} from './comfy/index.js';
import {
    debounce,
    notifyVue,
    createWidgetVisibilityManager,
    smartResize,
    isVueMode,
} from './eclipse-widget-performance-utils.js';
import {
    injectComboChipCSS,
    createComboChipWidget
} from './eclipse-combo-chip.js';
import { onSmlRegistryChanged } from './eclipse-sml-registry-events.js';
const NODE_NAME = "Smart Detection [Eclipse]";
injectComboChipCSS('sdet');
const MODE_OPTIONS = [{
    label: 'Cleanup',
    tooltip: 'VRAM garbage collection — clear VRAM cache and run Python garbage collection before loading the model'
}, {
    label: 'Keep Loaded',
    tooltip: 'Keep the detection model cached in VRAM between runs to skip loading/unloading latency (highly recommended for performance)'
}, {
    label: 'Preview Boxes',
    tooltip: 'Superimpose colored bounding box outlines and text labels on the output preview image'
}, {
    label: 'Adjust',
    tooltip: 'Toggle visibility of post-processing filters/adjustments (box size filters, crop expansion, mask dilation)'
}, {
    label: 'Advanced',
    tooltip: 'Toggle visibility of advanced hardware settings and backend sampling options'
}, {
    label: 'Delete',
    tooltip: 'Show button to permanently delete the selected model files from local storage'
}, ];
const MODE_DEFAULTS = ['Cleanup', 'Preview Boxes'];
const MODE_TO_BACKING = {
    'Cleanup': 'cleanup',
    'Keep Loaded': 'keep_model_loaded',
    'Preview Boxes': 'enable_preview_boxes',
    'Adjust': 'show_adjust',
    'Advanced': 'show_advanced',
};
const SPECIAL_SEED_RANDOM = -1;
const SPECIAL_SEED_INCREMENT = -2;
const SPECIAL_SEED_DECREMENT = -3;
const SPECIAL_SEEDS = [SPECIAL_SEED_RANDOM, SPECIAL_SEED_INCREMENT, SPECIAL_SEED_DECREMENT];
const LAST_SEED_BUTTON_LABEL = "🌘 (Use Last Queued Seed)";
const MODEL_SEPARATOR_LABELS = {
    '__SEP__DETECTION_VLM__': 'VLM models',
    '__SEP__YOLO__': 'YOLO models',
};

function buildSeparator(label, targetLen) {
    const inner = ' ' + label + ' ';
    const pad = Math.max(0, targetLen - inner.length);
    const left = Math.floor(pad / 2);
    const right = pad - left;
    return '─'.repeat(left) + inner + '─'.repeat(right);
}

function isSeparatorEntry(name) {
    return typeof name === 'string' && name.length > 0 && name.charAt(0) === '─';
}

function mapModelSeparators(rawList) {
    const maxLen = rawList.reduce((m, n) => MODEL_SEPARATOR_LABELS[n] ? m : Math.max(m, n.length), 0);
    return rawList.map(n => {
        const label = MODEL_SEPARATOR_LABELS[n];
        return label ? buildSeparator(label, maxLen) : n;
    });
}
const DET_FLORENCE_TASKS = ['Caption to Phrase Grounding', 'Region Caption', 'Dense Region Caption', 'Region Proposal', 'Referring Expression Segmentation', 'OCR With Region', 'DocVQA', ];
const DET_QWEN_TASKS = ['Caption to Phrase Grounding', 'Region Caption'];

function getTasksForFamily(family) {
    if (family === 'Florence') return DET_FLORENCE_TASKS;
    if (family === 'Qwen') return DET_QWEN_TASKS;
    return [];
}
const TASKS_REQUIRING_USER_INPUT = new Set(['Caption to Phrase Grounding', 'Referring Expression Segmentation', 'DocVQA', ]);
const DET_FAMILY_WIDGET_SUPPORT = {
    _default: {
        device: true,
        num_beams: true,
        do_sample: true,
        use_torch_compile: true,
        convert_to_bboxes: true,
        temperature: true,
        top_p: true,
        top_k: true,
        repetition_penalty: true
    },
    Florence: {
        temperature: false,
        top_p: false,
        top_k: false,
        repetition_penalty: false
    },
    Qwen: {
        convert_to_bboxes: false
    },
    YOLO: {
        num_beams: false,
        do_sample: false,
        use_torch_compile: false,
        convert_to_bboxes: false,
        temperature: false,
        top_p: false,
        top_k: false,
        repetition_penalty: false
    },
};

function getDetFamilySupport(family) {
    return {
        ...DET_FAMILY_WIDGET_SUPPORT._default,
        ...(DET_FAMILY_WIDGET_SUPPORT[family] || {})
    };
}

function getBackendFromName(displayName) {
    if (!displayName) return 'transformers';
    if (displayName.endsWith('-GGUF')) return 'gguf';
    if (displayName.endsWith('-vLLM')) return 'vllm';
    if (displayName.endsWith('-SGLang')) return 'sglang';
    if (displayName.endsWith('-Ollama')) return 'ollama';
    if (displayName.endsWith('-llama.cpp')) return 'llamacpp';
    return 'transformers';
}
async function fetchDetectionModelList(force = false) {
    try {
        const resp = await fetch('/smartlml/detection/model_list');
        if (resp.ok) return resp.json();
    } catch (e) {
        console.warn('[SML-Det] Error fetching detection model list:', e);
    }
    return [];
}
async function fetchModelEntry(displayName) {
    if (!displayName) return null;
    try {
        const resp = await fetch(`/smartlml/model_entry?name=${encodeURIComponent(displayName)}`);
        if (resp.ok) return resp.json();
    } catch (e) {
        console.warn('[SML-Det] Error fetching model entry:', e);
    }
    return null;
}

function updateDropdown(widget, values, defaultValue = null) {
    if (!widget) return;
    widget.options.values = values;
    const def = defaultValue !== null ? defaultValue : (values[0] || '');
    if (!values.includes(widget.value)) {
        widget.value = def;
    }
}
const smlDetectionExtension = {
    name: "Eclipse.SMLDetection",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name !== NODE_NAME) return;
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function() {
            const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            const vis = createWidgetVisibilityManager(node);
            // Pre-hide conditional widgets so Vue's first render paints the final
            // layout. First registry entry is always transformers, so at defaults:
            // model, task, user_input, confidence, nms_iou_threshold,
            // detection_filter, select_index, seed are visible; quantization
            // (gguf-only) is hidden.
            vis.hideInitially([
                'quantization',
                'drop_size', 'crop_factor', 'dilation',
                'device', 'num_beams', 'do_sample', 'use_torch_compile',
                'convert_to_bboxes', 'temperature', 'top_p', 'top_k',
                'repetition_penalty',
                'cleanup', 'keep_model_loaded', 'enable_preview_boxes',
                'show_adjust', 'show_advanced',
            ]);
            const getWidget = (name) => node.widgets?.find(w => w.name === name);
            const modelWidget = getWidget('model_name');
            const quantWidget = getWidget('quantization');
            const taskWidget = getWidget('task');
            const modelWidgetIdx = modelWidget ? node.widgets.indexOf(modelWidget) : 0;
            const modeBarIdx = modelWidgetIdx;

            function readModeFromBacking() {
                const chips = [];
                for (const [chip, backing] of Object.entries(MODE_TO_BACKING)) {
                    const w = getWidget(backing);
                    if (w?.value === true) chips.push(chip);
                }
                return chips;
            }
            let modeInitial = MODE_DEFAULTS.slice();
            const backingChips = readModeFromBacking();
            if (backingChips.length > 0) {
                modeInitial = backingChips;
            }
            const modeBarWidget = createComboChipWidget({
                node,
                options: MODE_OPTIONS,
                savedValue: modeInitial,
                origIdx: modeBarIdx,
                widgetName: '_sdet_mode_bar',
                cssPrefix: 'sdet',
                radioGroups: null,
                radioToggle: false,
                serialize: false,
            });
            node._SDET_modeBarWidget = modeBarWidget;

            function syncModeToBacking(selectedSet) {
                for (const [chip, backing] of Object.entries(MODE_TO_BACKING)) {
                    const w = getWidget(backing);
                    if (w) w.value = selectedSet.has(chip);
                }
            }
            modeBarWidget.callback = function() {
                const selectedSet = new Set(modeBarWidget.value);
                syncModeToBacking(selectedSet);
                vis.markUserDriven();
                updateAllVisibility();
            };
            for (const backing of Object.values(MODE_TO_BACKING)) {
                vis.setVisible(backing, false);
            }
            let currentFamily = '';
            async function onModelChanged(modelName) {
                const backend = getBackendFromName(modelName);
                const isGGUF = backend === 'gguf' || backend === 'llamacpp';
                let entry = null;
                if (modelName && !isSeparatorEntry(modelName)) {
                    entry = await fetchModelEntry(modelName);
                }
                currentFamily = entry?.family || '';
                if (isGGUF && entry?.quantizations?.length) {
                    updateDropdown(quantWidget, entry.quantizations, entry.quantizations[0]);
                }
                if (taskWidget) {
                    const tasks = getTasksForFamily(currentFamily);
                    if (tasks.length > 0) {
                        updateDropdown(taskWidget, tasks, tasks[0]);
                    }
                }
                updateAllVisibility();
            }
            if (modelWidget) {
                if (modelWidget.options?.values) {
                    modelWidget.options.values = mapModelSeparators(modelWidget.options.values);
                }
                if (MODEL_SEPARATOR_LABELS[modelWidget.value]) {
                    const mapped = modelWidget.options?.values || [];
                    const first = mapped.find(v => !isSeparatorEntry(v));
                    if (first) modelWidget.value = first;
                }
                const origModelCb = modelWidget.callback;
                modelWidget.callback = function(value) {
                    if (isSeparatorEntry(value)) {
                        const opts = modelWidget.options?.values || [];
                        const idx = opts.indexOf(value);
                        let newVal = null;
                        for (let i = idx + 1; i < opts.length; i++) {
                            if (!isSeparatorEntry(opts[i])) {
                                newVal = opts[i];
                                break;
                            }
                        }
                        if (!newVal) {
                            for (let i = idx - 1; i >= 0; i--) {
                                if (!isSeparatorEntry(opts[i])) {
                                    newVal = opts[i];
                                    break;
                                }
                            }
                        }
                        if (!newVal) newVal = opts.find(v => !isSeparatorEntry(v)) || '';
                        modelWidget.value = newVal;
                        if (origModelCb) origModelCb.call(this, newVal);
                        vis.markUserDriven();
                        onModelChanged(newVal);
                        return;
                    }
                    if (origModelCb) origModelCb.apply(this, arguments);
                    vis.markUserDriven();
                    onModelChanged(value);
                };
            }
            const deleteBtn = node.addWidget('button', '🗑️ Delete Model', '', async () => {
                const modelName = modelWidget?.value || '';
                if (!modelName || isSeparatorEntry(modelName)) return;
                if (!confirm(`Delete "${modelName}" from disk?\n\nThis cannot be undone.`)) return;
                try {
                    const resp = await api.fetchApi('/smartlml/model/delete', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            display_name: modelName
                        }),
                    });
                    const result = await resp.json();
                    if (!result.success) {
                        alert(`Delete failed: ${result.error || 'Unknown error'}`);
                    }
                } catch (e) {
                    alert(`Delete request failed: ${e.message || e}`);
                }
            }, {
                serialize: false
            });
            deleteBtn.hidden = true;
            if (deleteBtn.options) deleteBtn.options.hidden = true;
            {
                const quantIdx = getWidget('quantization') ? node.widgets.indexOf(getWidget('quantization')) : -1;
                const modelIdx = modelWidget ? node.widgets.indexOf(modelWidget) : -1;
                const afterIdx = Math.max(quantIdx, modelIdx);
                if (afterIdx >= 0) {
                    const btnIdx = node.widgets.indexOf(deleteBtn);
                    if (btnIdx !== afterIdx + 1) {
                        node.widgets.splice(btnIdx, 1);
                        node.widgets.splice(afterIdx + 1, 0, deleteBtn);
                    }
                }
            }
            if (taskWidget) {
                const origTaskCb = taskWidget.callback;
                taskWidget.callback = function(value) {
                    if (origTaskCb) origTaskCb.apply(this, arguments);
                    vis.markUserDriven();
                    updateAllVisibility();
                };
            }

            function updateAllVisibility() {
                if (node.id === -1) return;
                if (!node.widgets) return;
                const modelName = modelWidget?.value || '';
                const backend = getBackendFromName(modelName);
                const isGGUF = backend === 'gguf' || backend === 'llamacpp';
                const isYOLO = currentFamily === 'YOLO';
                const modeSet = new Set(modeBarWidget.value);
                const showAdvanced = modeSet.has('Advanced');
                vis.setVisible('quantization', isGGUF);
                vis.setVisible('task', !isYOLO);
                const currentTask = taskWidget?.value || '';
                vis.setVisible('user_input', isYOLO || TASKS_REQUIRING_USER_INPUT.has(currentTask));
                vis.setVisible('confidence', true);
                vis.setVisible('nms_iou_threshold', true);
                vis.setVisible('detection_filter', true);
                vis.setVisible('select_index', true);
                const showAdjust = modeSet.has('Adjust');
                vis.setVisible('drop_size', showAdjust);
                vis.setVisible('crop_factor', showAdjust);
                vis.setVisible('dilation', showAdjust);
                vis.setVisible('seed', true);
                const support = getDetFamilySupport(currentFamily || '_default');
                const advWidgets = ['device', 'num_beams', 'do_sample', 'use_torch_compile', 'convert_to_bboxes', 'temperature', 'top_p', 'top_k', 'repetition_penalty', ];
                for (const name of advWidgets) {
                    vis.setVisible(name, showAdvanced && support[name] !== false);
                }
                for (const backing of Object.values(MODE_TO_BACKING)) {
                    vis.setVisible(backing, false);
                }
                const showDelete = modeSet.has('Delete') && modelName && !isSeparatorEntry(modelName);
                if (deleteBtn) {
                    deleteBtn.hidden = !showDelete;
                    if (deleteBtn.options) deleteBtn.options.hidden = !showDelete;
                }
                smartResize(node);
            }
            node._SDET_lastSeed = undefined;
            node._SDET_cachedInputSeed = null;
            node._SDET_cachedResolvedSeed = null;
            let seedWidget = null;
            let controlAfterGenerateIndex = -1;
            for (const [i, widget] of this.widgets.entries()) {
                const wname = (widget.name || '').toLowerCase();
                if (wname === 'seed') seedWidget = widget;
                else if (wname === 'control_after_generate') controlAfterGenerateIndex = i;
            }
            if (controlAfterGenerateIndex >= 0) {
                this.widgets.splice(controlAfterGenerateIndex, 1);
            }
            if (seedWidget) {
                node._SDET_seedWidget = seedWidget;
                node._SDET_randomMin = 0;
                node._SDET_randomMax = Number.MAX_SAFE_INTEGER;
                node.generateRandomSeed = function() {
                    const step = this._SDET_seedWidget?.options?.step || 1;
                    const range = (this._SDET_randomMax - this._SDET_randomMin) / (step / 10);
                    let seed = Math.floor(Math.random() * range) * (step / 10) + this._SDET_randomMin;
                    if (SPECIAL_SEEDS.includes(seed)) seed = 0;
                    return seed;
                };
                node.getSeedToUse = function() {
                    const inputSeed = Number(this._SDET_seedWidget.value);
                    if (this._SDET_cachedInputSeed === inputSeed && this._SDET_cachedResolvedSeed != null) {
                        return this._SDET_cachedResolvedSeed;
                    }
                    let seedToUse = null;
                    if (SPECIAL_SEEDS.includes(inputSeed)) {
                        if (typeof this._SDET_lastSeed === 'number' && !SPECIAL_SEEDS.includes(this._SDET_lastSeed)) {
                            if (inputSeed === SPECIAL_SEED_INCREMENT) seedToUse = this._SDET_lastSeed + 1;
                            else if (inputSeed === SPECIAL_SEED_DECREMENT) seedToUse = this._SDET_lastSeed - 1;
                        }
                        if (seedToUse == null || SPECIAL_SEEDS.includes(seedToUse)) {
                            seedToUse = this.generateRandomSeed();
                        }
                    }
                    const finalSeed = seedToUse != null ? seedToUse : inputSeed;
                    this._SDET_cachedInputSeed = inputSeed;
                    this._SDET_cachedResolvedSeed = finalSeed;
                    return finalSeed;
                };
                const origSeedCb = seedWidget.callback;
                seedWidget.callback = (value) => {
                    node._SDET_cachedInputSeed = null;
                    node._SDET_cachedResolvedSeed = null;
                    if (origSeedCb) origSeedCb.call(seedWidget, value);
                };
                const seedWidgetIndex = node.widgets.indexOf(seedWidget);
                const randomizeBtn = node.addWidget('button', '🌑 Randomize Each Time', '', () => {
                    seedWidget.value = SPECIAL_SEED_RANDOM;
                    if (seedWidget.callback) seedWidget.callback(SPECIAL_SEED_RANDOM);
                }, {
                    serialize: false
                });
                const newRandomBtn = node.addWidget('button', '🌕 New Fixed Random', '', () => {
                    const newSeed = node.generateRandomSeed();
                    seedWidget.value = newSeed;
                    if (seedWidget.callback) seedWidget.callback(newSeed);
                }, {
                    serialize: false
                });
                const lastSeedBtn = node.addWidget('button', LAST_SEED_BUTTON_LABEL, '', () => {
                    if (node._SDET_lastSeed != null) {
                        seedWidget.value = node._SDET_lastSeed;
                        lastSeedBtn.name = LAST_SEED_BUTTON_LABEL;
                        lastSeedBtn.disabled = true;
                        if (isVueMode()) notifyVue(node);
                    }
                }, {
                    serialize: false
                });
                lastSeedBtn.disabled = true;
                node._SDET_lastSeedBtn = lastSeedBtn;
                const btns = [randomizeBtn, newRandomBtn, lastSeedBtn];
                for (let i = btns.length - 1; i >= 0; i--) {
                    const btn = btns[i];
                    const cur = node.widgets.indexOf(btn);
                    if (cur !== seedWidgetIndex + 1) {
                        node.widgets.splice(cur, 1);
                        node.widgets.splice(seedWidgetIndex + 1, 0, btn);
                    }
                }
                const origOnExecuted = node.onExecuted;
                node.onExecuted = function(message) {
                    const result = origOnExecuted ? origOnExecuted.apply(this, arguments) : undefined;
                    if (message && message.seed !== undefined) {
                        this._SDET_lastSeed = message.seed;
                    }
                    return result;
                };
            }
            setTimeout(() => {
                if (node._SDET_initialized) return;
                node._SDET_initialized = true;
                if (node._SDET_configuredFromWorkflow) return;
                if (modelWidget && isSeparatorEntry(modelWidget.value)) {
                    const opts = modelWidget.options?.values || [];
                    const first = opts.find(v => !isSeparatorEntry(v));
                    if (first) modelWidget.value = first;
                }
                updateAllVisibility();
                // Sync size-shrink — smartResize's async rAF pass leaves a
                // visible tall-node gap on fresh add (hideInitially pre-hid
                // ~15 widgets before Vue's first computeSize() pass).
                const _oldH = node.size[1];
                node.size[1] = 0;
                const _c = node.computeSize();
                if (_c[1] !== _oldH) node.setSize?.([node.size[0], _c[1]]);
                else node.size[1] = _oldH;
                (async () => {
                    if (modelWidget?.value) {
                        await onModelChanged(modelWidget.value);
                    }
                })();
            }, 0);
            const origOnConfigure = node.onConfigure;
            node.onConfigure = function(info) {
                node._SDET_configuredFromWorkflow = true;
                if (origOnConfigure) origOnConfigure.apply(this, arguments);
                setTimeout(async () => {
                    if (modeBarWidget) {
                        modeBarWidget.value = readModeFromBacking();
                    }
                    if (modelWidget?.value) {
                        await onModelChanged(modelWidget.value);
                    } else {
                        updateAllVisibility();
                    }
                }, 150);
            };
            return r;
        };
    },
    async setup() {
        const originalGraphToPrompt = app.graphToPrompt;
        app.graphToPrompt = async function() {
            const result = await originalGraphToPrompt.apply(this, arguments);
            if (!result || !result.output) return result;
            const nodes = app.graph._nodes;
            for (const node of nodes) {
                if (node.type !== NODE_NAME || !node._SDET_seedWidget) continue;
                if (node.mode === 2 || node.mode === 4) continue;
                const nodeId = String(node.id);
                if (!result.output[nodeId]) continue;
                const seedToUse = node.getSeedToUse();
                if (result.output[nodeId].inputs?.seed !== undefined) {
                    result.output[nodeId].inputs.seed = seedToUse;
                }
                if (Number(node._SDET_lastSeed) !== Number(seedToUse)) {
                    node._SDET_lastSeed = seedToUse;
                }
                node._SDET_cachedInputSeed = null;
                node._SDET_cachedResolvedSeed = null;
                if (node._SDET_lastSeedBtn) {
                    const curVal = Number(node._SDET_seedWidget.value);
                    if (SPECIAL_SEEDS.includes(curVal)) {
                        node._SDET_lastSeedBtn.name = `🌘 ${seedToUse}`;
                        node._SDET_lastSeedBtn.disabled = false;
                    } else {
                        node._SDET_lastSeedBtn.name = LAST_SEED_BUTTON_LABEL;
                        node._SDET_lastSeedBtn.disabled = true;
                    }
                    if (isVueMode()) notifyVue(node);
                }
                if (result.workflow?.nodes) {
                    const wfNode = result.workflow.nodes.find(n => n.id === node.id);
                    if (wfNode?.widgets_values) {
                        const seedIdx = node.widgets.indexOf(node._SDET_seedWidget);
                        if (seedIdx >= 0 && wfNode.widgets_values[seedIdx] !== seedToUse) {
                            wfNode.widgets_values[seedIdx] = seedToUse;
                        }
                    }
                }
            }
            return result;
        };
        let lastExecRefreshTime = 0;
        api.addEventListener("executed", async () => {
            const now = Date.now();
            if (now - lastExecRefreshTime < 5000) return;
            const hasNodes = (app.graph?._nodes || []).some(n => n.type === NODE_NAME);
            if (!hasNodes) return;
            lastExecRefreshTime = now;
        });
        onSmlRegistryChanged(() => smlDetectionExtension.refreshComboInNodes({ reload: false }));
    },
    async refreshComboInNodes({ reload = true } = {}) {
        // R-key refresh — only run if at least one Smart Detection node exists.
        const nodes = app.graph?._nodes || [];
        const targets = nodes.filter(n => n.type === NODE_NAME);
        if (targets.length === 0) return;
        if (reload) {
            try { await fetch('/smartlml/registry/reload', { method: 'POST' }); } catch (_) {}
        }
        try {
            const fresh = await fetchDetectionModelList(true);
            if (!Array.isArray(fresh) || fresh.length === 0) return;
            const mapped = mapModelSeparators(fresh);
            for (const node of targets) {
                const mw = node.widgets?.find(w => w.name === 'model_name');
                if (!mw) continue;
                const prev = mw.value;
                mw.options.values = mapped;
                if (!mapped.includes(prev) || isSeparatorEntry(prev)) {
                    const first = mapped.find(v => !isSeparatorEntry(v));
                    if (first) {
                        mw.value = first;
                        if (mw.callback) mw.callback(first);
                    }
                }
            }
        } catch (_) {}
    },
};

app.registerExtension(smlDetectionExtension);
