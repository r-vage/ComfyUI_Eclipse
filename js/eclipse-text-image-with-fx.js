/**
 * eclipse-text-image-with-fx.js — Text Image with FX dynamic widget visibility
 *
 * Visibility rules:
 *   enable_glow = true   → show glow_intensity, glow_range, glow_blur, glow_inner_color, glow_outer_color
 *   enable_glow = false  → hide glow widgets
 *   enable_shadow = true  → show shadow_offset_x, shadow_offset_y, shadow_grow, shadow_blur, shadow_color, shadow_opacity
 *   enable_shadow = false → hide shadow widgets
 *   stroke_width > 0     → show stroke_color
 *   stroke_width = 0     → hide stroke_color
 *
 * Color string widgets get a canvas swatch + native color picker on click.
 *
 * Copyright (c) 2026 r-vage. MIT License.
 */
import { app } from './comfy/index.js';
import { createVueColorInputPatcher, installColorPickers } from './eclipse-color-picker-utils.js';
import { canvasDirtyBatcher, createWidgetVisibilityManager, isConfiguringGraph, isVueMode, notifyVue, smartResize } from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'Text Image with FX [Eclipse]';

const GLOW_WIDGETS = ['glow_intensity', 'glow_range', 'glow_blur', 'glow_inner_color', 'glow_outer_color'];
const SHADOW_WIDGETS = ['shadow_offset_x', 'shadow_offset_y', 'shadow_grow', 'shadow_blur', 'shadow_color', 'shadow_opacity'];
const BG_WIDGETS = ['position', 'margin_x', 'margin_y'];
const COLOR_WIDGETS = ['text_color', 'stroke_color', 'glow_inner_color', 'glow_outer_color', 'shadow_color'];

app.registerExtension({
    name: 'Eclipse.TextImageWithFX',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            const vis = createWidgetVisibilityManager(node);
            const scheduleVueColorInputPatch = createVueColorInputPatcher(
                node,
                COLOR_WIDGETS
            );

            // Pre-hide all conditional widgets — defaults: enable_glow=False,
            // enable_shadow=False, stroke_width=0, background_image not connected.
            vis.hideInitially([...GLOW_WIDGETS, ...SHADOW_WIDGETS, ...BG_WIDGETS, 'stroke_color']);

            const hasBgConnected = () => {
                const idx = node.inputs?.findIndex((inp) => inp.name === 'background_image');
                return idx >= 0 && node.inputs[idx].link != null;
            };

            const updateVisibility = () => {
                const d = (name, show) => vis.setVisible(name, show);

                const hasGlow = !!vis.getValue('enable_glow');
                for (const w of GLOW_WIDGETS) d(w, hasGlow);

                const hasShadow = !!vis.getValue('enable_shadow');
                for (const w of SHADOW_WIDGETS) d(w, hasShadow);

                const strokeWidth = parseInt(vis.getValue('stroke_width')) || 0;
                d('stroke_color', strokeWidth > 0);

                // Position/margin only relevant with background image
                const hasBg = hasBgConnected();
                for (const w of BG_WIDGETS) d(w, hasBg);

                smartResize(node);
                if (isVueMode()) {
                    notifyVue(node);
                    canvasDirtyBatcher.markDirty(node, true, true);
                    scheduleVueColorInputPatch();
                }
            };

            // Hook widget callbacks for user-driven visibility changes
            const triggerWidgets = ['enable_glow', 'enable_shadow', 'stroke_width'];
            for (const wName of triggerWidgets) {
                const w = node.widgets?.find((ww) => ww.name === wName);
                if (!w) continue;
                const orig = w.callback;
                w.callback = function () {
                    if (orig) orig.apply(this, arguments);
                    vis.clearCache();
                    vis.markUserDriven();
                    updateVisibility();
                };
            }

            installColorPickers(node, COLOR_WIDGETS);

            // Workflow restore
            const origConfigure = node.onConfigure;
            node.onConfigure = function () {
                if (origConfigure) origConfigure.apply(this, arguments);
                vis.clearCache();
                updateVisibility();
                requestAnimationFrame(() => {
                    vis.clearCache();
                    updateVisibility();
                });
            };

            // Re-evaluate when links change (background_image connect/disconnect)
            const origOnConnectionsChange = node.onConnectionsChange;
            node.onConnectionsChange = function () {
                if (origOnConnectionsChange) origOnConnectionsChange.apply(this, arguments);
                vis.clearCache();
                updateVisibility();
            };

            // Initial visibility — skip during workflow load, onConfigure runs
            // updateVisibility right after with the actual widget/link state.
            if (!isConfiguringGraph()) {
                updateVisibility();
                requestAnimationFrame(() => {
                    vis.clearCache();
                    updateVisibility();
                });
            }

            return ret;
        };
    },
});
