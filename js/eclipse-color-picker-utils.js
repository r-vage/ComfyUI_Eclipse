/**
 * Shared Nodes 2.0 color-input compatibility helpers.
 *
 * Copyright (c) 2026 r-vage. MIT License.
 */

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

    return function scheduleVueColorInputPatch() {
        const currentGeneration = ++generation;
        let attempts = 0;
        const tryPatch = () => {
            if (currentGeneration !== generation) return;
            if (patchVueColorInputs(node, colorWidgetNames) || ++attempts > 30) return;
            requestAnimationFrame(tryPatch);
        };
        requestAnimationFrame(tryPatch);
    };
}
