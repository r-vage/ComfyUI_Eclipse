import {
    app
} from './comfy/index.js';
import {
    setupAnyTypeHandling
} from './eclipse-any-type-handler.js';
const CONVERSION_NODES = {
    'Convert Primitive [Eclipse]': {
        widgetName: 'convert_to',
        typeMap: {
            STRING: {
                type: 'STRING',
                name: 'STRING'
            },
            INT: {
                type: 'INT',
                name: 'INT'
            },
            FLOAT: {
                type: 'FLOAT',
                name: 'FLOAT'
            },
            COMBO: {
                type: 'COMBO',
                name: 'COMBO'
            },
        },
        defaultType: '*',
        useAnyTypeHandling: true,
    },
    'Convert To Batch [Eclipse]': {
        widgetName: 'convert_to',
        typeMap: {
            IMAGE_LIST_TO_BATCH: {
                type: 'IMAGE',
                name: 'IMAGE'
            },
            MASK_LIST_TO_BATCH: {
                type: 'MASK',
                name: 'MASK'
            },
        },
        defaultType: '*',
        useAnyTypeHandling: true,
    },
    'Convert to List [Eclipse]': {
        widgetName: 'convert_to',
        typeMap: {
            IMAGE_BATCH_TO_LIST: {
                type: 'IMAGE',
                name: 'IMAGE'
            },
            MASK_BATCH_TO_LIST: {
                type: 'MASK',
                name: 'MASK'
            },
        },
        defaultType: '*',
        useAnyTypeHandling: false,
    },
    'Image Convert [Eclipse]': {
        fixedType: {
            type: 'IMAGE',
            name: 'IMAGE'
        },
        useAnyTypeHandling: false,
    },
};

function updateOutputTypeFromWidget(node, config) {
    if (config.fixedType) return;
    const comboWidget = node.widgets?.find((w) => w.name === config.widgetName);
    if (!comboWidget || !node.outputs || node.outputs.length === 0) return;
    const selectedValue = comboWidget.value;
    const output = node.outputs[0];
    const mapping = config.typeMap[selectedValue] || {
        type: config.defaultType || '*',
        name: ''
    };
    const newType = mapping.type;
    const newName = mapping.name;
    if (output.type === newType) return;
    if (output.links && output.links.length > 0) {
        const linksToRemove = [];
        for (const linkId of output.links) {
            const link = app.graph.links[linkId];
            if (link) {
                const targetNode = app.graph.getNodeById(link.target_id);
                if (targetNode && targetNode.inputs && targetNode.inputs[link.target_slot]) {
                    const targetInput = targetNode.inputs[link.target_slot];
                    if (targetInput.type !== '*' && newType !== '*' && targetInput.type !== newType) {
                        linksToRemove.push(linkId);
                    }
                }
            }
        }
        for (const linkId of linksToRemove) app.graph.removeLink(linkId);
    }
    output.type = newType;
    output.name = newName;
    if (output.links && output.links.length > 0) {
        const linkColor = LGraphCanvas.link_type_colors[newType];
        for (const linkId of output.links) {
            const link = app.graph.links[linkId];
            if (link && linkColor) link.color = linkColor;
        }
    }
    node.setDirtyCanvas?.(true, true);
}
app.registerExtension({
    name: 'Eclipse.conversionNodes',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        const config = CONVERSION_NODES[nodeData.name];
        if (!config) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            if (config.useAnyTypeHandling) setupAnyTypeHandling(this, 0, 0);
            if (config.fixedType) return ret;
            const refresh = () => updateOutputTypeFromWidget(node, config);
            const comboWidget = node.widgets?.find((w) => w.name === config.widgetName);
            if (comboWidget) {
                const origCb = comboWidget.callback;
                comboWidget.callback = function () {
                    if (origCb) origCb.apply(this, arguments);
                    refresh();
                };
            }
            const origOnConnectionsChange = node.onConnectionsChange;
            node.onConnectionsChange = function (ioType, slotIndex, isConnected, linkInfo) {
                if (origOnConnectionsChange) origOnConnectionsChange.apply(this, arguments);
                setTimeout(() => refresh(), 10);
            };
            setTimeout(() => refresh(), 100);
            return ret;
        };
    },
});
