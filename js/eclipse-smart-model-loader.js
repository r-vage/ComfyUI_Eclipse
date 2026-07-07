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
    { label: 'templates', tooltip: 'Toggle visibility of preset templates to quickly load, save, or delete entire node configurations' },
    { label: 'clip', tooltip: 'Toggle visibility of text encoder / CLIP loader settings (CLIP source, count, models, architecture type, layer skip)' },
    { label: 'vae', tooltip: 'Toggle visibility of VAE wrapper settings (Baked checkpoint vs External VAE files)' },
    { label: 'audio_vae', tooltip: 'Toggle visibility of audio decoder/VAE parameters (useful for LTXV/LTX2 video generation)' },
    { label: 'latent', tooltip: 'Toggle visibility of empty latent resolution presets, custom sizing, and batch size controls' },
    { label: 'sampler', tooltip: 'Toggle visibility of ComfyUI KSampler algorithms, schedulers, steps, CFG, and Flux guidance scales' },
    { label: 'lora', tooltip: 'Toggle visibility of LoRA slots (enable switches, files, and weights)' },
    { label: 'model_sampling', tooltip: 'Toggle visibility of model-level scheduling curves (Universal shifts, Flux base shifts, target dimensions)' },
    { label: 'block_swap', tooltip: 'Toggle visibility of block-swapping memory managers (offload transformer layers to CPU RAM to save VRAM)' },
    { label: 'memory_cleanup', tooltip: 'VRAM garbage collection — clear VRAM cache and run Python garbage collection before model loading' },
    { label: 'integrity', tooltip: 'Toggle visibility of file verification methods and CivitAI AIR automatic downloads' },
    { label: 'seed', tooltip: 'Toggle visibility of seed controls' },
];
const DEFAULT_FEATURES = ['clip', 'vae', 'memory_cleanup'];
injectComboChipCSS('sml');
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
    integrity: ['verify_file', 'expected_hashes', 'download_locators', 'download_target_role', 'air_or_hash'],
    seed: ['seed'],
};
const MODEL_TYPE_WIDGETS = ['ckpt_name', 'unet_name', 'nunchaku_name', 'qwen_name', 'zimage_name', 'gguf_name', 'weight_dtype', 'data_type', 'cache_threshold', 'attention', 'i2f_mode', 'cpu_offload', 'num_blocks_on_gpu', 'use_pin_memory', 'gguf_dequant_dtype', 'gguf_patch_dtype', 'gguf_patch_on_device'];
const TEMPLATE_BUTTON = '_btn_template_action';
const DOWNLOAD_BUTTON = '_btn_civitai_download';
const SEED_BUTTONS = ['_btn_randomize', '_btn_new_random', '_btn_last_seed'];
const ALL_FEATURE_CONTROLLED = Object.values(FEATURE_WIDGETS).flat();
const ALL_CONTROLLED = ALL_FEATURE_CONTROLLED.concat(MODEL_TYPE_WIDGETS, [TEMPLATE_BUTTON, DOWNLOAD_BUTTON], SEED_BUTTONS);
// UI-only suffix appended to file widget values when the file is missing on disk.
const MISSING_SUFFIX = ' (missing)';
const stripMissing = (v) => (typeof v === 'string' && v.endsWith(MISSING_SUFFIX)) ? v.slice(0, -MISSING_SUFFIX.length) : v;

