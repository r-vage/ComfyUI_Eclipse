import {
    app,
    api
} from './comfy/index.js';
import {
    debounce,
    canvasDirtyBatcher,
    notifyVue,
    smartResize,
    createWidgetVisibilityManager,
    onVueModeChange,
    isConfiguringGraph,
    isVueMode,
} from './eclipse-widget-performance-utils.js';
import {
    injectComboChipCSS,
    createComboChipWidget as _createComboChipWidget
} from './eclipse-combo-chip.js';
import {
    fetchSharedModelFiles,
    fetchSharedTemplateList,
    broadcastTemplateListChanged,
    TEMPLATE_CHANGED_EVENT,
} from './eclipse-loader-shared.js';
import { storeQueuedSeed, enterGraphToPromptHook, exitGraphToPromptHook, getGraphNodeList, clearNodeQueuedSeed, findWorkflowNode } from './eclipse-seed-utils.js';
const NODE_NAME = 'Smart Model Loader [Eclipse]';
const SPECIAL_SEEDS = [-1, -2, -3];
const FEATURE_OPTIONS = [
    { label: 'templates', tooltip: 'Show template management widgets (load/save/delete)' },
    { label: 'clip', tooltip: 'Show CLIP loader widgets (source, count, names, type, layer)' },
    { label: 'vae', tooltip: 'Show VAE loader widgets (source, name)' },
    { label: 'audio_vae', tooltip: 'Show LTXV/LTX2 audio VAE loader widgets (source, name)' },
    { label: 'latent', tooltip: 'Show latent widgets (resolution, width/height, batch_size)' },
    { label: 'sampler', tooltip: 'Show sampler widgets (sampler_name, scheduler, steps, cfg, flux_guidance)' },
    { label: 'lora', tooltip: 'Show LoRA stack widgets (count, switches, names, weights)' },
    { label: 'model_sampling', tooltip: 'Show model-sampling widgets (shift, base_shift, sigma range, etc.)' },
    { label: 'block_swap', tooltip: 'Show block-swap widgets (offload model blocks to CPU/RAM)' },
    { label: 'memory_cleanup', tooltip: 'Free VRAM before loading the model' },
    { label: 'seed', tooltip: 'Show the seed widgets' },
    { label: '🎲 random', tooltip: 'Seed: random each run' },
    { label: '⏫ increment', tooltip: 'Seed: +1 each run' },
    { label: '⏬ decrement', tooltip: 'Seed: -1 each run' },
];
const DEFAULT_FEATURES = ['clip', 'vae', 'memory_cleanup'];
injectComboChipCSS('smll');
const SEED_MODE_CHIPS = ['🎲 random', '⏫ increment', '⏬ decrement'];
const SEED_MODE_CHIP_TO_VAL = {
    '🎲 random': -1,
    '⏫ increment': -2,
    '⏬ decrement': -3,
};
const FEATURE_WIDGETS = {
    templates: ['template_action', 'template_name', 'new_template_name'],
    clip: ['clip_source', 'clip_count', 'clip_name1', 'clip_name2', 'clip_name3', 'clip_name4', 'clip_type', 'enable_clip_layer', 'stop_at_clip_layer'],
    vae: ['vae_source', 'vae_name'],
    audio_vae: ['audio_vae_source', 'audio_vae_name'],
    latent: ['resolution', 'width', 'height', 'batch_size'],
    sampler: ['sampler_name', 'scheduler', 'steps', 'cfg', 'flux_guidance'],
    lora: ['lora_count', 'lora_switch_1', 'lora_name_1', 'lora_weight_1', 'lora_switch_2', 'lora_name_2', 'lora_weight_2', 'lora_switch_3', 'lora_name_3', 'lora_weight_3'],
    model_sampling: ['sampling_method', 'sampling_subtype', 'shift', 'base_shift', 'sampling_width', 'sampling_height', 'original_timesteps', 'zsnr', 'sigma_max', 'sigma_min'],
    block_swap: ['blocks_to_swap', 'offload_embeddings'],
    memory_cleanup: [],
    seed: ['seed'],
};
const MODEL_TYPE_WIDGETS = ['ckpt_name', 'unet_name', 'nunchaku_name', 'qwen_name', 'zimage_name', 'gguf_name', 'weight_dtype', 'data_type', 'cache_threshold', 'attention', 'i2f_mode', 'cpu_offload', 'num_blocks_on_gpu', 'use_pin_memory', 'gguf_dequant_dtype', 'gguf_patch_dtype', 'gguf_patch_on_device'];
const TEMPLATE_BUTTON = '_btn_template_action';
const SEED_BUTTONS = ['_btn_last_seed'];
const ALL_FEATURE_CONTROLLED = Object.values(FEATURE_WIDGETS).flat();
const ALL_CONTROLLED = ALL_FEATURE_CONTROLLED.concat(MODEL_TYPE_WIDGETS, [TEMPLATE_BUTTON], SEED_BUTTONS);

