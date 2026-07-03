/**
 * Eclipse Groups Panel
 *
 * Lists every group in the active root graph with a tri-state Active /
 * Mute / Bypass control per group, plus sort, filter, and "set all"
 * batch buttons.
 *
 * Two surfaces, sharing the same body markup and behavior:
 *   - new menu  → registered as a left-sidebar tab (Groups icon).
 *   - legacy menu → topbar button toggles a fixed slide-out on the right.
 *
 * Scope: root graph only (app.graph.groups). No subgraph traversal.
 */

import { app } from './comfy/index.js';

// ---------------------------------------------------------------------------
// Mode constants — duplicated locally to keep this file standalone (no
// cross-node imports).
// ---------------------------------------------------------------------------
const MODE_ALWAYS = 0;
const MODE_MUTE = 2;
const MODE_BYPASS = 4;

const STATE_ACTIVE = { mode: MODE_ALWAYS, label: 'active' };
const STATE_BYPASS = { mode: MODE_BYPASS, label: 'bypass' };
const STATE_MUTE   = { mode: MODE_MUTE,   label: 'muted'  };
const SEGMENTS = [STATE_ACTIVE, STATE_BYPASS, STATE_MUTE];

const PANEL_WIDTH = 420;
// Tick is just a watchdog for external state changes (mode toggled via canvas
// menu, group renamed/colored). User-driven mutations call render() directly,
// so the interval can be slow. We additionally fingerprint-diff to skip
// re-rendering when nothing changed — this prevents button hover-flicker.
const TICK_INTERVAL_MS = 2000;
const SETTING_SORT_BY = 'Comfy.Eclipse.GroupsPanel.SortBy';

