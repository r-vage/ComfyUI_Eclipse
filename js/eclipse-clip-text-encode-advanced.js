import { app } from './comfy/index.js';
import { createWidgetVisibilityManager, smartResize } from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'CLIP Text Encode (Advanced) [Eclipse]';

app.registerExtension({
    name: 'Eclipse.CLIPTextEncodeAdvanced',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : void 0;
            const node = this;
            const vis = createWidgetVisibilityManager(node);
            node._Eclipse_vis = vis;

            // Initially hide the per_layer_weights widget unless it is set to custom
            const updateVisibility = () => {
                const rebalancePreset = vis.getValue('rebalance_preset');
                const isCustom = rebalancePreset === 'custom';
                vis.setVisible('per_layer_weights', isCustom);
                smartResize(node);
            };

            // Set up callback/listener for changes to rebalance_preset
            const rebalancePresetWidget = node.widgets?.find(w => w.name === 'rebalance_preset');
            if (rebalancePresetWidget) {
                const origCallback = rebalancePresetWidget.callback;
                rebalancePresetWidget.callback = function (value) {
                    origCallback?.call(this, value);
                    vis.markUserDriven();
                    updateVisibility();
                };
            }

            // Sync visibility initially and on configure
            const origOnConfigure = node.onConfigure;
            node.onConfigure = function (config) {
                origOnConfigure && origOnConfigure.apply(this, arguments);
                updateVisibility();
            };

            // Run initial visibility check
            setTimeout(() => {
                updateVisibility();
            }, 1);

            return ret;
        };
    }
});
