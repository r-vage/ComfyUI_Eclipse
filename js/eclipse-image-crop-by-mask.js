/**
 * Image Crop by Mask labels and dimension controls.
 * Copyright (c) 2026 r-vage. MIT License.
 */

import { app } from './comfy/index.js';
import { isVueMode, notifyVue } from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'Image Crop by Mask [Eclipse]';

function dimensionStep(divisibility) {
    // Zero disables divisibility rounding, so allow individual pixels.
    return Math.max(1, Number(divisibility.value) || 1);
}

function alignedDimension(widget, value, step) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return widget.value;
    const { min = 64, max = 16384 } = widget.options;
    // Match the backend's upward rounding, including older saved dimensions.
    return Math.min(max, Math.max(min, Math.ceil(numeric / step) * step));
}

app.registerExtension({
    name: 'Eclipse.ImageCropByMask',
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const original = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = original?.apply(this, arguments);
            const node = this;
            const blur = node.widgets?.find(widget => widget.name === 'mask_blur');
            if (blur) blur.label = 'Mask blur';
            const divisibility = node.widgets?.find(widget => widget.name === 'divisible_by');
            if (!divisibility) return result;
            divisibility.label = 'Divisible by';
            const dimensions = node.widgets.filter(widget =>
                widget.name === 'target_width' || widget.name === 'target_height');

            const syncDimensions = () => {
                const step = dimensionStep(divisibility);
                for (const widget of dimensions) {
                    widget.options ??= {};
                    // Classic uses the legacy 10x step; Nodes 2.0 uses step2.
                    // Keep the registered Vue state in sync when it is separate.
                    for (const options of new Set([widget.options, widget._state?.options])) {
                        if (!options) continue;
                        options.step = step * 10;
                        options.step2 = step;
                    }
                    widget.value = alignedDimension(widget, widget.value, step);
                }
                if (isVueMode()) notifyVue(node);
                node.setDirtyCanvas?.(true, true);
            };

            for (const widget of dimensions) {
                const callback = widget.callback;
                widget.callback = function (value, ...args) {
                    widget.value = alignedDimension(widget, value, dimensionStep(divisibility));
                    // Normalize before the native INT callback can round down.
                    return callback?.call(this, widget.value, ...args);
                };
            }
            const divisibilityCallback = divisibility.callback;
            divisibility.callback = function (...args) {
                const value = divisibilityCallback?.apply(this, args);
                syncDimensions();
                return value;
            };
            const onConfigure = node.onConfigure;
            node.onConfigure = function (...args) {
                const value = onConfigure?.apply(this, args);
                syncDimensions();
                return value;
            };
            syncDimensions();
            return result;
        };
    },
});
