/**
 * Danbooru Prompt Forge combo-chip integration for Nodes 2.0 and classic canvas.
 */

import { app } from './comfy/index.js';
import {
    createWidgetVisibilityManager,
    isConfiguringGraph,
    isVueMode,
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
const CLASSIC_WIDGET_NAME = '_dpf_features';
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

function hideClassicBackingWidget(widget) {
    widget.hidden = true;
    if (widget.options) widget.options.hidden = true;
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
            const vueMode = isVueMode();
            const visibility = createWidgetVisibilityManager(node);

            if (vueMode) {
                backingWidget.onRemove?.();
                node.widgets.splice(originalIndex, 1);
            } else {
                hideClassicBackingWidget(backingWidget);
            }

            const chipWidget = createComboChipWidget({
                node,
                options: FEATURE_OPTIONS,
                savedValue: savedFeatures,
                origIdx: originalIndex,
                widgetName: vueMode ? 'features' : CLASSIC_WIDGET_NAME,
                cssPrefix: 'dpf',
                serialize: vueMode,
            });
            node._Eclipse_danbooruFeatureWidget = chipWidget;

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
                    if (!vueMode) {
                        backingWidget.value = encodeFeatures(chipWidget.value);
                    }
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
            if (!vueMode) {
                backingWidget.value = encodeFeatures(savedFeatures);
            }

            visibility.hideInitially(['exclude_tag_categories']);

            const origOnConfigure = node.onConfigure;
            node.onConfigure = function () {
                const configureResult = origOnConfigure?.apply(this, arguments);
                if (!vueMode) {
                    hideClassicBackingWidget(backingWidget);
                    chipWidget.value = normalizeFeatures(backingWidget.value);
                    backingWidget.value = encodeFeatures(chipWidget.value);
                } else {
                    chipWidget.value = normalizeFeatures(chipWidget.value, []);
                }
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
