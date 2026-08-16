/**
 * Eclipse — reusable read-only DOM text preview controller.
 */

import { captureScrollableWheelInVue } from './eclipse-widget-performance-utils.js';

export const DOM_TEXT_PREVIEW_NAME = '_eclipse_dom_text';
export const DOM_TEXT_MIN_HEIGHT = 36;

export function normalizeDOMText(value) {
    const values = Array.isArray(value) ? value : [value];
    return values.map(item => item == null ? '' : String(item)).join('\n\n');
}

export function createDOMTextController(node, options = {}) {
    const minHeight = options.minHeight ?? DOM_TEXT_MIN_HEIGHT;
    const textarea = document.createElement('textarea');
    textarea.readOnly = true;
    textarea.value = normalizeDOMText(options.value ?? '');
    textarea.style.width = '100%';
    textarea.style.height = '100%';
    textarea.style.boxSizing = 'border-box';
    textarea.style.resize = 'none';

    let element = textarea;
    if (options.variant !== 'comfy') {
        const wrapper = document.createElement('div');
        wrapper.style.cssText = 'display:flex;flex-direction:column;width:100%;height:100%;gap:4px;';
        textarea.style.cssText += 'flex:1 1 auto;border:none;outline:none;background:#1a1a1a;' +
            'color:#ccc;font:12px monospace;padding:6px;cursor:default;overflow-y:auto;' +
            'border-radius:4px;min-height:30px;';
        wrapper.appendChild(textarea);
        element = wrapper;
    } else {
        textarea.className = 'comfy-multiline-input';
        textarea.style.opacity = '0.6';
    }

    const widget = node.addDOMWidget(
        options.name ?? DOM_TEXT_PREVIEW_NAME,
        options.type ?? 'custom',
        element,
        {
            getMinHeight: () => minHeight,
            getValue: () => textarea.value,
            hideOnZoom: false,
            serialize: false,
            setValue: value => { textarea.value = normalizeDOMText(value); },
        },
    );
    widget.computeLayoutSize = () => ({
        minHeight,
        minWidth: 100,
        maxHeight: undefined,
    });
    widget.inputEl = textarea;
    widget.serialize = false;

    const disposeWheelCapture = captureScrollableWheelInVue(textarea);
    const onRemove = widget.onRemove;
    let disposed = false;
    const dispose = () => {
        if (disposed) return;
        disposed = true;
        disposeWheelCapture();
    };
    widget.onRemove = function () {
        dispose();
        return onRemove?.apply(this, arguments);
    };

    return {
        clear() {
            textarea.value = '';
        },
        dispose,
        element,
        getValue() {
            return textarea.value;
        },
        setValue(value) {
            textarea.value = normalizeDOMText(value);
        },
        textarea,
        widget,
    };
}

