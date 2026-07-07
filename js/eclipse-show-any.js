import {
    app
} from './comfy/index.js';

const NODE_NAMES = ['Show Any [Eclipse]', 'Show Any Stop [Eclipse]'];
const TEXT_WIDGET_NAME = '_eclipse_dom_text';
const MIN_TEXT_H = 36;

function createDOMText(node) {
    const wrapper = document.createElement('div');
    wrapper.style.cssText = 'display:flex;flex-direction:column;width:100%;height:100%;gap:4px;';
    
    const el = document.createElement('textarea');
    el.readOnly = true;
    el.style.cssText = 'width:100%;flex:1 1 auto;resize:none;border:none;outline:none;' + 'background:#1a1a1a;color:#ccc;font:12px monospace;padding:6px;' + 'box-sizing:border-box;cursor:default;overflow-y:auto;' + 'border-radius:4px;min-height:30px;';
    el.value = '';
    wrapper.appendChild(el);
    
    const widget = node.addDOMWidget(TEXT_WIDGET_NAME, 'custom', wrapper, {
        hideOnZoom: false,
        serialize: false,
        getMinHeight: () => MIN_TEXT_H,
    });
    widget.computeLayoutSize = () => ({
        minHeight: MIN_TEXT_H,
        minWidth: 100,
        maxHeight: undefined
    });
    node._eclipseDomText = {
        el,
        widget
    };
    return widget;
}

function setDOMText(node, texts) {
    const dt = node._eclipseDomText;
    if (!dt) return;
    dt.el.value = texts.join('\n\n');
    updateTextMinHeight(node);
}

function clearDOMText(node) {
    const dt = node._eclipseDomText;
    if (!dt) return;
    dt.el.value = '';
    updateTextMinHeight(node);
}

function updateTextMinHeight(node) {
    const dt = node._eclipseDomText;
    if (!dt) return;
    dt.widget.computeLayoutSize = () => ({
        minHeight: MIN_TEXT_H,
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
                setDOMText(this, output.text);
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
