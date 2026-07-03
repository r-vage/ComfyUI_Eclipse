import {
    app
} from './comfy/index.js';
import {
    notifyVue,
    smartResize,
    isConfiguringGraph,
    isVueMode,
} from './eclipse-widget-performance-utils.js';
const NODE_NAME = 'Video Resolution [Eclipse]';
app.registerExtension({
    name: 'Eclipse.VideoResolution',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            const resolutionW = node.widgets?.find((w) => w.name === 'resolution');
            const widthW = node.widgets?.find((w) => w.name === 'width');
            const heightW = node.widgets?.find((w) => w.name === 'height');
            if (!resolutionW || !widthW || !heightW) {
                console.warn('[Eclipse.VideoResolution] Required widgets not found');
                return ret;
            }
            const setVisible = (widget, visible) => {
                if (widget) {
                    widget.hidden = !visible;
                    if (widget.options) widget.options.hidden = !visible;
                }
            };
            const updateVisibility = (value) => {
                const isCustom = value === 'Custom';
                setVisible(widthW, isCustom);
                setVisible(heightW, isCustom);
                if (isVueMode()) notifyVue(node);
                smartResize(node, {
                    minWidth: 0,
                    minHeight: 50,
                    padding: 0
                });
            };
            const origCallback = resolutionW.callback;
            resolutionW.callback = function (value) {
                if (origCallback) origCallback.apply(this, arguments);
                updateVisibility(value);
            };
            // Skip initial pass during workflow load — onConfigure runs the
            // equivalent logic next with serialized state.
            if (!isConfiguringGraph()) {
                updateVisibility(resolutionW.value);
            }
            return ret;
        };
        const origConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (data) {
            if (origConfigure) origConfigure.apply(this, arguments);
            const node = this;
            const resolutionW = node.widgets?.find((w) => w.name === 'resolution');
            if (!resolutionW) return;
            const isCustom = resolutionW.value === 'Custom';
            const widthW = node.widgets?.find((w) => w.name === 'width');
            const heightW = node.widgets?.find((w) => w.name === 'height');
            const setVisible = (widget, visible) => {
                if (widget) {
                    widget.hidden = !visible;
                    if (widget.options) widget.options.hidden = !visible;
                }
            };
            setVisible(widthW, isCustom);
            setVisible(heightW, isCustom);
            if (isVueMode()) notifyVue(node);
            smartResize(node, {
                minWidth: 0,
                minHeight: 50,
                padding: 0
            });
        };
    },
});
