/**
 * Save Video with Generation Data — feature chips, conditional trim controls,
 * and a resizable output preview.
 * Copyright (c) 2026 r-vage. MIT License.
 */
import { app } from './comfy/index.js';
import {
    createWidgetVisibilityManager,
    isConfiguringGraph,
} from './eclipse-widget-performance-utils.js';
import {
    createComboChipWidget,
    injectComboChipCSS,
} from './eclipse-combo-chip.js';
import {
    attachVideoPreview,
    setVideoPreviewSource,
    stopVideoPreview,
} from './eclipse-video-preview-common.js';

const NODE_NAME = 'Save Video Data [Eclipse]';
const FEATURE_OPTIONS = [
    { label: 'embed_workflow', tooltip: 'Embed the raw ComfyUI prompt and workflow in the MP4' },
    { label: 'save_gen_data', tooltip: 'Embed A1111-compatible generation data from the optional PIPE' },
    { label: 'remove_prompts', tooltip: 'Blank positive and negative prompts in generation metadata' },
    { label: 'save_json', tooltip: 'Write the workflow beside the MP4 as a JSON sidecar' },
    { label: 'loras_to_prompt', tooltip: 'Append used LoRA names and weights to positive prompt metadata' },
    { label: 'trim', tooltip: 'Show and apply audio trim and loop controls' },
];
const DEFAULT_FEATURES = ['embed_workflow', 'save_gen_data', 'trim'];
const CHIP_TO_BACKING = {
    embed_workflow: 'embed_workflow',
    save_gen_data: 'save_generation_data',
    remove_prompts: 'remove_prompts',
    save_json: 'save_workflow_as_json',
    loras_to_prompt: 'add_loras_to_prompt',
    trim: 'enable_trim',
};
const BACKING_WIDGETS = Object.values(CHIP_TO_BACKING);
const LOOP_WIDGETS = ['loop_search_pct', 'loop_metric', 'loop_trim_start'];
const BLEND_WIDGETS = ['loop_blend_frames'];
const SERIALIZED_WIDGET_DEFAULTS = {
    features: DEFAULT_FEATURES.join(','),
    embed_workflow: true,
    save_generation_data: true,
    remove_prompts: false,
    save_workflow_as_json: false,
    add_loras_to_prompt: false,
    enable_trim: true,
    fps: 24,
    filename_prefix: 'video/ComfyUI_Eclipse',
    format: 'mp4',
    codec: 'h264',
    crf: 19,
    preset: 'veryfast',
    trim_mode: 'video_to_audio',
    loop_search_pct: 50,
    loop_blend_frames: 8,
    loop_metric: 'ncc',
    loop_trim_start: false,
};
const SERIALIZED_WIDGETS = Object.keys(SERIALIZED_WIDGET_DEFAULTS);

injectComboChipCSS('svd');

function widget(node, name) {
    return node.widgets?.find((candidate) => candidate.name === name);
}

function readChipsFromBacking(node) {
    const selected = new Set();
    for (const [chip, backing] of Object.entries(CHIP_TO_BACKING)) {
        if (widget(node, backing)?.value === true) selected.add(chip);
    }
    return selected;
}

function syncChipsToBacking(node, selected) {
    for (const [chip, backing] of Object.entries(CHIP_TO_BACKING)) {
        const backingWidget = widget(node, backing);
        const value = selected.has(chip);
        if (backingWidget && backingWidget.value !== value) backingWidget.value = value;
    }
    const featuresWidget = widget(node, 'features');
    if (featuresWidget) featuresWidget.value = [...selected].join(',');
}

function nativeWidgets(node) {
    return SERIALIZED_WIDGETS.map((name) => widget(node, name)).filter(Boolean);
}

function workflowValue(value) {
    if (value == null || typeof value !== 'object') return value ?? null;
    return JSON.parse(JSON.stringify(value));
}

function serializeNativeWidgets(node, data) {
    const widgets = nativeWidgets(node);
    data.widgets_values = widgets.map((item) => workflowValue(item.value));
    data.widgets_values_named = Object.fromEntries(
        widgets.map((item) => [item.name, workflowValue(item.value)])
    );
    return data;
}

function isFeatureState(value) {
    if (typeof value !== 'string') return false;
    const knownFeatures = new Set(FEATURE_OPTIONS.map(({ label }) => label));
    return value.split(',').map((item) => item.trim()).filter(Boolean)
        .every((item) => knownFeatures.has(item));
}