function createComboChipWidget(node, savedValue, origIdx) {
    const w = _createComboChipWidget({
        node,
        options: FEATURE_OPTIONS,
        savedValue,
        origIdx,
        cssPrefix: 'sml',
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
    name: 'Eclipse.SmartModelLoader',
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
            }).catch(() => { });
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
                const LAST_SEED_LABEL = '🌘 (Use Last Queued Seed)';
                const seedIdx = node.widgets.indexOf(seedWidget);
                // 🌑 Randomize Each Time — sets seed to special -1 (random per run)
                const btnRandomize = node.addWidget('button', '_btn_randomize', '', () => {
                    seedWidget.value = -1;
                    seedWidget.callback?.(-1);
                    if (isVueMode()) notifyVue(node);
                }, {
                    serialize: false
                });
                btnRandomize.label = '🌑 Randomize Each Time';
                // 🌕 New Fixed Random — picks a new concrete random seed now
                const btnNewRandom = node.addWidget('button', '_btn_new_random', '', () => {
                    const newSeed = generateRandomSeed();
                    seedWidget.value = newSeed;
                    seedWidget.callback?.(newSeed);
                    if (isVueMode()) notifyVue(node);
                }, {
                    serialize: false
                });
                btnNewRandom.label = '🌕 New Fixed Random';
                // 🌘 Use Last Queued Seed
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
                // Place the three buttons immediately after the seed widget,
                // ordered: seed → randomize → new_random → last_seed.
                for (const btn of [btnLastSeed, btnNewRandom, btnRandomize]) {
                    const bi = node.widgets.indexOf(btn);
                    if (bi >= 0) node.widgets.splice(bi, 1);
                    node.widgets.splice(seedIdx + 1, 0, btn);
                }
            }
            let lastTemplateName = 'None';
            let lastTemplateAction = 'None';
            let isLoadingTemplate = false;
            let templateButton = null;
            let downloadButton = null;
            const originalModelLists = {};
            const originalClipLists = {};
            const TEMPLATE_BUTTON_LABELS = {
                None: '🔄 Reset Template Fields',
                Load: '🗑️ Delete Template',
                Save: '💾 Save Template',
            };
            const gv = (name) => vis.getValue(name);
            const PENDING_FILE_WIDGETS = new Set([
                'ckpt_name', 'unet_name', 'nunchaku_name', 'qwen_name', 'zimage_name', 'gguf_name',
                'clip_name1', 'clip_name2', 'clip_name3', 'clip_name4',
                'vae_name', 'audio_vae_name',
                'lora_name_1', 'lora_name_2', 'lora_name_3',
            ]);
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
                            // While loading template configs, keep unresolved filenames selectable
                            // so they can be downloaded later instead of getting reset to None.
                            if (isLoadingTemplate && PENDING_FILE_WIDGETS.has(name) && val && val !== 'None') {
                                w.options.values = [...w.options.values, val];
                                // Remember the basename so updateVisibility's disk filter doesn't
                                // wipe it (old templates have no expected_hashes to anchor it).
                                const pbn = String(val).replace(/\\/g, '/').split('/').pop();
                                if (pbn) {
                                    if (!(node._Eclipse_pendingMissing instanceof Set)) node._Eclipse_pendingMissing = new Set();
                                    node._Eclipse_pendingMissing.add(pbn);
                                }
                            }
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
            const parseExpectedHashes = () => {
                const raw = gv('expected_hashes');
                if (raw && typeof raw === 'object') {
                    return raw;
                }
                if (typeof raw === 'string' && raw.trim()) {
                    try {
                        const parsed = JSON.parse(raw);
                        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                            return parsed;
                        }
                    } catch (_e) {
                        return {};
                    }
                }
                return {};
            };
            const setExpectedHashes = (obj) => {
                sv('expected_hashes', JSON.stringify(obj || {}));
            };
            const parseDownloadLocators = () => {
                const raw = gv('download_locators');
                if (Array.isArray(raw)) return raw;
                if (typeof raw === 'string' && raw.trim()) {
                    try {
                        const parsed = JSON.parse(raw);
                        if (Array.isArray(parsed)) return parsed;
                    } catch (_e) {
                        return [];
                    }
                }
                return [];
            };
            const setDownloadLocators = (arr) => {
                sv('download_locators', JSON.stringify(Array.isArray(arr) ? arr : []));
            };
            const toBaseName = (value) => {
                if (!value || value === 'None') return '';
                const norm = stripMissing(String(value)).replace(/\\/g, '/');
                return norm.split('/').pop() || norm;
            };
            const collectExpectedTargets = () => {
                const targets = new Set();
                const mt = gv('model_type');

                const addFromWidget = (name) => {
                    const bn = toBaseName(gv(name));
                    if (bn) targets.add(bn);
                };

                if (mt === 'Standard Checkpoint') addFromWidget('ckpt_name');
                else if (mt === 'UNet Model') addFromWidget('unet_name');
                else if (mt === 'Nunchaku Flux') addFromWidget('nunchaku_name');
                else if (mt === 'Nunchaku Qwen') addFromWidget('qwen_name');
                else if (mt === 'Nunchaku ZImage') addFromWidget('zimage_name');
                else if (mt === 'GGUF Model') addFromWidget('gguf_name');

                return targets;
            };

            const getActiveModelTarget = () => {
                const mt = gv('model_type');
                let raw = '';
                if (mt === 'Standard Checkpoint') raw = gv('ckpt_name');
                else if (mt === 'UNet Model') raw = gv('unet_name');
                else if (mt === 'Nunchaku Flux') raw = gv('nunchaku_name');
                else if (mt === 'Nunchaku Qwen') raw = gv('qwen_name');
                else if (mt === 'Nunchaku ZImage') raw = gv('zimage_name');
                else if (mt === 'GGUF Model') raw = gv('gguf_name');

                if (!raw || raw === 'None') return '';
                return stripMissing(String(raw)).replace(/\\/g, '/');
            };

            // Disk-free missing detection: an active file selection whose basename is not in the
            // server-known file set (populated by refreshModelFiles). Returns [] until lists load.
            const getMissingActiveFiles = () => {
                const known = node._Eclipse_knownFiles;
                if (!(known instanceof Set) || known.size === 0) return [];
                const missing = [];
                for (const bn of collectExpectedTargets()) {
                    if (bn && bn !== 'None' && !known.has(bn)) missing.push(bn);
                }
                return missing;
            };
            // Single AIR/SHA value field (air_or_hash) drives both modes:
            //  - model is active  → annotate that file (expected_hashes[file])
            //  - model is empty   → locator-only download (download_target_role + value)
            let _expectedEditorSyncing = false;
            const loadEditorValueForTarget = () => {
                // When the selected target changes, show that file's stored value (or clear).
                const target = getActiveModelTarget();
                _expectedEditorSyncing = true;
                try {
                    if (!target) {
                        const locators = parseDownloadLocators();
                        const currentRole = (gv('download_target_role') || '').trim();
                        const currentValue = (gv('air_or_hash') || '').trim();

                        if (locators && locators.length > 0 && (!currentRole || !currentValue)) {
                            const first = locators[0];
                            sv('download_target_role', currentRole || first.target_role || '');
                            sv('air_or_hash', currentValue || first.air || first.sha256 || '');
                        } else if (!currentValue) {
                            sv('air_or_hash', '');
                        }
                        return;
                    }
                    const map = parseExpectedHashes();
                    const bn = toBaseName(target);
                    const entry = (map[bn] && typeof map[bn] === 'object') ? map[bn] : {};
                    // Prefer AIR: Allows easier selection of specific versions via model_precision.
                    // SHA256 is used as a fallback if no AIR is present.
                    sv('air_or_hash', entry.air || entry.sha256 || '');
                    if (entry.precision) {
                        sv('model_precision', entry.precision);
                    }
                } finally {
                    _expectedEditorSyncing = false;
                }
                // Pre-fill download_target_role with the auto-detected role when the file is
                // missing and no role is set yet — saves the user having to pick it manually.
                const knownFiles = node._Eclipse_knownFiles;
                const isMissing = !(knownFiles instanceof Set && knownFiles.has(target));
                if (isMissing && !(gv('download_target_role') || '').trim()) {
                    const detectedRole = getRoleForTarget(target);
                    if (detectedRole) sv('download_target_role', detectedRole);
                }
            };
            const applyExpectedEditorToMap = () => {
                if (_expectedEditorSyncing) return;
                const target = getActiveModelTarget();
                const value = (gv('air_or_hash') || '').trim();
                if (!value) return;

                if (target) {
                    // File annotation mode.
                    const map = parseExpectedHashes();
                    const prev = (map[target] && typeof map[target] === 'object') ? map[target] : {};
                    const next = { ...prev };
                    if (value.toLowerCase().startsWith('urn:air:')) next.air = value;
                    else next.sha256 = value;

                    const prec = gv('model_precision');
                    if (prec && prec !== 'default') next.precision = prec;

                    if (prev.air !== next.air || prev.sha256 !== next.sha256 || prev.precision !== next.precision) {
                        setFileStatus(target, null);
                    }

                    map[target] = next;
                    setExpectedHashes(map);
                } else {
                    // Locator-only mode (no filename yet) — needs a target role.
                    applyLocatorEditorToList();
                }
            };
            const applyLocatorEditorToList = () => {
                const targetRole = (gv('download_target_role') || '').trim();
                const value = (gv('air_or_hash') || '').trim();
                if (!targetRole || !value) return;

                const list = parseDownloadLocators();
                const item = {
                    target_role: targetRole,
                    ...(value.toLowerCase().startsWith('urn:air:') ? { air: value } : { sha256: value }),
                };

                const exists = list.some((x) =>
                    x && x.target_role === item.target_role &&
                    ((item.air && x.air === item.air) || (item.sha256 && x.sha256 === item.sha256))
                );
                if (!exists) {
                    list.push(item);
                    setDownloadLocators(list);
                }
            };
            const getRoleForTarget = (target) => {
                if (!target) return null;
                const same = (widgetName) => gv(widgetName) === target || stripMissing(String(gv(widgetName))).replace(/\\/g, '/') === target;

                if (same('ckpt_name')) return 'checkpoints';
                if (same('unet_name') || same('nunchaku_name') || same('qwen_name') || same('zimage_name') || same('gguf_name')) return 'diffusion_models';

                return null;
            };
            const handleCivitaiDownload = async () => {
                const target = getActiveModelTarget();
                let targetRole = null;
                let air = '';
                let sha256 = '';
                let locatorIndex = -1;

                if (target) {
                    applyExpectedEditorToMap();
                    const map = parseExpectedHashes();
                    const entry = (map[target] && typeof map[target] === 'object') ? map[target] : {};
                    const editorValue = (gv('air_or_hash') || '').trim();
                    air = (entry.air || (editorValue.toLowerCase().startsWith('urn:air:') ? editorValue : '') || '').trim();
                    sha256 = (entry.sha256 || (!editorValue.toLowerCase().startsWith('urn:air:') ? editorValue : '') || '').trim();
                    targetRole = getRoleForTarget(target);
                } else {
                    applyLocatorEditorToList();
                    const locatorRole = (gv('download_target_role') || '').trim();
                    const locatorValue = (gv('air_or_hash') || '').trim();

                    if (locatorValue) {
                        if (!locatorRole) {
                            alert("Please select a target folder (Download Target Role) for the pasted AIR/SHA.");
                            return;
                        }
                        targetRole = locatorRole;
                        if (locatorValue.toLowerCase().startsWith('urn:air:')) air = locatorValue;
                        else sha256 = locatorValue;
                    } else {
                        const locators = parseDownloadLocators();
                        locatorIndex = locators.findIndex((x) => x && x.target_role && (x.air || x.sha256));
                        if (locatorIndex >= 0) {
                            const item = locators[locatorIndex];
                            targetRole = item.target_role;
                            air = item.air || '';
                            sha256 = item.sha256 || '';
                        }
                    }
                }

                if (!targetRole) {
                    console.warn('[Smart Model Loader] No target role found for download.');
                    return;
                }
                if (!air && !sha256) {
                    console.warn('[Smart Model Loader] No AIR/SHA found for download.');
                    return;
                }

                if (downloadButton) {
                    downloadButton.name = '⬇ Downloading...';
                    downloadButton.disabled = true;
                    if (isVueMode()) notifyVue(node);
                }

                try {
                    const isMismatch = target && getFileStatus(target) === 'mismatch';
                    const resp = await api.fetchApi('/eclipse/civitai/download', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            target_role: targetRole,
                            air: air || null,
                            sha256: sha256 || null,
                            node_id: String(node.id),
                            requested_filename: target || null,
                            download_preference: String(gv('model_precision') || 'default'),
                            api_key: String(gv('civitai_api_key') || ''),
                            conflict_policy: isMismatch ? 'rename' : 'skip',
                        }),
                    });
                    const result = await resp.json();
                    if (!result?.success) {
                        console.error('[Smart Model Loader] Download failed:', result?.error || 'Unknown error');
                        return;
                    }

                    if (result.unverified) {
                        console.warn('[Smart Model Loader] Downloaded without hash verification \u2014 no expected SHA was available.');
                    }

                    const resolvedName = toBaseName(result.filename || target) || target;
                    // Detect whether this download was a retry (rename policy \u2014 file already existed).
                    const isRetryDownload = !!target && resolvedName !== target;
                    const verifyStatus = result.verify_status; // 'ok', 'mismatch', 'no-expected', etc.

                    // Widget map for setting the file widget after download.
                    const widgetMap = {
                        'checkpoints': 'ckpt_name',
                        'diffusion_models': gv('model_type') === 'UNet Model' ? 'unet_name'
                            : gv('model_type') === 'Nunchaku Flux' ? 'nunchaku_name'
                                : gv('model_type') === 'Nunchaku Qwen' ? 'qwen_name'
                                    : gv('model_type') === 'Nunchaku ZImage' ? 'zimage_name'
                                        : gv('model_type') === 'GGUF Model' ? 'gguf_name'
                                            : 'unet_name',
                        'vae': 'vae_name',
                        'clip': 'clip_name1',
                        'loras': 'lora_name_1'
                    };
                    let forceWidgets = [];
                    // Effective name defaults to the original target for retry flows.
                    let effectiveName = isRetryDownload ? target : resolvedName;

                    if (isRetryDownload && target) {
                        addRetryFile(target, resolvedName);
                        if (verifyStatus === 'ok') {
                            // Successful re-download: promote the verified file to the original name
                            // and clean up ALL previous retry files + their sidecars.
                            const fileStatusEntry = node._Eclipse_fileStatus.get(target) || { retryFiles: new Set() };
                            const allRetries = Array.from(fileStatusEntry.retryFiles instanceof Set ? fileStatusEntry.retryFiles : []);
                            const cleanupFiles = allRetries.filter(n => n !== resolvedName);
                            console.log(`[Smart Model Loader] Calling /promote. original: ${target}, replacement: ${resolvedName}, cleanup:`, cleanupFiles);
                            const promoteRole = getRoleForTarget(target) || targetRole;
                            try {
                                const promoteResult = await api.fetchApi('/eclipse/integrity/promote', {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({
                                        target_role: promoteRole || '',
                                        original_filename: target,
                                        replacement_filename: resolvedName,
                                        cleanup_filenames: cleanupFiles,
                                    }),
                                }).then(r => r.json());

                                console.log(`[Smart Model Loader] /promote result:`, promoteResult);
                                if (promoteResult?.success) {
                                    // Happy path: file renamed to original, garbage cleaned up.
                                    clearFileStatus(target);
                                    setFileStatus(target, 'verified');
                                    effectiveName = target;
                                } else {
                                    throw new Error(promoteResult?.error || 'Promote returned failure');
                                }
                            } catch (e) {
                                // Fallback: promote failed — use the verified retry file directly.
                                console.error('[Smart Model Loader] Promote failed, using retry file directly:', e);
                                effectiveName = resolvedName;
                                const wn = widgetMap[promoteRole];
                                if (wn) {
                                    sv(wn, resolvedName);
                                    forceWidgets.push(wn);
                                }
                                clearFileStatus(target);
                                setFileStatus(resolvedName, 'verified');
                            }
                        } else if (verifyStatus === 'mismatch') {
                            // Keep mismatch status on the original target so button shows Re-download.
                            setFileStatus(target, 'mismatch');
                        }
                    } else if (verifyStatus === 'mismatch') {
                        setFileStatus(resolvedName, 'mismatch');
                    } else if (verifyStatus === 'ok') {
                        setFileStatus(resolvedName, 'verified');
                    }

                    // Update expected_hashes map \u2014 key under effectiveName.
                    const updated = parseExpectedHashes();
                    const baseEntry = (updated[target] && typeof updated[target] === 'object') ? updated[target] : {};
                    const merged = {
                        ...baseEntry,
                        ...(result.air ? { air: result.air } : {}),
                        ...(result.sha256 ? { sha256: result.sha256 } : {}),
                        ...(result.precision ? { precision: result.precision } : {}),
                    };

                    if (effectiveName !== target && updated[target]) {
                        // Fallback case: template now points to retry filename.
                        updated[effectiveName] = merged;
                        delete updated[target];
                    } else {
                        updated[effectiveName] = merged;
                    }

                    if (!target && resolvedName) {
                        updated[resolvedName] = {
                            ...(updated[resolvedName] && typeof updated[resolvedName] === 'object' ? updated[resolvedName] : {}),
                            ...merged,
                        };
                    }

                    setExpectedHashes(updated);
                    sv('download_target_role', '');

                    if (locatorIndex >= 0) {
                        const locators = parseDownloadLocators();
                        locators.splice(locatorIndex, 1);
                        setDownloadLocators(locators);
                    }

                    await refreshModelFiles();
                    updateVisibility();

                    if (targetRole) {
                        const widgetName = widgetMap[targetRole];
                        if (widgetName && !forceWidgets.includes(widgetName)) {
                            sv(widgetName, effectiveName);
                            forceWidgets.push(widgetName);
                        }
                    }

                    // Auto-save template to capture updated expected_hashes / sha256 after download.
                    await autoSaveTemplate(forceWidgets);
                } catch (e) {
                    console.error('[Smart Model Loader] Download request failed:', e);
                } finally {
                    node._Eclipse_dlProgress = null;
                    if (downloadButton) {
                        downloadButton.disabled = false;
                        // Label is recomputed by updateVisibility based on present/missing state.
                        if (isVueMode()) notifyVue(node);
                    }
                    node.setDirtyCanvas?.(true, true);
                    updateVisibility();
                }
            };
            // Whether the file currently selected exists on disk.
            const isSelectedFilePresent = () => {
                const target = getActiveModelTarget();
                if (!target) return false;
                const bn = toBaseName(target);
                const known = node._Eclipse_knownFiles;
                return known instanceof Set && known.has(bn);
            };
            // Mismatch tracking: Map(originalBasename → {retryFiles: Set<string>, status: 'mismatch'|'ok'})
            // retryFiles = all rename-downloaded basenames for that original (to be cleaned up on success).
            if (!(node._Eclipse_fileStatus instanceof Map)) node._Eclipse_fileStatus = new Map();
            const setFileStatus = (basename, status) => {
                const cur = node._Eclipse_fileStatus.get(basename) || { retryFiles: new Set() };
                node._Eclipse_fileStatus.set(basename, { ...cur, status });
            };
            const getFileStatus = (basename) => (node._Eclipse_fileStatus.get(basename) || {}).status || null;
            const addRetryFile = (originalBasename, retryBasename) => {
                const cur = node._Eclipse_fileStatus.get(originalBasename) || { retryFiles: new Set() };
                cur.retryFiles = cur.retryFiles instanceof Set ? cur.retryFiles : new Set();
                cur.retryFiles.add(retryBasename);
                node._Eclipse_fileStatus.set(originalBasename, cur);
            };
            const clearFileStatus = (basename) => { node._Eclipse_fileStatus.delete(basename); };
            // Auto-save the current template when integrity data changes (after download/verify).
            // Only fires when a real named template is loaded (action=Load, name≠None).
            // forceFields: array of widget names to force-overwrite even if they already exist in the template.
            const autoSaveTemplate = async (forceFields = []) => {
                const action = gv('template_action');
                const tmplName = gv('template_name');
                if (action !== 'Load' || !tmplName || tmplName === 'None') return;
                try {
                    const existingData = await loadTemplateData(tmplName) || {};
                    const currentConfig = buildTemplateConfig();

                    const integrityFields = ['expected_hashes', 'download_locators'];

                    for (const key in currentConfig) {
                        let val = currentConfig[key];
                        if (typeof val === 'string' && val.endsWith(MISSING_SUFFIX)) {
                            val = val.slice(0, -MISSING_SUFFIX.length);
                        }

                        if (integrityFields.includes(key) || forceFields.includes(key)) {
                            existingData[key] = val;
                        } else if (existingData[key] === undefined) {
                            existingData[key] = val;
                        }
                    }

                    await api.fetchApi('/eclipse/loader_templates/save', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name: tmplName, config: existingData }),
                    });
                } catch (e) {
                    console.warn('[Smart Model Loader] Auto-save failed:', e);
                }
            };

            const handleVerifyNow = async () => {
                const target = getActiveModelTarget();
                if (!target) return;
                applyExpectedEditorToMap();
                const targetRole = getRoleForTarget(target);
                const editorValue = (gv('air_or_hash') || '').trim();

                if (downloadButton) {
                    downloadButton.name = '… Hashing';
                    downloadButton.disabled = true;
                    if (isVueMode()) notifyVue(node);
                }

                try {
                    const resp = await api.fetchApi('/eclipse/integrity/verify', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            target_role: targetRole || '',
                            filename: target,
                            air_or_hash: editorValue,
                            download_preference: String(gv('model_precision') || 'default'),
                            api_key: String(gv('civitai_api_key') || ''),
                            node_id: String(node.id),
                        }),
                    });
                    const result = await resp.json();
                    if (result?.success) {
                        const s = result.status;

                        // Fallback Sync: If the user didn't provide a hash but the server verified 
                        // against one (e.g. from the .eclipse.json sidecar), sync it to the UI and map.
                        if (result.expected) {
                            if (!(gv('air_or_hash') || '').trim()) {
                                sv('air_or_hash', result.expected);
                            }
                            if (result.expected_precision) {
                                sv('model_precision', result.expected_precision);
                            }
                            applyExpectedEditorToMap();
                        }

                        if (s === 'ok') {
                            setFileStatus(target, 'verified');
                            downloadButton.name = '✓ Verified';
                        } else if (s === 'mismatch') {
                            setFileStatus(target, 'mismatch');
                            downloadButton.name = '✗ Hash mismatch';
                            console.warn(`[Smart Model Loader] ${target}: expected ${result.expected}, got ${result.actual}`);
                        } else if (s === 'no-expected' && result.actual) {
                            setFileStatus(target, 'hashed');
                            downloadButton.name = '✓ Hashed';
                        } else {
                            downloadButton.name = s === 'no-expected' ? 'ⓘ No expected value'
                                : s === 'missing' ? '✗ File missing'
                                    : 'ⓘ Unverifiable';
                        }
                    } else {
                        console.error('[Smart Model Loader] Verify failed:', result?.error || 'Unknown error');
                        if (downloadButton) downloadButton.name = '✗ Verify error';
                    }
                    if (downloadButton && isVueMode()) notifyVue(node);
                    // Hold the result label ~2.5s, then recompute.
                    setTimeout(() => {
                        if (downloadButton) downloadButton.disabled = false;
                        updateVisibility();
                    }, 2500);
                    // Auto-save template to persist the expected hash entered by the user,
                    // regardless of whether verification passed or failed.
                    let forceWidgets = [];
                    if (targetRole) {
                        const widgetMap = {
                            'checkpoints': 'ckpt_name',
                            'diffusion_models': gv('model_type') === 'UNet Model' ? 'unet_name'
                                : gv('model_type') === 'Nunchaku Flux' ? 'nunchaku_name'
                                    : gv('model_type') === 'Nunchaku Qwen' ? 'qwen_name'
                                        : gv('model_type') === 'Nunchaku ZImage' ? 'zimage_name'
                                            : gv('model_type') === 'GGUF Model' ? 'gguf_name'
                                                : 'unet_name',
                            'vae': 'vae_name',
                            'clip': 'clip_name1',
                            'loras': 'lora_name_1'
                        };
                        const widgetName = widgetMap[targetRole];
                        if (widgetName) {
                            forceWidgets.push(widgetName);
                        }
                    }
                    await autoSaveTemplate(forceWidgets);
                } catch (e) {
                    console.error('[Smart Model Loader] Verify request failed:', e);
                    if (downloadButton) downloadButton.disabled = false;
                    updateVisibility();
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
                    const expectedKeys = new Set(Object.keys(parseExpectedHashes()));
                    const pendingMissing = (node._Eclipse_pendingMissing instanceof Set) ? node._Eclipse_pendingMissing : null;
                    const updateList = (widgetName, newValues) => {
                        const w = node.widgets?.find(w => w.name === widgetName);
                        if (!w?.options?.values) return;
                        let values = [...newValues];
                        // Strip UI suffix before comparing with server list.
                        const cleanCurrent = stripMissing(w.value != null ? String(w.value) : '');
                        if (!newValues.includes(cleanCurrent)) {
                            const fwd = cleanCurrent.replace(/\\/g, '/');
                            const bnCurrent = fwd.split('/').pop() || fwd;
                            const keep = bnCurrent && (expectedKeys.has(bnCurrent) || (pendingMissing && pendingMissing.has(bnCurrent)));
                            if (keep && fwd !== 'None') {
                                // File expected but absent — show with warning suffix.
                                const missingLabel = fwd + MISSING_SUFFIX;
                                if (!values.includes(missingLabel)) values.push(missingLabel);
                            }
                        }
                        w.options.values = values;
                        if (!values.includes(w.value)) {
                            const cleanV = stripMissing(w.value != null ? String(w.value) : '');
                            const fwd = cleanV.replace(/\\/g, '/');
                            // File now present on disk (e.g. just downloaded) — use clean name.
                            if (newValues.includes(fwd)) {
                                w.value = fwd;
                            } else if (fwd !== cleanV && newValues.includes(cleanV)) {
                                w.value = cleanV;
                            } else {
                                const bn = fwd.split('/').pop();
                                const sm = bn ? values.find(v => v === fwd || v === bn || v.endsWith('/' + bn) || v === fwd + MISSING_SUFFIX || v === bn + MISSING_SUFFIX) : null;
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
                    // Build the server-known basename set for disk-free missing-file detection.
                    const known = new Set();
                    for (const arr of [data.checkpoints, data.diffusion_models, data.diffusion_models_gguf, data.vae, data.clip_combined, data.loras]) {
                        if (Array.isArray(arr)) {
                            for (const v of arr) {
                                if (v && v !== 'None') known.add(toBaseName(v));
                            }
                        }
                    }
                    node._Eclipse_knownFiles = known;
                    // Clear cached lists so updateVisibility re-captures fresh server data,
                    // which matters after a download adds a file that was previously missing.
                    for (const k of Object.keys(originalModelLists)) delete originalModelLists[k];
                    canvasDirtyBatcher.markDirty(node, true, true);
                    debouncedUpdate();
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
                sv('expected_hashes', '{}');
                sv('air_or_hash', '');
                sv('download_locators', '[]');
                sv('download_target_role', '');
                // Clear stale pending-missing entries from any previous template load.
                node._Eclipse_pendingMissing = new Set();
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
                let data = await loadTemplateData(name);
                if (!data) {
                    updateVisibility();
                    return;
                }
                const prevFeats = Array.isArray(featWidget.value) ? featWidget.value : [];
                const hadTemplates = prevFeats.includes('templates');
                const hadMemoryCleanup = prevFeats.includes('memory_cleanup');
                const hadSeed = prevFeats.includes('seed');
                const hadIntegrity = prevFeats.includes('integrity');
                const hadLatent = prevFeats.includes('latent');

                // Cache the user's current latent field values so they are not wiped
                const prevRes = gv('resolution');
                const prevWidth = gv('width');
                const prevHeight = gv('height');
                const prevBatch = gv('batch_size');

                isLoadingTemplate = true;
                try {
                    resetAllFields();

                    // Restore the user's latent field values
                    if (prevRes !== undefined) sv('resolution', prevRes);
                    if (prevWidth !== undefined) sv('width', prevWidth);
                    if (prevHeight !== undefined) sv('height', prevHeight);
                    if (prevBatch !== undefined) sv('batch_size', prevBatch);

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
                        newFeatures = data.features.filter(f => f !== 'templates' && f !== 'memory_cleanup' && f !== 'seed' && f !== 'integrity' && f !== 'latent');
                    } else {
                        newFeatures = templateFeatures.filter(f => f !== 'latent');
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
                    if (hadIntegrity && !newFeatures.includes('integrity')) {
                        newFeatures.push('integrity');
                    }
                    if (hadLatent && !newFeatures.includes('latent')) {
                        newFeatures.push('latent');
                    }
                    featWidget.value = newFeatures;
                    const fields = ['model_type', 'weight_dtype', 'blocks_to_swap', 'offload_embeddings', 'sampling_method', 'sampling_subtype', 'shift', 'base_shift', 'sampling_width', 'sampling_height', 'original_timesteps', 'zsnr', 'sigma_max', 'sigma_min', 'data_type', 'cache_threshold', 'attention', 'i2f_mode', 'cpu_offload', 'num_blocks_on_gpu', 'use_pin_memory', 'gguf_dequant_dtype', 'gguf_patch_dtype', 'gguf_patch_on_device', 'clip_source', 'clip_count', 'clip_name1', 'clip_name2', 'clip_name3', 'clip_name4', 'clip_type', 'enable_clip_layer', 'stop_at_clip_layer', 'vae_source', 'vae_name', 'audio_vae_source', 'audio_vae_name', 'lora_count', 'ckpt_name', 'unet_name', 'nunchaku_name', 'qwen_name', 'zimage_name', 'gguf_name', 'expected_hashes', 'download_locators',];
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
                    await refreshModelFiles();
                    // After template load: sync integrity editor with the active model target
                    loadEditorValueForTarget();
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

                if (!confirm(`Are you sure you want to delete the template "${tmplName}"?`)) return;

                let deleteModels = false;
                try {
                    const getResp = await api.fetchApi(`/eclipse/loader_templates/${encodeURIComponent(tmplName)}.json`);
                    if (getResp.ok) {
                        const config = await getResp.json();
                        const modelFiles = [];
                        const modelKeys = [
                            'ckpt_name', 'unet_name', 'nunchaku_name', 'qwen_name', 'zimage_name', 'gguf_name'
                        ];

                        for (const key of modelKeys) {
                            const val = config[key];
                            if (val && val !== 'None' && val !== '') {
                                const basename = val.split(/[/\\]/).pop();
                                if (!modelFiles.includes(basename)) {
                                    modelFiles.push(basename);
                                }
                            }
                        }

                        if (modelFiles.length > 0) {
                            deleteModels = confirm(
                                `Would you also like to delete the associated model file(s) from disk?\n\n` +
                                `Files to delete:\n` +
                                modelFiles.map(f => `• ${f}`).join('\n') +
                                `\n\nThis cannot be undone.`
                            );
                        }
                    }
                } catch (e) {
                    console.error('[Smart Model Loader] Failed to fetch template config for model list:', e);
                }

                try {
                    const resp = await api.fetchApi('/eclipse/loader_templates/delete', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            name: tmplName,
                            delete_models: deleteModels
                        }),
                    });
                    const result = await resp.json();
                    if (result.success) {
                        broadcastTemplateListChanged(await refreshTemplateList(), node.id);
                        sv('template_name', 'None');
                        sv('new_template_name', '');
                        resetAllFields();
                        updateVisibility();

                        if (deleteModels && result.deleted_models && result.deleted_models.length > 0) {
                            const deletedList = result.deleted_models.map(f => f.split(/[/\\]/).pop());
                            alert(`Deleted template "${tmplName}" and the following model files:\n\n` + deletedList.join('\n'));
                        } else {
                            alert(`Deleted template "${tmplName}".`);
                        }
                    } else {
                        alert(`Delete failed: ${result.error || 'Unknown error'}`);
                    }
                } catch (e) {
                    alert(`Delete request failed: ${e.message || e}`);
                }
            };
            const buildTemplateConfig = () => {
                const cfg = {};
                const raw = vis.getValue('features');
                const feats = typeof raw === 'string' ? raw.split(',').map(s => s.trim()) : (Array.isArray(raw) ? raw : []);
                cfg.features = feats.filter(f => f !== 'templates' && f !== 'memory_cleanup' && f !== 'seed' && f !== 'integrity' && f !== 'latent');
                const mt = gv('model_type');
                cfg.model_type = mt;
                cfg.configure_clip = feats.includes('clip');
                cfg.configure_vae = feats.includes('vae');
                cfg.configure_sampler = feats.includes('sampler');
                cfg.configure_model_only_lora = feats.includes('lora');
                cfg.configure_model_sampling = feats.includes('model_sampling');
                cfg.configure_blockswap = feats.includes('block_swap');
                cfg.blocks_to_swap = gv('blocks_to_swap');
                cfg.offload_embeddings = gv('offload_embeddings');
                if (mt === 'Standard Checkpoint') {
                    const v = stripMissing(gv('ckpt_name'));
                    if (v && v !== 'None') cfg.ckpt_name = v;
                } else if (mt === 'UNet Model') {
                    const v = stripMissing(gv('unet_name'));
                    if (v && v !== 'None') cfg.unet_name = v;
                    cfg.weight_dtype = gv('weight_dtype');
                } else if (mt === 'Nunchaku Flux') {
                    const v = stripMissing(gv('nunchaku_name'));
                    if (v && v !== 'None') cfg.nunchaku_name = v;
                    cfg.data_type = gv('data_type');
                    cfg.cache_threshold = gv('cache_threshold');
                    cfg.attention = gv('attention');
                    cfg.i2f_mode = gv('i2f_mode');
                    cfg.cpu_offload = gv('cpu_offload');
                } else if (mt === 'Nunchaku Qwen') {
                    const v = stripMissing(gv('qwen_name'));
                    if (v && v !== 'None') cfg.qwen_name = v;
                    cfg.cpu_offload = gv('cpu_offload');
                    cfg.num_blocks_on_gpu = gv('num_blocks_on_gpu');
                    cfg.use_pin_memory = gv('use_pin_memory');
                } else if (mt === 'Nunchaku ZImage') {
                    const v = stripMissing(gv('zimage_name'));
                    if (v && v !== 'None') cfg.zimage_name = v;
                    cfg.cpu_offload = gv('cpu_offload');
                    cfg.num_blocks_on_gpu = gv('num_blocks_on_gpu');
                    cfg.use_pin_memory = gv('use_pin_memory');
                } else if (mt === 'GGUF Model') {
                    const v = stripMissing(gv('gguf_name'));
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
                            const v = stripMissing(gv(`clip_name${i}`));
                            if (v && v !== 'None') cfg[`clip_name${i}`] = v;
                        }
                    }
                }
                if (feats.includes('vae')) {
                    const vs = gv('vae_source');
                    cfg.vae_source = vs;
                    if (vs === 'External') {
                        const v = stripMissing(gv('vae_name'));
                        if (v && v !== 'None') cfg.vae_name = v;
                    }
                }
                if (feats.includes('audio_vae')) {
                    const avs = gv('audio_vae_source');
                    cfg.audio_vae_source = avs;
                    if (avs !== 'Baked') {
                        const v = stripMissing(gv('audio_vae_name'));
                        if (v && v !== 'None') cfg.audio_vae_name = v;
                    }
                }
                // Latent configuration is intentionally decoupled from templates and not saved.
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
                        cfg[`lora_name_${i}`] = stripMissing(gv(`lora_name_${i}`));
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

                const rawHashes = gv('expected_hashes');
                if (rawHashes && rawHashes !== '{}') {
                    try {
                        const parsedHashes = JSON.parse(rawHashes);
                        const filteredHashes = {};
                        for (const key of Object.keys(parsedHashes)) {
                            // Check if 'key' is the basename of ANY value in cfg
                            const isUsed = Object.values(cfg).some(v => {
                                if (typeof v === 'string' && v && v !== 'None') {
                                    const bn = stripMissing(v).replace(/\\/g, '/');
                                    // Match full path or fallback to matching basenames (to survive subfolder/root discrepancies)
                                    return bn === key || bn.split('/').pop() === key.split('/').pop();
                                }
                                return false;
                            });
                            if (isUsed) {
                                filteredHashes[key] = parsedHashes[key];
                            }
                        }
                        cfg.expected_hashes = JSON.stringify(filteredHashes);
                    } catch (e) {
                        cfg.expected_hashes = rawHashes;
                    }
                } else {
                    cfg.expected_hashes = '{}';
                }

                cfg.download_locators = gv('download_locators') || '[]';
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

                // Integrity controls: hide behind the 'integrity' chip unless a file is missing
                const missingActiveFiles = getMissingActiveFiles();
                const isTargetEmpty = !getActiveModelTarget() || getActiveModelTarget() === 'None';
                const hasPendingLocators = isTargetEmpty && parseDownloadLocators().some((x) => x && x.target_role && (x.air || x.sha256));

                const forceIntegrity = missingActiveFiles.length > 0 || hasPendingLocators;
                if (forceIntegrity && gv('verify_file') !== 'verify') {
                    sv('verify_file', 'verify');
                }
                const verifyMode = gv('verify_file') || 'off';

                const hasIntegrityChip = feats.has('integrity');
                const showIntegrityBlock = hasIntegrityChip || forceIntegrity;
                const revealIntegrityEditor = showIntegrityBlock && verifyMode !== 'off';
                const isVerifyMode = verifyMode === 'verify';

                d('verify_file', showIntegrityBlock);
                d('expected_hashes', false);
                d('download_locators', false);

                // Role picker: show ONLY in verify mode when locator-only downloads OR 
                // when the active model is missing (so it can be correctly placed post-download).
                const selectedForMissing = revealIntegrityEditor && !!getActiveModelTarget() && !isSelectedFilePresent();
                d('download_target_role', showIntegrityBlock && isVerifyMode && (!getActiveModelTarget() || selectedForMissing));

                d('air_or_hash', showIntegrityBlock && isVerifyMode);

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
                const expectedKeysForInject = new Set(Object.keys(parseExpectedHashes()));
                const pendingMissing = (node._Eclipse_pendingMissing instanceof Set) ? node._Eclipse_pendingMissing : null;
                for (const [wName, info] of Object.entries(modelFilter)) {
                    const w = node.widgets?.find(w => w.name === wName);
                    if (!w?.options) continue;
                    if (!originalModelLists[wName]) originalModelLists[wName] = [...w.options.values];
                    const filtered = originalModelLists[wName].filter(v => {
                        if (v === 'None') return true;
                        return info.exts.some(ext => v.toLowerCase().endsWith(ext));
                    });
                    const prevValue = w.value;
                    // Strip UI suffix for all comparisons — the suffix is display-only.
                    const cleanPrev = stripMissing(prevValue != null ? String(prevValue) : '');
                    w.options.values = filtered;
                    if (filtered.includes(cleanPrev)) {
                        // Clean name is present: file exists (or suffix was just removed after download).
                        w.value = cleanPrev;
                    } else if (!filtered.includes(prevValue)) {
                        const fwd = cleanPrev.replace(/\\/g, '/');
                        if (fwd !== cleanPrev && filtered.includes(fwd)) {
                            w.value = fwd;
                        } else {
                            const bn = fwd.split('/').pop();
                            const match = bn ? filtered.find(v => v.endsWith('/' + bn) || v === bn) : null;
                            if (match) {
                                w.value = match;
                            } else if (bn && bn !== 'None' && (expectedKeysForInject.has(bn) || (pendingMissing && pendingMissing.has(bn)))) {
                                // Missing-but-referenced file: show with suffix when absent, clean when present.
                                const knownFiles = node._Eclipse_knownFiles;
                                const isActuallyMissing = !(knownFiles instanceof Set) || !knownFiles.has(bn);
                                const displayLabel = isActuallyMissing ? bn + MISSING_SUFFIX : bn;
                                if (!filtered.includes(displayLabel)) filtered.push(displayLabel);
                                w.options.values = filtered;
                                w.value = displayLabel;
                            } else {
                                w.value = 'None';
                            }
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

                const locatorRole = (gv('download_target_role') || '').trim();
                const editorValue = (gv('air_or_hash') || '').trim();
                const selectedTarget = getActiveModelTarget();
                const selectedPresent = isSelectedFilePresent();
                // Present file → Verify (unless it has a mismatch, then Re-download); missing → Download.
                const selectedMismatch = selectedPresent && selectedTarget && getFileStatus(selectedTarget) === 'mismatch';
                const selectedVerified = selectedPresent && selectedTarget && getFileStatus(selectedTarget) === 'verified';
                const selectedHashed = selectedPresent && selectedTarget && getFileStatus(selectedTarget) === 'hashed';
                const verifyMethod = revealIntegrityEditor && !!selectedTarget && selectedPresent && !selectedMismatch;

                const canShowDownload = revealIntegrityEditor && (
                    (!!selectedTarget && !selectedPresent) ||
                    (!!selectedTarget && selectedMismatch) ||
                    (!!locatorRole && !!editorValue) ||
                    verifyMethod
                );
                if (canShowDownload && !downloadButton) {
                    const initialLabel = verifyMethod ? (selectedVerified ? '✓ Verified' : selectedHashed ? '✓ Hashed' : (editorValue ? '✓ Verify now' : '✓ Hash now')) : '⬇ Download from CivitAI';
                    downloadButton = node.addWidget('button', initialLabel, null, null);
                    downloadButton.serialize = false;
                    node._Eclipse_downloadButton = downloadButton;
                } else if (!canShowDownload && downloadButton) {
                    const idx = node.widgets.indexOf(downloadButton);
                    if (idx >= 0) node.widgets.splice(idx, 1);
                    downloadButton = null;
                    node._Eclipse_downloadButton = null;
                }
                // Keep the button's label + action in sync (skip while a transient op is busy).
                if (downloadButton && !downloadButton.disabled) {
                    const desiredLabel = verifyMethod ? (
                        selectedVerified ? '✓ Verified' :
                            selectedHashed ? '✓ Hashed' :
                                (editorValue ? '✓ Verify now' : '✓ Hash now')
                    )
                        : selectedMismatch ? '⬇ Re-download'
                            : '⬇ Download from CivitAI';
                    if (downloadButton.name !== desiredLabel) {
                        downloadButton.name = desiredLabel;
                        if (node.graph) node.graph.setDirtyCanvas(true, true);
                        if (isVueMode()) notifyVue(node);
                    }
                    downloadButton.callback = verifyMethod ? handleVerifyNow : handleCivitaiDownload;
                }

                d('model_precision', showIntegrityBlock);

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
                const enableClipLayer = gv('enable_clip_layer');
                d('enable_clip_layer', hasClip && isStd);
                d('stop_at_clip_layer', hasClip && isStd && enableClipLayer);
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
                    debouncedUpdate();
                    if (autoFeaturesW) autoFeaturesW.value = (Array.isArray(featWidget.value) ? featWidget.value : []).join(',');
                };
            }
            const triggerWidgets = ['template_action', 'template_name', 'model_type', 'sampling_method', 'clip_source', 'clip_count', 'clip_type', 'enable_clip_layer', 'vae_source', 'audio_vae_source', 'resolution', 'lora_count', 'verify_file', 'air_or_hash', 'download_target_role', 'model_precision', 'ckpt_name', 'unet_name', 'nunchaku_name', 'qwen_name', 'zimage_name', 'gguf_name'];
            for (const wName of triggerWidgets) {
                const w = node.widgets?.find(w => w.name === wName);
                if (!w) continue;
                const origCb = w.callback;
                w.callback = function () {
                    if (origCb) origCb.apply(this, arguments);
                    vis.markUserDriven();
                    if (['ckpt_name', 'unet_name', 'nunchaku_name', 'qwen_name', 'zimage_name', 'gguf_name'].includes(wName)) {
                        // Switching target loads that file's stored value (no stale write).
                        loadEditorValueForTarget();
                    }
                    if (wName === 'air_or_hash' || wName === 'download_target_role' || wName === 'model_precision') {
                        applyExpectedEditorToMap();
                        const target = getActiveModelTarget();
                        if (target) setFileStatus(target, null);
                    }
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
            // Custom onDrawForeground removed — download/hash progress is now displayed on the button label.
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
        // One global listener for CivitAI download/hash progress → update the matching node.
        api.addEventListener('eclipse.download_progress', (e) => {
            const d = e?.detail;
            if (!d || d.node_id == null) return;
            const nodes = app.graph?._nodes || [];
            const node = nodes.find((n) => String(n.id) === String(d.node_id) && n.type === NODE_NAME);
            if (!node) return;
            const btn = node._Eclipse_downloadButton;
            if (btn) {
                const isHashing = !!d.is_hashing;
                btn.name = isHashing ? `… Hashing ${d.pct}%` : `⬇ Downloading ${d.pct}%`;
                if (isVueMode()) notifyVue(node);
            }
            node.setDirtyCanvas?.(true, false);
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
            // Strip UI-only '(missing)' suffix from all SmartModelLoader file inputs
            // before sending to the backend — the backend wants clean filenames.
            if (result?.output) {
                for (const { node: smlNode, outputKey: smlKey } of getGraphNodeList(app.graph)) {
                    if (smlNode.type !== NODE_NAME) continue;
                    const inputs = result.output[smlKey]?.inputs;
                    if (!inputs) continue;
                    for (const [k, v] of Object.entries(inputs)) {
                        if (typeof v === 'string' && v.endsWith(MISSING_SUFFIX))
                            inputs[k] = v.slice(0, -MISSING_SUFFIX.length);
                    }
                }
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
                        btn.label = `🌘 ${resolved}`;
                        btn.disabled = false;
                    } else {
                        btn.label = '🌘 (Use Last Queued Seed)';
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
