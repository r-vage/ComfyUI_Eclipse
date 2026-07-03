import {
    app
} from './comfy/index.js';
import {
    createWidgetVisibilityManager,
    smartResize,
    notifyVue,
    onVueModeChange,
    isConfiguringGraph,
    isVueMode,
} from './eclipse-widget-performance-utils.js';
import {
    injectComboChipCSS,
    createComboChipWidget as _createComboChipWidget
} from './eclipse-combo-chip.js';
import { storeQueuedSeed, enterGraphToPromptHook, exitGraphToPromptHook, getGraphNodeList, clearNodeQueuedSeed, findWorkflowNode } from './eclipse-seed-utils.js';
const NODE_NAME = 'Smart Sampler Settings v2 [Eclipse]';
const SPECIAL_SEEDS = [-1, -2, -3];
const FEATURE_OPTIONS = [
    { label: 'allow_overwrite', tooltip: 'Allow downstream nodes to override these settings' },
    { label: 'sampler', tooltip: 'Show the sampler_name selector' },
    { label: 'scheduler', tooltip: 'Show the scheduler selector' },
    { label: 'steps', tooltip: 'Show the sampling steps slider' },
    { label: 'cfg', tooltip: 'Show the CFG scale slider' },
    { label: 'guidance', tooltip: 'Show the guidance value (Flux/SD3)' },
    { label: 'denoise', tooltip: 'Show the denoise strength slider' },
    { label: 'noise_injection', tooltip: 'Show sigmas_denoise + noise_strength widgets' },
    { label: 'upscale', tooltip: 'Show upscale_steps + upscale_denoise + upscale_value widgets' },
    { label: 'image_seed', tooltip: 'Show the image seed input' },
    { label: '🎲 img random', tooltip: 'Image seed: random each run' },
    { label: '⏫ img increment', tooltip: 'Image seed: +1 each run' },
    { label: '⏬ img decrement', tooltip: 'Image seed: -1 each run' },
    { label: 'prompt_seed', tooltip: 'Show the prompt seed input (wildcards/dynamic prompts)' },
    { label: '🎲 prm random', tooltip: 'Prompt seed: random each run' },
    { label: '⏫ prm increment', tooltip: 'Prompt seed: +1 each run' },
    { label: '⏬ prm decrement', tooltip: 'Prompt seed: -1 each run' },
];
const DEFAULT_FEATURES = ['sampler', 'scheduler', 'steps', 'cfg', 'denoise', 'image_seed', ];
injectComboChipCSS('');
const FEATURE_WIDGETS = {
    allow_overwrite: ['allow_overwrite'],
    sampler: ['sampler_name'],
    scheduler: ['scheduler'],
    steps: ['steps'],
    cfg: ['cfg'],
    guidance: ['guidance'],
    denoise: ['denoise'],
    noise_injection: ['sigmas_denoise', 'noise_strength'],
    upscale: ['upscale_steps', 'upscale_denoise', 'upscale_value'],
    image_seed: ['image_seed'],
    prompt_seed: ['prompt_seed'],
};
const IMG_MODE_CHIPS = ['🎲 img random', '⏫ img increment', '⏬ img decrement'];
const IMG_MODE_CHIP_TO_VAL = {
    '🎲 img random': -1,
    '⏫ img increment': -2,
    '⏬ img decrement': -3,
};
const PRM_MODE_CHIPS = ['🎲 prm random', '⏫ prm increment', '⏬ prm decrement'];
const PRM_MODE_CHIP_TO_VAL = {
    '🎲 prm random': -1,
    '⏫ prm increment': -2,
    '⏬ prm decrement': -3,
};
const ALL_MODE_CHIPS = new Set([...IMG_MODE_CHIPS, ...PRM_MODE_CHIPS]);
const IMG_SEED_BUTTONS = ['_btn_last_image_seed'];
const PRM_SEED_BUTTONS = ['_btn_last_prompt_seed'];
const ALL_CONTROLLED = Object.values(FEATURE_WIDGETS).flat().concat(IMG_SEED_BUTTONS).concat(PRM_SEED_BUTTONS);

function createComboChipWidget(node, savedValue, origIdx) {
    const w = _createComboChipWidget({
        node,
        options: FEATURE_OPTIONS,
        savedValue,
        origIdx,
        momentaryChips: [...ALL_MODE_CHIPS],
    });
    w.serializeValue = () => {
        const val = Array.isArray(w.value) ? w.value : [];
        return val.filter((f) => !ALL_MODE_CHIPS.has(f)).join(',');
    };
    return w;
}