function positionalNativeValues(data) {
    if (!Array.isArray(data?.widgets_values)) return null;
    let values = data.widgets_values.slice();
    // Frontend 1.48.x writes a null for the leading cosmetic DOM widget even
    // though it is non-serializing. Remove that legacy placeholder first.
    if (values.length > SERIALIZED_WIDGETS.length && values[0] == null) {
        values = values.slice(1);
    }
    // A workflow saved again after one or more affected reloads may already
    // contain shifted native values. Find the encoded feature state and move it
    // back to the first native position; only lost trailing values use defaults.
    const featureIndex = values.slice(0, BACKING_WIDGETS.length + 1)
        .findIndex((value) => isFeatureState(value));
    if (featureIndex > 0) values = values.slice(featureIndex);
    return SERIALIZED_WIDGETS.map((name, index) =>
        index < values.length ? values[index] : SERIALIZED_WIDGET_DEFAULTS[name]
    );
}

function restoreNativeWidgets(node, data) {
    const widgets = nativeWidgets(node);
    const named = data?.widgets_values_named;
    if (named && !Array.isArray(named) && Object.keys(named).length > 0) {
        for (const item of widgets) {
            if (Object.prototype.hasOwnProperty.call(named, item.name)) {
                item.value = named[item.name] ?? undefined;
            }
        }
        return;
    }
    const values = positionalNativeValues(data);
    if (!values) return;
    for (let index = 0; index < widgets.length; index += 1) {
        widgets[index].value = values[index];
    }
}

function refreshVisibility(node, vis, chipWidget) {
    if (node.id === -1) return;
    const selected = new Set(chipWidget?.value ?? readChipsFromBacking(node));
    for (const name of ['features', ...BACKING_WIDGETS, 'format', 'codec']) {
        vis.setVisible(name, false);
    }

    const trimWidget = widget(node, 'trim_mode');
    const hasTrim = selected.has('trim');
    if (!hasTrim && trimWidget?.value !== 'none') trimWidget.value = 'none';
    vis.setVisible('trim_mode', hasTrim);

    const mode = hasTrim ? (trimWidget?.value ?? 'none') : 'none';
    const isLoop = mode === 'loop_match' || mode === 'loop_match_blend';
    for (const name of LOOP_WIDGETS) vis.setVisible(name, isLoop);
    for (const name of BLEND_WIDGETS) vis.setVisible(name, mode === 'loop_match_blend');
    // Keep the user's node geometry stable. The flexible preview row absorbs
    // the space gained or consumed when controls are hidden or shown.
}

app.registerExtension({
    name: 'Eclipse.SaveVideoData',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;

        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = originalCreated?.apply(this, arguments);
            const node = this;
            const vis = createWidgetVisibilityManager(node);
            node._Eclipse_saveVideoDataVis = vis;
            vis.hideInitially([
                'features',
                ...BACKING_WIDGETS,
                'format',
                'codec',
                'trim_mode',
                ...LOOP_WIDGETS,
                ...BLEND_WIDGETS,
            ]);

            const featuresWidget = widget(node, 'features');
            const featureIndex = featuresWidget ? node.widgets.indexOf(featuresWidget) : 0;
            const savedFeatures = readChipsFromBacking(node);
            const initialFeatures = savedFeatures.size ? [...savedFeatures] : DEFAULT_FEATURES;
            const originalSerialize = node.serialize;
            node.serialize = function () {
                const data = originalSerialize?.apply(this, arguments) ?? {};
                return serializeNativeWidgets(this, data);
            };
            const chipWidget = createComboChipWidget({
                node,
                options: FEATURE_OPTIONS,
                savedValue: initialFeatures,
                origIdx: featureIndex,
                widgetName: '_svd_features',
                cssPrefix: 'svd',
                serialize: false,
            });

            chipWidget.callback = () => {
                const selected = new Set(chipWidget.value);
                syncChipsToBacking(node, selected);
                vis.markUserDriven();
                refreshVisibility(node, vis, chipWidget);
            };
            syncChipsToBacking(node, new Set(initialFeatures));

            const trimWidget = widget(node, 'trim_mode');
            if (trimWidget) {
                const originalCallback = trimWidget.callback;
                trimWidget.callback = function () {
                    originalCallback?.apply(this, arguments);
                    vis.markUserDriven();
                    refreshVisibility(node, vis, chipWidget);
                };
            }

            if (!node._Eclipse_saveVideoDataInitialized && !isConfiguringGraph()) {
                node._Eclipse_saveVideoDataInitialized = true;
                requestAnimationFrame(() => refreshVisibility(node, vis, chipWidget));
            }

            const originalConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                originalConfigure?.apply(this, arguments);
                restoreNativeWidgets(node, data);
                vis.clearCache?.();
                chipWidget.value = [...readChipsFromBacking(node)];
                refreshVisibility(node, vis, chipWidget);
            };

            attachVideoPreview(node, { sourceType: 'output' });
            return result;
        };

        const originalExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            originalExecuted?.apply(this, arguments);
            const previews = message?.eclipse_video;
            if (Array.isArray(previews) && previews.length) {
                setVideoPreviewSource(this, previews[0]);
            }
        };

        const originalRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            stopVideoPreview(this);
            originalRemoved?.apply(this, arguments);
        };
    },
});
