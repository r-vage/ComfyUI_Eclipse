import {
    app
} from './comfy/index.js';
export function setupAnyTypeHandling(node, inputSlot = 0, outputSlot = 0) {
    node.properties = node.properties || {};
    node.onGraphConfigured = function () {
        this.configured = true;
    };
    node.onConnectionsChange = function (ioType, slotIndex, isConnected, linkInfo) {
        if (!linkInfo) return;
        const inp = this.inputs?.[inputSlot];
        const out = this.outputs?.[outputSlot];
        if (!inp || !out) return;
        const g = this.graph || app.graph;
        if (isConnected) {
            if (ioType === LiteGraph.INPUT && slotIndex === inputSlot) {
                const sourceNode = g.getNodeById(linkInfo.origin_id);
                if (!sourceNode) return;
                const sourceOutput = sourceNode.outputs?.[linkInfo.origin_slot];
                if (!sourceOutput) return;
                const connType = sourceOutput.type;
                const linkColor = LGraphCanvas.link_type_colors[connType];
                out.type = connType;
                out.name = connType;
                inp.type = connType;
                const lnk = g.links?.[linkInfo.id] ?? g.links?.get?.(linkInfo.id);
                if (lnk) lnk.color = linkColor;
            }
            if (ioType === LiteGraph.OUTPUT && slotIndex === outputSlot && inp.link === null) {
                inp.type = linkInfo.type;
                out.type = linkInfo.type;
                out.name = linkInfo.type;
            }
        } else {
            const inputDisconnected = ioType === LiteGraph.INPUT && slotIndex === inputSlot;
            const outputDisconnected = ioType === LiteGraph.OUTPUT && slotIndex === outputSlot;
            const inputEmpty = inp.link === null;
            const outputEmpty = out.links === null || out.links.length === 0;
            if ((inputDisconnected && outputEmpty) || (outputDisconnected && inputEmpty)) {
                inp.type = '*';
                out.name = '';
                out.type = '*';
            }
        }
        this.computeSize();
    };
    node.onAdded = function () {
        this.inputs[inputSlot].type = '*';
        this.outputs[outputSlot].name = '';
        this.outputs[outputSlot].type = '*';
    };
}
