import {
    app
} from './comfy/index.js';
import {
    createDOMPreview,
    feedDOMPreview
} from './eclipse-dom-preview.js';
const SIMPLE_PREVIEW_NODES = ["Preview Image [Eclipse]", "Preview Image (DOM) [Eclipse]", "Preview Image (DOM) [Stop] [Eclipse]", "Preview Mask [Eclipse]", ];
const EXTENDED_PREVIEW_NODES = ["Save Images [Eclipse]", "Load Image From Folder [Eclipse]", "Load Image From Folder (Pipe) [Eclipse]", ];
const COMPACT_PREVIEW_NODES = new Set(["Load Image From Folder [Eclipse]", "Load Image From Folder (Pipe) [Eclipse]", ]);
const ALL_NODES = new Set([...SIMPLE_PREVIEW_NODES, ...EXTENDED_PREVIEW_NODES]);

function getDOMPreviewNodeOutputKey(node) {
    const graph = node.graph;
    const isSubgraph = graph?.isRootGraph === false ||
        (graph?.rootGraph && graph.rootGraph !== graph);
    return isSubgraph && graph.id != null
        ? `${graph.id}:${node.id}`
        : String(node.id);
}

function suppressNativeImageOutput(node) {
    node.hideOutputImages = true;
    node.imgs = null;
    node.imageIndex = null;
    const output = app.nodeOutputs?.[getDOMPreviewNodeOutputKey(node)];
    if (!output) return;
    queueMicrotask(() => {
        if (output.images) delete output.images;
        node.imgs = null;
    });
}

app.registerExtension({
    name: "Eclipse.DOMPreviewNodes",
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (!ALL_NODES.has(nodeData.name)) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated?.apply(this, arguments);
            createDOMPreview(this, {
                minHeight: COMPACT_PREVIEW_NODES.has(nodeData.name) ? 50 : 200,
                freeResize: true
            });
            this.hideOutputImages = true;
            this._Eclipse_syncFolderPreview?.();
            const stopWidget = this.widgets?.find(w => w.name === 'stop_review');
            if (stopWidget) {
                const idx = this.widgets.indexOf(stopWidget);
                this.widgets.splice(idx, 1);
                this.widgets.push(stopWidget);
            }
            return ret;
        };
        const origOnExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (output) {
            const savedImages = output.images;
            delete output.images;
            origOnExecuted?.apply(this, arguments);
            if (savedImages) output.images = savedImages;
            feedDOMPreview(this, output, { publish: false });
            suppressNativeImageOutput(this);
        };
    },
});
