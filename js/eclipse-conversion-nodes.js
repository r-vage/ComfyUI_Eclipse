/**
 * eclipse-conversion-nodes.js — conversion output typing and Image Convert color picker
 *
 * Copyright (c) 2026 r-vage. MIT License.
 */
import {
    app
} from './comfy/index.js';
import {
    setupAnyTypeHandling
} from './eclipse-any-type-handler.js';
import {
    isVueMode
} from './eclipse-widget-performance-utils.js';
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
            BOOLEAN: {
                type: 'BOOLEAN',
                name: 'BOOLEAN'
            },
            COMBO: {
                type: 'COMBO',
                name: 'COMBO'
            },
        },
        defaultType: '*',
        useAnyTypeHandling: false,
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

const IMAGE_CONVERT_NODE = 'Image Convert [Eclipse]';
const IMAGE_CONVERT_DEFAULT_BACKGROUND = '#000000';
const IMAGE_CONVERT_COLOR_WIDGET = 'background_color';
const IMAGE_CONVERT_COLOR_CLASS = 'eclipse-image-convert-color-picker';
const IMAGE_CONVERT_COLOR_STYLE_ID = 'eclipse-image-convert-color-picker-styles';
let imageConvertColorPicker = null;
let imageConvertColorPickerCallback = null;

