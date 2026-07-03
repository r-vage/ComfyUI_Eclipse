import {
    app
} from './comfy/index.js';
import {
    smartResize,
    createWidgetVisibilityManager,
    isConfiguringGraph,
} from './eclipse-widget-performance-utils.js';
const SAMPLER_OVERWRITE_NODES = ['Sampler Settings [Eclipse]', 'Sampler Settings NI [Eclipse]', 'Sampler Settings NI v2 [Eclipse]', 'Sampler Settings NI+Seed [Eclipse]', 'Sampler Settings NI+Seed v2 [Eclipse]', 'Sampler Settings NI+Seed v2.1 [Eclipse]', 'Sampler Settings+Seed [Eclipse]', 'Sampler Settings+Seed v2 [Eclipse]', 'Sampler Settings Small [Eclipse]', 'Sampler Settings Small+Seed [Eclipse]', ];
const WIDGETS_TO_HIDE = ['sampler_name', 'scheduler', 'steps', 'cfg'];
app.registerExtension({
    name: 'Eclipse.SamplerOverwrite',
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (!SAMPLER_OVERWRITE_NODES.includes(nodeData.name)) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : void 0;
            const node = this;
            const mgr = createWidgetVisibilityManager(node);
            const updateVisibility = () => {
                const allowOverwrite = mgr.getValue('allow_overwrite');
                for (const name of WIDGETS_TO_HIDE) {
                    mgr.setVisible(name, !allowOverwrite);
                }
                smartResize(node, {
                    minWidth: 0,
                    minHeight: 0,
                    padding: 0
                });
            };
            const overwriteWidget = node.widgets?.find((w) => w.name === 'allow_overwrite');
            if (overwriteWidget) {
                const origCallback = overwriteWidget.callback;
                overwriteWidget.callback = function () {
                    origCallback && origCallback.apply(this, arguments);
                    mgr.markUserDriven();
                    updateVisibility();
                };
            }
            if (!isConfiguringGraph()) {
                updateVisibility();
            }
            const origOnConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                origOnConfigure && origOnConfigure.apply(this, arguments);
                updateVisibility();
            };
            return result;
        };
    },
});
