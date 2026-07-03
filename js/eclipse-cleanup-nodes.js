import {
    app
} from './comfy/index.js';
import {
    setupAnyTypeHandling
} from './eclipse-any-type-handler.js';
const CLEANUP_NODES = ['VRAM Cleanup [Eclipse]', 'RAM Cleanup [Eclipse]', ];
app.registerExtension({
    name: 'Eclipse.CleanupNodes',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (!CLEANUP_NODES.includes(nodeData.name)) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origOnNodeCreated && origOnNodeCreated.apply(this, arguments);
            setupAnyTypeHandling(this, 0, 0);
        };
    },
});