// ---------------------------------------------------------------------------
// CSS injection.
// ---------------------------------------------------------------------------
const CSS_ID = 'eclipse-groups-panel-css';
const CSS = `
.eclipse-gp-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 28px;
    min-width: 28px;
    padding: 0 8px;
    margin: 0 2px;
    border: 1px solid var(--border-color, #4e4e4e);
    border-radius: 6px;
    background: transparent;
    color: var(--input-text, #ddd);
    font-size: 13px;
    cursor: pointer;
    line-height: 1;
    box-sizing: border-box;
    transition: background-color 120ms, border-color 120ms;
}
.eclipse-gp-btn:hover {
    background: var(--comfy-input-bg, #222);
    border-color: var(--input-text, #888);
}
.eclipse-gp-btn.eclipse-gp-btn--open {
    background: rgba(102, 170, 102, 0.18);
    border-color: #6a6;
    color: #cfe9cf;
}
.eclipse-gp-btn-legacy {
    width: 100%;
    margin: 8px 0 4px 0;
    padding-top: 6px;
    height: 32px;
    border-radius: 8px;
    background: var(--comfy-input-bg, #222);
    border-top: 1px solid var(--border-color, #4e4e4e);
}

/* Floating slide-out (legacy menu only). */
.eclipse-gp-panel {
    position: fixed;
    top: 0;
    right: 0;
    width: ${PANEL_WIDTH}px;
    max-width: 100vw;
    height: 100vh;
    z-index: 1100;
    display: flex;
    flex-direction: column;
    background: var(--comfy-menu-bg, #353535);
    color: var(--input-text, #ddd);
    border-left: 1px solid var(--border-color, #4e4e4e);
    box-shadow: -4px 0 16px rgba(0, 0, 0, 0.4);
    transform: translateX(100%);
    transition: transform 220ms ease-out;
    font-size: 13px;
    box-sizing: border-box;
}
.eclipse-gp-panel.eclipse-gp-panel--open {
    transform: translateX(0);
}

/* Body host — shared between floating and sidebar surfaces. */
.eclipse-gp-body {
    flex: 1 1 auto;
    display: flex;
    flex-direction: column;
    min-height: 0;
    color: var(--input-text, #ddd);
    font-size: 13px;
    box-sizing: border-box;
}

.eclipse-gp-header {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 12px;
    border-bottom: 1px solid var(--border-color, #4e4e4e);
    font-weight: 600;
    font-size: 14px;
}
.eclipse-gp-close {
    background: transparent;
    border: none;
    color: var(--descrip-text, #999);
    font-size: 18px;
    line-height: 1;
    cursor: pointer;
    padding: 2px 6px;
    border-radius: 4px;
}
.eclipse-gp-close:hover {
    background: var(--comfy-input-bg, #222);
    color: var(--input-text, #ddd);
}

.eclipse-gp-toolbar {
    flex: 0 0 auto;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border-color, #4e4e4e);
    display: flex;
    flex-direction: column;
    gap: 6px;
}
.eclipse-gp-toolbar label {
    font-size: 11px;
    color: var(--descrip-text, #999);
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.eclipse-gp-toolbar select,
.eclipse-gp-toolbar input[type='text'] {
    width: 100%;
    height: 26px;
    padding: 0 8px;
    background: var(--comfy-input-bg, #222);
    color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #4e4e4e);
    border-radius: 4px;
    font-size: 12px;
    box-sizing: border-box;
}
.eclipse-gp-toolbar input[type='text']::placeholder {
    color: var(--descrip-text, #999);
}

.eclipse-gp-list {
    flex: 1 1 auto;
    overflow-y: auto;
    padding: 4px 0;
}
.eclipse-gp-empty {
    padding: 24px 16px;
    text-align: center;
    color: var(--descrip-text, #999);
    font-style: italic;
}

.eclipse-gp-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    border-bottom: 1px solid var(--border-color, #4e4e4e);
}
.eclipse-gp-row:hover {
    background: var(--comfy-input-bg, #222);
}
.eclipse-gp-row__dot {
    flex: 0 0 auto;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--border-color, #4e4e4e);
    border: 1px solid rgba(255, 255, 255, 0.15);
}
.eclipse-gp-row__title {
    flex: 1 1 auto;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 12px;
    cursor: pointer;
}
.eclipse-gp-row__count {
    flex: 0 0 auto;
    font-size: 10px;
    color: var(--descrip-text, #999);
    min-width: 22px;
    text-align: right;
}

.eclipse-gp-segs {
    flex: 0 0 auto;
    display: inline-flex;
    border: 1px solid var(--border-color, #4e4e4e);
    border-radius: 4px;
    overflow: hidden;
    height: 22px;
}
.eclipse-gp-seg {
    background: transparent;
    border: none;
    color: var(--descrip-text, #999);
    padding: 0 8px;
    font-size: 11px;
    line-height: 22px;
    cursor: pointer;
    transition: background-color 100ms, color 100ms;
    border-right: 1px solid var(--border-color, #4e4e4e);
    min-width: 22px;
}
.eclipse-gp-seg:last-child { border-right: none; }
.eclipse-gp-seg:hover { background: rgba(255, 255, 255, 0.06); }
.eclipse-gp-seg--active { color: #fff; font-weight: 600; }
.eclipse-gp-seg--active.eclipse-gp-seg--state-active {
    background: rgba(102, 170, 102, 0.30);
    color: #cfe9cf;
}
.eclipse-gp-seg--active.eclipse-gp-seg--state-bypass {
    background: rgba(170, 170, 102, 0.30);
    color: #ecead0;
}
.eclipse-gp-seg--active.eclipse-gp-seg--state-mute {
    background: rgba(170, 68, 68, 0.30);
    color: #f0caca;
}
.eclipse-gp-seg--disabled {
    opacity: 0.4;
    cursor: default;
}

.eclipse-gp-footer {
    flex: 0 0 auto;
    padding: 10px 12px;
    border-top: 1px solid var(--border-color, #4e4e4e);
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
}
.eclipse-gp-footer__label {
    font-size: 11px;
    color: var(--descrip-text, #999);
    margin-right: 4px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.eclipse-gp-footer button {
    flex: 1 1 0;
    height: 26px;
    border: 1px solid var(--border-color, #4e4e4e);
    background: var(--comfy-input-bg, #222);
    color: var(--input-text, #ddd);
    border-radius: 4px;
    font-size: 11px;
    cursor: pointer;
    padding: 0 6px;
}
.eclipse-gp-footer button:hover {
    background: var(--secondary-background-hover, #444);
}
.eclipse-gp-footer .eclipse-gp-footer__active:hover  { border-color: #6a6; color: #cfe9cf; }
.eclipse-gp-footer .eclipse-gp-footer__bypass:hover  { border-color: #aa6; color: #ecead0; }
.eclipse-gp-footer .eclipse-gp-footer__mute:hover    { border-color: #a44; color: #f0caca; }
`;