function updateFeatureVisibility(node, vis) {
    if (node.id === -1) return;
    const raw = vis.getValue('features');
    const selected = Array.isArray(raw) ? raw : [];
    const selectedSet = new Set(selected);
    for (const name of ALL_CONTROLLED) vis.setVisible(name, false);
    for (const feature of selectedSet) {
        const widgets = FEATURE_WIDGETS[feature];
        if (widgets)
            for (const name of widgets) vis.setVisible(name, true);
    }
    const imgSeedVisible = selectedSet.has('image_seed');
    vis.setVisible('image_seed', imgSeedVisible);
    for (const name of IMG_SEED_BUTTONS) vis.setVisible(name, imgSeedVisible);
    const prmSeedVisible = selectedSet.has('prompt_seed');
    vis.setVisible('prompt_seed', prmSeedVisible);
    for (const name of PRM_SEED_BUTTONS) vis.setVisible(name, prmSeedVisible);
    smartResize(node);
}

function generateRandomSeed() {
    const max = Number.MAX_SAFE_INTEGER;
    let seed = Math.floor(Math.random() * max);
    if (SPECIAL_SEEDS.includes(seed)) seed = 0;
    return seed;
}

function resolveSeed(input, lastSeed) {
    if (!SPECIAL_SEEDS.includes(input)) return input;
    let resolved = null;
    if (typeof lastSeed === 'number' && !SPECIAL_SEEDS.includes(lastSeed)) {
        if (input === -2) resolved = lastSeed + 1;
        else if (input === -3) resolved = lastSeed - 1;
    }
    if (resolved == null || SPECIAL_SEEDS.includes(resolved))
        resolved = generateRandomSeed();
    return resolved;
}

function setupSeedChannel(node, seedWidgetName, btnName, lastSeedLabel, statePrefix) {
    const seedWidget = node.widgets?.find((w) => w.name === seedWidgetName);
    if (!seedWidget) return;
    const stateKeys = {
        widget: `_Eclipse_${statePrefix}Widget`,
        last: `_Eclipse_last${statePrefix}`,
        button: `_Eclipse_${statePrefix}Button`,
        cachedInput: `_Eclipse_cached${statePrefix}Input`,
        cachedResolved: `_Eclipse_cached${statePrefix}Resolved`,
    };
    node[stateKeys.widget] = seedWidget;
    node[stateKeys.last] = undefined;
    node[stateKeys.cachedInput] = null;
    node[stateKeys.cachedResolved] = null;
    const origCb = seedWidget.callback;
    seedWidget.callback = (v) => {
        node[stateKeys.cachedInput] = null;
        node[stateKeys.cachedResolved] = null;
        if (origCb) origCb.call(seedWidget, v);
    };
    const seedIdx = node.widgets.indexOf(seedWidget);
    const btnLastSeed = node.addWidget('button', btnName, '', () => {
        const last = node[stateKeys.last];
        if (last != null) {
            seedWidget.value = last;
            btnLastSeed.label = lastSeedLabel;
            btnLastSeed.disabled = true;
            const vis = node._Eclipse_vis;
            if (vis) updateFeatureVisibility(node, vis);
            if (isVueMode()) notifyVue(node);
        }
    }, {
        serialize: false
    });
    btnLastSeed.label = lastSeedLabel;
    btnLastSeed.disabled = true;
    node[stateKeys.button] = btnLastSeed;
    const btnIdx = node.widgets.indexOf(btnLastSeed);
    if (btnIdx !== seedIdx + 1) {
        node.widgets.splice(btnIdx, 1);
        node.widgets.splice(seedIdx + 1, 0, btnLastSeed);
    }
    return {
        seedWidget,
        stateKeys
    };
}


