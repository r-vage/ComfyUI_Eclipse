/**
 * Lyric caption colors and animation controls.
 * Copyright (c) 2026 r-vage. MIT License.
 */
import { app } from './comfy/index.js';
import { createVueColorInputPatcher, installColorPickers } from './eclipse-color-picker-utils.js';
import {
    createWidgetVisibilityManager, isConfiguringGraph, smartResize,
} from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'Render Lyric Captions [Eclipse]';
const COLORS = ['text_color', 'highlight_color', 'outline_color', 'background_color',
    'glow_inner_color', 'glow_outer_color'];
const GLOW = ['glow_intensity', 'glow_range', 'glow_blur', 'glow_inner_color', 'glow_outer_color'];
const FLOATING = ['circle_radius', 'float_distance', 'max_simultaneous', 'seed'];
const SHARED_ANIMATION = ['fade_in', 'fade_out', 'min_display'];
const ANIMATION = [...FLOATING, ...SHARED_ANIMATION, 'max_words', 'rotation_axis'];

app.registerExtension({
    name: 'Eclipse.LyricCaptions',
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            installColorPickers(this, COLORS);
            const schedule = createVueColorInputPatcher(this, COLORS);
            const node = this;
            const visibility = createWidgetVisibilityManager(node);
            visibility.hideInitially([...ANIMATION, ...GLOW]);
            const refresh = () => {
                if (node.id === -1) return;
                const mode = node.widgets.find(w => w.name === 'mode')?.value;
                const floating = mode === 'floating-words' || mode === 'floating-lines';
                const rotating = mode === 'rotating-words' || mode === 'rotating-lines';
                for (const name of FLOATING) visibility.setVisible(name, floating);
                for (const name of SHARED_ANIMATION) visibility.setVisible(name, floating || rotating);
                visibility.setVisible('max_words', mode === 'floating-words' || mode === 'rotating-words');
                visibility.setVisible('rotation_axis', rotating);
                visibility.setVisible('position', !floating);
                visibility.setVisible('highlight_color', mode === 'active-word');
                const glow = !!node.widgets.find(w => w.name === 'enable_glow')?.value;
                for (const name of GLOW) visibility.setVisible(name, glow);
                smartResize(node);
                schedule();
            };
            for (const name of ['mode', 'enable_glow']) {
                const widget = node.widgets.find(w => w.name === name);
                const callback = widget.callback;
                widget.callback = function () {
                    const result = callback?.apply(this, arguments);
                    visibility.markUserDriven();
                    refresh();
                    return result;
                };
            }
            for (const event of ['onConfigure', 'onAdded', 'onConnectionsChange']) {
                const original = this[event];
                this[event] = function () {
                    const result = original?.apply(this, arguments);
                    refresh();
                    return result;
                };
            }
            if (!isConfiguringGraph()) {
                refresh();
                if (node.id !== -1 && !node.flags?.collapsed) {
                    const previousHeight = node.size[1];
                    node.size[1] = 0;
                    const computed = node.computeSize();
                    node.size[1] = previousHeight;
                    node.setSize?.([node.size[0], computed[1]]);
                }
            }
            schedule();
            return result;
        };
    },
});