function injectCSS() {
    if (document.getElementById(CSS_ID)) return;
    const style = document.createElement('style');
    style.id = CSS_ID;
    style.textContent = CSS;
    document.head.appendChild(style);
}

// ---------------------------------------------------------------------------
// Group state helpers.
// ---------------------------------------------------------------------------
function getGroupNodeModes(group) {
    if (!group) return [];
    if (typeof group.recomputeInsideNodes === 'function') {
        try { group.recomputeInsideNodes(); } catch (_) { /* noop */ }
    }
    const nodes = group._nodes || [];
    const out = [];
    for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        if (n && typeof n.mode === 'number') out.push(n.mode);
    }
    return out;
}

// "Active wins": all-NEVER → 'mute', all-BYPASS → 'bypass', else → 'active'.
function computeGroupState(group) {
    const modes = getGroupNodeModes(group);
    if (modes.length === 0) return 'empty';
    let allMute = true, allBypass = true;
    for (let i = 0; i < modes.length; i++) {
        if (modes[i] !== MODE_MUTE) allMute = false;
        if (modes[i] !== MODE_BYPASS) allBypass = false;
        if (!allMute && !allBypass) break;
    }
    if (allMute) return 'mute';
    if (allBypass) return 'bypass';
    return 'active';
}

function applyGroupState(group, targetMode) {
    if (!group) return false;
    if (typeof group.recomputeInsideNodes === 'function') {
        try { group.recomputeInsideNodes(); } catch (_) { /* noop */ }
    }
    const nodes = group._nodes || [];
    let changed = false;
    for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        if (!n || typeof n.mode !== 'number') continue;
        if (n.mode !== targetMode) {
            n.mode = targetMode;
            changed = true;
        }
    }
    return changed;
}

function getGroupTitle(g) {
    return (g && typeof g.title === 'string') ? g.title : '';
}

// ---------------------------------------------------------------------------
// Sort.
// ---------------------------------------------------------------------------
const SORT_OPTIONS = [
    { id: 'workflow',  label: 'Workflow order' },
    { id: 'az',        label: 'Alphabetical (A → Z)' },
    { id: 'za',        label: 'Alphabetical (Z → A)' },
    { id: 'pos_top',   label: 'Position (top → bottom)' },
    { id: 'pos_left',  label: 'Position (left → right)' },
    { id: 'color',     label: 'Color' },
    { id: 'count',     label: 'Node count' },
    { id: 'state',     label: 'State (Empty / Mute / Bypass / Active)' },
];

const STATE_SORT_RANK = { empty: 0, mute: 1, bypass: 2, active: 3 };

function buildComparator(sortId, indexById) {
    const titleOf = (g) => getGroupTitle(g).toLocaleLowerCase();
    const byIndex = (a, b) => (indexById.get(a) ?? 0) - (indexById.get(b) ?? 0);
    switch (sortId) {
        case 'az':
            return (a, b) => titleOf(a).localeCompare(titleOf(b)) || byIndex(a, b);
        case 'za':
            return (a, b) => titleOf(b).localeCompare(titleOf(a)) || byIndex(a, b);
        case 'pos_top':
            return (a, b) => {
                const ay = a.pos?.[1] ?? 0, by = b.pos?.[1] ?? 0;
                if (ay !== by) return ay - by;
                const ax = a.pos?.[0] ?? 0, bx = b.pos?.[0] ?? 0;
                return ax - bx || byIndex(a, b);
            };
        case 'pos_left':
            return (a, b) => {
                const ax = a.pos?.[0] ?? 0, bx = b.pos?.[0] ?? 0;
                if (ax !== bx) return ax - bx;
                const ay = a.pos?.[1] ?? 0, by = b.pos?.[1] ?? 0;
                return ay - by || byIndex(a, b);
            };
        case 'color':
            return (a, b) => {
                const ac = a.color || '\uffff', bc = b.color || '\uffff';
                return ac.localeCompare(bc) || byIndex(a, b);
            };
        case 'count':
            return (a, b) => {
                const an = (a._nodes?.length) ?? 0, bn = (b._nodes?.length) ?? 0;
                return bn - an || byIndex(a, b);
            };
        case 'state':
            return (a, b) => {
                const ar = STATE_SORT_RANK[computeGroupState(a)] ?? 9;
                const br = STATE_SORT_RANK[computeGroupState(b)] ?? 9;
                return ar - br || byIndex(a, b);
            };
        case 'workflow':
        default:
            return byIndex;
    }
}