app.registerExtension({
    name: 'Eclipse.SmartSamplerSettings_v2',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : void 0;
            const node = this;
            const vis = createWidgetVisibilityManager(node);
            node._Eclipse_vis = vis;
            const autoFeaturesW = node.widgets?.find((w) => w.name === 'features');
            let featWidget;
            const origIdx = autoFeaturesW ? node.widgets.indexOf(autoFeaturesW) : 0;
            let savedValue = DEFAULT_FEATURES.slice();
            if (autoFeaturesW) {
                const v = autoFeaturesW.value;
                if (typeof v === 'string' && v.trim()) {
                    savedValue = v.split(',').map(s => s.trim()).filter(Boolean);
                } else if (Array.isArray(v) && v.length > 0) {
                    savedValue = v.slice();
                }
                autoFeaturesW.onRemove?.();
                node.widgets.splice(origIdx, 1);
            }
            featWidget = createComboChipWidget(node, savedValue, origIdx);
            node._Eclipse_chipWidget = featWidget;
            for (let i = node.widgets.length - 1; i >= 0; i--) {
                const wName = (node.widgets[i].name || '').toLowerCase();
                if (wName === 'control_after_generate') {
                    node.widgets.splice(i, 1);
                }
            }
            setupSeedChannel(node, 'image_seed', '_btn_last_image_seed', '♻️ (Use Last Queued Image Seed)', 'ImageSeed');
            setupSeedChannel(node, 'prompt_seed', '_btn_last_prompt_seed', '♻️ (Use Last Queued Prompt Seed)', 'PromptSeed');
            const origFeatCallback = featWidget.callback;
            featWidget.callback = function (value) {
                if (node._Eclipse_updatingChips) return;
                // Handle momentary chip clicks (seed mode actions)
                if (value && typeof value === 'object' && value.momentary) {
                    const chip = value.momentary;
                    let seedWidget = null;
                    let chipToVal = null;
                    let parentChip = null;
                    if (IMG_MODE_CHIPS.includes(chip)) {
                        seedWidget = node._Eclipse_ImageSeedWidget;
                        chipToVal = IMG_MODE_CHIP_TO_VAL;
                        parentChip = 'image_seed';
                    } else if (PRM_MODE_CHIPS.includes(chip)) {
                        seedWidget = node._Eclipse_PromptSeedWidget;
                        chipToVal = PRM_MODE_CHIP_TO_VAL;
                        parentChip = 'prompt_seed';
                    }
                    if (seedWidget && chipToVal) {
                        seedWidget.value = chipToVal[chip];
                        seedWidget.callback?.(seedWidget.value);
                        // Auto-enable the parent seed chip so the widget is visible
                        const sel = new Set(Array.isArray(featWidget.value) ? featWidget.value : []);
                        if (!sel.has(parentChip)) {
                            sel.add(parentChip);
                            node._Eclipse_updatingChips = true;
                            featWidget.value = [...sel];
                            node._Eclipse_updatingChips = false;
                        }
                    }
                    vis.markUserDriven();
                    updateFeatureVisibility(node, vis);
                    return;
                }
                origFeatCallback?.call(this, value);
                const selectedSet = new Set(Array.isArray(featWidget.value) ? featWidget.value : []);
                // Reset image_seed to stable value when image_seed chip is deselected
                if (!selectedSet.has('image_seed') && node._Eclipse_ImageSeedWidget
                    && SPECIAL_SEEDS.includes(Number(node._Eclipse_ImageSeedWidget.value))) {
                    const fallback = (typeof node._Eclipse_lastImageSeed === 'number'
                        && !SPECIAL_SEEDS.includes(node._Eclipse_lastImageSeed))
                        ? node._Eclipse_lastImageSeed : 0;
                    node._Eclipse_ImageSeedWidget.value = fallback;
                }
                // Reset prompt_seed to stable value when prompt_seed chip is deselected
                if (!selectedSet.has('prompt_seed') && node._Eclipse_PromptSeedWidget
                    && SPECIAL_SEEDS.includes(Number(node._Eclipse_PromptSeedWidget.value))) {
                    const fallback = (typeof node._Eclipse_lastPromptSeed === 'number'
                        && !SPECIAL_SEEDS.includes(node._Eclipse_lastPromptSeed))
                        ? node._Eclipse_lastPromptSeed : 0;
                    node._Eclipse_PromptSeedWidget.value = fallback;
                }
                vis.markUserDriven();
                updateFeatureVisibility(node, vis);
                if (autoFeaturesW) autoFeaturesW.value = (Array.isArray(featWidget.value) ? featWidget.value : []).join(',');
            };
            // Skip initial pass during workflow load — onConfigure runs it next.
            if (!isConfiguringGraph()) {
                requestAnimationFrame(() => updateFeatureVisibility(node, vis));
            }
            return ret;
        };
        nodeType.prototype._resolveSeed = function (statePrefix) {
            const widget = this[`_Eclipse_${statePrefix}Widget`];
            if (!widget) return 0;
            const input = Number(widget.value);
            const cachedInputKey = `_Eclipse_cached${statePrefix}Input`;
            const cachedResolvedKey = `_Eclipse_cached${statePrefix}Resolved`;
            const lastKey = `_Eclipse_last${statePrefix}`;
            if (this[cachedInputKey] === input && this[cachedResolvedKey] != null)
                return this[cachedResolvedKey];
            const resolved = resolveSeed(input, this[lastKey]);
            this[cachedInputKey] = input;
            this[cachedResolvedKey] = resolved;
            return resolved;
        };
        const origOnExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (data) {
            const ret = origOnExecuted ? origOnExecuted.apply(this, arguments) : void 0;
            if (data) {
                if (data.image_seed !== undefined) this._Eclipse_lastImageSeed = data.image_seed;
                if (data.prompt_seed !== undefined) this._Eclipse_lastPromptSeed = data.prompt_seed;
            }
            return ret;
        };
        const origOnConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (data) {
            const ret = origOnConfigure ? origOnConfigure.call(this, data) : void 0;
            const node = this;
            const vis = node._Eclipse_vis || createWidgetVisibilityManager(node);
            vis.clearCache();
            requestAnimationFrame(() => {
                updateFeatureVisibility(node, vis);
            });
            return ret;
        };
    },
    async setup() {
        onVueModeChange(() => {
            app.graph?.setDirtyCanvas?.(true, true);
        });
        const origGraphToPrompt = app.graphToPrompt;
        app.graphToPrompt = async function () {
            // Shared node list across all chained hooks — one graph walk per queue call
            const nodeFilter = n => n.type === NODE_NAME;
            enterGraphToPromptHook();
            for (const { node } of getGraphNodeList(app.graph)) {
                if (nodeFilter(node)) clearNodeQueuedSeed(node);
            }
            let result;
            try {
                result = await origGraphToPrompt.apply(this, arguments);
            } catch (e) {
                exitGraphToPromptHook();
                throw e;
            }
            for (const { node, outputKey } of getGraphNodeList(app.graph)) {
                if (!nodeFilter(node)) continue;
                if (node.mode === 2 || node.mode === 4) continue;
                if (!result.output?.[outputKey]) continue;
                const rawFeatures = result.output[outputKey].inputs?.features;
                if (rawFeatures != null) {
                    if (Array.isArray(rawFeatures)) {
                        result.output[outputKey].inputs.features = rawFeatures.filter((f) => !ALL_MODE_CHIPS.has(f));
                    } else if (typeof rawFeatures === 'object' && '__value__' in rawFeatures && Array.isArray(rawFeatures.__value__)) {
                        rawFeatures.__value__ = rawFeatures.__value__.filter((f) => !ALL_MODE_CHIPS.has(f));
                    }
                }
                if (node._Eclipse_chipWidget) {
                    const wfNode = findWorkflowNode(result.workflow, outputKey);
                    if (wfNode?.widgets_values) {
                        const featIdx = node.widgets.indexOf(node._Eclipse_chipWidget);
                        if (featIdx >= 0 && wfNode.widgets_values[featIdx] != null) {
                            const wfFeat = wfNode.widgets_values[featIdx];
                            if (Array.isArray(wfFeat)) {
                                wfNode.widgets_values[featIdx] = wfFeat.filter((f) => !ALL_MODE_CHIPS.has(f));
                            } else if (typeof wfFeat === 'string') {
                                wfNode.widgets_values[featIdx] = wfFeat.split(',').map(s => s.trim()).filter(f => f && !ALL_MODE_CHIPS.has(f)).join(',');
                            }
                        }
                    }
                }
                const seeds = [{
                    prefix: 'ImageSeed',
                    inputKey: 'image_seed',
                    label: '♻️ (Use Last Queued Image Seed)'
                }, {
                    prefix: 'PromptSeed',
                    inputKey: 'prompt_seed',
                    label: '♻️ (Use Last Queued Prompt Seed)'
                }, ];
                for (const {
                        prefix,
                        inputKey,
                        label
                    }
                    of seeds) {
                    const widget = node[`_Eclipse_${prefix}Widget`];
                    if (!widget) continue;
                    const resolved = node._resolveSeed(prefix);
                    storeQueuedSeed(node, resolved, prefix);
                    if (result.output[outputKey].inputs?.[inputKey] !== undefined) {
                        const current = result.output[outputKey].inputs[inputKey];
                        if (Number(current) !== Number(resolved))
                            result.output[outputKey].inputs[inputKey] = resolved;
                    }
                    if (inputKey === 'image_seed' && result.output[outputKey].inputs?.seed !== undefined) {
                        result.output[outputKey].inputs.seed = resolved;
                    }
                    const lastKey = `_Eclipse_last${prefix}`;
                    if (Number(node[lastKey]) !== Number(resolved)) {
                        node[lastKey] = resolved;
                    }
                    node[`_Eclipse_cached${prefix}Input`] = null;
                    node[`_Eclipse_cached${prefix}Resolved`] = null;
                    const btn = node[`_Eclipse_${prefix}Button`];
                    if (btn) {
                        const seedVal = widget.value;
                        if (SPECIAL_SEEDS.includes(seedVal)) {
                            btn.label = `♻️ ${resolved}`;
                            btn.disabled = false;
                        } else {
                            btn.label = label;
                            btn.disabled = true;
                        }
                        if (isVueMode()) notifyVue(node);
                    }
                    if (result.workflow) {
                        const wfNode = findWorkflowNode(result.workflow, outputKey);
                        if (wfNode?.widgets_values) {
                            const idx = node.widgets.indexOf(widget);
                            if (idx >= 0 && wfNode.widgets_values[idx] !== resolved)
                                wfNode.widgets_values[idx] = resolved;
                        }
                    }
                }
            }
            exitGraphToPromptHook();
            return result;
        };
    },
});
