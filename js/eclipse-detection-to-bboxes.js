import {
    app
} from './comfy/index.js';
import {
    debounce,
    canvasDirtyBatcher,
    isVueMode,
    notifyVue,
    smartResize
} from './eclipse-widget-performance-utils.js';
const NODE_NAME = 'Detection to Bboxes [Eclipse]';
app.registerExtension({
    name: 'Eclipse.DetectionToBboxes',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            const setVisible = (name, visible) => {
                const w = node.widgets?.find((w) => w.name === name);
                if (w) {
                    w.hidden = !visible;
                    if (w.options) w.options.hidden = !visible;
                }
            };
            const getValue = (name) => {
                const w = node.widgets?.find((w) => w.name === name);
                return w ? w.value : null;
            };
            const updateVisibility = () => {
                const getMask = getValue('get_mask_from_image');
                const combineMasks = getValue('combine_masks');
                setVisible('detect_color', getMask);
                setVisible('threshold', getMask);
                setVisible('min_area', getMask);
                setVisible('indices', !combineMasks);
                if (isVueMode()) notifyVue(node);
                smartResize(node);
            };
            const debouncedUpdate = debounce(updateVisibility, 100);
            const getMaskW = node.widgets?.find((w) => w.name === 'get_mask_from_image');
            if (getMaskW) {
                const origCb = getMaskW.callback;
                getMaskW.callback = function () {
                    if (origCb) origCb.apply(this, arguments);
                    debouncedUpdate();
                };
            }
            const combineMasksW = node.widgets?.find((w) => w.name === 'combine_masks');
            if (combineMasksW) {
                const origCb = combineMasksW.callback;
                combineMasksW.callback = function () {
                    if (origCb) origCb.apply(this, arguments);
                    debouncedUpdate();
                };
            }
            if (!node._Eclipse_initialized) {
                node._Eclipse_initialized = true;
                updateVisibility();
            }
            const origConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                if (origConfigure) origConfigure.apply(this, arguments);
                setTimeout(() => updateVisibility(), 100);
            };
            return ret;
        };
    },
});
