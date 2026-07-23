/**
 * eclipse-context-menu-utils.js — Renderer-aware cascading menu helpers.
 *
 * Copyright (c) 2026 r-vage. MIT License.
 */
import { isVueMode } from './eclipse-widget-performance-utils.js';

const CASCADE_CLASS = 'eclipse-context-menu-cascade';

let activeCascade = null;
let pendingCascade = null;
let latestPointer = {
    x: Math.round(window.innerWidth / 2),
    y: Math.round(window.innerHeight / 2),
};

function rememberPointer(event) {
    if (!Number.isFinite(event.clientX) || !Number.isFinite(event.clientY)) return;
    latestPointer = {
        x: event.clientX,
        y: event.clientY,
    };
}

document.addEventListener('pointerdown', rememberPointer, true);
document.addEventListener('contextmenu', rememberPointer, true);

function createPointerEvent() {
    return new MouseEvent('click', {
        bubbles: true,
        cancelable: true,
        clientX: latestPointer.x,
        clientY: latestPointer.y,
        view: window,
    });
}

function closeActiveCascade() {
    if (pendingCascade !== null) {
        clearTimeout(pendingCascade);
        pendingCascade = null;
    }
    activeCascade?.close?.();
    activeCascade = null;
    document.querySelectorAll(`.${CASCADE_CLASS}`).forEach((root) => root.remove());
}

export function openCascadingMenu(items, options = {}) {
    closeActiveCascade();
    if (!Array.isArray(items) || items.length === 0) return;

    pendingCascade = setTimeout(() => {
        pendingCascade = null;
        closeActiveCascade();
        const ContextMenu = LiteGraph?.ContextMenu;
        if (typeof ContextMenu !== 'function') {
            console.debug('[Eclipse] LiteGraph.ContextMenu is unavailable');
            return;
        }
        activeCascade = new ContextMenu(items, {
            callback: options.callback,
            event: createPointerEvent(),
            extra: options.extra,
            ignore_item_callbacks: options.ignore_item_callbacks,
            title: options.title,
            autoopen: true,
            className: CASCADE_CLASS,
        });
        activeCascade.root?.classList.add(CASCADE_CLASS);
    }, 0);
}

export function createRendererAwareSubmenuEntry(entry) {
    if (!isVueMode() || !entry?.submenu?.options) return entry;

    const { submenu, callback, ...launcher } = entry;
    delete launcher.has_submenu;
    launcher.callback = function () {
        callback?.apply(this, arguments);
        openCascadingMenu(submenu.options, submenu);
    };
    return launcher;
}

export function adaptNestedMenuItems(items) {
    if (!isVueMode() || !Array.isArray(items)) return items;
    return items.map((item) => createRendererAwareSubmenuEntry(item));
}
