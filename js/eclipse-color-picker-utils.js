/**
 * Shared Nodes 2.0 color-input compatibility helpers.
 *
 * Copyright (c) 2026 r-vage. MIT License.
 */
import { isVueMode, onVueModeChange } from './eclipse-widget-performance-utils.js';

/** Keep classic painting without opting into Nodes 2.0's legacy renderer. */
export function installClassicColorDraw(widget, draw) {
    Object.defineProperty(widget, 'draw', {
        configurable: true,
        enumerable: true,
        get() { return isVueMode() ? undefined : draw; },
    });
}

const COLOR_INPUT_CLASS = 'eclipse-fx-color-picker';
const COLOR_INPUT_STYLE_ID = 'eclipse-fx-color-picker-styles';

function injectColorInputStyles() {
    if (document.getElementById(COLOR_INPUT_STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = COLOR_INPUT_STYLE_ID;
    style.textContent = `
        input.${COLOR_INPUT_CLASS} {
            appearance: none !important;
            -webkit-appearance: none !important;
            min-height: 2rem;
            padding: 4px !important;
            overflow: hidden;
            cursor: pointer;
        }
        input.${COLOR_INPUT_CLASS}::-webkit-color-swatch-wrapper {
            width: 100%;
            height: 100%;
            padding: 0;
        }
        input.${COLOR_INPUT_CLASS}::-webkit-color-swatch {
            min-height: 1.5rem;
            border: 0;
            border-radius: 6px;
        }
        input.${COLOR_INPUT_CLASS}::-moz-color-swatch {
            min-height: 1.5rem;
            border: 0;
            border-radius: 6px;
        }
    `;
    document.head.appendChild(style);
}

function patchColorInput(input) {
    injectColorInputStyles();
    input._eclipse_color_patched = true;
    input.type = 'color';
    input.classList.add(COLOR_INPUT_CLASS);
    input.style.cursor = 'pointer';
}

function getVisibleColorNames(node, colorWidgetNames) {
    const names = new Set();
    for (const name of colorWidgetNames) {
        const widget = node.widgets?.find((candidate) => candidate.name === name);
        if (!widget || widget.hidden || widget.options?.hidden) continue;
        names.add(name);
    }
    return names;
}

/**
 * Patch every currently visible color-string widget rendered by Nodes 2.0.
 * The aria-label path matches current ComfyUI. The label traversal remains as
 * a compatibility fallback for older frontend DOM layouts.
 */
export function patchVueColorInputs(node, colorWidgetNames) {
    const element = document.querySelector(`[data-node-id="${node.id}"]`);
    if (!element) return false;

    const pendingNames = getVisibleColorNames(node, colorWidgetNames);

    // Current Nodes 2.0 exposes the widget name directly on the input.
    for (const input of element.querySelectorAll('input[aria-label]')) {
        const name = input.getAttribute('aria-label');
        if (!pendingNames.has(name)) continue;
        patchColorInput(input);
        pendingNames.delete(name);
    }

    // Older frontend fallback: find the widget row through its visible label.
    if (pendingNames.size) {
        const labels = element.querySelectorAll('.widget-label, label, span');
        for (const label of labels) {
            const name = (label.textContent || '').trim();
            if (!pendingNames.has(name)) continue;
            const row = label.closest(
                '.widget-item, .comfy-widget, [class*="widget"]'
            ) || label.parentElement;
            if (!row) continue;
            const input = row.querySelector(
                'input[type="text"], input:not([type]), input[type="color"]'
            );
            if (!input) continue;
            patchColorInput(input);
            pendingNames.delete(name);
        }
    }

    return pendingNames.size === 0;
}

/**
 * Return a coalescing rAF scheduler that waits for Vue to mount every visible
 * color widget. Calling it again cancels the prior retry generation.
 */
export function createVueColorInputPatcher(node, colorWidgetNames) {
    let generation = 0;
    let disposed = false;
    let frame = null;

    function scheduleVueColorInputPatch() {
        const currentGeneration = ++generation;
        if (frame !== null) cancelAnimationFrame(frame);
        frame = null;
        if (disposed || !isVueMode()) return;
        let attempts = 0;
        const tryPatch = () => {
            frame = null;
            if (currentGeneration !== generation) return;
            if (patchVueColorInputs(node, colorWidgetNames) || ++attempts > 30) return;
            frame = requestAnimationFrame(tryPatch);
        };
        frame = requestAnimationFrame(tryPatch);
    }

    const unsubscribe = onVueModeChange(scheduleVueColorInputPatch);
    const originalOnRemoved = node.onRemoved;
    node.onRemoved = function () {
        disposed = true;
        scheduleVueColorInputPatch();
        unsubscribe();
        return originalOnRemoved?.apply(this, arguments);
    };
    return scheduleVueColorInputPatch;
}

// One native classic picker per page; release the callback when its node leaves.
let picker = null;
let pickerOwner = null;
let pickerCallback = null;

function releasePicker(node) {
    if (pickerOwner !== node) return;
    picker?.remove();
    picker = pickerOwner = pickerCallback = null;
}

function openColorPicker(node, value, callback) {
    if (!picker) {
        picker = document.createElement('input');
        picker.type = 'color';
        picker.style.cssText = 'position:fixed;top:50%;left:50%;width:1px;height:1px;opacity:0.01;pointer-events:none;';
        const fire = event => pickerCallback?.(event.target.value);
        picker.addEventListener('input', fire);
        picker.addEventListener('change', fire);
        document.body.appendChild(picker);
    }
    pickerOwner = node;
    pickerCallback = callback;
    picker.value = /^#[0-9a-f]{6}$/i.test(value) ? value : '#000000';
    picker.click();
}

/** Install identical classic swatches and pointer behavior for color strings. */
export function installColorPickers(node, names) {
    for (const name of names) {
        const widget = node.widgets?.find(candidate => candidate.name === name);
        if (!widget) continue;
        widget.onPointerDown = function () {
            openColorPicker(node, widget.value, hex => {
                widget.value = hex;
                widget.callback?.(hex);
                node.setDirtyCanvas?.(true, true);
            });
            return true;
        };
        installClassicColorDraw(widget, function (ctx, _node, width, y, height) {
            ctx.save();
            const hex = widget.value || '#000000';
            const margin = 15;
            ctx.fillStyle = '#232323';
            ctx.beginPath();
            ctx.roundRect(margin, y, width - margin * 2, height, 4);
            ctx.fill();
            ctx.fillStyle = '#aaa';
            ctx.font = '12px Arial';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillText(widget.name, margin + 10, y + height * 0.5);
            ctx.fillStyle = '#ddd';
            ctx.textAlign = 'right';
            ctx.fillText(hex, width - margin - 34, y + height * 0.5);
            ctx.fillStyle = hex;
            ctx.beginPath();
            ctx.roundRect(width - margin - 26, y + 4, 20, height - 8, 3);
            ctx.fill();
            ctx.strokeStyle = '#666';
            ctx.lineWidth = 1;
            ctx.stroke();
            ctx.restore();
        });
    }
    const originalRemoved = node.onRemoved;
    node.onRemoved = function () {
        releasePicker(node);
        return originalRemoved?.apply(this, arguments);
    };
}
