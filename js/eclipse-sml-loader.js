import {
    app,
    api
} from './comfy/index.js';
import {
    debounce,
    notifyVue,
    createWidgetVisibilityManager,
    isVueMode,
    isConfiguringGraph,
    smartResize,
} from './eclipse-widget-performance-utils.js';
import {
    injectComboChipCSS,
    createComboChipWidget
} from './eclipse-combo-chip.js';
import {
    enterGraphToPromptHook,
    exitGraphToPromptHook,
    getGraphNodeList,
    findWorkflowNode,
} from './eclipse-seed-utils.js';
import { onSmlRegistryChanged } from './eclipse-sml-registry-events.js';
const NODE_NAME = "Smart LM Loader [Eclipse]";
const LEGACY_NODE_NAMES = new Set([
    "Smart Language Model Loader v2 [SmartLML]",
    "Smart Language Model Loader v2 [Eclipse]",
    "Smart Language Model Loader v3 [SmartLML]",
]);
injectComboChipCSS('smldr');
const MODE_OPTIONS = [{
    label: 'Cleanup',
    tooltip: 'VRAM garbage collection — clear VRAM cache and run Python garbage collection before model loading'
}, {
    label: 'Keep Loaded',
    tooltip: 'Keep the model cached in VRAM between runs (highly recommended for speed, avoids reloading)'
}, {
    label: 'Multi-Task',
    tooltip: 'Enable multi-task mode — sequence multiple tasks (Task 1 -> Task 2 etc.) in a single run'
}, {
    label: 'Training',
    tooltip: 'Include task-specific few-shot examples in the prompt to improve structure and formatting adherence'
}, {
    label: 'Advanced',
    tooltip: 'Toggle visibility of advanced sampling, hardware device, and compile parameters'
}, {
    label: 'Use Advanced',
    tooltip: 'Force applying advanced sampling parameters. When disabled, safe/conservative defaults are used'
}, {
    label: '⚠ Trust Remote Code',
    tooltip: '⚠ SECURITY OVERRIDE: Allow Hugging Face models to run custom Python modeling code locally. Only enable for trusted models'
}, {
    label: 'Delete',
    tooltip: 'Show button to permanently delete the selected model files from local storage'
}, ];
const MODE_DEFAULTS = ['Cleanup', 'Training'];
const MODE_TO_BACKING = {
    'Multi-Task': 'multi_task_mode',
    'Cleanup': 'memory_cleanup',
    'Keep Loaded': 'keep_model_loaded',
    'Training': 'use_few_shot_training',
    'Advanced': 'show_advanced',
    'Use Advanced': 'use_advanced',
    '⚠ Trust Remote Code': 'trust_remote_code',
};
const SPECIAL_SEED_RANDOM = -1;
const SPECIAL_SEED_INCREMENT = -2;
const SPECIAL_SEED_DECREMENT = -3;
const SPECIAL_SEEDS = [SPECIAL_SEED_RANDOM, SPECIAL_SEED_INCREMENT, SPECIAL_SEED_DECREMENT];
const LAST_SEED_BUTTON_LABEL = "🌘 (Use Last Queued Seed)";
const USER_PROMPT_MIN_HEIGHT = 26;
const TASK_SEPARATOR_LABELS = {
    '__SEP__VISION__': 'Vision tasks',
    '__SEP__TEXT__': 'Text tasks',
};
const MODEL_SEPARATOR_LABELS = {
    '__SEP__VISION_MODELS__': 'Vision models',
    '__SEP__TEXT_MODELS__': 'Text models',
    '__SEP__WD14_MODELS__': 'WD14 models',
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
const FAMILY_WIDGET_SUPPORT = {
    _default: {
        device: true,
        use_torch_compile: true,
        temperature: true,
        top_p: true,
        top_k: true,
        num_beams: true,
        do_sample: true,
        repetition_penalty: true,
        frame_count: true
    },
    Florence: {
        temperature: false,
        top_p: false,
        top_k: false,
        repetition_penalty: false,
        frame_count: false
    },
    Mistral: {
        frame_count: false
    },
    LLaVA: {
        frame_count: false
    },
    LLM_TEXT: {
        frame_count: false,
        num_beams: false,
        do_sample: false,
        use_torch_compile: false
    },
    VLM: {
        frame_count: false
    },
};

function getFamilySupport(family) {
    return {
        ...FAMILY_WIDGET_SUPPORT._default,
        ...(FAMILY_WIDGET_SUPPORT[family] || {})
    };
}
let modelListCache = null;
let modelListPromise = null;
let taskListCache = {};
async function fetchModelList(force = false) {
    if (!force && modelListCache) return modelListCache;
    if (modelListPromise) return modelListPromise;
    modelListPromise = (async () => {
        try {
            const resp = await fetch('/smartlml/model_list');
            if (resp.ok) {
                modelListCache = await resp.json();
            } else {
                console.warn('[SML] Failed to fetch model list');
                modelListCache = [];
            } 4.7
        } catch (e) {
            console.warn('[SML] Error fetching model list:', e);
            modelListCache = [];
        }
        modelListPromise = null;
        return modelListCache;
    })();
    return modelListPromise;
}
async function fetchModelEntry(displayName) {
    if (!displayName) return null;
    try {
        const resp = await fetch(`/smartlml/model_entry?name=${encodeURIComponent(displayName)}`);
        if (resp.ok) return resp.json();
    } catch (e) {
        console.warn('[SML] Error fetching model entry:', e);
    }
    return null;
}
async function fetchTaskList(hasVision, family = '') {
    const key = `${hasVision}|${family}`;
    if (taskListCache[key]) return taskListCache[key];
    try {
        let url = `/smartlml/task_list?has_vision=${hasVision}`;
        if (family) url += `&family=${encodeURIComponent(family)}`;
        const resp = await fetch(url);
        if (resp.ok) {
            const tasks = await resp.json();
            taskListCache[key] = tasks;
            return tasks;
        }
    } catch (e) {
        console.warn('[SML] Error fetching task list:', e);
    }
    return [];
}

function updateDropdown(widget, values, defaultValue = null) {
    if (!widget) return;
    widget.options.values = values;
    const def = defaultValue !== null ? defaultValue : (values[0] || '');
    if (!values.includes(widget.value)) {
        widget.value = def;
    }
}

function mapTaskSeparators(rawTasks) {
    const maxLen = rawTasks.reduce((m, t) => TASK_SEPARATOR_LABELS[t] ? m : Math.max(m, t.length), 0);
    return rawTasks.map(t => {
        const label = TASK_SEPARATOR_LABELS[t];
        return label ? buildSeparator(label, maxLen) : t;
    });
}

function updateTaskDropdown(widget, rawTasks, defaultValue = null) {
    if (!widget) return;
    const display = mapTaskSeparators(rawTasks);
    widget.options.values = display;
    const selectable = display.filter(v => !isSeparatorEntry(v));
    const def = defaultValue ?? selectable[0] ?? '';
    if (!selectable.includes(widget.value)) {
        widget.value = def;
    }
}

function getBackendFromName(displayName) {
    if (!displayName) return 'transformers';
    if (displayName.endsWith('-GGUF')) return 'gguf';
    if (displayName.endsWith('-vLLM')) return 'vllm';
    if (displayName.endsWith('-SGLang')) return 'sglang';
    if (displayName.endsWith('-Ollama')) return 'ollama';
    if (displayName.endsWith('-llama.cpp')) return 'llamacpp';
    if (displayName.startsWith('WD14-')) return 'wd14';
    return 'transformers';
}

function isDockerBackend(backend) {
    return backend === 'vllm' || backend === 'sglang' || backend === 'ollama' || backend === 'llamacpp';
}
const smlLoaderExtension = {
    name: "Eclipse.SmartLML",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name !== NODE_NAME && !LEGACY_NODE_NAMES.has(nodeData.name)) return;
        const isLegacy = LEGACY_NODE_NAMES.has(nodeData.name);
        if (!isLegacy) await fetchModelList();

        // For legacy node names: only patch drawWidgets to prevent crash when old workflow
        // loads an array value into the features ComboWidget (pre-chips migration format).
        if (isLegacy) {
            const origDW = nodeType.prototype.drawWidgets ?? null;
            nodeType.prototype.drawWidgets = function (...args) {
                if (this.widgets) {
                    for (const w of this.widgets) {
                        if (Array.isArray(w.value)) w.value = w.value.join(',');
                    }
                }
                return origDW ? origDW.apply(this, args) : undefined;
            };
            return;
        }
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function() {
            const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            const vis = createWidgetVisibilityManager(node);
            // Pre-hide conditional widgets so workflow loading cannot size the
            // node from a widget that the restored backend hides asynchronously.
            // The first visibility pass reveals the correct backend-specific set.
            vis.hideInitially([
                'quantization', 'attention_mode',
                'task_2', 'task_3', 'task_4',
                'device', 'temperature', 'top_p', 'top_k', 'num_beams',
                'do_sample', 'repetition_penalty', 'frame_count', 'use_torch_compile',
                'min_p', 'mirostat', 'mirostat_eta', 'mirostat_tau', 'repeat_last_n', 'stop_sequences',
                'threshold', 'char_threshold', 'replace_underscore',
                'memory_cleanup', 'keep_model_loaded', 'multi_task_mode', 'show_advanced',
                'use_advanced',
                'use_few_shot_training',
                'trust_remote_code',
            ]);
            const getWidget = (name) => node.widgets?.find(w => w.name === name);
            const setWidgetValue = (name, value) => {
                const w = getWidget(name);
                if (w && w.value !== value) {
                    w.value = value;
                    if (w.callback) w.callback(value);
                }
            };
            const modelWidget = getWidget('model');
            const quantizationWidget = getWidget('quantization');
            const userPromptWidget = getWidget('user_prompt');
            if (userPromptWidget?.options) {
                // Keep the flexible prompt area compact while allowing larger
                // manually saved heights to distribute extra space into it.
                userPromptWidget.options.getMinHeight = () => USER_PROMPT_MIN_HEIGHT;
            }
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
            if (backingChips.length > 0 || getWidget('multi_task_mode')?.value === true) {
                modeInitial = backingChips;
            }
            const modeBarWidget = createComboChipWidget({
                node,
                options: MODE_OPTIONS,
                savedValue: modeInitial,
                origIdx: modeBarIdx,
                widgetName: '_sml_mode_bar',
                cssPrefix: 'smldr',
                radioGroups: null,
                radioToggle: false,
                serialize: false,
            });
            node._SML_modeBarWidget = modeBarWidget;

            function syncModeToBacking(selectedSet) {
                for (const [chip, backing] of Object.entries(MODE_TO_BACKING)) {
                    const w = getWidget(backing);
                    if (w) w.value = selectedSet.has(chip);
                }
            }
            modeBarWidget.callback = function() {
                const selectedSet = new Set(modeBarWidget.value);
                // When Multi-Task is toggled OFF, reset chained tasks (task_2/3/4) to 'None'
                // so hidden tasks don't silently re-run on the next execute.
                const multiTaskNow = selectedSet.has('Multi-Task');
                if (node._SML_prevMultiTask === true && !multiTaskNow) {
                    for (const tName of ['task_2', 'task_3', 'task_4']) {
                        const tw = getWidget(tName);
                        if (tw && tw.value !== 'None') {
                            tw.value = 'None';
                            if (tw.callback) {
                                try { tw.callback.call(tw, 'None'); } catch (e) { /* noop */ }
                            }
                        }
                    }
                }
                node._SML_prevMultiTask = multiTaskNow;
                syncModeToBacking(selectedSet);
                vis.markUserDriven();
                updateAllVisibility();
            };
            // Seed the previous-state tracker from the initial mode set.
            node._SML_prevMultiTask = new Set(modeBarWidget.value).has('Multi-Task');
            for (const backing of Object.values(MODE_TO_BACKING)) {
                vis.setVisible(backing, false);
            }
            let currentModelEntry = null;
            async function onModelChanged(modelName) {
                const backend = getBackendFromName(modelName);
                const isWD14 = backend === 'wd14';
                const isGGUF = backend === 'gguf' || backend === 'llamacpp';
                currentModelEntry = null;
                if (modelName) {
                    currentModelEntry = await fetchModelEntry(modelName);
                }
                if (isGGUF && currentModelEntry?.quantizations?.length) {
                    updateDropdown(quantizationWidget, currentModelEntry.quantizations, currentModelEntry.quantizations[0]);
                }

                if (!isWD14) {
                    const hasVision = currentModelEntry?.has_vision ?? true;
                    const family = currentModelEntry?.family || '';
                    const tasks = await fetchTaskList(hasVision, family);
                    const taskWidget = getWidget('task');
                    if (taskWidget && tasks.length) {
                        updateTaskDropdown(taskWidget, tasks);
                    }
                    const textTasks = await fetchTaskList(false, '');
                    const noneTextTasks = ['None', ...mapTaskSeparators(textTasks)];
                    for (const tName of ['task_2', 'task_3', 'task_4']) {
                        const tw = getWidget(tName);
                        if (tw) updateDropdown(tw, noneTextTasks, 'None');
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
                const quantIdx = quantizationWidget ? node.widgets.indexOf(quantizationWidget) : -1;
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

            function addSeparatorGuard(widget) {
                if (!widget || widget._SML_separatorGuarded) return;
                widget._SML_separatorGuarded = true;
                const originalCb = widget.callback;
                widget.callback = function(value) {
                    if (isSeparatorEntry(value)) {
                        const opts = widget.options?.values || [];
                        let idx = opts.indexOf(value);
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
                        widget.value = newVal;
                        if (originalCb) originalCb.call(this, newVal);
                        return;
                    }
                    if (originalCb) originalCb.call(this, value);
                };
            }
            const taskWidget = getWidget('task');
            if (taskWidget) {
                addSeparatorGuard(taskWidget);
                const prevTaskCb = taskWidget.callback;
                taskWidget.callback = function(value) {
                    if (prevTaskCb) prevTaskCb.call(this, value);
                    vis.markUserDriven();
                    updateAllVisibility();
                };
            }
            for (const tName of ['task_2', 'task_3', 'task_4']) {
                addSeparatorGuard(getWidget(tName));
            }

            function updateAllVisibility() {
                if (node.id === -1) return;
                if (!node.widgets) return;
                const modelName = modelWidget?.value || '';
                const backend = getBackendFromName(modelName);
                const isWD14 = backend === 'wd14';
                const isGGUF = backend === 'gguf' || backend === 'llamacpp';
                const isTransformers = backend === 'transformers';
                const isDocker = isDockerBackend(backend);
                const modeSet = new Set(modeBarWidget.value);
                const multiTask = modeSet.has('Multi-Task');
                const showAdvanced = modeSet.has('Advanced');
                const family = currentModelEntry?.family || '';
                // Known families with no system role — force-set is skipped for these.
                // Future/unknown families default to having system support.
                const NO_SYSTEM_FAMILIES = new Set(['Florence']);
                const sysPromptInput = node.inputs?.find(i => i.name === 'system_prompt');
                const isSystemOverride = !!(sysPromptInput && sysPromptInput.link != null);
                const taskWidget = getWidget('task');
                const supportsSystem = !NO_SYSTEM_FAMILIES.has(family);
                // Defensive: only force-set if 'Direct Chat' actually exists in the task options.
                const taskOpts = taskWidget?.options?.values;
                const hasDirectChat = Array.isArray(taskOpts) && taskOpts.includes('Direct Chat');
                if (!isWD14 && isSystemOverride && supportsSystem && hasDirectChat && taskWidget) {
                    if (taskWidget.value !== 'Direct Chat') {
                        if (!node._Eclipse_priorTask) node._Eclipse_priorTask = taskWidget.value;
                        taskWidget.value = 'Direct Chat';
                    }
                    taskWidget.disabled = true;
                } else if (taskWidget) {
                    if (node._Eclipse_priorTask) {
                        taskWidget.value = node._Eclipse_priorTask;
                        node._Eclipse_priorTask = null;
                    }
                    taskWidget.disabled = false;
                }
                vis.setVisible('quantization', isGGUF);
                vis.setVisible('task', !isWD14);
                vis.setVisible('user_prompt', !isWD14);
                vis.setVisible('max_tokens', !isWD14);
                vis.setVisible('context_size', !isWD14);
                vis.setVisible('attention_mode', !isWD14 && isTransformers);
                const task2 = getWidget('task_2')?.value || 'None';
                const task3 = getWidget('task_3')?.value || 'None';
                vis.setVisible('task_2', !isWD14 && multiTask);
                vis.setVisible('task_3', !isWD14 && multiTask && task2 !== 'None');
                vis.setVisible('task_4', !isWD14 && multiTask && task2 !== 'None' && task3 !== 'None');
                const support = getFamilySupport(family);
                const advWidgets = ['device', 'temperature', 'top_p', 'top_k', 'num_beams', 'do_sample', 'repetition_penalty', 'frame_count', 'use_torch_compile', ];
                for (const name of advWidgets) {
                    vis.setVisible(name, !isWD14 && showAdvanced && support[name] !== false);
                }
                // Universal advanced sampling (all generative backends except Florence)
                const isFlorence = family === 'Florence';
                const advUniv = ['min_p', 'stop_sequences'];
                for (const name of advUniv) {
                    vis.setVisible(name, !isWD14 && !isFlorence && showAdvanced);
                }
                // llama.cpp-family-only sampling (mirostat / repeat_last_n)
                // Backends: gguf, llamacpp, ollama (exposes mirostat via options)
                const isLlamaCppFamily = (backend === 'gguf' || backend === 'llamacpp' || backend === 'ollama');
                const advLlamaCpp = ['mirostat', 'mirostat_eta', 'mirostat_tau', 'repeat_last_n'];
                for (const name of advLlamaCpp) {
                    vis.setVisible(name, !isWD14 && !isFlorence && showAdvanced && isLlamaCppFamily);
                }
                vis.setVisible('threshold', isWD14);
                vis.setVisible('char_threshold', isWD14);

                vis.setVisible('replace_underscore', isWD14);
                for (const backing of Object.values(MODE_TO_BACKING)) {
                    vis.setVisible(backing, false);
                }
                const showDelete = modeSet.has('Delete') && modelName && !isSeparatorEntry(modelName);
                if (deleteBtn) {
                    deleteBtn.hidden = !showDelete;
                    if (deleteBtn.options) deleteBtn.options.hidden = !showDelete;
                }
                vis.setVisible('seed', true);

                // Smart resize logic:
                // - In WD14 mode, shrink the node to be compact.
                // - In LLM/VLM mode, only grow the node if it's too small for the visible widgets,
                //   preserving user's custom height if they manually resized it.
                if (isWD14) {
                    smartResize(node);
                } else {
                    const computed = node.computeSize();
                    if (computed && node.size[1] < computed[1]) {
                        smartResize(node);
                    }
                }
            }
            for (const tName of ['task_2', 'task_3', 'task_4']) {
                const tw = getWidget(tName);
                if (tw) {
                    const prevCb = tw.callback;
                    tw.callback = function(value) {
                        if (prevCb) prevCb.call(this, value);
                        vis.markUserDriven();
                        updateAllVisibility();
                    };
                }
            }
            const origOnConnectionsChange = node.onConnectionsChange;
            node.onConnectionsChange = function(type, index, connected, linkInfo) {
                if (origOnConnectionsChange) origOnConnectionsChange.apply(this, arguments);
                // Skip during workflow load — onConfigure→onModelChanged runs a
                // full updateAllVisibility pass with final link state.
                if (isConfiguringGraph()) return;
                if (type === 1) {
                    const input = this.inputs[index];
                    if (input && (input.name === 'system_prompt' || input.name === 'user_prompt')) {
                        requestAnimationFrame(() => updateAllVisibility());
                    }
                }
            };
            node._SML_lastSeed = undefined;
            node._SML_cachedInputSeed = null;
            node._SML_cachedResolvedSeed = null;
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
                node._SML_seedWidget = seedWidget;
                node._SML_randomMin = 0;
                node._SML_randomMax = Number.MAX_SAFE_INTEGER;
                node.generateRandomSeed = function() {
                    const step = this._SML_seedWidget?.options?.step || 1;
                    const range = (this._SML_randomMax - this._SML_randomMin) / (step / 10);
                    let seed = Math.floor(Math.random() * range) * (step / 10) + this._SML_randomMin;
                    if (SPECIAL_SEEDS.includes(seed)) seed = 0;
                    return seed;
                };
                node.getSeedToUse = function() {
                    const inputSeed = Number(this._SML_seedWidget.value);
                    if (this._SML_cachedInputSeed === inputSeed && this._SML_cachedResolvedSeed != null) {
                        return this._SML_cachedResolvedSeed;
                    }
                    let seedToUse = null;
                    if (SPECIAL_SEEDS.includes(inputSeed)) {
                        if (typeof this._SML_lastSeed === 'number' && !SPECIAL_SEEDS.includes(this._SML_lastSeed)) {
                            if (inputSeed === SPECIAL_SEED_INCREMENT) seedToUse = this._SML_lastSeed + 1;
                            else if (inputSeed === SPECIAL_SEED_DECREMENT) seedToUse = this._SML_lastSeed - 1;
                        }
                        if (seedToUse == null || SPECIAL_SEEDS.includes(seedToUse)) {
                            seedToUse = this.generateRandomSeed();
                        }
                    }
                    const finalSeed = seedToUse != null ? seedToUse : inputSeed;
                    this._SML_cachedInputSeed = inputSeed;
                    this._SML_cachedResolvedSeed = finalSeed;
                    return finalSeed;
                };
                const origSeedCb = seedWidget.callback;
                seedWidget.callback = (value) => {
                    node._SML_cachedInputSeed = null;
                    node._SML_cachedResolvedSeed = null;
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
                    if (node._SML_lastSeed != null) {
                        seedWidget.value = node._SML_lastSeed;
                        lastSeedBtn.name = LAST_SEED_BUTTON_LABEL;
                        lastSeedBtn.disabled = true;
                        if (isVueMode()) notifyVue(node);
                    }
                }, {
                    serialize: false
                });
                lastSeedBtn.disabled = true;
                node._SML_lastSeedBtn = lastSeedBtn;
                // The three seed buttons are appended after the seed widget by
                // addWidget; they sit immediately after seed in render order
                // because seed is now the last visible widget in the Python
                // schema. No reordering needed.
                const origOnExecuted = node.onExecuted;
                node.onExecuted = function(message) {
                    const result = origOnExecuted ? origOnExecuted.apply(this, arguments) : undefined;
                    if (message && message.seed !== undefined) {
                        this._SML_lastSeed = message.seed;
                    }
                    return result;
                };
            }
            setTimeout(() => {
                if (node._SML_initialized) return;
                node._SML_initialized = true;
                if (node._SML_configuredFromWorkflow) return;
                if (modelWidget && isSeparatorEntry(modelWidget.value)) {
                    const opts = modelWidget.options?.values || [];
                    const first = opts.find(v => !isSeparatorEntry(v));
                    if (first) modelWidget.value = first;
                }
                updateAllVisibility();
                // Sync size-shrink — smartResize's async rAF pass leaves a
                // visible tall-node gap on fresh add (hideInitially pre-hid
                // ~22 widgets before Vue's first computeSize() pass).
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
                node._SML_configuredFromWorkflow = true;
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
        let _sml_isQueueing = false;
        const originalQueuePrompt = app.queuePrompt;
        app.queuePrompt = async function(...args) {
            _sml_isQueueing = true;
            try {
                return await originalQueuePrompt.apply(this, args);
            } finally {
                _sml_isQueueing = false;
            }
        };
        const originalGraphToPrompt = app.graphToPrompt;
        app.graphToPrompt = async function() {
            enterGraphToPromptHook();
            try {
                const result = await originalGraphToPrompt.apply(this, arguments);
                if (!result || !result.output) {
                    return result;
                }
                for (const { node, outputKey } of getGraphNodeList(app.graph)) {
                    if (node.type !== NODE_NAME || !node._SML_seedWidget) continue;
                    if (node.mode === 2 || node.mode === 4) continue;
                    if (!result.output[outputKey]) continue;
                    const seedToUse = node.getSeedToUse();
                    if (result.output[outputKey].inputs?.seed !== undefined) {
                        result.output[outputKey].inputs.seed = seedToUse;
                    }
                    if (Number(node._SML_lastSeed) !== Number(seedToUse)) {
                        node._SML_lastSeed = seedToUse;
                    }
                    node._SML_cachedInputSeed = null;
                    node._SML_cachedResolvedSeed = null;
                    if (node._SML_lastSeedBtn) {
                        const curVal = Number(node._SML_seedWidget.value);
                        if (SPECIAL_SEEDS.includes(curVal)) {
                            node._SML_lastSeedBtn.name = `🌘 ${seedToUse}`;
                            node._SML_lastSeedBtn.disabled = false;
                        } else {
                            node._SML_lastSeedBtn.name = LAST_SEED_BUTTON_LABEL;
                            node._SML_lastSeedBtn.disabled = true;
                        }
                        if (isVueMode()) notifyVue(node);
                    }
                    if (result.workflow) {
                        const wfNode = findWorkflowNode(result.workflow, outputKey);
                        if (wfNode?.widgets_values) {
                            const seedIdx = node.widgets.indexOf(node._SML_seedWidget);
                            if (seedIdx >= 0 && wfNode.widgets_values[seedIdx] !== seedToUse) {
                                wfNode.widgets_values[seedIdx] = seedToUse;
                            }
                        }
                    }
                }
                return result;
            } finally {
                exitGraphToPromptHook();
            }
        };
        let lastExecRefreshTime = 0;
        api.addEventListener("executed", async () => {
            const now = Date.now();
            if (now - lastExecRefreshTime < 5000) return;
            const hasNodes = (app.graph?._nodes || []).some(n => n.type === NODE_NAME);
            if (!hasNodes) return;
            lastExecRefreshTime = now;
            modelListCache = null;
            taskListCache = {};
        });
        onSmlRegistryChanged(() => smlLoaderExtension.refreshComboInNodes({ reload: false }));
    },
    async refreshComboInNodes({ reload = true } = {}) {
        // R-key refresh — only run if at least one Smart LM Loader node exists.
        const nodes = app.graph?._nodes || [];
        const targets = nodes.filter(n => n.type === NODE_NAME);
        if (targets.length === 0) return;
        if (reload) {
            try { await fetch('/smartlml/registry/reload', { method: 'POST' }); } catch (_) {}
        }
        modelListCache = null;
        taskListCache = {};
        try {
            const fresh = await fetchModelList(true);
            if (!Array.isArray(fresh) || fresh.length === 0) return;
            // API returns objects: build grouped name list with separator tokens.
            const vision = [], text = [], wd14 = [];
            for (const m of fresh) {
                const name = m?.display_name;
                if (!name) continue;
                if (m.backend === 'wd14') wd14.push(name);
                else if (m.has_vision) vision.push(name);
                else text.push(name);
            }
            const raw = [];
            if (vision.length) { raw.push('__SEP__VISION_MODELS__'); raw.push(...vision.sort()); }
            if (text.length)   { raw.push('__SEP__TEXT_MODELS__');   raw.push(...text.sort()); }
            if (wd14.length)   { raw.push('__SEP__WD14_MODELS__');   raw.push(...wd14.sort()); }
            const mapped = mapModelSeparators(raw);
            for (const node of targets) {
                const mw = node.widgets?.find(w => w.name === 'model');
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

app.registerExtension(smlLoaderExtension);
