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
import { storeQueuedSeed, enterGraphToPromptHook, exitGraphToPromptHook, getGraphNodeList, clearNodeQueuedSeed, findWorkflowNode } from './eclipse-seed-utils.js';
import {
    injectComboChipCSS,
    createComboChipWidget as _createComboChipWidget
} from './eclipse-combo-chip.js';

const NODE_NAME = 'Smart Sampler Settings [Eclipse]';
const LAST_SEED_BUTTON_LABEL = '🌘 (Use Last Queued Seed)';
const SPECIAL_SEEDS = [-1, -2, -3];

const FEATURE_OPTIONS = [
    { label: 'allow_overwrite', tooltip: 'Allow downstream nodes to override these settings' },
    { label: 'sampler', tooltip: 'Show the sampler_name selector' },
    { label: 'scheduler', tooltip: 'Show the scheduler selector' },
    { label: 'steps', tooltip: 'Show the sampling steps slider' },
    { label: 'cfg', tooltip: 'Show the CFG scale slider' },
    { label: 'guidance', tooltip: 'Show the guidance value (Flux/SD3)' },
    { label: 'denoise', tooltip: 'Show the denoise strength slider' },
    { label: 'seed', tooltip: 'Show the seed input and randomization buttons' },
    { label: 'noise_injection', tooltip: 'Show sigmas_denoise + noise_strength widgets' },
    { label: 'upscale', tooltip: 'Show upscale_value widget' },
];

const DEFAULT_FEATURES = ['sampler', 'scheduler', 'steps', 'cfg', 'denoise'];
injectComboChipCSS('');

const FEATURE_WIDGETS = {
    allow_overwrite: ['allow_overwrite'],
    sampler: ['sampler_name'],
    scheduler: ['scheduler'],
    steps: ['steps'],
    cfg: ['cfg'],
    guidance: ['guidance'],
    denoise: ['denoise'],
    seed: ['seed'],
    noise_injection: ['sigmas_denoise', 'noise_strength'],
    upscale: ['upscale_value'],
};

const SEED_BUTTONS = ['_btn_randomize', '_btn_new_fixed', '_btn_last_seed'];
const ALL_CONTROLLED = Object.values(FEATURE_WIDGETS).flat().concat(SEED_BUTTONS);

function createComboChipWidget(node, savedValue, origIdx) {
    return _createComboChipWidget({
        node,
        options: FEATURE_OPTIONS,
        savedValue,
        origIdx
    });
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
    const seedVisible = selectedSet.has('seed');
    for (const name of SEED_BUTTONS) vis.setVisible(name, seedVisible);
    smartResize(node);
}

