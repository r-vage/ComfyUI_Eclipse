/**
 * eclipse-vue-classic-node-context-menu.js — Optional classic target menus for Nodes 2.0.
 *
 * Copyright (c) 2026 r-vage. MIT License.
 */
import { app } from './comfy/index.js';
import { hasEclipseContextMenuOwner } from './eclipse-context-menu-ownership.js';
import { isVueMode } from './eclipse-widget-performance-utils.js';

const SETTING_ID = 'Eclipse.VueClassicNodeContextMenu';
const NODES_2_SETTINGS_CATEGORY = ['Eclipse', 'Nodes 2.0'];
const TEXT_INPUT_TYPES = new Set([
    'email',
    'number',
    'password',
    'search',
    'tel',
    'text',
    'url',
]);

let enabled = false;
let listenerInstalled = false;
const GROUP_MENU_WRAPPER = Symbol('Eclipse.VueClassicGroupContextMenu');

function resolveNodeElement(event) {
    return event.target?.closest?.('.lg-node[data-node-id]') ?? null;
}

function resolveNode(nodeElement, canvas) {
    const nodeId = nodeElement?.dataset?.nodeId ?? nodeElement?.getAttribute?.('data-node-id');
    const graph = canvas?.graph;
    if (nodeId == null || typeof graph?.getNodeById !== 'function') return null;

    let node = graph.getNodeById(nodeId);
    if (!node && /^-?\d+$/.test(nodeId)) node = graph.getNodeById(Number(nodeId));
    return node || null;
}

function isEditableTarget(target) {
    const editable = target?.closest?.('input, textarea, [contenteditable]');
    const tagName = editable?.tagName?.toLowerCase();
    if (tagName === 'textarea') return true;
    if (tagName === 'input') {
        const type = String(editable.type || editable.getAttribute?.('type') || 'text').toLowerCase();
        return TEXT_INPUT_TYPES.has(type);
    }
    return editable?.getAttribute?.('contenteditable') !== 'false' && editable != null;
}

function resolveClickedImage(event, nodeElement) {
    const directImage = event.target?.closest?.('img');
    if (directImage && nodeElement.contains?.(directImage)) return directImage;

    const hitElements = document.elementsFromPoint?.(event.clientX, event.clientY) ?? [];
    return Array.from(hitElements).find((element) =>
        element?.tagName?.toLowerCase() === 'img' && nodeElement.contains?.(element)
    ) ?? null;
}

function syncLegacyImageState(node, image) {
    const existingIndex = Array.isArray(node.imgs)
        ? node.imgs.findIndex((candidate) =>
            candidate === image || candidate?.src === image.src
        )
        : -1;
    if (existingIndex >= 0) {
        node.imageIndex = existingIndex;
        return;
    }
    node.imgs = [image];
    node.imageIndex = 0;
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

function hasRerouteAtEvent(canvas, event) {
    if (canvas.links_render_mode === globalThis.LiteGraph?.HIDDEN_LINK) return false;
    const graph = canvas.graph;
    if (typeof graph?.getRerouteOnPos !== 'function') return false;
    return Boolean(graph.getRerouteOnPos(
        event.canvasX,
        event.canvasY,
        canvas._visibleReroutes
    ));
}

function openClassicGroupContextMenu(canvas, event) {
    const graph = canvas?.graph;
    if (!graph || typeof graph.getGroupOnPos !== 'function') return false;
    if (!Number.isFinite(event?.canvasX) || !Number.isFinite(event?.canvasY)) return false;
    if (hasRerouteAtEvent(canvas, event)) return false;

    const group = graph.getGroupOnPos(event.canvasX, event.canvasY);
    if (!group || typeof group.getMenuOptions !== 'function') return false;
    if (typeof canvas.getCanvasMenuOptions !== 'function') return false;
    if (typeof globalThis.LiteGraph?.ContextMenu !== 'function') return false;

    const menuItems = canvas.getCanvasMenuOptions();
    const groupItems = group.getMenuOptions();
    if (!Array.isArray(menuItems) || !Array.isArray(groupItems)) return false;

    menuItems.push(null, {
        content: 'Edit Group',
        has_submenu: true,
        submenu: {
            title: 'Group',
            extra: group,
            options: groupItems,
        },
    });

    globalThis.LGraphCanvas.active_canvas = canvas;
    globalThis.LiteGraph.closeAllContextMenus(canvas.getCanvasWindow?.() ?? window);
    new globalThis.LiteGraph.ContextMenu(menuItems, { event });
    return true;
}

function installGroupContextMenuOverride(canvas = app.canvas) {
    const original = canvas?.processContextMenu;
    if (typeof original !== 'function' || original[GROUP_MENU_WRAPPER]) return;

    function processContextMenuWithClassicGroups(node, event) {
        if (enabled && isVueMode() && !node && openClassicGroupContextMenu(this, event)) return;
        return original.apply(this, arguments);
    }
    Object.defineProperty(processContextMenuWithClassicGroups, GROUP_MENU_WRAPPER, {
        value: true,
    });
    canvas.processContextMenu = processContextMenuWithClassicGroups;
}

function handleVueNodeContextMenu(event) {
    if (!enabled || !isVueMode()) return;
    if (hasEclipseContextMenuOwner(event.target)) return;

    const canvas = app.canvas;
    installGroupContextMenuOverride(canvas);
    if (!hasRequiredClassicMenuAPIs(canvas)) return;
    const nodeElement = resolveNodeElement(event);
    const node = resolveNode(nodeElement, canvas);
    if (!node) return;
    if (isEditableTarget(event.target)) {
        event.stopImmediatePropagation();
        return;
    }

    canvas.adjustMouseEvent(event);
    if (!Number.isFinite(event.canvasX) || !Number.isFinite(event.canvasY)) return;

    const clickedImage = resolveClickedImage(event, nodeElement);
    if (clickedImage) syncLegacyImageState(node, clickedImage);

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
    installGroupContextMenuOverride();
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
        installGroupContextMenuOverride(appRef.canvas);
        appRef.ui.settings.addSetting({
            id: 'Eclipse.VueClassicNodeContextMenu',
            category: [...NODES_2_SETTINGS_CATEGORY, 'VueClassicNodeContextMenu'],
            name: '🖱️ Classic Node & Group Context Menus',
            type: 'boolean',
            tooltip: 'Use classic canvas context menus in Nodes 2.0. Group right-clicks include the complete canvas menu and Edit Group submenu; Eclipse preview menus, browser text editing, and native media menus take precedence. Applies immediately.',
            defaultValue: false,
            sortOrder: 50,
            onChange: setEnabled,
        });
    },
});
