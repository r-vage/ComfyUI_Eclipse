import {
    app
} from './comfy/index.js';
import {
    createDOMPreview,
    feedDOMPreview,
    clearDOMPreview
} from './eclipse-dom-preview.js';
const NODE_NAME = 'Show Any [Eclipse]';
const TEXT_WIDGET_NAME = '_eclipse_dom_text';
const MIN_TEXT_H = 36;
const MODE_OPTIONS = ['show', 'hide'];
const MODE_TOOLTIPS = {
    show: 'Show image previews in the node body',
    hide: 'Hide image previews (text output only)',
};
const MODE_BAR_H = 20;

function buildModeBarEl(backingW, onChange) {
    const bar = document.createElement('div');
    Object.assign(bar.style, {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '4px',
        width: '100%',
        height: MODE_BAR_H + 'px',
        flexShrink: '0',
        padding: '0 4px',
        background: '#1a1a1a',
        border: 'none',
        borderRadius: '4px',
        boxSizing: 'border-box',
        userSelect: 'none',
    });
    const label = document.createElement('span');
    Object.assign(label.style, {
        color: '#888',
        fontSize: '11px',
        fontFamily: 'sans-serif',
        marginRight: '6px',
    });
    label.textContent = 'images';
    bar.appendChild(label);
    const chips = {};
    for (const opt of MODE_OPTIONS) {
        const chip = document.createElement('span');
        Object.assign(chip.style, {
            padding: '2px 10px',
            borderRadius: '3px',
            fontSize: '11px',
            fontFamily: 'sans-serif',
            cursor: 'pointer',
            transition: 'background 0.15s, color 0.15s',
        });
        chip.textContent = opt;
        if (MODE_TOOLTIPS[opt]) chip.title = MODE_TOOLTIPS[opt];
        chip.addEventListener('pointerdown', (e) => {
            e.stopPropagation();
            e.preventDefault();
            if (backingW) backingW.value = opt;
            updateChips(opt);
            onChange(opt);
        });
        bar.appendChild(chip);
        chips[opt] = chip;
    }

    function updateChips(active) {
        for (const [key, el] of Object.entries(chips)) {
            if (key === active) {
                Object.assign(el.style, {
                    background: '#2a5a3a',
                    color: '#e0e0e0'
                });
            } else {
                Object.assign(el.style, {
                    background: 'transparent',
                    color: '#666'
                });
            }
        }
    }
    const initial = backingW?.value || 'hide';
    updateChips(initial);
    return {
        bar,
        updateChips
    };
}

function createDOMText(node, backingW, onChange) {
    const wrapper = document.createElement('div');
    wrapper.style.cssText = 'display:flex;flex-direction:column;width:100%;height:100%;gap:4px;';
    const {
        bar: modeBarEl,
        updateChips
    } = buildModeBarEl(backingW, onChange);
    wrapper.appendChild(modeBarEl);
    const el = document.createElement('textarea');
    el.readOnly = true;
    el.style.cssText = 'width:100%;flex:1 1 50%;resize:none;border:none;outline:none;' + 'background:#1a1a1a;color:#ccc;font:12px monospace;padding:6px;' + 'box-sizing:border-box;cursor:default;overflow-y:auto;' + 'border-radius:4px;min-height:30px;';
    el.value = '';
    wrapper.appendChild(el);
    const previewContainer = createDOMPreview(node, {
        minHeight: 60,
        noWidget: true
    });
    previewContainer.style.cssText = 'flex:1 1 50%;min-height:30px;display:none;';
    wrapper.appendChild(previewContainer);
    const totalMin = MODE_BAR_H + 4 + MIN_TEXT_H;
    const widget = node.addDOMWidget(TEXT_WIDGET_NAME, 'custom', wrapper, {
        hideOnZoom: false,
        serialize: false,
        getMinHeight: () => totalMin,
    });
    widget.computeLayoutSize = () => ({
        minHeight: totalMin,
        minWidth: 100,
        maxHeight: undefined
    });
    node._eclipseDomText = {
        el,
        widget
    };
    node._eclipseModeBar = {
        updateChips
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
const PREVIEW_MIN = 30;
const MIN_SHOWN = MODE_BAR_H + 4 + 30 + 4 + PREVIEW_MIN;
const MIN_HIDDEN = MODE_BAR_H + 4 + MIN_TEXT_H;

function setPreviewVisible(node, visible) {
    const preview = node._eclipseDomPreview;
    if (!preview) return;
    preview.container.style.display = visible ? '' : 'none';
    const dt = node._eclipseDomText;
    if (dt) {
        const totalMin = visible ? MIN_SHOWN : MIN_HIDDEN;
        dt.widget.computeLayoutSize = () => ({
            minHeight: totalMin,
            minWidth: 100,
            maxHeight: undefined
        });
    }
}

function updateTextMinHeight(node) {
    const dt = node._eclipseDomText;
    if (!dt) return;
    const visible = node.showImages || false;
    const totalMin = visible ? MIN_SHOWN : MIN_HIDDEN;
    dt.widget.computeLayoutSize = () => ({
        minHeight: totalMin,
        minWidth: 100,
        maxHeight: undefined
    });
}
app.registerExtension({
    name: 'Eclipse.showAny',
    async beforeRegisterNodeDef(nodeType, nodeData, appRef) {
        if (nodeData.name !== NODE_NAME) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated?.apply(this, arguments);
            const node = this;
            const showW = node.widgets?.find(w => w.name === 'show_images');
            if (showW) {
                showW.hidden = true;
                if (showW.options) showW.options.hidden = true;
            }
            createDOMText(node, showW, (val) => {
                node.showImages = (val === 'show');
                setPreviewVisible(node, node.showImages);
                node.setDirtyCanvas(true, true);
            });
            node.showImages = (showW?.value === 'show');
            setPreviewVisible(node, node.showImages);
            return ret;
        };
        const origOnExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (output) {
            const savedImages = output.images;
            delete output.images;
            origOnExecuted?.apply(this, arguments);
            if (savedImages) output.images = savedImages;
            this.imgs = null;
            if (output.text) {
                setDOMText(this, output.text);
            }
            if (this.showImages !== false && output.images) {
                feedDOMPreview(this, output);
            } else {
                clearDOMPreview(this);
            }
            const nodeOutputs = app.nodeOutputs?.[this.id];
            if (nodeOutputs?.images) delete nodeOutputs.images;
        };
        const origConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            origConfigure?.apply(this, arguments);
            const showW = this.widgets?.find(w => w.name === 'show_images');
            if (showW) {
                this.showImages = (showW.value === 'show');
                setPreviewVisible(this, this.showImages);
                this._eclipseModeBar?.updateChips(showW.value);
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
                clearDOMPreview(this);
                this.imgs = null;
                appRef.graph.setDirtyCanvas(true, false);
            }
        };
    },
});