// ---------------------------------------------------------------------------
// Shared state — sort persists across surfaces; filter does not.
// ---------------------------------------------------------------------------
const sharedState = {
    sortId: 'workflow',
};

function loadSortPref() {
    try {
        const v = app.ui?.settings?.getSettingValue?.(SETTING_SORT_BY) ?? 'workflow';
        if (v && typeof v === 'string') sharedState.sortId = v;
    } catch (_) { /* noop */ }
}

function persistSortPref(val) {
    try { app.ui?.settings?.setSettingValue?.(SETTING_SORT_BY, val); } catch (_) { /* noop */ }
}

function focusGroupOnCanvas(group) {
    try {
        const canvas = app.canvas;
        const ds = canvas?.ds;
        if (!ds || !group?.pos || !group?.size) return;
        const cx = group.pos[0] + group.size[0] / 2;
        const cy = group.pos[1] + group.size[1] / 2;
        const rect = canvas.canvas?.getBoundingClientRect?.();
        if (!rect) return;
        ds.offset[0] = (rect.width / 2) / ds.scale - cx;
        ds.offset[1] = (rect.height / 2) / ds.scale - cy;
        app.graph.setDirtyCanvas(true, true);
    } catch (_) { /* noop */ }
}

// ---------------------------------------------------------------------------
// GroupsView — body markup + behavior, mounts into any host element.
// ---------------------------------------------------------------------------
class GroupsView {
    constructor() {
        this.host = null;
        this.listEl = null;
        this.sortEl = null;
        this.searchEl = null;
        this.searchValue = '';
        this.tickHandle = null;
        this.lastFingerprint = '';
    }

    // Cheap signature of everything render() draws. If unchanged between
    // ticks, skip the DOM rebuild (avoids :hover flicker on buttons).
    computeFingerprint() {
        const groups = app.graph?.groups || [];
        const parts = [sharedState.sortId, this.searchValue, String(groups.length)];
        for (const g of groups) {
            parts.push(getGroupTitle(g));
            parts.push(computeGroupState(g));
            parts.push(g.color || '');
        }
        return parts.join('\x1f');
    }

