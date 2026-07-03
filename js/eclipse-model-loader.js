import {
    app
} from './comfy/index.js';
import {
    debounce,
    canvasDirtyBatcher,
    smartResize,
    createWidgetVisibilityManager,
    onVueModeChange,
    isConfiguringGraph,
} from './eclipse-widget-performance-utils.js';
import {
    injectComboChipCSS,
    createComboChipWidget as _createComboChipWidget
} from './eclipse-combo-chip.js';
import {
    fetchSharedModelFiles
} from './eclipse-loader-shared.js';
const NODE_CONFIGS = {
    'Model Loader [Eclipse]': {
        extName: 'Eclipse.ModelLoader',
        cssPrefix: 'ml'
    },
    'Model Loader Pipe [Eclipse]': {
        extName: 'Eclipse.ModelLoaderPipe',
        cssPrefix: 'mlp'
    },
};
const NODE_NAMES = Object.keys(NODE_CONFIGS);
const FEATURE_OPTIONS = [
    { label: 'lora', tooltip: 'Show LoRA stack widgets (count, switches, names, weights)' },
    { label: 'model_sampling', tooltip: 'Show model-sampling widgets (shift, base_shift, sigma range, etc.)' },
    { label: 'block_swap', tooltip: 'Show block-swap widgets (offload model blocks to CPU/RAM)' },
    { label: 'memory_cleanup', tooltip: 'Free VRAM before loading the model' },
];
const DEFAULT_FEATURES = ['memory_cleanup'];
for (const prefix of new Set(Object.values(NODE_CONFIGS).map(c => c.cssPrefix))) {
    injectComboChipCSS(prefix);
}
const FEATURE_WIDGETS = {
    lora: ['lora_count', 'lora_switch_1', 'lora_name_1', 'lora_weight_1', 'lora_switch_2', 'lora_name_2', 'lora_weight_2', 'lora_switch_3', 'lora_name_3', 'lora_weight_3'],
    model_sampling: ['sampling_method', 'sampling_subtype', 'shift', 'base_shift', 'sampling_width', 'sampling_height', 'original_timesteps', 'zsnr', 'sigma_max', 'sigma_min'],
    block_swap: ['blocks_to_swap', 'offload_embeddings'],
    memory_cleanup: [],
};
const MODEL_TYPE_WIDGETS = ['ckpt_name', 'unet_name', 'nunchaku_name', 'qwen_name', 'zimage_name', 'gguf_name', 'weight_dtype', 'data_type', 'cache_threshold', 'attention', 'i2f_mode', 'cpu_offload', 'num_blocks_on_gpu', 'use_pin_memory', 'gguf_dequant_dtype', 'gguf_patch_dtype', 'gguf_patch_on_device'];
const ALL_FEATURE_CONTROLLED = Object.values(FEATURE_WIDGETS).flat();
const ALL_CONTROLLED = ALL_FEATURE_CONTROLLED.concat(MODEL_TYPE_WIDGETS);

