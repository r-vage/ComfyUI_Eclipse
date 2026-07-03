import {
    app
} from './comfy/index.js';
import {
    createWidgetVisibilityManager,
    isConfiguringGraph,
    smartResize,
} from './eclipse-widget-performance-utils.js';
const NODE_NAME = 'Save Prompt [Eclipse]';
app.registerExtension({
    name: 'Eclipse.SavePrompt',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            const vis = createWidgetVisibilityManager(node);
            // Pre-hide conditional widgets — default extension='txt' hides both
            // csv-only and json-only widgets.
            vis.hideInitially(['csv_positive_name', 'csv_negative_prompt', 'nsfw_level']);
            const csvWidgets = ['csv_positive_name', 'csv_negative_prompt'];
            const jsonWidgets = ['nsfw_level'];
            const updateVisibility = () => {
                if (node.id === -1) return;
                const ext = vis.getValue('extension');
                const isCsv = ext === 'csv';
                const isJson = ext === 'json';
                for (const name of csvWidgets) vis.setVisible(name, isCsv);
                for (const name of jsonWidgets) vis.setVisible(name, isJson);
                smartResize(node);
            };
            const extensionWidget = node.widgets?.find((w) => w.name === 'extension');
            if (extensionWidget) {
                const origCallback = extensionWidget.callback;
                extensionWidget.callback = function () {
                    if (origCallback) origCallback.apply(this, arguments);
                    vis.markUserDriven();
                    updateVisibility();
                };
            }
            if (!node._Eclipse_initialized && !isConfiguringGraph()) {
                node._Eclipse_initialized = true;
                updateVisibility();
            }
            const origConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                if (origConfigure) origConfigure.apply(this, arguments);
                updateVisibility();
            };
            return ret;
        };
    },
});