function createComboChipWidget(node, savedValue, origIdx) {
    const w = _createComboChipWidget({
        node,
        options: FEATURE_OPTIONS,
        savedValue,
        origIdx,
        cssPrefix: 'smll',
        momentaryChips: SEED_MODE_CHIPS,
    });
    return w;
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
app.registerExtension({
    name: 'Eclipse.SmartModelLoaderLegacy',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : void 0;
            const node = this;
            const vis = createWidgetVisibilityManager(node);
            node._Eclipse_vis = vis;
            const autoFeaturesW = node.widgets?.find(w => w.name === 'features');
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
            fetch('/eclipse/config/all').then(r => r.json()).then(cfg => {
                if (cfg?.has_native_dynamic_vram && featWidget?.setDisabledChips) {
                    featWidget.setDisabledChips(new Set(['block_swap']));
                }
            }).catch(() => {});
            for (let i = node.widgets.length - 1; i >= 0; i--) {
                const wName = (node.widgets[i].name || '').toLowerCase();
                if (wName === 'control_after_generate') {
                    node.widgets.splice(i, 1);
                }
            }
            const seedWidget = node.widgets?.find(w => w.name === 'seed');
            if (seedWidget) {
                node._Eclipse_seedWidget = seedWidget;
                node._Eclipse_lastSeed = undefined;
                node._Eclipse_cachedSeedInput = null;
                node._Eclipse_cachedSeedResolved = null;
                const origSeedCb = seedWidget.callback;
                seedWidget.callback = (v) => {
                    node._Eclipse_cachedSeedInput = null;
                    node._Eclipse_cachedSeedResolved = null;
                    if (origSeedCb) origSeedCb.call(seedWidget, v);
                };
                const LAST_SEED_LABEL = '♻️ (Use Last Queued Seed)';
                const seedIdx = node.widgets.indexOf(seedWidget);
                const btnLastSeed = node.addWidget('button', '_btn_last_seed', '', () => {
                    const last = node._Eclipse_lastSeed;
                    if (last != null) {
                        seedWidget.value = last;
                        btnLastSeed.label = LAST_SEED_LABEL;
                        btnLastSeed.disabled = true;
                        if (isVueMode()) notifyVue(node);
                    }
                }, {
                    serialize: false
                });
                btnLastSeed.label = LAST_SEED_LABEL;
                btnLastSeed.disabled = true;
                node._Eclipse_lastSeedButton = btnLastSeed;
                const btnIdx = node.widgets.indexOf(btnLastSeed);
                if (btnIdx !== seedIdx + 1) {
                    node.widgets.splice(btnIdx, 1);
                    node.widgets.splice(seedIdx + 1, 0, btnLastSeed);
                }
            }
            let lastTemplateName = 'None';
            let lastTemplateAction = 'None';
            let isLoadingTemplate = false;
            let templateButton = null;
            const originalModelLists = {};
            const originalClipLists = {};
            const TEMPLATE_BUTTON_LABELS = {
                None: '🔄 Reset Template Fields',
                Load: '🗑️ Delete Template',
                Save: '💾 Save Template',
            };
            const gv = (name) => vis.getValue(name);
            const sv = (name, val) => {
                const w = node.widgets?.find(w => w.name === name);
                if (!w) return;
                if (w.type === 'toggle' || name.includes('_switch_') || name.startsWith('enable_') || name === 'gguf_patch_on_device' || name === 'offload_embeddings' || name === 'zsnr') {
                    const bval = Boolean(val);
                    if (isLoadingTemplate || w.value !== bval) {
                        w.value = bval;
                        if (w.callback && !isLoadingTemplate) w.callback(bval);
                    }
                } else {
                    if (typeof val === 'string' && w.options?.values) {
                        if (val.includes('\\')) {
                            const fwd = val.replace(/\\/g, '/');
                            if (w.options.values.includes(fwd)) val = fwd;
                        }
                        if (!w.options.values.includes(val)) {
                            const bn = String(val).replace(/\\/g, '/').split('/').pop();
                            if (bn) {
                                const match = w.options.values.find(v => v.endsWith('/' + bn) || v === bn);
                                if (match) val = match;
                            }
                        }
                    }
                    if (w.value !== val) {
                        w.value = val;
                        if (w.callback && !isLoadingTemplate) w.callback(val);
                    }
                }
            };
            const refreshTemplateList = async () => {
                try {
                    const templates = await fetchSharedTemplateList();
                    if (templates) {
                        const w = node.widgets?.find(w => w.name === 'template_name');
                        if (w?.options?.values) {
                            w.options.values = templates;
                            if (!templates.includes(w.value)) w.value = 'None';
                            canvasDirtyBatcher.markDirty(node, true, true);
                        }
                    }
                    return templates;
                } catch (e) {
                    return null;
                }
            };
            const refreshModelFiles = async () => {
                try {
                    const data = await fetchSharedModelFiles();
                    if (!data) return;
                    const updateList = (widgetName, newValues) => {
                        const w = node.widgets?.find(w => w.name === widgetName);
                        if (!w?.options?.values) return;
                        w.options.values = newValues;
                        if (!newValues.includes(w.value)) {
                            const fwd = (w.value != null ? String(w.value) : '').replace(/\\/g, '/');
                            if (fwd !== w.value && newValues.includes(fwd)) {
                                w.value = fwd;
                            } else {
                                const bn = fwd.split('/').pop();
                                const sm = bn ? newValues.find(v => v.endsWith('/' + bn) || v === bn) : null;
                                sm ? (w.value = sm) : (w.value = newValues[0] || 'None');
                            }
                        }
                    };
                    if (data.checkpoints) updateList('ckpt_name', data.checkpoints);
                    if (data.diffusion_models) {
                        updateList('unet_name', data.diffusion_models);
                        updateList('nunchaku_name', data.diffusion_models);
                        updateList('qwen_name', data.diffusion_models);
                    }
                    if (data.diffusion_models_gguf) updateList('gguf_name', data.diffusion_models_gguf);
                    if (data.vae) {
                        updateList('vae_name', data.vae);
                        updateList('audio_vae_name', ['None', ...data.vae]);
                    }
                    if (data.clip_combined) {
                        updateList('clip_name1', data.clip_combined);
                        updateList('clip_name2', data.clip_combined);
                        updateList('clip_name3', data.clip_combined);
                        updateList('clip_name4', data.clip_combined);
                    }
                    if (data.loras) {
                        updateList('lora_name_1', data.loras);
                        updateList('lora_name_2', data.loras);
                        updateList('lora_name_3', data.loras);
                    }
                    canvasDirtyBatcher.markDirty(node, true, true);
                } catch (e) {
                    console.warn('[Smart Model Loader] Failed to refresh model files:', e);
                }
            };
            const resetAllFields = () => {
                sv('model_type', 'Standard Checkpoint');
                sv('ckpt_name', 'None');
                sv('unet_name', 'None');
                sv('nunchaku_name', 'None');
                sv('qwen_name', 'None');
                sv('zimage_name', 'None');
                sv('gguf_name', 'None');
                sv('weight_dtype', 'default');
                sv('data_type', 'bfloat16');
                sv('cache_threshold', 0);
                sv('attention', 'flash-attention2');
                sv('i2f_mode', 'enabled');
                sv('cpu_offload', 'auto');
                sv('num_blocks_on_gpu', 30);
                sv('use_pin_memory', 'enable');
                sv('gguf_dequant_dtype', 'default');
                sv('gguf_patch_dtype', 'default');
                sv('gguf_patch_on_device', false);
                sv('blocks_to_swap', 10);
                sv('offload_embeddings', false);
                sv('sampling_method', 'None');
                sv('sampling_subtype', 'eps');
                sv('shift', 3);
                sv('base_shift', 0.5);
                sv('sampling_width', 1024);
                sv('sampling_height', 1024);
                sv('original_timesteps', 50);
                sv('zsnr', false);
                sv('sigma_max', 120);
                sv('sigma_min', 0.002);
                sv('clip_source', 'Baked');
                sv('clip_count', '1');
                sv('clip_name1', 'None');
                sv('clip_name2', 'None');
                sv('clip_name3', 'None');
                sv('clip_name4', 'None');
                sv('clip_type', 'flux');
                sv('enable_clip_layer', true);
                sv('stop_at_clip_layer', -2);
                sv('vae_source', 'Baked');
                sv('vae_name', 'None');
                sv('audio_vae_source', 'External');
                sv('audio_vae_name', 'None');
                sv('resolution', '1024x1024 (1:1 XL/SD3/Flux/HiDream)');
                sv('width', 1024);
                sv('height', 1024);
                sv('lora_count', '1');
                for (let i = 1; i <= 3; i++) {
                    sv(`lora_switch_${i}`, false);
                    sv(`lora_name_${i}`, 'None');
                    sv(`lora_weight_${i}`, 1);
                }
                sv('sampler_name', 'euler');
                sv('scheduler', 'normal');
                sv('steps', 20);
                sv('cfg', 8);
                sv('flux_guidance', 3.5);
                sv('batch_size', 1);
                sv('seed', 0);
            };
            const loadTemplateData = async (name) => {
                if (!name || name === 'None') return null;
                try {
                    const ts = Date.now();
                    const resp = await fetch(`/eclipse/loader_templates/${name}.json?t=${ts}`, {
                        cache: 'no-store'
                    });
                    if (resp.ok) return await resp.json();
                } catch (e) {
                    console.error(`Failed to load template ${name}:`, e);
                }
                return null;
            };
            const applyTemplate = async (name) => {
                const data = await loadTemplateData(name);
                if (!data) {
                    updateVisibility();
                    return;
                }
                const prevFeats = Array.isArray(featWidget.value) ? featWidget.value : [];
                const hadTemplates = prevFeats.includes('templates');
                const hadMemoryCleanup = prevFeats.includes('memory_cleanup');
                const hadSeed = prevFeats.includes('seed');
                isLoadingTemplate = true;
                try {
                    resetAllFields();
                    const templateFeatures = [];
                    if (data.configure_clip !== false) templateFeatures.push('clip');
                    if (data.configure_vae !== false) templateFeatures.push('vae');
                    if (data.configure_latent) templateFeatures.push('latent');
                    if (data.configure_sampler) templateFeatures.push('sampler');
                    if (data.configure_model_only_lora) templateFeatures.push('lora');
                    if (data.configure_model_sampling) templateFeatures.push('model_sampling');
                    if (data.configure_blockswap) templateFeatures.push('block_swap');
                    let newFeatures;
                    if (data.features && Array.isArray(data.features)) {
                        newFeatures = data.features.filter(f => f !== 'templates' && f !== 'memory_cleanup' && f !== 'seed');
                    } else {
                        newFeatures = templateFeatures;
                    }
                    if (hadTemplates && !newFeatures.includes('templates')) {
                        newFeatures.push('templates');
                    }
                    if (hadMemoryCleanup && !newFeatures.includes('memory_cleanup')) {
                        newFeatures.push('memory_cleanup');
                    }
                    if (hadSeed && !newFeatures.includes('seed')) {
                        newFeatures.push('seed');
                    }
                    featWidget.value = newFeatures;
                    const fields = ['model_type', 'weight_dtype', 'blocks_to_swap', 'offload_embeddings', 'sampling_method', 'sampling_subtype', 'shift', 'base_shift', 'sampling_width', 'sampling_height', 'original_timesteps', 'zsnr', 'sigma_max', 'sigma_min', 'data_type', 'cache_threshold', 'attention', 'i2f_mode', 'cpu_offload', 'num_blocks_on_gpu', 'use_pin_memory', 'gguf_dequant_dtype', 'gguf_patch_dtype', 'gguf_patch_on_device', 'clip_source', 'clip_count', 'clip_name1', 'clip_name2', 'clip_name3', 'clip_name4', 'clip_type', 'enable_clip_layer', 'stop_at_clip_layer', 'vae_source', 'vae_name', 'audio_vae_source', 'audio_vae_name', 'resolution', 'width', 'height', 'batch_size', 'lora_count', 'ckpt_name', 'unet_name', 'nunchaku_name', 'qwen_name', 'zimage_name', 'gguf_name', ];
                    for (const f of fields) {
                        if (data[f] !== undefined) sv(f, data[f]);
                    }
                    for (let i = 1; i <= 3; i++) {
                        if (data[`lora_switch_${i}`] !== undefined) sv(`lora_switch_${i}`, data[`lora_switch_${i}`]);
                        if (data[`lora_name_${i}`] !== undefined) sv(`lora_name_${i}`, data[`lora_name_${i}`]);
                        if (data[`lora_weight_${i}`] !== undefined) sv(`lora_weight_${i}`, data[`lora_weight_${i}`]);
                    }
                    if (data.sampler_name !== undefined) sv('sampler_name', data.sampler_name);
                    else if (data.sampler !== undefined) sv('sampler_name', data.sampler);
                    if (data.scheduler !== undefined) sv('scheduler', data.scheduler);
                    if (data.steps !== undefined) sv('steps', data.steps);
                    if (data.cfg !== undefined) sv('cfg', data.cfg);
                    if (data.flux_guidance !== undefined) sv('flux_guidance', data.flux_guidance);
                } finally {
                    isLoadingTemplate = false;
                    updateVisibility();
                    node.setDirtyCanvas(true, true);
                }
            };
            const handleTemplateAction = async () => {
                const action = gv('template_action');
                const tmplName = gv('template_name');
                const newName = gv('new_template_name');
                await refreshTemplateList();
                if (action === 'None') {
                    sv('template_name', 'None');
                    sv('new_template_name', '');
                    resetAllFields();
                    updateVisibility();
                } else if (action === 'Load' && tmplName && tmplName !== 'None') {
                    await applyTemplate(tmplName);
                } else if (action === 'Save' && newName && newName.trim()) {
                    const saveName = newName.trim();
                    const config = buildTemplateConfig();
                    try {
                        const resp = await api.fetchApi('/eclipse/loader_templates/save', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({
                                name: saveName,
                                config
                            }),
                        });
                        const result = await resp.json();
                        if (result.success) {
                            broadcastTemplateListChanged(await refreshTemplateList(), node.id);
                            sv('template_action', 'Load');
                            sv('template_name', saveName);
                            sv('new_template_name', '');
                            updateVisibility();
                        } else {
                            console.error(`[Smart Model Loader] Save failed: ${result.error}`);
                        }
                    } catch (e) {
                        console.error('[Smart Model Loader] Save request failed:', e);
                    }
                }
            };
            const handleTemplateDelete = async () => {
                const tmplName = gv('template_name');
                if (!tmplName || tmplName === 'None') return;
                try {
                    const resp = await api.fetchApi('/eclipse/loader_templates/delete', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            name: tmplName
                        }),
                    });
                    const result = await resp.json();
                    if (result.success) {
                        broadcastTemplateListChanged(await refreshTemplateList(), node.id);
                        sv('template_name', 'None');
                        sv('new_template_name', '');
                        resetAllFields();
                        updateVisibility();
                    } else {
                        console.error(`[Smart Model Loader] Delete failed: ${result.error}`);
                    }
                } catch (e) {
                    console.error('[Smart Model Loader] Delete request failed:', e);
                }
            };
            const buildTemplateConfig = () => {
                const cfg = {};
                const raw = vis.getValue('features');
                const feats = Array.isArray(raw) ? raw : [];
                cfg.features = feats.filter(f => f !== 'templates' && f !== 'memory_cleanup' && f !== 'seed');
                const mt = gv('model_type');
                cfg.model_type = mt;
                cfg.configure_clip = feats.includes('clip');
                cfg.configure_vae = feats.includes('vae');
                cfg.configure_latent = feats.includes('latent');
                cfg.configure_sampler = feats.includes('sampler');
                cfg.configure_model_only_lora = feats.includes('lora');
                cfg.configure_model_sampling = feats.includes('model_sampling');
                cfg.configure_blockswap = feats.includes('block_swap');
                cfg.blocks_to_swap = gv('blocks_to_swap');
                cfg.offload_embeddings = gv('offload_embeddings');
                if (mt === 'Standard Checkpoint') {
                    const v = gv('ckpt_name');
                    if (v && v !== 'None') cfg.ckpt_name = v;
                } else if (mt === 'UNet Model') {
                    const v = gv('unet_name');
                    if (v && v !== 'None') cfg.unet_name = v;
                    cfg.weight_dtype = gv('weight_dtype');
                } else if (mt === 'Nunchaku Flux') {
                    const v = gv('nunchaku_name');
                    if (v && v !== 'None') cfg.nunchaku_name = v;
                    cfg.data_type = gv('data_type');
                    cfg.cache_threshold = gv('cache_threshold');
                    cfg.attention = gv('attention');
                    cfg.i2f_mode = gv('i2f_mode');
                    cfg.cpu_offload = gv('cpu_offload');
                } else if (mt === 'Nunchaku Qwen') {
                    const v = gv('qwen_name');
                    if (v && v !== 'None') cfg.qwen_name = v;
                    cfg.cpu_offload = gv('cpu_offload');
                    cfg.num_blocks_on_gpu = gv('num_blocks_on_gpu');
                    cfg.use_pin_memory = gv('use_pin_memory');
                } else if (mt === 'Nunchaku ZImage') {
                    const v = gv('zimage_name');
                    if (v && v !== 'None') cfg.zimage_name = v;
                    cfg.cpu_offload = gv('cpu_offload');
                    cfg.num_blocks_on_gpu = gv('num_blocks_on_gpu');
                    cfg.use_pin_memory = gv('use_pin_memory');
                } else if (mt === 'GGUF Model') {
                    const v = gv('gguf_name');
                    if (v && v !== 'None') cfg.gguf_name = v;
                    cfg.gguf_dequant_dtype = gv('gguf_dequant_dtype');
                    cfg.gguf_patch_dtype = gv('gguf_patch_dtype');
                    cfg.gguf_patch_on_device = gv('gguf_patch_on_device');
                }
                if (feats.includes('clip')) {
                    const cs = gv('clip_source');
                    cfg.clip_source = cs;
                    if (mt === 'Standard Checkpoint') {
                        cfg.enable_clip_layer = gv('enable_clip_layer');
                        cfg.stop_at_clip_layer = gv('stop_at_clip_layer');
                    }
                    if (cs !== 'Baked') {
                        cfg.clip_count = gv('clip_count');
                        cfg.clip_type = gv('clip_type');
                        for (let i = 1; i <= 4; i++) {
                            const v = gv(`clip_name${i}`);
                            if (v && v !== 'None') cfg[`clip_name${i}`] = v;
                        }
                    }
                }
                if (feats.includes('vae')) {
                    const vs = gv('vae_source');
                    cfg.vae_source = vs;
                    if (vs === 'External') {
                        const v = gv('vae_name');
                        if (v && v !== 'None') cfg.vae_name = v;
                    }
                }
                if (feats.includes('audio_vae')) {
                    const avs = gv('audio_vae_source');
                    cfg.audio_vae_source = avs;
                    if (avs !== 'Baked') {
                        const v = gv('audio_vae_name');
                        if (v && v !== 'None') cfg.audio_vae_name = v;
                    }
                }
                if (feats.includes('latent')) {
                    const res = gv('resolution');
                    cfg.resolution = res;
                    if (res === 'Custom') {
                        cfg.width = gv('width');
                        cfg.height = gv('height');
                    }
                }
                if (feats.includes('sampler')) {
                    cfg.sampler_name = gv('sampler_name');
                    cfg.scheduler = gv('scheduler');
                    cfg.steps = gv('steps');
                    cfg.cfg = gv('cfg');
                    const ct = gv('clip_type');
                    if (mt === 'Nunchaku Flux' || (['flux', 'flux2'].includes(ct) && ['UNet Model', 'GGUF Model'].includes(mt))) {
                        cfg.flux_guidance = gv('flux_guidance');
                    }
                }
                if (feats.includes('lora')) {
                    cfg.lora_count = gv('lora_count');
                    for (let i = 1; i <= 3; i++) {
                        cfg[`lora_switch_${i}`] = gv(`lora_switch_${i}`);
                        cfg[`lora_name_${i}`] = gv(`lora_name_${i}`);
                        cfg[`lora_weight_${i}`] = gv(`lora_weight_${i}`);
                    }
                }
                if (feats.includes('model_sampling')) {
                    const sm = gv('sampling_method');
                    cfg.sampling_method = sm;
                    cfg.shift = gv('shift');
                    if (sm === 'Flux' || sm === 'LTXV') cfg.base_shift = gv('base_shift');
                    if (sm === 'Flux') {
                        cfg.sampling_width = gv('sampling_width');
                        cfg.sampling_height = gv('sampling_height');
                    } else if (sm === 'LCM') {
                        cfg.original_timesteps = gv('original_timesteps');
                        cfg.zsnr = gv('zsnr');
                    } else if (sm === 'ContinuousEDM') {
                        cfg.sampling_subtype = gv('sampling_subtype');
                        cfg.sigma_max = gv('sigma_max');
                        cfg.sigma_min = gv('sigma_min');
                    } else if (sm === 'ContinuousV') {
                        cfg.sigma_max = gv('sigma_max');
                        cfg.sigma_min = gv('sigma_min');
                    }
                }
                return cfg;
            };
            const updateVisibility = () => {
                const raw = vis.getValue('features');
                const feats = new Set(Array.isArray(raw) ? raw : []);
                const mt = gv('model_type');
                const isStd = mt === 'Standard Checkpoint';
                const isUnet = mt === 'UNet Model';
                const isNFlux = mt === 'Nunchaku Flux';
                const isNQwen = mt === 'Nunchaku Qwen';
                const isNZimg = mt === 'Nunchaku ZImage';
                const isGGUF = mt === 'GGUF Model';
                const d = (name, show) => vis.setVisible(name, show);
                d('ckpt_name', isStd);
                d('unet_name', isUnet);
                d('nunchaku_name', isNFlux);
                d('qwen_name', isNQwen);
                d('zimage_name', isNZimg);
                d('gguf_name', isGGUF);
                d('weight_dtype', isUnet);
                d('data_type', isNFlux);
                d('cache_threshold', isNFlux);
                d('attention', isNFlux);
                d('i2f_mode', isNFlux);
                d('cpu_offload', isNFlux || isNQwen || isNZimg);
                d('num_blocks_on_gpu', isNQwen || isNZimg);
                d('use_pin_memory', isNQwen || isNZimg);
                d('gguf_dequant_dtype', isGGUF);
                d('gguf_patch_dtype', isGGUF);
                d('gguf_patch_on_device', isGGUF);
                const modelFilter = {
                    ckpt_name: {
                        show: isStd,
                        exts: ['.safetensors', '.ckpt', '.pt', '.bin', '.sft']
                    },
                    unet_name: {
                        show: isUnet,
                        exts: ['.safetensors', '.pt', '.bin', '.sft']
                    },
                    nunchaku_name: {
                        show: isNFlux,
                        exts: ['.safetensors', '.pt', '.bin', '.sft']
                    },
                    qwen_name: {
                        show: isNQwen,
                        exts: ['.safetensors', '.pt', '.bin', '.sft']
                    },
                    zimage_name: {
                        show: isNZimg,
                        exts: ['.safetensors', '.pt', '.bin', '.sft']
                    },
                    gguf_name: {
                        show: isGGUF,
                        exts: ['.gguf']
                    },
                };
                for (const [wName, info] of Object.entries(modelFilter)) {
                    const w = node.widgets?.find(w => w.name === wName);
                    if (!w?.options) continue;
                    if (!originalModelLists[wName]) originalModelLists[wName] = [...w.options.values];
                    const filtered = originalModelLists[wName].filter(v => {
                        if (v === 'None') return true;
                        return info.exts.some(ext => v.toLowerCase().endsWith(ext));
                    });
                    w.options.values = filtered;
                    if (!filtered.includes(w.value)) {
                        const fwd = (w.value != null ? String(w.value) : '').replace(/\\/g, '/');
                        if (fwd !== w.value && filtered.includes(fwd)) w.value = fwd;
                        else {
                            const bn = fwd.split('/').pop();
                            const match = bn ? filtered.find(v => v.endsWith('/' + bn) || v === bn) : null;
                            match ? (w.value = match) : (w.value = 'None');
                        }
                    }
                }
                for (const cn of ['clip_name1', 'clip_name2', 'clip_name3', 'clip_name4']) {
                    const w = node.widgets?.find(w => w.name === cn);
                    if (w?.options) {
                        if (!originalClipLists[cn]) originalClipLists[cn] = [...w.options.values];
                        w.options.values = originalClipLists[cn];
                    }
                }
                const hasTemplates = feats.has('templates');
                const tmplAction = gv('template_action');
                const isSave = tmplAction === 'Save';
                const isLoad = tmplAction === 'Load';
                d('template_action', hasTemplates);
                d('template_name', hasTemplates && isLoad);
                d('new_template_name', hasTemplates && isSave);
                const showButton = hasTemplates && (isLoad ? (gv('template_name') && gv('template_name') !== 'None') : true);
                const btnCallback = isLoad ? handleTemplateDelete : handleTemplateAction;
                if (showButton && !templateButton) {
                    templateButton = node.addWidget('button', TEMPLATE_BUTTON_LABELS[tmplAction] || tmplAction, null, btnCallback);
                    templateButton.serialize = false;
                } else if (showButton && templateButton) {
                    const label = TEMPLATE_BUTTON_LABELS[tmplAction] || tmplAction;
                    if (templateButton.name !== label) {
                        templateButton.name = label;
                        if (isVueMode()) notifyVue(node);
                    }
                    templateButton.callback = btnCallback;
                } else if (!showButton && templateButton) {
                    const idx = node.widgets.indexOf(templateButton);
                    if (idx >= 0) node.widgets.splice(idx, 1);
                    templateButton = null;
                }
                const hasClip = feats.has('clip');
                const clipExternal = gv('clip_source') !== 'Baked';
                const clipCount = parseInt(gv('clip_count')) || 1;
                d('clip_source', hasClip);
                d('clip_count', hasClip && clipExternal);
                d('clip_name1', hasClip && clipExternal && clipCount >= 1);
                d('clip_name2', hasClip && clipExternal && clipCount >= 2);
                d('clip_name3', hasClip && clipExternal && clipCount >= 3);
                d('clip_name4', hasClip && clipExternal && clipCount >= 4);
                d('clip_type', hasClip && clipExternal);
                d('enable_clip_layer', hasClip && isStd);
                d('stop_at_clip_layer', hasClip && isStd);
                const hasVae = feats.has('vae');
                const vaeExternal = gv('vae_source') === 'External';
                d('vae_source', hasVae);
                d('vae_name', hasVae && vaeExternal);
                const hasAudioVae = feats.has('audio_vae');
                const audioVaeExternal = gv('audio_vae_source') !== 'Baked';
                d('audio_vae_source', hasAudioVae);
                d('audio_vae_name', hasAudioVae && audioVaeExternal);
                const hasLatent = feats.has('latent');
                const isCustomRes = gv('resolution') === 'Custom';
                d('resolution', hasLatent);
                d('width', hasLatent && isCustomRes);
                d('height', hasLatent && isCustomRes);
                d('batch_size', hasLatent);
                const hasSampler = feats.has('sampler');
                const clipType = gv('clip_type');
                const isFluxLike = isNFlux || (['flux', 'flux2'].includes(clipType) && (isUnet || isGGUF));
                d('sampler_name', hasSampler);
                d('scheduler', hasSampler);
                d('steps', hasSampler);
                d('cfg', hasSampler);
                d('flux_guidance', hasSampler && isFluxLike);
                const hasLora = feats.has('lora');
                const loraCount = parseInt(gv('lora_count')) || 3;
                d('lora_count', hasLora);
                for (let i = 1; i <= 3; i++) {
                    const show = hasLora && i <= loraCount;
                    const switchOn = show && gv(`lora_switch_${i}`);
                    d(`lora_switch_${i}`, show);
                    d(`lora_name_${i}`, switchOn);
                    d(`lora_weight_${i}`, switchOn);
                }
                const hasMS = feats.has('model_sampling');
                const sm = gv('sampling_method');
                const isFlux = sm === 'Flux';
                const isLTXV = sm === 'LTXV';
                const isLCM = sm === 'LCM';
                const isCEDM = sm === 'ContinuousEDM';
                const isCont = isCEDM || sm === 'ContinuousV';
                d('sampling_method', hasMS);
                d('shift', hasMS && sm !== 'None' && !isLCM && !isCont);
                d('base_shift', hasMS && (isFlux || isLTXV));
                d('sampling_width', hasMS && isFlux && !hasLatent);
                d('sampling_height', hasMS && isFlux && !hasLatent);
                d('original_timesteps', hasMS && isLCM);
                d('zsnr', hasMS && isLCM);
                d('sampling_subtype', hasMS && isCEDM);
                d('sigma_max', hasMS && isCont);
                d('sigma_min', hasMS && isCont);
                const hasBS = feats.has('block_swap');
                const isNunchaku = isNFlux || isNQwen || isNZimg;
                d('blocks_to_swap', hasBS && !isNunchaku);
                d('offload_embeddings', hasBS && !isNunchaku);
                const seedVisible = feats.has('seed');
                d('seed', seedVisible);
                for (const name of SEED_BUTTONS) d(name, seedVisible);
                smartResize(node);
            };
            const debouncedUpdate = debounce(updateVisibility, 100);
            const origFeatCallback = featWidget?.callback;
            if (featWidget) {
                featWidget.callback = function (value) {
                    if (node._Eclipse_updatingChips) return;
                    // Handle momentary chip clicks (seed mode actions)
                    if (value && typeof value === 'object' && value.momentary) {
                        const modeVal = SEED_MODE_CHIP_TO_VAL[value.momentary];
                        if (modeVal !== undefined && node._Eclipse_seedWidget) {
                            node._Eclipse_seedWidget.value = modeVal;
                            node._Eclipse_seedWidget.callback?.(modeVal);
                            // Auto-enable seed feature if not already visible
                            const feats = Array.isArray(featWidget.value) ? featWidget.value : [];
                            if (!feats.includes('seed')) {
                                node._Eclipse_updatingChips = true;
                                featWidget.value = [...feats, 'seed'];
                                node._Eclipse_updatingChips = false;
                                vis.markUserDriven();
                                updateVisibility();
                                if (autoFeaturesW) autoFeaturesW.value = (Array.isArray(featWidget.value) ? featWidget.value : []).join(',');
                            }
                        }
                        return;
                    }
                    origFeatCallback?.call(this, value);
                    const feats = Array.isArray(featWidget.value) ? featWidget.value : [];
                    if (!feats.includes('templates')) {
                        sv('template_action', 'None');
                        sv('template_name', 'None');
                        sv('new_template_name', '');
                        lastTemplateName = 'None';
                        lastTemplateAction = 'None';
                    }
                    // Reset seed to stable value when seed chip is deselected
                    if (!feats.includes('seed') && node._Eclipse_seedWidget
                        && SPECIAL_SEEDS.includes(Number(node._Eclipse_seedWidget.value))) {
                        const fallback = (typeof node._Eclipse_lastSeed === 'number'
                            && !SPECIAL_SEEDS.includes(node._Eclipse_lastSeed))
                            ? node._Eclipse_lastSeed : 0;
                        node._Eclipse_seedWidget.value = fallback;
                    }
                    vis.markUserDriven();
                    updateVisibility();
                    if (autoFeaturesW) autoFeaturesW.value = (Array.isArray(featWidget.value) ? featWidget.value : []).join(',');
                };
            }
            const triggerWidgets = ['template_action', 'template_name', 'model_type', 'sampling_method', 'clip_source', 'clip_count', 'clip_type', 'vae_source', 'audio_vae_source', 'resolution', 'lora_count', ];
            for (const wName of triggerWidgets) {
                const w = node.widgets?.find(w => w.name === wName);
                if (!w) continue;
                const origCb = w.callback;
                w.callback = function () {
                    if (origCb) origCb.apply(this, arguments);
                    vis.markUserDriven();
                    if (wName === 'template_action' || wName === 'template_name') {
                        const action = gv('template_action');
                        const tmpl = gv('template_name');
                        if (wName === 'template_action' && action === 'Save' && tmpl && tmpl !== 'None') {
                            sv('new_template_name', tmpl);
                        }
                        if (action === 'Load' && tmpl && tmpl !== 'None') {
                            if (tmpl !== lastTemplateName || action !== lastTemplateAction) {
                                applyTemplate(tmpl);
                                lastTemplateName = tmpl;
                                lastTemplateAction = action;
                            }
                        }
                    }
                    if (wName === 'sampling_method') {
                        const sm = gv('sampling_method');
                        const curShift = gv('shift');
                        const defaults = {
                            SD3: 3,
                            AuraFlow: 1.73,
                            Flux: 1.15,
                            'Stable Cascade': 2,
                            LTXV: 2.05
                        };
                        if ((Object.values(defaults).some(v => Math.abs(curShift - v) < 0.01) || curShift === 3) && defaults[sm]) {
                            sv('shift', defaults[sm]);
                        }
                        if (sm === 'ContinuousEDM') {
                            sv('sigma_max', 120);
                            sv('sigma_min', 0.002);
                        } else if (sm === 'ContinuousV') {
                            sv('sigma_max', 500);
                            sv('sigma_min', 0.03);
                        }
                    }
                    debouncedUpdate();
                };
            }
            const onTemplateChanged = (e) => {
                const {
                    templates,
                    sourceNodeId
                } = e.detail;
                if (sourceNodeId === node.id || !templates) return;
                const w = node.widgets?.find(w => w.name === 'template_name');
                if (w?.options?.values) {
                    w.options.values = templates;
                    if (!templates.includes(w.value)) w.value = 'None';
                    canvasDirtyBatcher.markDirty(node, true, true);
                }
            };
            document.addEventListener(TEMPLATE_CHANGED_EVENT, onTemplateChanged);
            const origOnRemoved = node.onRemoved;
            node.onRemoved = function () {
                document.removeEventListener(TEMPLATE_CHANGED_EVENT, onTemplateChanged);
                if (origOnRemoved) origOnRemoved.apply(this, arguments);
            };
            for (let i = 1; i <= 3; i++) {
                const sw = node.widgets?.find(w => w.name === `lora_switch_${i}`);
                if (sw) {
                    const origCb = sw.callback;
                    sw.callback = function () {
                        if (origCb) origCb.apply(this, arguments);
                        vis.markUserDriven();
                        debouncedUpdate();
                    };
                }
            }
            node._Eclipse_refreshLists = async () => {
                await refreshTemplateList();
                await refreshModelFiles();
            };
            // Skip initial updateVisibility during workflow load — onConfigure will run
            // it right after with the actual widget values. Fresh adds (no onConfigure)
            // still need this pass.
            if (!isConfiguringGraph()) {
                updateVisibility();
            }
            refreshTemplateList();
            refreshModelFiles();
            const origOnConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                if (origOnConfigure) origOnConfigure.apply(this, arguments);
                refreshModelFiles();
                const action = gv('template_action');
                const tmpl = gv('template_name');
                const feats = Array.isArray(featWidget?.value) ? featWidget.value : [];
                if (feats.includes('templates') && action === 'Load' && tmpl && tmpl !== 'None') {
                    applyTemplate(tmpl);
                } else {
                    updateVisibility();
                }
            };
            return ret;
        };
        nodeType.prototype._resolveSeed = function () {
            const widget = this._Eclipse_seedWidget;
            if (!widget) return 0;
            const input = Number(widget.value);
            if (this._Eclipse_cachedSeedInput === input && this._Eclipse_cachedSeedResolved != null)
                return this._Eclipse_cachedSeedResolved;
            const resolved = resolveSeed(input, this._Eclipse_lastSeed);
            this._Eclipse_cachedSeedInput = input;
            this._Eclipse_cachedSeedResolved = resolved;
            return resolved;
        };
        const origOnExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (data) {
            const ret = origOnExecuted ? origOnExecuted.apply(this, arguments) : void 0;
            if (data && data.seed !== undefined) {
                this._Eclipse_lastSeed = data.seed;
            }
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
                const resolved = node._resolveSeed();
                storeQueuedSeed(node, resolved);
                if (result.output[outputKey].inputs?.seed !== undefined) {
                    const current = result.output[outputKey].inputs.seed;
                    if (Number(current) !== Number(resolved))
                        result.output[outputKey].inputs.seed = resolved;
                }
                if (Number(node._Eclipse_lastSeed) !== Number(resolved)) {
                    node._Eclipse_lastSeed = resolved;
                }
                node._Eclipse_cachedSeedInput = null;
                node._Eclipse_cachedSeedResolved = null;
                const btn = node._Eclipse_lastSeedButton;
                if (btn) {
                    const seedVal = node._Eclipse_seedWidget.value;
                    if (SPECIAL_SEEDS.includes(seedVal)) {
                        btn.label = `♻️ ${resolved}`;
                        btn.disabled = false;
                    } else {
                        btn.label = '♻️ (Use Last Queued Seed)';
                        btn.disabled = true;
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
    async refreshComboInNodes() {
        const nodes = app.graph?._nodes || [];
        for (const node of nodes) {
            if (node.type === NODE_NAME && node._Eclipse_refreshLists) {
                node._Eclipse_refreshLists();
            }
        }
    },
});