function injectImageConvertColorPickerStyles() {
    if (document.getElementById(IMAGE_CONVERT_COLOR_STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = IMAGE_CONVERT_COLOR_STYLE_ID;
    style.textContent = `
        input.${IMAGE_CONVERT_COLOR_CLASS} {
            appearance: none !important;
            -webkit-appearance: none !important;
            min-height: 2rem;
            padding: 4px !important;
            overflow: hidden;
            cursor: pointer;
        }
        input.${IMAGE_CONVERT_COLOR_CLASS}::-webkit-color-swatch-wrapper {
            width: 100%;
            height: 100%;
            padding: 0;
        }
        input.${IMAGE_CONVERT_COLOR_CLASS}::-webkit-color-swatch {
            min-height: 1.5rem;
            border: 0;
            border-radius: 6px;
        }
        input.${IMAGE_CONVERT_COLOR_CLASS}::-moz-color-swatch {
            min-height: 1.5rem;
            border: 0;
            border-radius: 6px;
        }
    `;
    document.head.appendChild(style);
}

function openImageConvertColorPicker(currentValue, onChange) {
    if (!imageConvertColorPicker) {
        imageConvertColorPicker = document.createElement('input');
        imageConvertColorPicker.type = 'color';
        imageConvertColorPicker.style.cssText = 'position:fixed;top:50%;left:50%;width:1px;height:1px;opacity:0.01;pointer-events:none;';
        document.body.appendChild(imageConvertColorPicker);
        const fire = (event) => {
            imageConvertColorPickerCallback?.(event.target.value);
        };
        imageConvertColorPicker.addEventListener('input', fire);
        imageConvertColorPicker.addEventListener('change', fire);
    }
    imageConvertColorPicker.value = /^#[0-9a-f]{6}$/i.test(currentValue)
        ? currentValue
        : IMAGE_CONVERT_DEFAULT_BACKGROUND;
    imageConvertColorPickerCallback = onChange;
    imageConvertColorPicker.click();
}

function setupImageConvertColorPicker(node) {
    const widget = node.widgets?.find(
        (candidate) => candidate.name === IMAGE_CONVERT_COLOR_WIDGET
    );
    if (!widget) return;

    widget.onPointerDown = function () {
        openImageConvertColorPicker(widget.value, (hex) => {
            widget.value = hex;
            widget.callback?.(hex);
            node.setDirtyCanvas?.(true, true);
        });
        return true;
    };

    widget.draw = function (ctx, _node, widgetWidth, y, height) {
        ctx.save();
        const hex = widget.value || IMAGE_CONVERT_DEFAULT_BACKGROUND;
        const margin = 15;

        ctx.fillStyle = '#232323';
        ctx.beginPath();
        ctx.roundRect(margin, y, widgetWidth - margin * 2, height, 4);
        ctx.fill();

        ctx.fillStyle = '#aaa';
        ctx.font = '12px Arial';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(widget.name, margin + 10, y + height * 0.5);

        ctx.fillStyle = '#ddd';
        ctx.textAlign = 'right';
        ctx.fillText(hex, widgetWidth - margin - 34, y + height * 0.5);

        const swatchWidth = 20;
        const swatchHeight = height - 8;
        const swatchX = widgetWidth - margin - swatchWidth - 6;
        const swatchY = y + 4;
        ctx.fillStyle = hex;
        ctx.beginPath();
        ctx.roundRect(swatchX, swatchY, swatchWidth, swatchHeight, 3);
        ctx.fill();
        ctx.strokeStyle = '#666';
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.restore();
    };

    if (!isVueMode()) return;
    injectImageConvertColorPickerStyles();
    const patchVueInput = () => {
        const element = document.querySelector(`[data-node-id="${node.id}"]`);
        if (!element) return false;
        const input = element.querySelector(
            `input[aria-label="${IMAGE_CONVERT_COLOR_WIDGET}"]`
        );
        if (!input) return false;
        if (input._eclipse_color_patched) return true;
        input._eclipse_color_patched = true;
        input.type = 'color';
        input.classList.add(IMAGE_CONVERT_COLOR_CLASS);
        return true;
    };
    let attempts = 0;
    const tryPatchVueInput = () => {
        if (patchVueInput() || ++attempts > 30) return;
        requestAnimationFrame(tryPatchVueInput);
    };
    requestAnimationFrame(tryPatchVueInput);
}

function migrateLegacyImageConvertWidgets(node) {
    const backgroundWidget = node.widgets?.find((widget) => widget.name === 'background_color');
    const styleWidget = node.widgets?.find((widget) => widget.name === 'style');
    if (!backgroundWidget || !styleWidget || styleWidget.value !== 'none') return;

    const styleOptions = styleWidget.options?.values;
    if (!Array.isArray(styleOptions) || !styleOptions.includes(backgroundWidget.value)) return;

    const legacyStyle = backgroundWidget.value;
    backgroundWidget.value = IMAGE_CONVERT_DEFAULT_BACKGROUND;
    styleWidget.value = legacyStyle;
    node.setDirtyCanvas?.(true, true);
}

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
    const typeChanged = output.type !== newType;
    if (!typeChanged && output.name === newName) return;
    if (typeChanged && output.links && output.links.length > 0) {
        const linksToRemove = [];
        for (const linkId of output.links) {
            const link = app.graph.links[linkId];
            if (link) {
                const targetNode = app.graph.getNodeById(link.target_id);
                if (targetNode && targetNode.inputs && targetNode.inputs[link.target_slot]) {
                    const targetInput = targetNode.inputs[link.target_slot];
                    if (!LiteGraph.isValidConnection(newType, targetInput.type)) {
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
            if (!link) continue;
            link.type = newType;
            if (linkColor) link.color = linkColor;
        }
    }
    node.setDirtyCanvas?.(true, true);
}
app.registerExtension({
    name: 'Eclipse.conversionNodes',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        const config = CONVERSION_NODES[nodeData.name];
        if (!config) return;
        if (nodeData.name === IMAGE_CONVERT_NODE) {
            const origOnConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function () {
                const configureRet = origOnConfigure
                    ? origOnConfigure.apply(this, arguments)
                    : undefined;
                migrateLegacyImageConvertWidgets(this);
                return configureRet;
            };
        }
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            if (nodeData.name === IMAGE_CONVERT_NODE) {
                setupImageConvertColorPicker(node);
            }
            if (config.useAnyTypeHandling) setupAnyTypeHandling(this, 0, 0);
            if (config.fixedType) return ret;
            const refresh = () => updateOutputTypeFromWidget(node, config);
            const origOnAdded = node.onAdded;
            node.onAdded = function () {
                const addedRet = origOnAdded ? origOnAdded.apply(this, arguments) : undefined;
                refresh();
                return addedRet;
            };
            const comboWidget = node.widgets?.find((w) => w.name === config.widgetName);
            if (comboWidget) {
                const origCb = comboWidget.callback;
                comboWidget.callback = function () {
                    if (origCb) origCb.apply(this, arguments);
                    refresh();
                };
            }
            const origOnConfigure = node.onConfigure;
            node.onConfigure = function () {
                const configureRet = origOnConfigure ? origOnConfigure.apply(this, arguments) : undefined;
                refresh();
                return configureRet;
            };
            const origOnConnectionsChange = node.onConnectionsChange;
            node.onConnectionsChange = function () {
                const connectionRet = origOnConnectionsChange
                    ? origOnConnectionsChange.apply(this, arguments)
                    : undefined;
                refresh();
                return connectionRet;
            };
            refresh();
            return ret;
        };
    },
});
