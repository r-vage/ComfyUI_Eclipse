import {
    app
} from './comfy/index.js';
import {
    createDOMTextController,
    DOM_TEXT_MIN_HEIGHT,
    DOM_TEXT_PREVIEW_NAME
} from './eclipse-dom-text.js';
import {
    publishSubgraphDOMPreview,
    registerSubgraphDOMPreviewProvider
} from './eclipse-subgraph-dom-previews.js';

const NODE_NAMES = ['Show Any [Eclipse]', 'Show Any Stop [Eclipse]'];

function createDOMText(node) {
    const controller = createDOMTextController(node);
    node._eclipseDomText = controller;
    registerSubgraphDOMPreviewProvider(node, {
        name: DOM_TEXT_PREVIEW_NAME,
        kind: 'text',
        label: 'Text preview',
        createProjection(host, projection) {
            const projected = createDOMTextController(host, { name: projection.name });
            return {
                clear: projected.clear,
                dispose: projected.dispose,
                setValue: projected.setValue,
                widget: projected.widget,
            };
        },
        getCurrentValue: controller.getValue,
        readOutput: output => output?.text ?? [],
    });
    return controller.widget;
}

function setDOMText(node, texts, options = {}) {
    const dt = node._eclipseDomText;
    if (!dt) return;
    dt.setValue(texts);
    updateTextMinHeight(node);
    if (options.publish !== false) {
        publishSubgraphDOMPreview(node, DOM_TEXT_PREVIEW_NAME, texts);
    }
}

function clearDOMText(node, options = {}) {
    const dt = node._eclipseDomText;
    if (!dt) return;
    dt.clear();
    updateTextMinHeight(node);
    if (options.publish !== false) {
        publishSubgraphDOMPreview(node, DOM_TEXT_PREVIEW_NAME, []);
    }
}

function updateTextMinHeight(node) {
    const dt = node._eclipseDomText;
    if (!dt) return;
    dt.widget.computeLayoutSize = () => ({
        minHeight: DOM_TEXT_MIN_HEIGHT,
        minWidth: 100,
        maxHeight: undefined
    });
}

app.registerExtension({
    name: 'Eclipse.showAny',
    async beforeRegisterNodeDef(nodeType, nodeData, appRef) {
        if (!NODE_NAMES.includes(nodeData.name)) return;
        
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated?.apply(this, arguments);
            const node = this;
            
            createDOMText(node);
            const stopWidget = node.widgets?.find(w => w.name === 'stop_review');
            if (stopWidget) {
                const idx = node.widgets.indexOf(stopWidget);
                node.widgets.splice(idx, 1);
                node.widgets.push(stopWidget);
            }
            return ret;
        };
        
        const origOnExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (output) {
            origOnExecuted?.apply(this, arguments);
            if (output.text) {
                setDOMText(this, output.text, { publish: false });
            }
        };
        
        const origConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            origConfigure?.apply(this, arguments);
            const stopWidget = this.widgets?.find(w => w.name === 'stop_review');
            if (stopWidget) {
                const idx = this.widgets.indexOf(stopWidget);
                this.widgets.splice(idx, 1);
                this.widgets.push(stopWidget);
            }
        };
        
        const origConnChange = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function (ioType, slotIndex, isConnected, linkInfo) {
            origConnChange?.apply(this, arguments);
            const inp = this.inputs?.[0];
            const out = this.outputs?.[0];
            if (inp && out && linkInfo) {
                if (isConnected) {
                    if (ioType === LiteGraph.INPUT && slotIndex === 0) {
                        const sourceNode = appRef.graph.getNodeById(linkInfo.origin_id);
                        if (sourceNode) {
                            const connType = sourceNode.outputs?.[linkInfo.origin_slot]?.type;
                            if (connType) {
                                inp.type = connType;
                                out.type = connType;
                                out.name = connType;
                                const linkColor = LGraphCanvas.link_type_colors[connType];
                                const graphLink = linkInfo.id != null ? appRef.graph.links[linkInfo.id] : null;
                                if (graphLink && linkColor) graphLink.color = linkColor;
                            }
                        }
                    } else if (ioType === LiteGraph.OUTPUT && slotIndex === 0 && inp.link === null) {
                        inp.type = linkInfo.type;
                        out.type = linkInfo.type;
                        out.name = linkInfo.type;
                    }
                } else {
                    const inputEmpty = inp.link === null;
                    const outputEmpty = !out.links || out.links.length === 0;
                    if ((ioType === LiteGraph.INPUT && slotIndex === 0 && outputEmpty) || (ioType === LiteGraph.OUTPUT && slotIndex === 0 && inputEmpty)) {
                        inp.type = '*';
                        out.type = '*';
                        out.name = '';
                    }
                }
                this.computeSize?.();
            }
            const hasConnection = this.inputs?.some(i => i.link != null);
            if (!hasConnection) {
                clearDOMText(this);
                appRef.graph.setDirtyCanvas(true, false);
            }
        };
    },
});
