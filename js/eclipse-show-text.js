/**
 * Eclipse — Show Text
 *
 * Universal text preview — displays any input as a read-only text widget.
 * The DOM widget value is serialized so it persists in subgraphs.
 * Inspired by ComfyUI core PreviewAny / pysssss ShowText.
 */

import { app } from './comfy/index.js';

const NODE_NAME = 'Show Text [Eclipse]';

// Build a read-only multiline text DOM widget.
// serialize defaults to true so the value persists in subgraphs.
function addReadonlyTextWidget(node, name, value) {
    const textarea = document.createElement('textarea');
    textarea.className = 'comfy-multiline-input';
    textarea.readOnly = true;
    textarea.value = value ?? '';
    textarea.style.opacity = '0.6';
    textarea.style.width = '100%';
    textarea.style.height = '100%';
    textarea.style.boxSizing = 'border-box';
    textarea.style.resize = 'none';
    const widget = node.addDOMWidget(name, 'customtext', textarea, {
        getValue() { return textarea.value; },
        setValue(v) { textarea.value = v ?? ''; },
        serialize: false,
    });
    widget.serialize = false;
    widget.inputEl = textarea;
    return widget;
}

app.registerExtension({
    name: 'Eclipse.ShowText',
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name !== NODE_NAME) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            onNodeCreated?.apply(this, arguments);
            // Add initial empty widget so it appears before first execution
            // and can be exposed to subgraph properties
            if (!this.widgets?.find(w => w.name === 'text_0')) {
                addReadonlyTextWidget(this, 'text_0', '');
            }
        };

        function populate(text) {
            // Remove old text widgets, keep the converted-input widget (if any)
            // at index 0 so the input slot stays aligned.
            if (this.widgets) {
                const isConvertedWidget = +!!(this.inputs?.[0]?.widget);
                for (let i = isConvertedWidget; i < this.widgets.length; i++) {
                    this.widgets[i].onRemove?.();
                }
                this.widgets.length = isConvertedWidget;
            }

            // text arrives as a tuple (value,) from the backend ui dict.
            const values = Array.isArray(text) ? text : [text];

            for (const t of values) {
                const str = (t == null) ? '' : String(t);
                const w = addReadonlyTextWidget(
                    this,
                    'text_' + (this.widgets?.length ?? 0),
                    str,
                );
                w.value = str;
            }

            requestAnimationFrame(() => {
                const sz = this.computeSize();
                if (sz[0] < this.size[0]) sz[0] = this.size[0];
                if (sz[1] < this.size[1]) sz[1] = this.size[1];
                this.onResize?.(sz);
                app.graph.setDirtyCanvas(true, false);
            });
        }

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            onExecuted?.apply(this, arguments);
            populate.call(this, message.text);
        };

        // Preserve widgets_values across configure() so reload restores text.
        const VALUES = Symbol();
        const configure = nodeType.prototype.configure;
        nodeType.prototype.configure = function () {
            this[VALUES] = arguments[0]?.widgets_values;
            return configure?.apply(this, arguments);
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            onConfigure?.apply(this, arguments);
            const widgets_values = this[VALUES];
            if (widgets_values?.length) {
                requestAnimationFrame(() => {
                    populate.call(
                        this,
                        widgets_values.slice(+(widgets_values.length > 1 && this.inputs?.[0]?.widget)),
                    );
                });
            }
        };
    },
});
