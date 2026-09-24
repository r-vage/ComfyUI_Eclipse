/**
 * Eclipse Preview Video — resizable DOM video preview (uses shared helper).
 */
import { app, api } from './comfy/index.js';
import { attachVideoPreview, setVideoPreviewSource, stopVideoPreview } from './eclipse-video-preview-common.js';

const NODE_NAME = 'Preview Video [Eclipse]';

app.registerExtension({
    name: 'Eclipse.PreviewVideo',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;

        const configure = nodeType.prototype.configure;
        nodeType.prototype.configure = function (data) {
            const named = data.widgets_values_named;
            const values = data.widgets_values ?? [];
            const fps = named?.fps ?? values[0];
            const review = named?.stop_review ?? (typeof values[1] === 'boolean' ? values[1] : false);
            const preview = named?.eclipse_preview ?? values.findLast(value => typeof value === 'string');
            const result = configure.apply(this, arguments);
            for (const [name, value] of [['fps', fps], ['stop_review', review], ['eclipse_preview', preview]]) {
                const widget = this.widgets?.find(widget => widget.name === name);
                if (widget && value !== undefined) widget.value = value;
            }
            return result;
        };

        const origCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origCreated?.apply(this, arguments);
            const stop = this.addWidget('button', 'Stop current execution', null, async () => {
                await api.interrupt(null);
            });
            stop.serialize = false;
            attachVideoPreview(this, { sourceType: 'temp', minHeight: 160, minContentHeight: 160 });
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
