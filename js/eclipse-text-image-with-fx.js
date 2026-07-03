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
import { canvasDirtyBatcher, createWidgetVisibilityManager, isConfiguringGraph, isVueMode, notifyVue, smartResize } from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'Text Image with FX [Eclipse]';

const GLOW_WIDGETS = ['glow_intensity', 'glow_range', 'glow_blur', 'glow_inner_color', 'glow_outer_color'];
const SHADOW_WIDGETS = ['shadow_offset_x', 'shadow_offset_y', 'shadow_grow', 'shadow_blur', 'shadow_color', 'shadow_opacity'];
const BG_WIDGETS = ['position', 'margin_x', 'margin_y'];
const COLOR_WIDGETS = ['text_color', 'stroke_color', 'glow_inner_color', 'glow_outer_color', 'shadow_color'];

// ---------------------------------------------------------------------------
// Shared hidden <input type="color"> — one per page, reused across nodes
// ---------------------------------------------------------------------------
let _picker = null;
let _pickerCb = null;

function openColorPicker(currentValue, onChange) {
    if (!_picker) {
        _picker = document.createElement('input');
        _picker.type = 'color';
        _picker.style.cssText = 'position:fixed;top:50%;left:50%;width:1px;height:1px;opacity:0.01;pointer-events:none;';
        document.body.appendChild(_picker);
        const fire = (e) => { if (_pickerCb) _pickerCb(e.target.value); };
        _picker.addEventListener('input', fire);
        _picker.addEventListener('change', fire);
    }
    _picker.value = (currentValue && /^#[0-9a-f]{6}$/i.test(currentValue)) ? currentValue : '#000000';
    _pickerCb = onChange;
    _picker.click();
}

app.registerExtension({
    name: 'Eclipse.TextImageWithFX',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            const vis = createWidgetVisibilityManager(node);

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

            // Color picker: replace default click behavior with native color picker.
            //
            // Classic mode:
            //   TextWidget.onClick() opens canvas.prompt() — override onClick on the
            //   instance to shadow the prototype method.  Custom draw renders
            //   a styled widget row with swatch.
            //
            // Vue mode:
            //   String widgets render as inline <input type="text">.  After the DOM
            //   mounts, replace each color input's type with "color" so the browser
            //   opens the native picker on click.
            const COLOR_SET = new Set(COLOR_WIDGETS);
            for (const wName of COLOR_WIDGETS) {
                const w = node.widgets?.find((ww) => ww.name === wName);
                if (!w) continue;

                // --- Classic mode: intercept via onPointerDown ---
                // processWidgetClick checks widget.onPointerDown BEFORE
                // toConcreteWidget (which creates a new TextWidget proxy
                // and loses any instance-level onClick override).
                // Returning true from onPointerDown short-circuits the
                // entire click chain, preventing canvas.prompt().
                w.onPointerDown = function (_pointer, _node, _canvas) {
                    openColorPicker(w.value, (hex) => {
                        w.value = hex;
                        if (w.callback) w.callback(hex);
                        node.setDirtyCanvas?.(true, true);
                    });
                    return true;
                };

                // Custom draw: styled row with swatch
                w.draw = function (ctx, _node, widgetWidth, y, H) {
                    ctx.save();
                    const hex = w.value || '#000000';
                    const margin = 15;

                    // Background
                    ctx.fillStyle = '#232323';
                    ctx.beginPath();
                    ctx.roundRect(margin, y, widgetWidth - margin * 2, H, 4);
                    ctx.fill();

                    // Label
                    ctx.fillStyle = '#aaa';
                    ctx.font = '12px Arial';
                    ctx.textAlign = 'left';
                    ctx.textBaseline = 'middle';
                    ctx.fillText(w.name, margin + 10, y + H * 0.5);

                    // Hex value
                    ctx.fillStyle = '#ddd';
                    ctx.textAlign = 'right';
                    ctx.fillText(hex, widgetWidth - margin - 34, y + H * 0.5);

                    // Color swatch
                    const sw = 20, sh = H - 8, sx = widgetWidth - margin - sw - 6, sy = y + 4;
                    ctx.fillStyle = hex;
                    ctx.beginPath();
                    ctx.roundRect(sx, sy, sw, sh, 3);
                    ctx.fill();
                    ctx.strokeStyle = '#666';
                    ctx.lineWidth = 1;
                    ctx.stroke();
                    ctx.restore();
                };
            }

            // --- Vue mode: patch DOM inputs to type="color" after mount ---
            if (isVueMode()) {
                const patchVueInputs = () => {
                    const el = document.querySelector(`[data-node-id="${node.id}"]`);
                    if (!el) return false;
                    const labels = el.querySelectorAll('.widget-label, label, span');
                    for (const label of labels) {
                        const txt = (label.textContent || '').trim();
                        if (!COLOR_SET.has(txt)) continue;
                        const row = label.closest('.widget-item, .comfy-widget, [class*="widget"]') || label.parentElement;
                        if (!row) continue;
                        const input = row.querySelector('input[type="text"], input:not([type])');
                        if (!input || input._eclipse_color_patched) continue;
                        input._eclipse_color_patched = true;
                        input.type = 'color';
                        input.style.cursor = 'pointer';
                    }
                    return true;
                };
                let attempts = 0;
                const tryPatch = () => {
                    if (patchVueInputs() || ++attempts > 30) return;
                    requestAnimationFrame(tryPatch);
                };
                requestAnimationFrame(tryPatch);
            }

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
