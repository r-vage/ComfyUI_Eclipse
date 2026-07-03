/**
 * eclipse-image-rescale.js — Image Rescale dynamic widget visibility
 *
 * Visibility rules:
 *   mode = "rescale" → show rescale_factor; hide resize_width, resize_height
 *   mode = "resize"  → show resize_width, resize_height; hide rescale_factor
 *   supersample off  → hide supersample_factor
 *
 * Always visible: image, mode, resampling, supersample
 *
 * Copyright (c) 2026 r-vage. MIT License.
 */
import { app } from './comfy/index.js';
import { canvasDirtyBatcher, createWidgetVisibilityManager, isVueMode, notifyVue, smartResize } from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'Image Rescale [Eclipse]';

app.registerExtension({
    name: 'Eclipse.ImageRescale',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            const vis = createWidgetVisibilityManager(node);

            // Pre-hide conditional widgets hidden at defaults
            // (mode='rescale', supersample=true → only resize_* hidden).
            vis.hideInitially(['resize_width', 'resize_height']);

            const updateVisibility = () => {
                const d = (name, show) => vis.setVisible(name, show);

                const mode = vis.getValue('mode') || 'rescale';
                const isRescale = mode === 'rescale';
                d('rescale_factor', isRescale);
                d('resize_width', !isRescale);
                d('resize_height', !isRescale);

                const supersample = vis.getValue('supersample');
                d('supersample_factor', !!supersample);

                smartResize(node);
                if (isVueMode()) {
                    notifyVue(node);
                    canvasDirtyBatcher.markDirty(node, true, true);
                }
            };

            const debouncedUpdate = () => updateVisibility();

            // Hook widget callbacks for user-driven changes
            const triggerWidgets = ['mode', 'supersample'];
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
