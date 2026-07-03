import {
    app
} from './comfy/index.js';
const IFELSE_NODES = ['IF A Else B [Eclipse]', 'IF A Else B Fallback [Eclipse]', ];
const ANYTYPE_INPUTS = new Set(['on_true', 'on_false']);
app.registerExtension({
    name: 'Eclipse.RouterIfElse',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (!IFELSE_NODES.includes(nodeData.name)) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origOnNodeCreated && origOnNodeCreated.apply(this, arguments);
            const node = this;
            node.properties = node.properties || {};
            node.onGraphConfigured = function () {
                this.configured = true;
            };
            const origOnConnectionsChange = node.onConnectionsChange;
            node.onConnectionsChange = function (ioType, slotIndex, isConnected, linkInfo) {
                if (origOnConnectionsChange) origOnConnectionsChange.apply(this, arguments);
                if (!linkInfo || !this.inputs || !this.outputs) return;
                const changedInput = this.inputs[slotIndex];
                if (!changedInput || !ANYTYPE_INPUTS.has(changedInput.name)) return;
                if (isConnected && ioType === LiteGraph.INPUT) {
                    const sourceNode = _app.graph.getNodeById(linkInfo.origin_id);
                    if (!sourceNode) return;
                    const sourceOutput = sourceNode.outputs?.[linkInfo.origin_slot];
                    if (!sourceOutput) return;
                    const connType = sourceOutput.type;
                    const linkColor = LGraphCanvas.link_type_colors[connType];
                    changedInput.type = connType;
                    const graphLink = linkInfo.id != null ? _app.graph.links[linkInfo.id] : null;
                    if (graphLink) graphLink.color = linkColor;
                    this.inputs.forEach((inp) => {
                        if (ANYTYPE_INPUTS.has(inp.name) && inp !== changedInput) {
                            inp.type = connType;
                        }
                    });
                    if (this.outputs[0]) {
                        this.outputs[0].type = connType;
                        this.outputs[0].name = connType;
                    }
                } else if (!isConnected && ioType === LiteGraph.INPUT) {
                    const connectedInputs = this.inputs.filter((inp) => ANYTYPE_INPUTS.has(inp.name) && inp.link !== null, );
                    if (connectedInputs.length > 0) {
                        const remainingType = connectedInputs[0].type;
                        this.inputs.forEach((inp) => {
                            if (ANYTYPE_INPUTS.has(inp.name)) inp.type = remainingType;
                        });
                        if (this.outputs[0]) {
                            this.outputs[0].type = remainingType;
                            this.outputs[0].name = remainingType;
                        }
                    } else {
                        this.inputs.forEach((inp) => {
                            if (ANYTYPE_INPUTS.has(inp.name)) inp.type = '*';
                        });
                        if (this.outputs[0]) {
                            this.outputs[0].type = '*';
                            this.outputs[0].name = '';
                        }
                    }
                }
                this.computeSize?.();
            };
        };
    },
});