function createComboChipWidget(node, savedValue, origIdx, cssPrefix) {
    return _createComboChipWidget({
        node,
        options: FEATURE_OPTIONS,
        savedValue,
        origIdx,
        cssPrefix
    });
}
for (const [nodeName, cfg] of Object.entries(NODE_CONFIGS)) {
    app.registerExtension({
        name: cfg.extName,
        async beforeRegisterNodeDef(nodeType, nodeData, _app) {
            if (nodeData.name !== nodeName) return;
            const origOnNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : void 0;
                const node = this;
                const vis = createWidgetVisibilityManager(node);
                node._Eclipse_vis = vis;
                const g = (name) => vis.getValue(name);
                const d = (name, show) => vis.setVisible(name, show);
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
                featWidget = createComboChipWidget(node, savedValue, origIdx, cfg.cssPrefix);
                fetch('/eclipse/config/all').then(r => r.json()).then(eclipseCfg => {
                    if (eclipseCfg?.has_native_dynamic_vram && featWidget?.setDisabledChips) {
                        featWidget.setDisabledChips(new Set(['block_swap']));
                    }
                }).catch(() => {});
                const origModelLists = {};
                const updateVisibility = () => {
                    const raw = vis.getValue('features');
                    const feats = new Set(Array.isArray(raw) ? raw : []);
                    const modelType = g('model_type');
                    const enableClipLayer = g('enable_clip_layer');
                    const loraCount = parseInt(g('lora_count')) || 1;
                    const samplingMethod = g('sampling_method');
                    const isStandard = modelType === 'Standard Checkpoint';
                    const isUnet = modelType === 'UNet Model';
                    const isNunchakuFlux = modelType === 'Nunchaku Flux';
                    const isNunchakuQwen = modelType === 'Nunchaku Qwen';
                    const isNunchakuZImage = modelType === 'Nunchaku ZImage';
                    const isGGUF = modelType === 'GGUF Model';
                    const isNunchaku = isNunchakuFlux || isNunchakuQwen || isNunchakuZImage;
                    const hasLora = feats.has('lora');
                    const hasModelSampling = feats.has('model_sampling');
                    const hasBlockSwap = feats.has('block_swap') && !isNunchaku;
                    const modelFileRules = {
                        ckpt_name: {
                            show: isStandard,
                            extensions: ['.safetensors', '.ckpt', '.pt', '.bin', '.sft']
                        },
                        unet_name: {
                            show: isUnet,
                            extensions: ['.safetensors', '.pt', '.bin', '.sft']
                        },
                        nunchaku_name: {
                            show: isNunchakuFlux,
                            extensions: ['.safetensors', '.pt', '.bin', '.sft']
                        },
                        qwen_name: {
                            show: isNunchakuQwen,
                            extensions: ['.safetensors', '.pt', '.bin', '.sft']
                        },
                        zimage_name: {
                            show: isNunchakuZImage,
                            extensions: ['.safetensors', '.pt', '.bin', '.sft']
                        },
                        gguf_name: {
                            show: isGGUF,
                            extensions: ['.gguf']
                        },
                    };
                    Object.entries(modelFileRules).forEach(([wName, rule]) => {
                        const w = node.widgets?.find((x) => x.name === wName);
                        if (!w || !w.options) return;
                        origModelLists[wName] || (origModelLists[wName] = [...w.options.values]);
                        const filtered = origModelLists[wName].filter((v) => {
                            if (v === 'None') return true;
                            const low = v.toLowerCase();
                            return rule.extensions.some((ext) => low.endsWith(ext));
                        });
                        w.options.values = filtered;
                        if (!filtered.includes(w.value)) {
                            const norm = (w.value || '').replace(/\\/g, '/');
                            if (norm !== w.value && filtered.includes(norm)) w.value = norm;
                            else w.value = 'None';
                        }
                    });
                    d('ckpt_name', isStandard);
                    d('unet_name', isUnet);
                    d('nunchaku_name', isNunchakuFlux);
                    d('qwen_name', isNunchakuQwen);
                    d('zimage_name', isNunchakuZImage);
                    d('gguf_name', isGGUF);
                    d('weight_dtype', isUnet);
                    d('data_type', isNunchakuFlux);
                    d('cache_threshold', isNunchakuFlux);
                    d('attention', isNunchakuFlux);
                    d('i2f_mode', isNunchakuFlux);
                    d('cpu_offload', isNunchakuFlux || isNunchakuQwen || isNunchakuZImage);
                    d('num_blocks_on_gpu', isNunchakuQwen || isNunchakuZImage);
                    d('use_pin_memory', isNunchakuQwen || isNunchakuZImage);
                    d('gguf_dequant_dtype', isGGUF);
                    d('gguf_patch_dtype', isGGUF);
                    d('gguf_patch_on_device', isGGUF);
                    d('enable_clip_layer', isStandard);
                    d('stop_at_clip_layer', isStandard && enableClipLayer);
                    d('ltx_text_encoder', isStandard || isUnet);
                    d('lora_count', hasLora);
                    for (let i = 1; i <= 3; i++) {
                        const show = hasLora && i <= loraCount;
                        const switchOn = show && g(`lora_switch_${i}`);
                        d(`lora_switch_${i}`, show);
                        d(`lora_name_${i}`, switchOn);
                        d(`lora_weight_${i}`, switchOn);
                    }
                    d('sampling_method', hasModelSampling);
                    const isFlux = samplingMethod === 'Flux';
                    const isLTXV = samplingMethod === 'LTXV';
                    const isLCM = samplingMethod === 'LCM';
                    const isContinuousEDM = samplingMethod === 'ContinuousEDM';
                    const isContinuousV = samplingMethod === 'ContinuousV';
                    const isContinuous = isContinuousEDM || isContinuousV;
                    d('shift', hasModelSampling && samplingMethod !== 'None' && !isLCM && !isContinuous);
                    d('base_shift', hasModelSampling && (isFlux || isLTXV));
                    d('sampling_width', hasModelSampling && isFlux);
                    d('sampling_height', hasModelSampling && isFlux);
                    d('original_timesteps', hasModelSampling && isLCM);
                    d('zsnr', hasModelSampling && isLCM);
                    d('sampling_subtype', hasModelSampling && isContinuousEDM);
                    d('sigma_max', hasModelSampling && isContinuous);
                    d('sigma_min', hasModelSampling && isContinuous);
                    d('blocks_to_swap', hasBlockSwap);
                    d('offload_embeddings', hasBlockSwap);
                    smartResize(node);
                };
                const debouncedUpdate = debounce(updateVisibility, 100);
                const origFeatCallback = featWidget?.callback;
                if (featWidget) {
                    featWidget.callback = function (value) {
                        origFeatCallback?.call(this, value);
                        if (autoFeaturesW) autoFeaturesW.value = (Array.isArray(featWidget.value) ? featWidget.value : []).join(',');
                        vis.markUserDriven();
                        updateVisibility();
                    };
                }
                ['model_type', 'enable_clip_layer', 'lora_count', 'sampling_method', ].forEach((wName) => {
                    const w = node.widgets?.find((x) => x.name === wName);
                    if (w) {
                        const orig = w.callback;
                        w.callback = function () {
                            orig && orig.apply(this, arguments);
                            vis.markUserDriven();
                            debouncedUpdate();
                        };
                    }
                });
                for (let i = 1; i <= 3; i++) {
                    const sw = node.widgets?.find((x) => x.name === `lora_switch_${i}`);
                    if (sw) {
                        const orig = sw.callback;
                        sw.callback = function () {
                            orig && orig.apply(this, arguments);
                            vis.markUserDriven();
                            debouncedUpdate();
                        };
                    }
                }
                const refreshModelFiles = async () => {
                    try {
                        const data = await fetchSharedModelFiles();
                        if (!data) return;
                        const applyList = (wName, list) => {
                            const w = node.widgets?.find((x) => x.name === wName);
                            if (w && w.options && w.options.values) {
                                w.options.values = list;
                                if (!list.includes(w.value)) {
                                    const norm = (w.value || '').replace(/\\/g, '/');
                                    if (norm !== w.value && list.includes(norm)) w.value = norm;
                                    else w.value = list[0] || 'None';
                                }
                            }
                        };
                        if (data.checkpoints) applyList('ckpt_name', data.checkpoints);
                        if (data.diffusion_models) {
                            applyList('unet_name', data.diffusion_models);
                            applyList('nunchaku_name', data.diffusion_models);
                            applyList('qwen_name', data.diffusion_models);
                            applyList('zimage_name', data.diffusion_models);
                        }
                        if (data.diffusion_models_gguf) applyList('gguf_name', data.diffusion_models_gguf);
                        if (data.clip_combined) applyList('ltx_text_encoder', ['None', ...data.clip_combined]);
                        if (data.loras) {
                            applyList('lora_name_1', data.loras);
                            applyList('lora_name_2', data.loras);
                            applyList('lora_name_3', data.loras);
                        }
                        canvasDirtyBatcher.markDirty(node, true, true);
                    } catch (e) {
                        console.warn('[Model Loader] Failed to refresh model file lists:', e);
                    }
                };
                node._Eclipse_refreshLists = refreshModelFiles;
                if (!isConfiguringGraph()) {
                    updateVisibility();
                }
                refreshModelFiles();
                const origOnConfigure = node.onConfigure;
                node.onConfigure = function (config) {
                    origOnConfigure && origOnConfigure.apply(this, arguments);
                    refreshModelFiles();
                    updateVisibility();
                };
                return ret;
            };
        },
        async refreshComboInNodes() {
            const nodes = app.graph?._nodes || [];
            for (const node of nodes) {
                if (NODE_NAMES.includes(node.comfyClass) && node._Eclipse_refreshLists) {
                    node._Eclipse_refreshLists();
                }
            }
        },
        async setup() {
            onVueModeChange(() => {
                app.graph?.setDirtyCanvas?.(true, true);
            });
        },
    });
}
