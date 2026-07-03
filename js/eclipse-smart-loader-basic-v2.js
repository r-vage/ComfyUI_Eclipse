import {
    app,
    api
} from './comfy/index.js';
import {
    debounce,
    canvasDirtyBatcher,
    smartResize,
    createWidgetVisibilityManager,
} from './eclipse-widget-performance-utils.js';
import {
    fetchSharedModelFiles
} from './eclipse-loader-shared.js';
const NODE_NAMES = ['Smart Loader Basic [Eclipse]', 'Smart Loader Basic v2 [Eclipse]'];
app.registerExtension({
    name: 'Eclipse.SmartLoaderBasic',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (!NODE_NAMES.includes(nodeData.name)) return;
        const _isV2 = nodeData.name.includes(' v2 ');
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            const vis = createWidgetVisibilityManager(node);
            const getVal = (name) => vis.getValue(name);
            const setVis = (name, visible) => vis.setVisible(name, visible);
            const originalClipValues = {};
            const originalModelValues = {};
            const refreshVisibility = () => {
                if (node.id === -1) return;
                const modelType = getVal('model_type');
                const cfgClip = getVal('configure_clip');
                const cfgVae = getVal('configure_vae');
                const cfgLora = getVal('configure_model_only_lora');
                const cfgBlockswap = _isV2 ? getVal('configure_blockswap') : false;
                const clipSrc = getVal('clip_source');
                const clipCount = parseInt(getVal('clip_count')) || 1;
                const vaeSrc = getVal('vae_source');
                const loraCount = parseInt(getVal('lora_count')) || 3;
                const isCheckpoint = modelType === 'Standard Checkpoint';
                const isUnet = modelType === 'UNet Model';
                const isGguf = modelType === 'GGUF Model';
                const isExternalClip = clipSrc === 'External';
                const isExternalVae = vaeSrc === 'External';
                (() => {
                    const currentType = getVal('model_type');
                    const filterMap = {
                        ckpt_name: {
                            show: currentType === 'Standard Checkpoint',
                            extensions: ['.safetensors', '.ckpt', '.pt', '.bin', '.sft'],
                        },
                        unet_name: {
                            show: currentType === 'UNet Model',
                            extensions: ['.safetensors', '.pt', '.bin', '.sft'],
                        },
                        gguf_name: {
                            show: currentType === 'GGUF Model',
                            extensions: ['.gguf'],
                        },
                    };
                    for (const [widgetName, filter] of Object.entries(filterMap)) {
                        const w = node.widgets?.find((w) => w.name === widgetName);
                        if (!w || !w.options) continue;
                        if (!originalModelValues[widgetName]) originalModelValues[widgetName] = [...w.options.values];
                        const filtered = originalModelValues[widgetName].filter((val) => {
                            if (val === 'None') return true;
                            const lower = val.toLowerCase();
                            return filter.extensions.some((ext) => lower.endsWith(ext));
                        });
                        w.options.values = filtered;
                        if (!filtered.includes(w.value)) {
                            const normalized = w.value.replace(/\\/g, '/');
                            if (normalized !== w.value && filtered.includes(normalized)) {
                                w.value = normalized;
                            } else {
                                w.value = 'None';
                            }
                        }
                    }
                })();
                for (const clipName of ['clip_name1', 'clip_name2', 'clip_name3', 'clip_name4']) {
                    const w = node.widgets?.find((w) => w.name === clipName);
                    if (w && w.options) {
                        if (!originalClipValues[clipName]) originalClipValues[clipName] = [...w.options.values];
                        w.options.values = originalClipValues[clipName];
                    }
                }
                setVis('ckpt_name', isCheckpoint);
                setVis('unet_name', isUnet);
                setVis('gguf_name', isGguf);
                setVis('weight_dtype', isUnet);
                setVis('gguf_dequant_dtype', isGguf);
                setVis('gguf_patch_dtype', isGguf);
                setVis('gguf_patch_on_device', isGguf);
                if (!_isV2) {
                    setVis('model_device', true);
                    setVis('clip_device', cfgClip);
                    setVis('vae_device', cfgVae);
                }
                setVis('clip_source', cfgClip);
                setVis('clip_count', cfgClip && isExternalClip);
                setVis('clip_name1', cfgClip && isExternalClip && clipCount >= 1);
                setVis('clip_name2', cfgClip && isExternalClip && clipCount >= 2);
                setVis('clip_name3', cfgClip && isExternalClip && clipCount >= 3);
                setVis('clip_name4', cfgClip && isExternalClip && clipCount >= 4);
                setVis('clip_type', cfgClip && isExternalClip);
                setVis('enable_clip_layer', cfgClip && isCheckpoint);
                setVis('stop_at_clip_layer', cfgClip && isCheckpoint);
                setVis('vae_source', cfgVae);
                setVis('vae_name', cfgVae && isExternalVae);
                setVis('lora_count', cfgLora);
                for (let i = 1; i <= 3; i++) {
                    const show = cfgLora && i <= loraCount;
                    setVis(`lora_switch_${i}`, show);
                    setVis(`lora_name_${i}`, show);
                    setVis(`lora_weight_${i}`, show);
                }
                setVis('blocks_to_swap', cfgBlockswap);
                setVis('offload_embeddings', cfgBlockswap);
                smartResize(node);
            };
            const debouncedRefresh = debounce(refreshVisibility, 100);
            ['model_type', 'configure_clip', 'configure_vae', 'configure_model_only_lora', 'configure_blockswap', 'clip_source', 'clip_count', 'vae_source', 'lora_count', ].forEach((wName) => {
                const w = node.widgets?.find((w) => w.name === wName);
                if (w) {
                    const origCb = w.callback;
                    w.callback = function () {
                        if (origCb) origCb.apply(this, arguments);
                        vis.markUserDriven();
                        debouncedRefresh();
                    };
                }
            });
            const refreshModelLists = async () => {
                try {
                    const files = await fetchSharedModelFiles();
                    if (!files) return;
                    const updateCombo = (widgetName, newValues) => {
                        const w = node.widgets?.find((w) => w.name === widgetName);
                        if (!w || !w.options || !w.options.values) return;
                        w.options.values = newValues;
                        if (!newValues.includes(w.value)) {
                            const normalized = w.value.replace(/\\\\/g, '/');
                            if (normalized !== w.value && newValues.includes(normalized)) {
                                w.value = normalized;
                            } else {
                                w.value = newValues[0] || 'None';
                            }
                        }
                    };
                    if (files.checkpoints) updateCombo('ckpt_name', files.checkpoints);
                    if (files.diffusion_models) updateCombo('unet_name', files.diffusion_models);
                    if (files.diffusion_models_gguf) updateCombo('gguf_name', files.diffusion_models_gguf);
                    if (files.vae) updateCombo('vae_name', files.vae);
                    if (files.clip_combined) {
                        updateCombo('clip_name1', files.clip_combined);
                        updateCombo('clip_name2', files.clip_combined);
                        updateCombo('clip_name3', files.clip_combined);
                        updateCombo('clip_name4', files.clip_combined);
                    }
                    if (files.loras) {
                        updateCombo('lora_name_1', files.loras);
                        updateCombo('lora_name_2', files.loras);
                        updateCombo('lora_name_3', files.loras);
                    }
                    canvasDirtyBatcher.markDirty(node, true, true);
                } catch (err) {
                    console.warn('[Smart Loader Basic] Failed to refresh model file lists:', err);
                }
            };
            setTimeout(() => {
                if (!node._Eclipse_initialized) {
                    node._Eclipse_initialized = true;
                    refreshVisibility();
                    refreshModelLists();
                }
            }, 0);
            node._Eclipse_refreshLists = refreshModelLists;
            const origOnConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                if (origOnConfigure) origOnConfigure.apply(this, arguments);
                refreshModelLists();
                setTimeout(() => refreshVisibility(), 100);
            };
            return ret;
        };
    },
    async refreshComboInNodes() {
        const nodes = app.graph?._nodes || [];
        for (const node of nodes) {
            if (NODE_NAMES.includes(node.type) && node._Eclipse_refreshLists) {
                node._Eclipse_refreshLists();
            }
        }
    },
});
