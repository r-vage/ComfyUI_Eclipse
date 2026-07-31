/**
 * eclipse-context-menu-ownership.js — Internal ownership marker for DOM menus.
 *
 * Copyright (c) 2026 r-vage. MIT License.
 */
const OWNER_ATTRIBUTE = 'data-eclipse-context-menu-owner';

export function markEclipseContextMenuOwner(element) {
    element?.setAttribute?.(OWNER_ATTRIBUTE, '');
}

export function hasEclipseContextMenuOwner(target) {
    return target?.closest?.(`[${OWNER_ATTRIBUTE}]`) != null;
}