    mount(host) {
        this.host = host;
        host.classList.add('eclipse-gp-body');

        // Toolbar.
        const toolbar = document.createElement('div');
        toolbar.className = 'eclipse-gp-toolbar';

        const sortLabel = document.createElement('label');
        sortLabel.textContent = 'Sort';
        toolbar.appendChild(sortLabel);

        this.sortEl = document.createElement('select');
        for (const opt of SORT_OPTIONS) {
            const o = document.createElement('option');
            o.value = opt.id;
            o.textContent = opt.label;
            this.sortEl.appendChild(o);
        }
        this.sortEl.value = sharedState.sortId;
        this.sortEl.addEventListener('change', () => {
            sharedState.sortId = this.sortEl.value;
            persistSortPref(sharedState.sortId);
            this.render();
        });
        toolbar.appendChild(this.sortEl);

        this.searchEl = document.createElement('input');
        this.searchEl.type = 'text';
        this.searchEl.placeholder = 'Filter groups…';
        this.searchEl.spellcheck = false;
        this.searchEl.addEventListener('input', () => {
            this.searchValue = this.searchEl.value || '';
            this.render();
        });
        toolbar.appendChild(this.searchEl);

        host.appendChild(toolbar);

        // List.
        this.listEl = document.createElement('div');
        this.listEl.className = 'eclipse-gp-list';
        host.appendChild(this.listEl);

        // Footer.
        const footer = document.createElement('div');
        footer.className = 'eclipse-gp-footer';
        const flabel = document.createElement('span');
        flabel.className = 'eclipse-gp-footer__label';
        flabel.textContent = 'Set all:';
        footer.appendChild(flabel);
        const mkFooterBtn = (label, mode, klass) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.textContent = label;
            b.className = klass;
            b.addEventListener('click', () => this.applyAll(mode));
            return b;
        };
        footer.appendChild(mkFooterBtn('Active', MODE_ALWAYS, 'eclipse-gp-footer__active'));
        footer.appendChild(mkFooterBtn('Bypass', MODE_BYPASS, 'eclipse-gp-footer__bypass'));
        footer.appendChild(mkFooterBtn('Mute',   MODE_MUTE,   'eclipse-gp-footer__mute'));
        host.appendChild(footer);
    }

    syncSortFromShared() {
        if (this.sortEl && this.sortEl.value !== sharedState.sortId) {
            this.sortEl.value = sharedState.sortId;
        }
    }

    render() {
        if (!this.listEl) return;
        this.syncSortFromShared();

        const groups = app.graph?.groups || [];
        if (groups.length === 0) {
            this.listEl.replaceChildren(this.buildEmpty());
            return;
        }

        const indexById = new Map();
        for (let i = 0; i < groups.length; i++) indexById.set(groups[i], i);

        const cmp = buildComparator(sharedState.sortId, indexById);
        const sorted = groups.slice().sort(cmp);

        const needle = (this.searchValue || '').trim().toLocaleLowerCase();
        const filtered = needle
            ? sorted.filter((g) => getGroupTitle(g).toLocaleLowerCase().includes(needle))
            : sorted;

        if (filtered.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'eclipse-gp-empty';
            empty.textContent = needle
                ? `No groups match "${this.searchValue}".`
                : 'No groups in this workflow. Right-click the canvas → Add Group.';
            this.listEl.replaceChildren(empty);
            return;
        }

        const frag = document.createDocumentFragment();
        for (const g of filtered) frag.appendChild(this.buildRow(g));
        this.listEl.replaceChildren(frag);
        this.lastFingerprint = this.computeFingerprint();
    }

    buildEmpty() {
        const empty = document.createElement('div');
        empty.className = 'eclipse-gp-empty';
        empty.textContent = 'No groups in this workflow. Right-click the canvas → Add Group.';
        return empty;
    }

    buildRow(group) {
        const row = document.createElement('div');
        row.className = 'eclipse-gp-row';

        const dot = document.createElement('span');
        dot.className = 'eclipse-gp-row__dot';
        if (group.color && typeof group.color === 'string') {
            dot.style.background = group.color;
        }
        row.appendChild(dot);

        const title = document.createElement('span');
        title.className = 'eclipse-gp-row__title';
        title.textContent = getGroupTitle(group) || '(unnamed group)';
        title.title = title.textContent;
        title.addEventListener('click', () => focusGroupOnCanvas(group));
        row.appendChild(title);

        const count = document.createElement('span');
        count.className = 'eclipse-gp-row__count';
        const nodeCount = (group._nodes?.length) ?? 0;
        count.textContent = String(nodeCount);
        row.appendChild(count);

        row.appendChild(this.buildSegments(group));
        return row;
    }

    buildSegments(group) {
        const state = computeGroupState(group);
        const segs = document.createElement('div');
        segs.className = 'eclipse-gp-segs';

        for (const seg of SEGMENTS) {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'eclipse-gp-seg';
            b.textContent = seg.label;
            b.title = `Set every node in this group to ${seg.label}`;
            const isActive = (
                (seg.mode === MODE_ALWAYS && state === 'active') ||
                (seg.mode === MODE_BYPASS && state === 'bypass') ||
                (seg.mode === MODE_MUTE   && state === 'mute')
            );
            if (isActive) {
                b.classList.add('eclipse-gp-seg--active');
                if (seg.mode === MODE_ALWAYS) b.classList.add('eclipse-gp-seg--state-active');
                if (seg.mode === MODE_BYPASS) b.classList.add('eclipse-gp-seg--state-bypass');
                if (seg.mode === MODE_MUTE)   b.classList.add('eclipse-gp-seg--state-mute');
            }
            if (state === 'empty') {
                b.classList.add('eclipse-gp-seg--disabled');
                b.disabled = true;
            } else {
                b.addEventListener('click', () => {
                    if (applyGroupState(group, seg.mode)) {
                        try { app.graph?.setDirtyCanvas?.(true, true); } catch (_) { /* noop */ }
                    }
                    this.render();
                });
            }
            segs.appendChild(b);
        }
        return segs;
    }

    applyAll(targetMode) {
        const groups = app.graph?.groups || [];
        let any = false;
        for (const g of groups) any = applyGroupState(g, targetMode) || any;
        if (any) {
            try { app.graph?.setDirtyCanvas?.(true, true); } catch (_) { /* noop */ }
        }
        this.render();
    }

    // Auto-stops if the host is detached from the DOM (e.g. sidebar tab
    // closed and Vue tore down the panel content).
    startTick() {
        this.stopTick();
        this.lastFingerprint = this.computeFingerprint();
        this.tickHandle = window.setInterval(() => {
            if (!this.host || !this.host.isConnected) {
                this.stopTick();
                return;
            }
            const fp = this.computeFingerprint();
            if (fp === this.lastFingerprint) return;
            this.lastFingerprint = fp;
            this.render();
        }, TICK_INTERVAL_MS);
    }

    stopTick() {
        if (this.tickHandle != null) {
            window.clearInterval(this.tickHandle);
            this.tickHandle = null;
        }
    }
}

