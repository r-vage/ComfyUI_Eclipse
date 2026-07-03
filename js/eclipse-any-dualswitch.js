import {
    app
} from './comfy/index.js';
app.registerExtension({
    name: 'Eclipse.RouterAnyDualSwitch',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== 'Any Dual-Switch [Eclipse]') return;
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
                if (!changedInput || (changedInput.name !== 'input1' && changedInput.name !== 'input2')) return;
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
                        if ((inp.name === 'input1' || inp.name === 'input2') && inp !== changedInput) {
                            inp.type = connType;
                        }
                    });
                    if (this.outputs[0]) {
                        this.outputs[0].type = connType;
                        this.outputs[0].name = connType;
                    }
                } else if (!isConnected && ioType === LiteGraph.INPUT) {
                    const connectedInputs = this.inputs.filter((inp) => (inp.name === 'input1' || inp.name === 'input2') && inp.link !== null, );
                    if (connectedInputs.length > 0) {
                        const remainingType = connectedInputs[0].type;
                        this.inputs.forEach((inp) => {
                            if (inp.name === 'input1' || inp.name === 'input2') inp.type = remainingType;
                        });
                        if (this.outputs[0]) {
                            this.outputs[0].type = remainingType;
                            this.outputs[0].name = remainingType;
                        }
                    } else {
                        this.inputs.forEach((inp) => {
                            if (inp.name === 'input1' || inp.name === 'input2') inp.type = '*';
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
