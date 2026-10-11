/**
 * Danbooru Prompt Forge combo-chip integration for Nodes 2.0 and classic canvas.
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

const NODE_NAME = 'Danbooru Prompt Forge [Eclipse]';
const FEATURE_OPTIONS = [
    { label: 'general', tooltip: 'Include the general content-rating pool' },
    { label: 'questionable', tooltip: 'Include the questionable content-rating pool' },
    { label: 'sensitive', tooltip: 'Include the sensitive content-rating pool' },
    { label: 'explicit', tooltip: 'Include the explicit content-rating pool' },
    { label: 'replace_underscores', tooltip: 'Replace underscores with spaces in the generated text' },
    { label: 'ignore_missing', tooltip: 'Ignore required tags absent from the enabled rating pools' },
    { label: 'categories', tooltip: 'Enable generated-tag category filtering' },
];
const FEATURE_NAMES = FEATURE_OPTIONS.map(({ label }) => label);
const DEFAULT_FEATURES = ['general', 'replace_underscores'];
const PREFIX_WIDGET_NAME = 'prefix';

injectComboChipCSS('dpf');

function normalizeFeatures(value, fallback = DEFAULT_FEATURES) {
    const values = typeof value === 'string'
        ? value.split(',').map((feature) => feature.trim()).filter(Boolean)
        : value;
    if (!Array.isArray(values)) return fallback.slice();
    return [...new Set(values.filter((feature) => FEATURE_NAMES.includes(feature)))];
}

function encodeFeatures(value) {
    return normalizeFeatures(value, []).join(',');
}

function preserveNodeSize(node, callback) {
    const originalSize = Array.isArray(node.size) ? node.size.slice() : null;
    const result = callback();
    if (
        originalSize
        && (node.size?.[0] !== originalSize[0] || node.size?.[1] !== originalSize[1])
    ) {
        node.setSize?.(originalSize);
    }
    return result;
}

app.registerExtension({
    name: 'Eclipse.DanbooruPromptForge',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated?.apply(this, arguments);
            const node = this;
            const backingWidget = node.widgets?.find((widget) => widget.name === 'features');
            if (!backingWidget) return ret;
            const prefixWidget = node.widgets?.find(
                (widget) => widget.name === PREFIX_WIDGET_NAME,
            );
            const originalIndex = node.widgets.indexOf(backingWidget);
            const backingFeatures = normalizeFeatures(backingWidget.value, []);
            const savedFeatures = !isConfiguringGraph() && backingFeatures.length === 0
                ? DEFAULT_FEATURES.slice()
                : backingFeatures;
            const visibility = createWidgetVisibilityManager(node);

            // This input is a STRING, so the DOM widget can own its value in
            // both renderers. An extra classic backing widget shifts every
            // subsequent value when a workflow is opened in the other renderer.
            backingWidget.onRemove?.();
            node.widgets.splice(originalIndex, 1);

            const chipWidget = createComboChipWidget({
                node,
                options: FEATURE_OPTIONS,
                savedValue: savedFeatures,
                origIdx: originalIndex,
                widgetName: 'features',
                cssPrefix: 'dpf',
            });
            node._Eclipse_danbooruFeatureWidget = chipWidget;

            const origConfigure = node.configure;
            node.configure = function (data) {
                const values = data?.widgets_values;
                const index = this.widgets.indexOf(chipWidget);
                // Old classic workflows saved the cosmetic selection followed
                // by the actual string input. Remove only that duplicate slot,
                // before LiteGraph restores text, seed, and widget callbacks.
                if (
                    Array.isArray(values)
                    && values.length === this.widgets.length + 1
                    && Array.isArray(values[index])
                    && values[index + 1] === encodeFeatures(values[index])
                ) {
                    const restoredValues = values.slice();
                    restoredValues.splice(index, 1);
                    data = { ...data, widgets_values: restoredValues };
                }
                return origConfigure.call(this, data);
            };

            const updateCategoryVisibility = () => {
                if (node.id === -1) return;
                visibility.setVisible(
                    'exclude_tag_categories',
                    normalizeFeatures(chipWidget.value, []).includes('categories'),
                );
            };

            const origCallback = chipWidget.callback;
            chipWidget.callback = function (value) {
                return preserveNodeSize(node, () => {
                    origCallback?.call(this, value);
                    updateCategoryVisibility();
                });
            };
            if (prefixWidget) {
                const origPrefixCallback = prefixWidget.callback;
                prefixWidget.callback = function (value) {
                    return preserveNodeSize(
                        node,
                        () => origPrefixCallback?.call(this, value),
                    );
                };
            }
            visibility.hideInitially(['exclude_tag_categories']);

            const origOnConfigure = node.onConfigure;
            node.onConfigure = function () {
                const configureResult = origOnConfigure?.apply(this, arguments);
                chipWidget.value = normalizeFeatures(chipWidget.value, []);
                visibility.setLoadMode(true);
                updateCategoryVisibility();
                visibility.setLoadMode(false);
                return configureResult;
            };

            if (!isConfiguringGraph()) {
                requestAnimationFrame(() => {
                    updateCategoryVisibility();
                });
            }
            return ret;
        };
    },
});
