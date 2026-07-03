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
    isVueMode,
} from './eclipse-widget-performance-utils.js';
import {
    fetchSharedModelFiles,
    fetchSharedTemplateList,
    broadcastTemplateListChanged,
    TEMPLATE_CHANGED_EVENT,
} from './eclipse-loader-shared.js';
const NODE_NAMES = ['Smart Loader [Eclipse]', 'Smart Loader v2 [Eclipse]'];
app.registerExtension({
    name: 'Eclipse.SmartLoader',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (!NODE_NAMES.includes(nodeData.name)) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            const vis = createWidgetVisibilityManager(node);
            let lastAction = 'None';
            let lastTemplateName = 'None';
            let isLoading = false;
            const refreshTemplateList = async () => {
                try {
                    const templates = await fetchSharedTemplateList();
                    if (templates) {
                        const templateW = node.widgets?.find((w) => w.name === 'template_name');
                        if (templateW && templateW.options && templateW.options.values) {
                            templateW.options.values = templates;
                            if (!templates.includes(templateW.value)) templateW.value = 'None';
                            canvasDirtyBatcher.markDirty(node, true, true);
                        }
                    }
                    return templates;
                } catch (err) {
                    console.error('Failed to refresh template list:', err);
                    return null;
                }
            }, refreshModelFiles = async () => {
                try {
                    const files = await fetchSharedModelFiles();
                    if (!files) return;
                    const updateCombo = (widgetName, newValues) => {
                        const w = node.widgets?.find((w) => w.name === widgetName);
                        if (w && w.options && w.options.values) {
                            const oldValues = w.options.values;
                            w.options.values = newValues;
                            if (!newValues.includes(w.value)) {
                                const normalized = w.value.replace(/\\/g, '/');
                                if (normalized !== w.value && newValues.includes(normalized)) {
                                    w.value = normalized;
                                } else {
                                    const bn = normalized.split('/').pop();
                                    const sm = bn ? newValues.find(v => v.endsWith('/' + bn) || v === bn) : null;
                                    sm ? (w.value = sm) : (w.value = newValues[0] || 'None');
                                }
                            }
                            newValues.filter((v) => !oldValues.includes(v)).length;
                        }
                    };
                    if (files.checkpoints) updateCombo('ckpt_name', files.checkpoints);
                    if (files.diffusion_models) {
                        updateCombo('unet_name', files.diffusion_models);
                        updateCombo('nunchaku_name', files.diffusion_models);
                        updateCombo('qwen_name', files.diffusion_models);
                    }
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
                    console.warn('[Smart Loader] Failed to refresh model file lists:', err);
                }
            }, handleTemplateAction = async () => {
                const action = getVal('template_action'),
                    templateName = getVal('template_name'),
                    newName = getVal('new_template_name');
                if ((await refreshTemplateList(), 'None' === action))
                    (setVal('template_name', 'None'), setVal('new_template_name', ''), setVal('model_type', 'Standard Checkpoint'), setVal('ckpt_name', 'None'), setVal('unet_name', 'None'), setVal('nunchaku_name', 'None'), setVal('qwen_name', 'None'), setVal('zimage_name', 'None'), setVal('gguf_name', 'None'), setVal('weight_dtype', 'default'), setVal('data_type', 'bfloat16'), setVal('cache_threshold', 0), setVal('attention', 'flash-attention2'), setVal('i2f_mode', 'enabled'), setVal('cpu_offload', 'auto'), setVal('num_blocks_on_gpu', 30), setVal('use_pin_memory', 'enable'), setVal('gguf_dequant_dtype', 'default'), setVal('gguf_patch_dtype', 'default'), setVal('gguf_patch_on_device', false), setVal('configure_clip', true), setVal('configure_vae', true), setVal('configure_model_only_lora', false), setVal('configure_model_sampling', false), setVal('configure_blockswap', false), setVal('blocks_to_swap', 10), setVal('offload_embeddings', false), setVal('sampling_method', 'None'), setVal('sampling_subtype', 'eps'), setVal('shift', 3), setVal('base_shift', 0.5), setVal('sampling_width', 1024), setVal('sampling_height', 1024), setVal('original_timesteps', 50), setVal('zsnr', false), setVal('sigma_max', 120), setVal('sigma_min', 0.002), setVal('clip_source', 'Baked'), setVal('clip_count', '1'), setVal('clip_name1', 'None'), setVal('clip_name2', 'None'), setVal('clip_name3', 'None'), setVal('clip_name4', 'None'), setVal('clip_type', 'flux'), setVal('enable_clip_layer', true), setVal('stop_at_clip_layer', -2), setVal('vae_source', 'Baked'), setVal('vae_name', 'None'), setVal('lora_count', '1'), setVal('lora_switch_1', false), setVal('lora_name_1', 'None'), setVal('lora_weight_1', 1), setVal('lora_switch_2', false), setVal('lora_name_2', 'None'), setVal('lora_weight_2', 1), setVal('lora_switch_3', false), setVal('lora_name_3', 'None'), setVal('lora_weight_3', 1), setVal('memory_cleanup', true), refreshVisibility(), console.log('[Smart Loader] ✓ All fields reset to defaults'));
                else if ('Load' === action && templateName && 'None' !== templateName) await loadTemplate(templateName);
                else if ('Save' === action && newName && newName.trim()) {
                    const saveName = newName.trim(),
                        config = (() => {
                            const cfg = {},
                                modelType = getVal('model_type'),
                                configClip = getVal('configure_clip'),
                                configVae = getVal('configure_vae'),
                                configLora = getVal('configure_model_only_lora'),
                                configSampling = getVal('configure_model_sampling');
                            cfg.model_type = modelType;
                            cfg.configure_clip = configClip;
                            cfg.configure_vae = configVae;
                            cfg.configure_model_only_lora = configLora;
                            cfg.configure_model_sampling = configSampling;
                            cfg.configure_blockswap = getVal('configure_blockswap');
                            cfg.blocks_to_swap = getVal('blocks_to_swap');
                            cfg.offload_embeddings = getVal('offload_embeddings');
                            if ('Standard Checkpoint' === modelType) {
                                const name = getVal('ckpt_name');
                                if (name && name !== 'None') cfg.ckpt_name = name;
                            } else if ('UNet Model' === modelType) {
                                const name = getVal('unet_name');
                                if (name && name !== 'None') cfg.unet_name = name;
                                cfg.weight_dtype = getVal('weight_dtype');
                            } else if ('Nunchaku Flux' === modelType) {
                                const name = getVal('nunchaku_name');
                                if (name && name !== 'None') cfg.nunchaku_name = name;
                                cfg.data_type = getVal('data_type');
                                cfg.cache_threshold = getVal('cache_threshold');
                                cfg.attention = getVal('attention');
                                cfg.i2f_mode = getVal('i2f_mode');
                                cfg.cpu_offload = getVal('cpu_offload');
                            } else if ('Nunchaku Qwen' === modelType) {
                                const name = getVal('qwen_name');
                                if (name && name !== 'None') cfg.qwen_name = name;
                                cfg.cpu_offload = getVal('cpu_offload');
                                cfg.num_blocks_on_gpu = getVal('num_blocks_on_gpu');
                                cfg.use_pin_memory = getVal('use_pin_memory');
                            } else if ('Nunchaku ZImage' === modelType) {
                                const name = getVal('zimage_name');
                                if (name && name !== 'None') cfg.zimage_name = name;
                                cfg.cpu_offload = getVal('cpu_offload');
                                cfg.num_blocks_on_gpu = getVal('num_blocks_on_gpu');
                                cfg.use_pin_memory = getVal('use_pin_memory');
                            } else if ('GGUF Model' === modelType) {
                                const name = getVal('gguf_name');
                                if (name && name !== 'None') cfg.gguf_name = name;
                                cfg.gguf_dequant_dtype = getVal('gguf_dequant_dtype');
                                cfg.gguf_patch_dtype = getVal('gguf_patch_dtype');
                                cfg.gguf_patch_on_device = getVal('gguf_patch_on_device');
                            }
                            if (configClip) {
                                const clipSrc = getVal('clip_source');
                                cfg.clip_source = clipSrc;
                                if ('Standard Checkpoint' === modelType) {
                                    cfg.enable_clip_layer = getVal('enable_clip_layer');
                                    cfg.stop_at_clip_layer = getVal('stop_at_clip_layer');
                                }
                                if ('External' === clipSrc) {
                                    cfg.clip_count = getVal('clip_count');
                                    cfg.clip_type = getVal('clip_type');
                                    for (let i = 1; i <= 4; i++) {
                                        const clipName = getVal(`clip_name${i}`);
                                        if (clipName && clipName !== 'None') cfg[`clip_name${i}`] = clipName;
                                    }
                                }
                            }
                            if (configVae) {
                                const vaeSrc = getVal('vae_source');
                                cfg.vae_source = vaeSrc;
                                if ('External' === vaeSrc) {
                                    const vaeName = getVal('vae_name');
                                    if (vaeName && vaeName !== 'None') cfg.vae_name = vaeName;
                                }
                            }
                            if (configLora) {
                                cfg.lora_count = getVal('lora_count');
                                for (let i = 1; i <= 3; i++) {
                                    cfg[`lora_switch_${i}`] = getVal(`lora_switch_${i}`);
                                    cfg[`lora_name_${i}`] = getVal(`lora_name_${i}`);
                                    cfg[`lora_weight_${i}`] = getVal(`lora_weight_${i}`);
                                }
                            }
                            if (configSampling) {
                                const method = getVal('sampling_method');
                                cfg.sampling_method = method;
                                cfg.shift = getVal('shift');
                                if (method === 'Flux' || method === 'LTXV') cfg.base_shift = getVal('base_shift');
                                if (method === 'Flux') {
                                    cfg.sampling_width = getVal('sampling_width');
                                    cfg.sampling_height = getVal('sampling_height');
                                } else if (method === 'LCM') {
                                    cfg.original_timesteps = getVal('original_timesteps');
                                    cfg.zsnr = getVal('zsnr');
                                } else if (method === 'ContinuousEDM') {
                                    cfg.sampling_subtype = getVal('sampling_subtype');
                                    cfg.sigma_max = getVal('sigma_max');
                                    cfg.sigma_min = getVal('sigma_min');
                                } else if (method === 'ContinuousV') {
                                    cfg.sigma_max = getVal('sigma_max');
                                    cfg.sigma_min = getVal('sigma_min');
                                }
                            }
                            return cfg;
                        })();
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
                            }),
                            result = await resp.json();
                        if (result.success) {
                            broadcastTemplateListChanged(await refreshTemplateList(), node.id);
                            setVal('template_action', 'Load');
                            setVal('template_name', saveName);
                            setVal('new_template_name', '');
                            refreshVisibility();
                        } else {
                            console.error(`[Smart Loader] Save failed: ${result.error}`);
                        }
                    } catch (err) {
                        console.error('[Smart Loader] Save request failed:', err);
                    }
                }
            }, handleDeleteTemplate = async () => {
                const templateName = getVal('template_name');
                if (!templateName || 'None' === templateName) return;
                try {
                    const resp = await api.fetchApi('/eclipse/loader_templates/delete', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({
                                name: templateName
                            }),
                        }),
                        result = await resp.json();
                    if (result.success) {
                        broadcastTemplateListChanged(await refreshTemplateList(), node.id);
                        setVal('template_name', 'None');
                        setVal('new_template_name', '');
                        setVal('model_type', 'Standard Checkpoint');
                        setVal('ckpt_name', 'None');
                        setVal('unet_name', 'None');
                        setVal('nunchaku_name', 'None');
                        setVal('qwen_name', 'None');
                        setVal('zimage_name', 'None');
                        setVal('gguf_name', 'None');
                        setVal('weight_dtype', 'default');
                        setVal('data_type', 'bfloat16');
                        setVal('cache_threshold', 0);
                        setVal('attention', 'flash-attention2');
                        setVal('i2f_mode', 'enabled');
                        setVal('cpu_offload', 'auto');
                        setVal('num_blocks_on_gpu', 30);
                        setVal('use_pin_memory', 'enable');
                        setVal('gguf_dequant_dtype', 'default');
                        setVal('gguf_patch_dtype', 'default');
                        setVal('gguf_patch_on_device', false);
                        setVal('configure_clip', true);
                        setVal('configure_vae', true);
                        setVal('configure_model_only_lora', false);
                        setVal('configure_model_sampling', false);
                        setVal('configure_blockswap', false);
                        setVal('blocks_to_swap', 10);
                        setVal('offload_embeddings', false);
                        setVal('sampling_method', 'None');
                        setVal('sampling_subtype', 'eps');
                        setVal('shift', 3);
                        setVal('base_shift', 0.5);
                        setVal('sampling_width', 1024);
                        setVal('sampling_height', 1024);
                        setVal('original_timesteps', 50);
                        setVal('zsnr', false);
                        setVal('sigma_max', 120);
                        setVal('sigma_min', 0.002);
                        setVal('clip_source', 'Baked');
                        setVal('clip_count', '1');
                        setVal('clip_name1', 'None');
                        setVal('clip_name2', 'None');
                        setVal('clip_name3', 'None');
                        setVal('clip_name4', 'None');
                        setVal('clip_type', 'flux');
                        setVal('enable_clip_layer', true);
                        setVal('stop_at_clip_layer', -2);
                        setVal('vae_source', 'Baked');
                        setVal('vae_name', 'None');
                        setVal('lora_count', '1');
                        setVal('lora_switch_1', false);
                        setVal('lora_name_1', 'None');
                        setVal('lora_weight_1', 1);
                        setVal('lora_switch_2', false);
                        setVal('lora_name_2', 'None');
                        setVal('lora_weight_2', 1);
                        setVal('lora_switch_3', false);
                        setVal('lora_name_3', 'None');
                        setVal('lora_weight_3', 1);
                        setVal('memory_cleanup', true);
                        refreshVisibility();
                        console.log('[Smart Loader] ✓ Template deleted, fields reset');
                    } else {
                        console.error(`[Smart Loader] Delete failed: ${result.error}`);
                    }
                } catch (err) {
                    console.error('[Smart Loader] Delete request failed:', err);
                }
            };
            let actionButton = null;
            const ACTION_LABELS = {
                    None: '🔄 Reset Template Fields',
                    Load: '🗑️ Delete Template',
                    Save: '💾 Save Template'
                },
                setVal = (name, value) => {
                    const w = node.widgets?.find((w) => w.name === name);
                    if (!w) return;
                    if ('toggle' === w.type || name.includes('_switch_') || name.startsWith('configure_') || name.includes('enable_')) {
                        const boolVal = Boolean(value);
                        if (isLoading || w.value !== boolVal) {
                            w.value = boolVal;
                            if (w.callback && !isLoading) w.callback(boolVal);
                        }
                    } else {
                        if ('string' == typeof value && w.options?.values) {
                            if (value.includes('\\')) {
                                const fwd = value.replace(/\\\\/g, '/');
                                if (w.options.values.includes(fwd)) value = fwd;
                            }
                            if (!w.options.values.includes(value)) {
                                const bn = String(value).replace(/\\/g, '/').split('/').pop();
                                if (bn) {
                                    const sm = w.options.values.find(v => v.endsWith('/' + bn) || v === bn);
                                    if (sm) value = sm;
                                }
                            }
                        }
                        if (w.value !== value) {
                            w.value = value;
                            if (w.callback && !isLoading) w.callback(value);
                        }
                    }
                },
                loadTemplate = async (templateName) => {
                    const templateData = await (async (name) => {
                        if (!name || 'None' === name) return null;
                        try {
                            const ts = new Date().getTime(),
                                resp = await fetch(`/eclipse/loader_templates/${name}.json?t=${ts}`, {
                                    cache: 'no-store'
                                });
                            if (resp.ok) return await resp.json();
                        } catch (err) {
                            console.error(`Failed to load template ${name}:`, err);
                        }
                        return null;
                    })(templateName);
                    if (templateData) {
                        isLoading = true;
                        try {
                            setVal('model_type', 'Standard Checkpoint');
                            setVal('ckpt_name', 'None');
                            setVal('unet_name', 'None');
                            setVal('nunchaku_name', 'None');
                            setVal('qwen_name', 'None');
                            setVal('gguf_name', 'None');
                            setVal('weight_dtype', 'default');
                            setVal('data_type', 'bfloat16');
                            setVal('cache_threshold', 0);
                            setVal('attention', 'flash-attention2');
                            setVal('i2f_mode', 'enabled');
                            setVal('cpu_offload', 'auto');
                            setVal('num_blocks_on_gpu', 30);
                            setVal('use_pin_memory', 'enable');
                            setVal('gguf_dequant_dtype', 'default');
                            setVal('gguf_patch_dtype', 'default');
                            setVal('gguf_patch_on_device', false);
                            setVal('configure_clip', true);
                            setVal('configure_vae', true);
                            setVal('configure_model_only_lora', false);
                            setVal('configure_model_sampling', false);
                            setVal('configure_blockswap', false);
                            setVal('blocks_to_swap', 10);
                            setVal('offload_embeddings', false);
                            setVal('sampling_method', 'None');
                            setVal('shift', 3);
                            setVal('base_shift', 0.5);
                            setVal('sampling_width', 1024);
                            setVal('sampling_height', 1024);
                            setVal('clip_source', 'Baked');
                            setVal('clip_count', '1');
                            setVal('clip_name1', 'None');
                            setVal('clip_name2', 'None');
                            setVal('clip_name3', 'None');
                            setVal('clip_name4', 'None');
                            setVal('clip_type', 'flux');
                            setVal('enable_clip_layer', true);
                            setVal('stop_at_clip_layer', -2);
                            setVal('vae_source', 'Baked');
                            setVal('vae_name', 'None');
                            setVal('lora_count', '1');
                            for (let i = 1; i <= 3; i++) {
                                setVal(`lora_switch_${i}`, false);
                                setVal(`lora_name_${i}`, 'None');
                                setVal(`lora_weight_${i}`, 1);
                            }
                            const t = templateData;
                            if (t.model_type !== undefined) setVal('model_type', t.model_type);
                            if (t.weight_dtype !== undefined) setVal('weight_dtype', t.weight_dtype);
                            if (t.model_type !== undefined) setVal('model_type', t.model_type);
                            if (t.weight_dtype !== undefined) setVal('weight_dtype', t.weight_dtype);
                            if (t.configure_clip !== undefined) setVal('configure_clip', t.configure_clip);
                            if (t.configure_vae !== undefined) setVal('configure_vae', t.configure_vae);
                            if (t.configure_model_only_lora !== undefined) setVal('configure_model_only_lora', t.configure_model_only_lora);
                            if (t.configure_model_sampling !== undefined) setVal('configure_model_sampling', t.configure_model_sampling);
                            if (t.configure_blockswap !== undefined) setVal('configure_blockswap', t.configure_blockswap);
                            if (t.blocks_to_swap !== undefined) setVal('blocks_to_swap', t.blocks_to_swap);
                            if (t.offload_embeddings !== undefined) setVal('offload_embeddings', t.offload_embeddings);
                            if (t.sampling_method !== undefined) setVal('sampling_method', t.sampling_method);
                            if (t.sampling_subtype !== undefined) setVal('sampling_subtype', t.sampling_subtype);
                            if (t.shift !== undefined) setVal('shift', t.shift);
                            if (t.base_shift !== undefined) setVal('base_shift', t.base_shift);
                            if (t.sampling_width !== undefined) setVal('sampling_width', t.sampling_width);
                            if (t.sampling_height !== undefined) setVal('sampling_height', t.sampling_height);
                            if (t.original_timesteps !== undefined) setVal('original_timesteps', t.original_timesteps);
                            if (t.zsnr !== undefined) setVal('zsnr', t.zsnr);
                            if (t.sigma_max !== undefined) setVal('sigma_max', t.sigma_max);
                            if (t.sigma_min !== undefined) setVal('sigma_min', t.sigma_min);
                            if (t.data_type !== undefined) setVal('data_type', t.data_type);
                            if (t.cache_threshold !== undefined) setVal('cache_threshold', t.cache_threshold);
                            if (t.attention !== undefined) setVal('attention', t.attention);
                            if (t.i2f_mode !== undefined) setVal('i2f_mode', t.i2f_mode);
                            if (t.cpu_offload !== undefined) setVal('cpu_offload', t.cpu_offload);
                            if (t.num_blocks_on_gpu !== undefined) setVal('num_blocks_on_gpu', t.num_blocks_on_gpu);
                            if (t.use_pin_memory !== undefined) setVal('use_pin_memory', t.use_pin_memory);
                            if (t.gguf_dequant_dtype !== undefined) setVal('gguf_dequant_dtype', t.gguf_dequant_dtype);
                            if (t.gguf_patch_dtype !== undefined) setVal('gguf_patch_dtype', t.gguf_patch_dtype);
                            if (t.gguf_patch_on_device !== undefined) setVal('gguf_patch_on_device', t.gguf_patch_on_device);
                            if (t.clip_source !== undefined) setVal('clip_source', t.clip_source);
                            if (t.clip_count !== undefined) setVal('clip_count', t.clip_count);
                            if (t.clip_name1 !== undefined) setVal('clip_name1', t.clip_name1);
                            if (t.clip_name2 !== undefined) setVal('clip_name2', t.clip_name2);
                            if (t.clip_name3 !== undefined) setVal('clip_name3', t.clip_name3);
                            if (t.clip_name4 !== undefined) setVal('clip_name4', t.clip_name4);
                            if (t.clip_type !== undefined) setVal('clip_type', t.clip_type);
                            if (t.enable_clip_layer !== undefined) setVal('enable_clip_layer', t.enable_clip_layer);
                            if (t.stop_at_clip_layer !== undefined) setVal('stop_at_clip_layer', t.stop_at_clip_layer);
                            if (t.vae_source !== undefined) setVal('vae_source', t.vae_source);
                            if (t.vae_name !== undefined) setVal('vae_name', t.vae_name);
                            if (t.lora_count !== undefined) setVal('lora_count', t.lora_count);
                            for (let i = 1; i <= 3; i++) {
                                if (t[`lora_switch_${i}`] !== undefined) setVal(`lora_switch_${i}`, t[`lora_switch_${i}`]);
                                if (t[`lora_name_${i}`] !== undefined) setVal(`lora_name_${i}`, t[`lora_name_${i}`]);
                                if (t[`lora_weight_${i}`] !== undefined) setVal(`lora_weight_${i}`, t[`lora_weight_${i}`]);
                            }
                            if (t.ckpt_name !== undefined) setVal('ckpt_name', t.ckpt_name);
                            if (t.unet_name !== undefined) setVal('unet_name', t.unet_name);
                            if (t.nunchaku_name !== undefined) setVal('nunchaku_name', t.nunchaku_name);
                            if (t.qwen_name !== undefined) setVal('qwen_name', t.qwen_name);
                            if (t.gguf_name !== undefined) setVal('gguf_name', t.gguf_name);
                        } finally {
                            isLoading = false;
                            debouncedRefresh();
                            canvasDirtyBatcher.markDirty(node, true, true);
                        }
                    }
                }, setVis = (widgetName, visible) => vis.setVisible(widgetName, visible), getVal = (widgetName) => vis.getValue(widgetName), clipOriginalValues = {}, modelOriginalValues = {}, refreshVisibility = () => {
                    if (-1 === node.id) return;
                    const templateAction = getVal('template_action'),
                        modelType = getVal('model_type'),
                        configClip = getVal('configure_clip'),
                        configVae = getVal('configure_vae'),
                        configLora = getVal('configure_model_only_lora'),
                        configSampling = getVal('configure_model_sampling'),
                        configBlockswap = getVal('configure_blockswap'),
                        samplingMethod = getVal('sampling_method'),
                        clipSource = getVal('clip_source'),
                        clipCount = parseInt(getVal('clip_count')) || 1,
                        vaeSource = getVal('vae_source'),
                        loraCount = parseInt(getVal('lora_count')) || 3,
                        isStdCheckpoint = 'Standard Checkpoint' === modelType,
                        isUNet = 'UNet Model' === modelType,
                        isNunchakuFlux = 'Nunchaku Flux' === modelType,
                        isNunchakuQwen = 'Nunchaku Qwen' === modelType,
                        isNunchakuZImage = 'Nunchaku ZImage' === modelType,
                        isGGUF = 'GGUF Model' === modelType,
                        isExternalClip = 'External' === clipSource,
                        isExternalVae = 'External' === vaeSource;
                    (() => {
                        const currentModelType = getVal('model_type'),
                            filterRules = {
                                ckpt_name: {
                                    show: 'Standard Checkpoint' === currentModelType,
                                    extensions: ['.safetensors', '.ckpt', '.pt', '.bin', '.sft'],
                                },
                                unet_name: {
                                    show: 'UNet Model' === currentModelType,
                                    extensions: ['.safetensors', '.pt', '.bin', '.sft'],
                                },
                                nunchaku_name: {
                                    show: 'Nunchaku Flux' === currentModelType,
                                    extensions: ['.safetensors', '.pt', '.bin', '.sft'],
                                },
                                qwen_name: {
                                    show: 'Nunchaku Qwen' === currentModelType,
                                    extensions: ['.safetensors', '.pt', '.bin', '.sft'],
                                },
                                zimage_name: {
                                    show: 'Nunchaku ZImage' === currentModelType,
                                    extensions: ['.safetensors', '.pt', '.bin', '.sft'],
                                },
                                gguf_name: {
                                    show: 'GGUF Model' === currentModelType,
                                    extensions: ['.gguf']
                                },
                            };
                        Object.entries(filterRules).forEach(([widgetName, rule]) => {
                            const w = node.widgets?.find((w) => w.name === widgetName);
                            if (!w || !w.options) return;
                            modelOriginalValues[widgetName] || (modelOriginalValues[widgetName] = [...w.options.values]);
                            const filtered = modelOriginalValues[widgetName].filter((val) => {
                                if ('None' === val) return true;
                                const lower = val.toLowerCase();
                                return rule.extensions.some((ext) => lower.endsWith(ext));
                            });
                            w.options.values = filtered;
                            if (!filtered.includes(w.value)) {
                                const fwd = w.value.replace(/\\/g, '/');
                                if (fwd !== w.value && filtered.includes(fwd)) {
                                    w.value = fwd;
                                } else {
                                    const bn = fwd.split('/').pop();
                                    const sm = bn ? filtered.find(v => v.endsWith('/' + bn) || v === bn) : null;
                                    sm ? (w.value = sm) : (w.value = 'None');
                                }
                            }
                        });
                    })();
                    ['clip_name1', 'clip_name2', 'clip_name3', 'clip_name4'].forEach((name) => {
                        const w = node.widgets?.find((w) => w.name === name);
                        if (w && w.options) {
                            clipOriginalValues[name] || (clipOriginalValues[name] = [...w.options.values]);
                            w.options.values = clipOriginalValues[name];
                        }
                    });
                    const isSave = 'Save' === templateAction,
                        isLoad = 'Load' === templateAction,
                        selectedTemplate = getVal('template_name');
                    setVis('template_name', isLoad);
                    setVis('new_template_name', isSave);
                    (() => {
                        const action = getVal('template_action'),
                            shouldShow = isLoad ? (selectedTemplate && 'None' !== selectedTemplate) : true;
                        const btnAction = isLoad ? handleDeleteTemplate : handleTemplateAction;
                        if (shouldShow && !actionButton) {
                            actionButton = node.addWidget('button', ACTION_LABELS[action] || action, null, btnAction);
                            actionButton.serialize = false;
                        } else if (shouldShow && actionButton) {
                            const label = ACTION_LABELS[action] || action;
                            if (actionButton.name !== label) {
                                actionButton.name = label;
                                if (isVueMode()) notifyVue(node);
                            }
                            actionButton.callback = btnAction;
                        } else if (!shouldShow && actionButton) {
                            const idx = node.widgets.indexOf(actionButton);
                            if (idx >= 0) node.widgets.splice(idx, 1);
                            actionButton = null;
                        }
                    })();
                    setVis('ckpt_name', isStdCheckpoint);
                    setVis('unet_name', isUNet);
                    setVis('nunchaku_name', isNunchakuFlux);
                    setVis('qwen_name', isNunchakuQwen);
                    setVis('zimage_name', isNunchakuZImage);
                    setVis('gguf_name', isGGUF);
                    setVis('weight_dtype', isUNet);
                    setVis('data_type', isNunchakuFlux);
                    setVis('cache_threshold', isNunchakuFlux);
                    setVis('attention', isNunchakuFlux);
                    setVis('i2f_mode', isNunchakuFlux);
                    setVis('cpu_offload', isNunchakuFlux || isNunchakuQwen || isNunchakuZImage);
                    setVis('num_blocks_on_gpu', isNunchakuQwen || isNunchakuZImage);
                    setVis('use_pin_memory', isNunchakuQwen || isNunchakuZImage);
                    setVis('gguf_dequant_dtype', isGGUF);
                    setVis('gguf_patch_dtype', isGGUF);
                    setVis('gguf_patch_on_device', isGGUF);
                    setVis('model_device', true);
                    setVis('clip_device', configClip);
                    setVis('vae_device', configVae);
                    setVis('clip_source', configClip);
                    setVis('clip_count', configClip && isExternalClip);
                    setVis('clip_name1', configClip && isExternalClip && clipCount >= 1);
                    setVis('clip_name2', configClip && isExternalClip && clipCount >= 2);
                    setVis('clip_name3', configClip && isExternalClip && clipCount >= 3);
                    setVis('clip_name4', configClip && isExternalClip && clipCount >= 4);
                    setVis('clip_type', configClip && isExternalClip);
                    setVis('enable_clip_layer', configClip && isStdCheckpoint);
                    setVis('stop_at_clip_layer', configClip && isStdCheckpoint);
                    setVis('vae_source', configVae);
                    setVis('vae_name', configVae && isExternalVae);
                    setVis('lora_count', configLora);
                    for (let i = 1; i <= 3; i++) {
                        const show = configLora && i <= loraCount;
                        setVis(`lora_switch_${i}`, show);
                        setVis(`lora_name_${i}`, show);
                        setVis(`lora_weight_${i}`, show);
                    }
                    setVis('sampling_method', configSampling);
                    const isFlux = 'Flux' === samplingMethod,
                        isLTXV = 'LTXV' === samplingMethod,
                        isLCM = 'LCM' === samplingMethod,
                        isContinuousEDM = 'ContinuousEDM' === samplingMethod,
                        isContinuous = isContinuousEDM || 'ContinuousV' === samplingMethod;
                    setVis('shift', configSampling && 'None' !== samplingMethod && !isLCM && !isContinuous);
                    setVis('base_shift', configSampling && (isFlux || isLTXV));
                    setVis('sampling_width', configSampling && isFlux);
                    setVis('sampling_height', configSampling && isFlux);
                    setVis('original_timesteps', configSampling && isLCM);
                    setVis('zsnr', configSampling && isLCM);
                    setVis('sampling_subtype', configSampling && isContinuousEDM);
                    setVis('sigma_max', configSampling && isContinuous);
                    setVis('sigma_min', configSampling && isContinuous);
                    setVis('configure_blockswap', !(isNunchakuFlux || isNunchakuQwen || isNunchakuZImage));
                    setVis('blocks_to_swap', configBlockswap && !(isNunchakuFlux || isNunchakuQwen || isNunchakuZImage));
                    setVis('offload_embeddings', configBlockswap && !(isNunchakuFlux || isNunchakuQwen || isNunchakuZImage));
                    smartResize(node);
                }, debouncedRefresh = debounce(refreshVisibility, 100);
            ['template_action', 'template_name', 'model_type', 'configure_clip', 'configure_vae', 'configure_model_only_lora', 'configure_model_sampling', 'configure_blockswap', 'sampling_method', 'clip_source', 'clip_count', 'vae_source', 'lora_count', ].forEach((widgetName) => {
                const w = node.widgets?.find((w) => w.name === widgetName);
                if (w) {
                    const origCallback = w.callback;
                    w.callback = function () {
                        if (origCallback) origCallback.apply(this, arguments);
                        vis.markUserDriven();
                        if ('template_action' === widgetName || 'template_name' === widgetName) {
                            const action = getVal('template_action'),
                                template = getVal('template_name');
                            if ('template_action' === widgetName && 'Save' === action && template && 'None' !== template) {
                                setVal('new_template_name', template);
                            }
                            if ('Load' === action && template && 'None' !== template) {
                                if (template !== lastTemplateName || action !== lastAction) {
                                    loadTemplate(template);
                                    lastTemplateName = template;
                                    lastAction = action;
                                }
                            }
                        }
                        if ('sampling_method' === widgetName) {
                            const method = getVal('sampling_method'),
                                currentShift = getVal('shift'),
                                shiftDefaults = {
                                    SD3: 3,
                                    AuraFlow: 1.73,
                                    Flux: 1.15,
                                    'Stable Cascade': 2,
                                    LTXV: 2.05
                                };
                            if ((Object.values(shiftDefaults).some((v) => Math.abs(currentShift - v) < 0.01) || 3 === currentShift) && shiftDefaults[method]) {
                                setVal('shift', shiftDefaults[method]);
                            }
                            if ('ContinuousEDM' === method) {
                                setVal('sigma_max', 120);
                                setVal('sigma_min', 0.002);
                            } else if ('ContinuousV' === method) {
                                setVal('sigma_max', 500);
                                setVal('sigma_min', 0.03);
                            }
                        }
                        debouncedRefresh();
                    };
                }
            });
            const onTemplateChangedEvent = (event) => {
                const {
                    templates,
                    sourceNodeId
                } = event.detail;
                if (sourceNodeId === node.id) return;
                if (!templates) return;
                const templateW = node.widgets?.find((w) => 'template_name' === w.name);
                if (templateW && templateW.options && templateW.options.values) {
                    templateW.options.values = templates;
                    if (!templates.includes(templateW.value)) templateW.value = 'None';
                    canvasDirtyBatcher.markDirty(node, true, true);
                }
            };
            document.addEventListener(TEMPLATE_CHANGED_EVENT, onTemplateChangedEvent);
            const origOnRemoved = node.onRemoved;
            node.onRemoved = function () {
                document.removeEventListener(TEMPLATE_CHANGED_EVENT, onTemplateChangedEvent);
                if (origOnRemoved) origOnRemoved.apply(this, arguments);
            };
            setTimeout(() => {
                if (!node._Eclipse_initialized) {
                    node._Eclipse_initialized = true;
                    refreshVisibility();
                    refreshTemplateList();
                    refreshModelFiles();
                }
            }, 0);
            node._Eclipse_refreshLists = async () => {
                await refreshTemplateList();
                await refreshModelFiles();
            };
            const origOnConfigure = node.onConfigure;
            return ((node.onConfigure = function (data) {
                if (origOnConfigure) origOnConfigure.apply(this, arguments);
                refreshModelFiles();
                setTimeout(() => {
                    const action = getVal('template_action'),
                        template = getVal('template_name');
                    if ('Load' === action && template && 'None' !== template) {
                        loadTemplate(template);
                    } else {
                        refreshVisibility();
                    }
                }, 100);
            }), ret);
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
