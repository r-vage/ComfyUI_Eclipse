import {
    app
} from './comfy/index.js';
import {
    setupAnyTypeHandling
} from './eclipse-any-type-handler.js';
class Eclipse_Stop {
    constructor(node) {
        this.node = node;
        setupAnyTypeHandling(this.node, 0, 0);
        this.node.computeSize = function () {
            const hasOutput = this.properties.showOutputText && this.outputs && this.outputs.length;
            const width = hasOutput ? LiteGraph.NODE_TEXT_SIZE * (this.outputs[0].name.length + 5) * 0.6 + 140 : 140;
            return [width, 1.3 * LiteGraph.NODE_SLOT_HEIGHT];
        };
    }
}
app.registerExtension({
    name: 'Stop [Eclipse]',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== 'Stop [Eclipse]') return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origOnNodeCreated && origOnNodeCreated.apply(this, []);
            this.Stop = new Eclipse_Stop(this);
        };
    },
});
