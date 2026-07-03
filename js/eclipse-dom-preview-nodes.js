import {
    app
} from './comfy/index.js';
import {
    createDOMPreview,
    feedDOMPreview
} from './eclipse-dom-preview.js';
const SIMPLE_PREVIEW_NODES = ["Preview Image [Eclipse]", "Preview Image (DOM) [Eclipse]", "Preview Mask [Eclipse]", ];
const EXTENDED_PREVIEW_NODES = ["Save Images v2 [Eclipse]", "Load Image From Folder [Eclipse]", "Load Image From Folder (Pipe) [Eclipse]", ];
const ALL_NODES = new Set([...SIMPLE_PREVIEW_NODES, ...EXTENDED_PREVIEW_NODES]);
app.registerExtension({
    name: "Eclipse.DOMPreviewNodes",
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (!ALL_NODES.has(nodeData.name)) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated?.apply(this, arguments);
            createDOMPreview(this, {
                minHeight: 200
            });
            return ret;
        };
        const origOnExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (output) {
            const savedImages = output.images;
            delete output.images;
            origOnExecuted?.apply(this, arguments);
            if (savedImages) output.images = savedImages;
            this.imgs = null;
            feedDOMPreview(this, output);
            const nodeOutputs = app.nodeOutputs?.[this.id];
            if (nodeOutputs?.images) delete nodeOutputs.images;
        };
    },
});