app.registerExtension({
    name: 'Eclipse.SmartSamplerSettings',
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name !== NODE_NAME) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : void 0;
            const node = this;
            const vis = createWidgetVisibilityManager(node);
            node._Eclipse_vis = vis;

            // Pre-hide widgets not in DEFAULT_FEATURES
            vis.hideInitially([
                'allow_overwrite', 'guidance', 'seed',
                'sigmas_denoise', 'noise_strength',
                'upscale_value',
            ]);

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
            for (let i = node.widgets.length - 1; i >= 0; i--) {
                const wName = (node.widgets[i].name || '').toLowerCase();
                if (wName === 'control_after_generate') {
                    node.widgets.splice(i, 1);
                }
            }
            const seedWidget = node.widgets?.find((w) => w.name === 'seed');
            if (seedWidget) {
                node._Eclipse_seedWidget = seedWidget;
                node._Eclipse_lastSeed = undefined;
                node._Eclipse_randomMin = 0;
                node._Eclipse_randomMax = Number.MAX_SAFE_INTEGER;
                node._Eclipse_cachedInputSeed = null;
                node._Eclipse_cachedResolvedSeed = null;
                const origSeedCb = seedWidget.callback;
                seedWidget.callback = (v) => {
                    node._Eclipse_cachedInputSeed = null;
                    node._Eclipse_cachedResolvedSeed = null;
                    if (origSeedCb) origSeedCb.call(seedWidget, v);
                };
                const seedIdx = node.widgets.indexOf(seedWidget);
                const btnRandomize = node.addWidget('button', '_btn_randomize', '', () => {
                    seedWidget.value = -1;
                    seedWidget.callback && seedWidget.callback(-1);
                }, {
                    serialize: false
                });
                btnRandomize.label = '🌑 Randomize Each Time';
                const btnNewFixed = node.addWidget('button', '_btn_new_fixed', '', () => {
                    const s = node.generateRandomSeed();
                    seedWidget.value = s;
                    seedWidget.callback && seedWidget.callback(s);
                }, {
                    serialize: false
                });
                btnNewFixed.label = '🌕 New Fixed Random';
                const btnLastSeed = node.addWidget('button', '_btn_last_seed', '', () => {
                    if (node._Eclipse_lastSeed != null) {
                        seedWidget.value = node._Eclipse_lastSeed;
                        btnLastSeed.label = LAST_SEED_BUTTON_LABEL;
                        btnLastSeed.disabled = true;
                        if (isVueMode()) notifyVue(node);
                    }
                }, {
                    serialize: false
                });
                btnLastSeed.label = LAST_SEED_BUTTON_LABEL;
                btnLastSeed.disabled = true;
                node._Eclipse_lastSeedButton = btnLastSeed;
                const buttons = [btnRandomize, btnNewFixed, btnLastSeed];
                for (let i = buttons.length - 1; i >= 0; i--) {
                    const btn = buttons[i];
                    const idx = node.widgets.indexOf(btn);
                    if (idx !== seedIdx + 1) {
                        node.widgets.splice(idx, 1);
                        node.widgets.splice(seedIdx + 1, 0, btn);
                    }
                }
            }
            const origFeatCallback = featWidget.callback;
            featWidget.callback = function (value) {
                origFeatCallback?.call(this, value);
                // Reset seed to stable value when seed chip is deselected
                const feats = Array.isArray(featWidget.value) ? featWidget.value : [];
                if (!feats.includes('seed') && node._Eclipse_seedWidget
                    && SPECIAL_SEEDS.includes(Number(node._Eclipse_seedWidget.value))) {
                    const fallback = (typeof node._Eclipse_lastSeed === 'number'
                        && !SPECIAL_SEEDS.includes(node._Eclipse_lastSeed))
                        ? node._Eclipse_lastSeed : 0;
                    node._Eclipse_seedWidget.value = fallback;
                }
                if (autoFeaturesW) autoFeaturesW.value = (Array.isArray(featWidget.value) ? featWidget.value : []).join(',');
                vis.markUserDriven();
                updateFeatureVisibility(node, vis);
            };
            // Skip initial refresh during workflow load — onConfigure runs one right after.
            requestAnimationFrame(() => { if (!isConfiguringGraph()) updateFeatureVisibility(node, vis); });
            return ret;
        };
        nodeType.prototype.generateRandomSeed = function () {
            const step = this._Eclipse_seedWidget?.options?.step || 1;
            const min = this._Eclipse_randomMin || 0;
            const range = ((this._Eclipse_randomMax || 0xFFFFFFFF) - min) / (step / 10);
            let seed = Math.floor(Math.random() * range) * (step / 10) + min;
            if (SPECIAL_SEEDS.includes(seed)) seed = 0;
            return seed;
        };
        nodeType.prototype.getSeedToUse = function () {
            const input = Number(this._Eclipse_seedWidget.value);
            if (this._Eclipse_cachedInputSeed === input && this._Eclipse_cachedResolvedSeed != null)
                return this._Eclipse_cachedResolvedSeed;
            let resolved = null;
            if (SPECIAL_SEEDS.includes(input)) {
                if (typeof this._Eclipse_lastSeed === 'number' && !SPECIAL_SEEDS.includes(this._Eclipse_lastSeed)) {
                    if (input === -2) resolved = this._Eclipse_lastSeed + 1;
                    else if (input === -3) resolved = this._Eclipse_lastSeed - 1;
                }
                if (resolved == null || SPECIAL_SEEDS.includes(resolved))
                    resolved = this.generateRandomSeed();
            }
            const final = resolved != null ? resolved : input;
            this._Eclipse_cachedInputSeed = input;
            this._Eclipse_cachedResolvedSeed = final;
            return final;
        };
        const origOnExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (data) {
            const ret = origOnExecuted ? origOnExecuted.apply(this, arguments) : void 0;
            if (data && data.seed !== undefined) {
                this._Eclipse_lastSeed = data.seed;
            }
            return ret;
        };
        const origOnConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (data) {
            const ret = origOnConfigure ? origOnConfigure.call(this, data) : void 0;
            const node = this;
            const vis = node._Eclipse_vis || createWidgetVisibilityManager(node);
            vis.clearCache();
            requestAnimationFrame(() => updateFeatureVisibility(node, vis));
            return ret;
        };
    },
    async setup() {
        onVueModeChange(() => {
            app.graph?.setDirtyCanvas?.(true, true);
        });
        const origGraphToPrompt = app.graphToPrompt;
        app.graphToPrompt = async function () {
            const seedFilter = n => n.type === NODE_NAME && n._Eclipse_seedWidget;
            enterGraphToPromptHook();
            for (const { node } of getGraphNodeList(app.graph)) {
                if (seedFilter(node)) clearNodeQueuedSeed(node);
            }
            let result;
            try {
                result = await origGraphToPrompt.apply(this, arguments);
            } catch (e) {
                exitGraphToPromptHook();
                throw e;
            }
            for (const { node, outputKey } of getGraphNodeList(app.graph)) {
                if (!seedFilter(node)) continue;
                if (node.mode === 2 || node.mode === 4) continue;
                if (!result.output?.[outputKey]) continue;
                const resolved = node.getSeedToUse();
                storeQueuedSeed(node, resolved);
                if (result.output[outputKey].inputs?.seed !== undefined) {
                    const current = result.output[outputKey].inputs.seed;
                    if (Number(current) !== Number(resolved))
                        result.output[outputKey].inputs.seed = resolved;
                }
                if (Number(node._Eclipse_lastSeed) !== Number(resolved)) {
                    node._Eclipse_lastSeed = resolved;
                }
                node._Eclipse_cachedInputSeed = null;
                node._Eclipse_cachedResolvedSeed = null;
                if (node._Eclipse_lastSeedButton) {
                    const seedVal = node._Eclipse_seedWidget.value;
                    if (SPECIAL_SEEDS.includes(seedVal)) {
                        node._Eclipse_lastSeedButton.label = `🌘 ${resolved}`;
                        node._Eclipse_lastSeedButton.disabled = false;
                    } else {
                        node._Eclipse_lastSeedButton.label = LAST_SEED_BUTTON_LABEL;
                        node._Eclipse_lastSeedButton.disabled = true;
                    }
                    if (isVueMode()) notifyVue(node);
                }
                if (result.workflow) {
                    const wfNode = findWorkflowNode(result.workflow, outputKey);
                    if (wfNode?.widgets_values) {
                        const idx = node.widgets.indexOf(node._Eclipse_seedWidget);
                        if (idx >= 0 && wfNode.widgets_values[idx] !== resolved)
                            wfNode.widgets_values[idx] = resolved;
                    }
                }
            }
            exitGraphToPromptHook();
            return result;
        };
    },
});
