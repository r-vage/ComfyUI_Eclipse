/**
 * Eclipse — Show Text
 *
 * Universal text preview — displays any input as a read-only text widget.
 * Inspired by ComfyUI core PreviewAny / pysssss ShowText.
 */

import { app } from './comfy/index.js';
import {
    createDOMTextController,
    DOM_TEXT_PREVIEW_NAME
} from './eclipse-dom-text.js';
import {
    publishSubgraphDOMPreview,
    registerSubgraphDOMPreviewProvider
} from './eclipse-subgraph-dom-previews.js';

const NODE_NAMES = ['Show Text [Eclipse]', 'Show Text [Stop] [Eclipse]'];
const STANDARD_NODE_NAME = 'Show Text [Eclipse]';

function addReadonlyTextWidget(node, name, value) {
    return createDOMTextController(node, {
        name,
        value,
        variant: 'comfy',
    }).widget;
}

function registerTextProvider(node) {
    node._eclipseSubgraphTextValue ??= [];
    registerSubgraphDOMPreviewProvider(node, {
        name: DOM_TEXT_PREVIEW_NAME,
        kind: 'text',
        label: 'Text preview',
        createProjection(host, projection) {
            const projected = createDOMTextController(host, { name: projection.name });
            return {
                clear: projected.clear,
                dispose: projected.dispose,
                setValue: projected.setValue,
                widget: projected.widget,
            };
        },
        getCurrentValue: () => node._eclipseSubgraphTextValue,
        readOutput: output => output?.text ?? [],
    });
}

app.registerExtension({
    name: 'Eclipse.ShowText',
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (!NODE_NAMES.includes(nodeData.name)) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            onNodeCreated?.apply(this, arguments);
            // Add initial empty widget so it appears before first execution
            // and can be exposed to subgraph properties
            if (!this.widgets?.find(w => w.name === 'text_0')) {
                addReadonlyTextWidget(this, 'text_0', '');
            }
            registerTextProvider(this);
            const stopWidget = this.widgets?.find(w => w.name === 'stop_review');
            if (stopWidget) {
                const idx = this.widgets.indexOf(stopWidget);
                this.widgets.splice(idx, 1);
                this.widgets.push(stopWidget);
            }
            // ComfyUI calculates a node's initial size before extension
            // onNodeCreated hooks run. The standard node has no native widgets,
            // so include the DOM preview in its fresh-add size explicitly. The
            // Stop variant already receives an adequate initial size from its
            // boolean widget and must retain that layout.
            if (nodeData.name === STANDARD_NODE_NAME) {
                const computed = this.computeSize();
                this.setSize?.([
                    Math.max(this.size[0], computed[0]),
                    Math.max(this.size[1], computed[1]),
                ]);
            }
        };

        function populate(text, options = {}) {
            // text arrives as a tuple (value,) from the backend ui dict.
            const values = Array.isArray(text) ? text : [text];
            this._eclipseSubgraphTextValue = values;
            let textWidget = this.widgets?.find(w => w.name === 'text_0');
            if (!textWidget) {
                textWidget = addReadonlyTextWidget(this, 'text_0', '');
            }
            // Keep the renderer-bound widget identity stable. Replacing it after
            // execution leaves Nodes 2's widget model pointing at the empty one.
            textWidget.value = values;

            const stopWidget = this.widgets?.find(w => w.name === 'stop_review');
            if (stopWidget) {
                const idx = this.widgets.indexOf(stopWidget);
                this.widgets.splice(idx, 1);
                this.widgets.push(stopWidget);
            }

            requestAnimationFrame(() => {
                const sz = this.computeSize();
                if (sz[0] < this.size[0]) sz[0] = this.size[0];
                if (sz[1] < this.size[1]) sz[1] = this.size[1];
                this.onResize?.(sz);
                app.graph.setDirtyCanvas(true, false);
            });
            if (options.publish !== false) {
                publishSubgraphDOMPreview(this, DOM_TEXT_PREVIEW_NAME, values);
            }
        }

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            onExecuted?.apply(this, arguments);
            populate.call(this, message.text, { publish: false });
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
                    const stopIdx = this.widgets?.findIndex(w => w.name === 'stop_review') ?? -1;
                    const startIdx = +(widgets_values.length > 1 && this.inputs?.[0]?.widget);
                    const endIdx = (stopIdx !== -1 && widgets_values.length > stopIdx) ? stopIdx : widgets_values.length;
                    populate.call(
                        this,
                        widgets_values.slice(startIdx, Math.max(startIdx, endIdx)),
                    );
                });
            }
        };
    },
});