// ---------------------------------------------------------------------------
// FloatingPanel — slide-out chrome for legacy menu mode.
// ---------------------------------------------------------------------------
class FloatingPanel {
    constructor() {
        this.root = null;
        this.view = null;
        this.button = null;
        this.open = false;
        this.escHandler = null;
        this.savedMenuPos = null;
    }

    ensureBuilt() {
        if (this.root) return;
        this.root = document.createElement('div');
        this.root.className = 'eclipse-gp-panel';

        const header = document.createElement('div');
        header.className = 'eclipse-gp-header';
        header.appendChild(document.createTextNode('Groups'));
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'eclipse-gp-close';
        close.title = 'Close (Esc)';
        close.textContent = '×';
        close.addEventListener('click', () => this.close());
        header.appendChild(close);
        this.root.appendChild(header);

        const body = document.createElement('div');
        this.root.appendChild(body);

        this.view = new GroupsView();
        this.view.mount(body);

        document.body.appendChild(this.root);
    }

    toggle() { this.open ? this.close() : this.openPanel(); }

    openPanel() {
        if (this.open) return;
        this.ensureBuilt();
        this.open = true;
        this.shiftLegacyMenuIfNeeded();
        // Force reflow so the transform animates.
        // eslint-disable-next-line no-unused-expressions
        this.root.getBoundingClientRect();
        this.root.classList.add('eclipse-gp-panel--open');
        this.button?.classList?.add('eclipse-gp-btn--open');
        this.view.render();
        this.view.startTick();
        this.escHandler = (e) => { if (e.key === 'Escape') this.close(); };
        window.addEventListener('keydown', this.escHandler);
    }

    close() {
        if (!this.open) return;
        this.open = false;
        this.root?.classList.remove('eclipse-gp-panel--open');
        this.button?.classList?.remove('eclipse-gp-btn--open');
        this.view?.stopTick();
        if (this.escHandler) {
            window.removeEventListener('keydown', this.escHandler);
            this.escHandler = null;
        }
        this.restoreLegacyMenuPos();
    }

    shiftLegacyMenuIfNeeded() {
        try {
            const menu = app.ui?.menuContainer;
            if (!menu) return;
            if (menu.style.display === 'none') return;
            const rect = menu.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return;
            const atRightEdge = rect.right >= window.innerWidth - 4;
            if (!atRightEdge) return;
            this.savedMenuPos = {
                left:  menu.style.left,
                right: menu.style.right,
                top:   menu.style.top,
            };
            menu.style.left = 'unset';
            menu.style.right = `${PANEL_WIDTH}px`;
        } catch (_) { /* noop */ }
    }

    restoreLegacyMenuPos() {
        if (!this.savedMenuPos) return;
        try {
            const menu = app.ui?.menuContainer;
            if (!menu) { this.savedMenuPos = null; return; }
            if (menu.style.right === `${PANEL_WIDTH}px`) {
                menu.style.left = this.savedMenuPos.left;
                menu.style.right = this.savedMenuPos.right;
                menu.style.top = this.savedMenuPos.top;
            }
        } catch (_) { /* noop */ }
        this.savedMenuPos = null;
    }
}

const floatingPanel = new FloatingPanel();

// ---------------------------------------------------------------------------
// Legacy topbar button.
// ---------------------------------------------------------------------------
function buildLegacyButton() {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'eclipse-gp-btn eclipse-gp-btn-legacy';
    btn.title = 'Eclipse Groups Panel — toggle slide-out';
    btn.textContent = '⊞ Groups';
    btn.addEventListener('click', () => floatingPanel.toggle());
    return btn;
}

