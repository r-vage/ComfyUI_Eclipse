/**
 * eclipse-image-resize.js — Image Resize dynamic widget visibility
 *
 * Visibility rules:
 *   scale_to = "custom"  → show custom_width, custom_height; hide size, aspect_ratio
 *   scale_to ≠ "custom"  → show size, aspect_ratio; hide custom_width, custom_height
 *   fit = "resize" | "stretch"                        → hide crop_position, pad_color
 *   fit = "pad"                                       → show crop_position, pad_color
 *   fit = "crop"|"pad_edge"|"pad_edge_pixel"|"pillarbox_blur" → show crop_position; hide pad_color
 *
 * Always visible: image, scale_to, fit, method, divisible_by, mask, device
 *
 * Copyright (c) 2026 r-vage. MIT License.
 */
import { app } from './comfy/index.js';
import { canvasDirtyBatcher, createWidgetVisibilityManager, isVueMode, notifyVue, smartResize } from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'Image Resize [Eclipse]';

app.registerExtension({
    name: 'Eclipse.ImageResize',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            const vis = createWidgetVisibilityManager(node);

            // Pre-hide conditional widgets hidden at defaults
            // (scale_to='longest', fit='resize').
            vis.hideInitially(['custom_width', 'custom_height', 'crop_position', 'pad_color']);

            const updateVisibility = () => {
                const d = (name, show) => vis.setVisible(name, show);

                const scaleTo = vis.getValue('scale_to') || 'longest';
                const isCustom = scaleTo === 'custom';

                // scale_to group
                d('size', !isCustom);
                d('custom_width', isCustom);
                d('custom_height', isCustom);
                d('aspect_ratio', !isCustom);

                // fit sub-widgets
                const fit = vis.getValue('fit') || 'resize';
                const needsCropPos = ['crop', 'pad', 'pad_edge', 'pad_edge_pixel', 'pillarbox_blur'].includes(fit);
                const needsPadColor = fit === 'pad';
                d('crop_position', needsCropPos);
                d('pad_color', needsPadColor);

                smartResize(node);
                if (isVueMode()) {
                    notifyVue(node);
                    canvasDirtyBatcher.markDirty(node, true, true);
                }
            };

            const debouncedUpdate = () => updateVisibility();

            // Hook widget callbacks for user-driven changes
            const triggerWidgets = ['scale_to', 'fit'];
            for (const wName of triggerWidgets) {
                const w = node.widgets?.find((ww) => ww.name === wName);
                if (!w) continue;
                const orig = w.callback;
                w.callback = function () {
                    if (orig) orig.apply(this, arguments);
                    vis.clearCache();
                    vis.markUserDriven();
                    debouncedUpdate();
                };
            }

            // Workflow restore
            const origConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                if (origConfigure) origConfigure.apply(this, arguments);
                vis.clearCache();
                updateVisibility();
                requestAnimationFrame(() => {
                    vis.clearCache();
                    updateVisibility();
                });
            };

            // Initial visibility
            updateVisibility();
            requestAnimationFrame(() => {
                vis.clearCache();
                updateVisibility();
            });

            return ret;
        };
    },
});
