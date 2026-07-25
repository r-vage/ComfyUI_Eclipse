/**
 * eclipse-vue-classic-node-context-menu.js — Optional classic node menus for Nodes 2.0.
 *
 * Copyright (c) 2026 r-vage. MIT License.
 */
import { app } from './comfy/index.js';
import { isVueMode } from './eclipse-widget-performance-utils.js';

const SETTING_ID = 'Eclipse.VueClassicNodeContextMenu';
const NODES_2_SETTINGS_CATEGORY = ['Eclipse', 'Nodes 2.0'];

let enabled = false;
let listenerInstalled = false;

function resolveNodeFromEvent(event, canvas) {
    const target = event.target;
    const nodeElement = target?.closest?.('.lg-node[data-node-id]');
    const nodeId = nodeElement?.dataset?.nodeId ?? nodeElement?.getAttribute?.('data-node-id');
    const graph = canvas?.graph;
    if (nodeId == null || typeof graph?.getNodeById !== 'function') return null;

    let node = graph.getNodeById(nodeId);
    if (!node && /^-?\d+$/.test(nodeId)) node = graph.getNodeById(Number(nodeId));
    return node || null;
}

function hasRequiredClassicMenuAPIs(canvas) {
    return typeof canvas?.adjustMouseEvent === 'function' &&
        typeof canvas?.processSelect === 'function' &&
        typeof canvas?.processContextMenu === 'function' &&
        canvas.mouse?.length >= 2 &&
        canvas.graph_mouse?.length >= 2 &&
        typeof globalThis.LiteGraph?.closeAllContextMenus === 'function' &&
        typeof globalThis.LGraphCanvas !== 'undefined';
}

function handleVueNodeContextMenu(event) {
    if (!enabled || !isVueMode()) return;

    const canvas = app.canvas;
    if (!hasRequiredClassicMenuAPIs(canvas)) return;
    const node = resolveNodeFromEvent(event, canvas);
    if (!node) return;

    canvas.adjustMouseEvent(event);
    if (!Number.isFinite(event.canvasX) || !Number.isFinite(event.canvasY)) return;

    canvas.mouse[0] = event.clientX;
    canvas.mouse[1] = event.clientY;
    canvas.graph_mouse[0] = event.canvasX;
    canvas.graph_mouse[1] = event.canvasY;

    globalThis.LGraphCanvas.active_canvas = canvas;
    canvas.processSelect(node, event, true);
    globalThis.LiteGraph.closeAllContextMenus(canvas.getCanvasWindow?.() ?? window);
    event.preventDefault();
    event.stopImmediatePropagation();
    canvas.processContextMenu(node, event);
}

function setEnabled(value) {
    enabled = value === true;
    if (enabled === listenerInstalled) return;
    listenerInstalled = enabled;
    if (enabled) document.addEventListener('contextmenu', handleVueNodeContextMenu, true);
    else document.removeEventListener('contextmenu', handleVueNodeContextMenu, true);
}

export function isVueClassicNodeContextMenuActive() {
    return enabled && isVueMode();
}

app.registerExtension({
    name: SETTING_ID,
    init(appRef) {
        appRef.ui.settings.addSetting({
            id: 'Eclipse.VueClassicNodeContextMenu',
            category: [...NODES_2_SETTINGS_CATEGORY, 'VueClassicNodeContextMenu'],
            name: '🖱️ Classic Node Context Menu',
            type: 'boolean',
            tooltip: 'Use the complete classic LiteGraph menu for every right-click inside a Nodes 2.0 node, including previews, text inputs, and widgets. Applies immediately.',
            defaultValue: false,
            sortOrder: 50,
            onChange: setEnabled,
        });
    },
});
