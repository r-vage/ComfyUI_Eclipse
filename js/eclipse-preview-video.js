/**
 * Eclipse Preview Video — resizable DOM video preview (uses shared helper).
 */
import { app } from './comfy/index.js';
import { attachVideoPreview, setVideoPreviewSource, stopVideoPreview } from './eclipse-video-preview-common.js';

const NODE_NAME = 'Preview Video [Eclipse]';

app.registerExtension({
    name: 'Eclipse.PreviewVideo',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;

        const origCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origCreated?.apply(this, arguments);
            attachVideoPreview(this, { sourceType: 'temp' });
        };

        const origExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            origExecuted?.apply(this, arguments);
            const list = message?.eclipse_video;
            if (Array.isArray(list) && list.length > 0) {
                setVideoPreviewSource(this, list[0]);
            }
        };

        const origRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            stopVideoPreview(this);
            origRemoved?.apply(this, arguments);
        };
    },
});
