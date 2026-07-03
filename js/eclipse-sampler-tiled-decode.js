import { app, api } from './comfy/index.js';

app.registerExtension({
    name: 'Eclipse.SamplerTiledDecodeVisibility',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        const samplerNodeTypes = ['Eclipse KSampler (Pipe) [Eclipse]', 'Eclipse KSampler (Kargim) [Eclipse]'];
        if (!samplerNodeTypes.includes(nodeData.name)) return;

        const origOnExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (output) {
            const res = origOnExecuted ? origOnExecuted.apply(this, arguments) : undefined;
            const previewModeWidget = this.widgets?.find(w => w.name === 'preview_mode');
            if (previewModeWidget?.value === "None") {
                this.imgs = null;
                this.images = null;
                this.preview = null;
                if (app.nodeOutputs?.[this.id]) {
                    delete app.nodeOutputs[this.id].images;
                }
                if (app.nodePreviewImages?.[this.id]) {
                    delete app.nodePreviewImages[this.id];
                }
                const previewWidgetIdx = this.widgets.findIndex(w => w.name === '$$canvas-image-preview' || w.type === 'IMAGE_PREVIEW');
                if (previewWidgetIdx > -1) {
                    const widget = this.widgets[previewWidgetIdx];
                    widget.onRemove?.();
                    this.widgets.splice(previewWidgetIdx, 1);
                }
                const size = this.computeSize();
                const width = this.size ? this.size[0] : size[0];
                this.setSize([width, size[1]]);
                this.setDirtyCanvas(true, true);
            } else {
                if (output?.images) {
                    this.imgs = output.images.map(img => {
                        const image = new Image();
                        image.src = api.apiURL(`/view?filename=${encodeURIComponent(img.filename)}&type=${encodeURIComponent(img.type)}&subfolder=${encodeURIComponent(img.subfolder)}`);
                        return image;
                    });
                    app.nodePreviewImages[this.id] = this.imgs.map(img => img.src);
                    this.setDirtyCanvas(true, true);
                }
            }
            return res;
        };

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const origResult = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;

            const tiledDecodeWidget = this.widgets.find(w => w.name === 'tiled_decode');
            const tileSizeWidget = this.widgets.find(w => w.name === 'tile_size');

            if (tiledDecodeWidget && tileSizeWidget) {
                const updateVisibility = () => {
                    const isTiled = !!tiledDecodeWidget.value;

                    if (isTiled) {
                        if (tileSizeWidget.type === "hidden" && tileSizeWidget.origType) {
                            tileSizeWidget.type = tileSizeWidget.origType;
                            if (tileSizeWidget.origComputeSize) {
                                tileSizeWidget.computeSize = tileSizeWidget.origComputeSize;
                            } else {
                                delete tileSizeWidget.computeSize;
                            }
                        }
                    } else {
                        if (tileSizeWidget.type !== "hidden") {
                            tileSizeWidget.origType = tileSizeWidget.type;
                            tileSizeWidget.origComputeSize = tileSizeWidget.computeSize;
                            tileSizeWidget.type = "hidden";
                            tileSizeWidget.computeSize = () => [0, -4];
                        }
                    }
                    const size = this.computeSize();
                    const width = this.size ? this.size[0] : size[0];
                    this.setSize([width, size[1]]);
                    this.setDirtyCanvas(true, true);
                };

                // Add listener / callback on tiled_decode value change
                const origCallback = tiledDecodeWidget.callback;
                tiledDecodeWidget.callback = function (val) {
                    const res = origCallback ? origCallback.apply(this, arguments) : undefined;
                    updateVisibility();
                    return res;
                };

                // Run initially to set correct state on load/creation
                setTimeout(updateVisibility, 0);
            }

            const previewModeWidget = this.widgets.find(w => w.name === 'preview_mode');
            if (previewModeWidget) {
                const updatePreviewVisibility = (val) => {
                    if (val === "None") {
                        node.imgs = null;
                        node.images = null;
                        node.preview = null;
                        if (app.nodeOutputs?.[node.id]) {
                            delete app.nodeOutputs[node.id].images;
                        }
                        if (app.nodePreviewImages?.[node.id]) {
                            delete app.nodePreviewImages[node.id];
                        }
                        const previewWidgetIdx = node.widgets.findIndex(w => w.name === '$$canvas-image-preview' || w.type === 'IMAGE_PREVIEW');
                        if (previewWidgetIdx > -1) {
                            const widget = node.widgets[previewWidgetIdx];
                            widget.onRemove?.();
                            node.widgets.splice(previewWidgetIdx, 1);
                        }
                        const size = node.computeSize();
                        const width = node.size ? node.size[0] : size[0];
                        node.setSize([width, size[1]]);
                        node.setDirtyCanvas(true, true);
                    }
                };

                const origPreviewCallback = previewModeWidget.callback;
                previewModeWidget.callback = function (val) {
                    const res = origPreviewCallback ? origPreviewCallback.apply(this, arguments) : undefined;
                    updatePreviewVisibility(val);
                    return res;
                };

                // Run initially to clear if default is None
                setTimeout(() => updatePreviewVisibility(previewModeWidget.value), 0);
            }

            return origResult;
        };
    }
});