function isNewMenuActive() {
    try {
        const v = app.ui?.settings?.getSettingValue?.('Comfy.UseNewMenu') || 'Top';
        return v !== 'Disabled';
    } catch (_) { return true; }
}

function injectLegacyButton() {
    if (isNewMenuActive()) {
        // Sidebar tab handles new-menu mode — remove any stale button.
        if (floatingPanel.button && floatingPanel.button.isConnected) {
            try { floatingPanel.button.remove(); } catch (_) { /* noop */ }
        }
        floatingPanel.button = null;
        return;
    }
    if (floatingPanel.button && floatingPanel.button.isConnected) return;
    const host = app.ui?.menuContainer;
    if (!host) return;
    const btn = buildLegacyButton();
    host.appendChild(btn);
    floatingPanel.button = btn;
    if (floatingPanel.open) btn.classList.add('eclipse-gp-btn--open');
}

// Watch for menu rebuilds (Vue tears down/rebuilds DOM when toggling
// Comfy.UseNewMenu).
function watchForMenuRebuilds() {
    const observer = new MutationObserver(() => {
        injectLegacyButton();
    });
    observer.observe(document.body, { childList: true, subtree: true });
}

// ---------------------------------------------------------------------------
// Sidebar tab (new menu).
// ---------------------------------------------------------------------------
let activeSidebarView = null;

function registerSidebarTab() {
    const em = app.extensionManager;
    if (!em || typeof em.registerSidebarTab !== 'function') return false;
    em.registerSidebarTab({
        id: 'eclipse-groups',
        icon: 'pi pi-objects-column',
        title: 'Groups',
        tooltip: 'Eclipse: tri-state Active/Mute/Bypass per group.',
        type: 'custom',
        render: (el) => {
            // Vue calls render() each time the tab is shown. The previous
            // GroupsView's host is now detached → its tick auto-stops on
            // the next interval. Mount a fresh view into el.
            try { activeSidebarView?.stopTick(); } catch (_) { /* noop */ }
            el.replaceChildren();
            const view = new GroupsView();
            view.mount(el);
            view.render();
            view.startTick();
            activeSidebarView = view;
        },
    });
    return true;
}

// ---------------------------------------------------------------------------
// Extension registration.
// ---------------------------------------------------------------------------
app.registerExtension({
    name: 'Eclipse.GroupsPanel',
    commands: [
        {
            id: 'Eclipse.GroupsPanel.Toggle',
            label: 'Eclipse: Toggle Groups Panel',
            function: () => {
                // In new-menu mode, route the command to the sidebar tab.
                if (isNewMenuActive()) {
                    try {
                        app.extensionManager?.command?.execute?.(
                            'Workspace.ToggleSidebarTab.eclipse-groups'
                        );
                    } catch (_) { /* noop */ }
                    return;
                }
                floatingPanel.toggle();
            },
        },
    ],
    async init(appRef) {
        injectCSS();
        appRef.ui.settings.addSetting({
            id: SETTING_SORT_BY,
            name: '🌒 Eclipse Groups Panel — sort order',
            type: 'combo',
            tooltip: 'Default row sort order in the Eclipse Groups Panel.',
            defaultValue: 'workflow',
            options: SORT_OPTIONS.map((o) => ({ value: o.id, text: o.label })),
            onChange(val) {
                if (typeof val !== 'string') return;
                sharedState.sortId = val;
                if (floatingPanel.view) floatingPanel.view.syncSortFromShared();
                if (activeSidebarView) {
                    activeSidebarView.syncSortFromShared();
                    activeSidebarView.render();
                }
                if (floatingPanel.open) floatingPanel.view.render();
            },
        });
    },
    async setup() {
        loadSortPref();

        // New menu: register a sidebar tab. Retry briefly because
        // extensionManager may mount slightly after setup() on cold start.
        if (!registerSidebarTab()) {
            let tries = 0;
            const t = setInterval(() => {
                tries += 1;
                if (registerSidebarTab() || tries > 20) clearInterval(t);
            }, 100);
        }

        // Legacy menu: inject a topbar button.
        injectLegacyButton();
        queueMicrotask(injectLegacyButton);
        setTimeout(injectLegacyButton, 0);
        setTimeout(injectLegacyButton, 250);
        watchForMenuRebuilds();
    },
});
