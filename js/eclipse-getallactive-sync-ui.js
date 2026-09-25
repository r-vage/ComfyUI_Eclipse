/**
 * Draft-only, renderer-independent shared-chain dialogs.
 * Copyright (c) 2026 r-vage. MIT License.
 */
import { app } from './comfy/index.js';
import {
    linkedChain, sharedChains, chainValues, mergeChainLists, inferChainMember,
    deriveChainList, previewChainEdit, applyChainEdit, createSharedChain,
    joinSharedChain, unlinkSharedChain,
} from './eclipse-getallactive-sync.js';
import { createRendererAwareSubmenuEntry } from './eclipse-context-menu-utils.js';
import { isVueMode } from './eclipse-widget-performance-utils.js';

let menuInstalled = false;
export function installSyncChainMenu() {
    if (menuInstalled) return;
    menuInstalled = true;
    // The native Vue multi-selection menu omits all node extension entries.
    // Use the complete LiteGraph menu for a selected Get All Active in this
    // specific case, preserving the selection needed by chain creation.
    document.addEventListener('contextmenu', event => {
        if (!isVueMode() || event.defaultPrevented || event.target.closest?.('input,textarea,[contenteditable="true"]')) return;
        const element = event.target.closest?.('.lg-node[data-node-id]');
        const canvas = app.canvas;
        const node = element && canvas?.graph?.getNodeById(element.dataset.nodeId);
        if (node?.type !== 'GetAllActiveNode' || !node.selected || Object.keys(canvas.selected_nodes || {}).length < 2) return;
        event.preventDefault(); event.stopImmediatePropagation();
        LiteGraph.closeAllContextMenus?.();
        canvas.adjustMouseEvent(event);
        canvas.mouse[0] = event.clientX; canvas.mouse[1] = event.clientY;
        canvas.graph_mouse[0] = event.canvasX; canvas.graph_mouse[1] = event.canvasY;
        canvas.processContextMenu(node, event);
    }, true);
}

let activeDialog;
function dialog(title) {
    activeDialog?.close();
    const root = document.createElement('dialog');
    root.setAttribute('aria-label', title);
    root.style.cssText = 'width:min(680px,90vw);max-height:85vh;overflow:auto;background:var(--comfy-menu-bg,#222);color:var(--fg-color,#ddd);border:1px solid #777;border-radius:8px;padding:20px;';
    const heading = document.createElement('h2'); heading.textContent = title; root.append(heading);
    const body = document.createElement('div'); root.append(body);
    const status = document.createElement('pre'); status.setAttribute('aria-live', 'polite');
    status.style.cssText = 'white-space:pre-wrap;max-height:30vh;overflow:auto;font:inherit;'; root.append(status);
    const buttons = document.createElement('div'); buttons.style.cssText = 'display:flex;gap:10px;justify-content:flex-end;'; root.append(buttons);
    const button = (label, callback) => {
        const b = document.createElement('button'); b.type = 'button'; b.textContent = label;
        b.addEventListener('click', callback); buttons.append(b); return b;
    };
    button('Cancel', () => root.close());
    root.addEventListener('close', () => { root.remove(); if (activeDialog === root) activeDialog = null; }, { once: true });
    document.body.append(root); activeDialog = root; root.showModal();
    return { root, body, status, button };
}
function field(body, title, element) {
    const label = document.createElement('label'); label.textContent = title;
    label.style.cssText = 'display:block;margin:10px 0;';
    element.setAttribute('aria-label', title); element.style.cssText = 'display:block;width:100%;margin-top:5px;';
    label.append(element); body.append(label); return element;
}
const previewText = plans => plans.map(({ node, next, member }) =>
    `Getter ${node.id} — start: ${member.start || '(none)'}\n${next.join(' → ') || '(one empty row)'}\nExclusions: ${member.exclusions.join(', ') || '(none)'}`).join('\n\n');
const report = error => app.extensionManager.toast.add({ severity: 'warn', summary: 'Eclipse Sync chain', detail: error.message, life: 7000 });

export function openChainEditor(node, selected) {
    try {
        const creation = !!selected;
        const chain = creation ? null : linkedChain(node);
        if (!creation && !chain) throw Error('This getter is no longer linked.');
        const original = creation ? mergeChainLists(selected) : [...chain.variables];
        const originalChain = JSON.stringify(chain);
        const graph = node.graph;
        const ui = dialog(creation ? 'Create sync chain' : 'Edit shared chain');
        const name = field(ui.body, 'Chain name', document.createElement('input'));
        name.value = chain?.name || 'Shared chain';
        const text = field(ui.body, 'Variables in priority order (one per line)', document.createElement('textarea'));
        text.rows = Math.min(15, Math.max(5, original.length)); text.value = original.join('\n');
        const hint = document.createElement('p'); hint.textContent = 'Add or delete a line to add or remove a variable. Move lines to reorder. Preview updates below; only Apply changes the workflow.'; ui.body.append(hint);
        const read = () => text.value.split('\n').map(v => v.trim()).filter(Boolean);
        const check = () => {
            if (node.graph !== graph || (!creation && JSON.stringify(linkedChain(node)) !== originalChain)) {
                throw Error('The chain changed while this draft was open. Cancel and reopen the editor.');
            }
        };
        let apply, remove;
        const preview = () => {
            try {
                check();
                const variables = read();
                let plans;
                if (creation) {
                    if (variables.length > 20 || new Set(variables).size !== variables.length) throw Error('Use at most 20 distinct variables.');
                    plans = selected.map(n => {
                        const member = inferChainMember(n, { id: '', variables });
                        return { node: n, member, next: deriveChainList(variables, member) };
                    });
                } else plans = previewChainEdit(node, variables);
                ui.status.textContent = previewText(plans); apply.disabled = false;
            } catch (error) { ui.status.textContent = error.message; apply.disabled = true; }
            if (remove) remove.disabled = !original.some(v => !read().includes(v));
        };
        if (!creation) remove = ui.button('Apply removals only', () => {
            try { check(); applyChainEdit(node, original.filter(v => read().includes(v))); ui.root.close(); }
            catch (error) { ui.status.textContent = error.message; }
        });
        apply = ui.button('Apply', () => {
            try {
                check();
                if (creation) createSharedChain(selected, name.value, read());
                else applyChainEdit(node, read(), { name: name.value });
                ui.root.close();
            } catch (error) { ui.status.textContent = error.message; }
        });
        text.addEventListener('input', preview); preview();
    } catch (error) { report(error); }
}

export function openChainMemberOptions(node) {
    const chain = linkedChain(node);
    if (!chain) return;
    const ui = dialog('Sync chain member options');
    const original = JSON.stringify(chain), saved = JSON.stringify(node.properties.eclipseSyncChain);
    const member = { ...node.properties.eclipseSyncChain, exclusions: [...node.properties.eclipseSyncChain.exclusions] };
    const select = field(ui.body, 'Starting variable', document.createElement('select'));
    for (const value of ['', ...chain.variables]) { const option = document.createElement('option'); option.value = value; option.textContent = value || '(none — empty row)'; select.append(option); }
    select.value = member.start || '';
    const legend = document.createElement('p'); legend.textContent = 'Exclude variables for this member:'; ui.body.append(legend);
    const exclusions = new Set(member.exclusions);
    let apply;
    const draft = () => ({ ...member, start: select.value || null, exclusions: [...exclusions] });
    const preview = () => {
        try {
            ui.status.textContent = previewText(previewChainEdit(node, chain.variables, new Map([[node, draft()]])));
            apply.disabled = false;
        } catch (error) { ui.status.textContent = error.message; apply.disabled = true; }
    };
    for (const variable of chain.variables) {
        const label = document.createElement('label'); label.style.cssText = 'display:block;';
        const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = exclusions.has(variable);
        checkbox.addEventListener('change', () => { if (checkbox.checked) exclusions.add(variable); else exclusions.delete(variable); preview(); });
        label.append(checkbox, document.createTextNode(variable)); ui.body.append(label);
    }
    apply = ui.button('Apply', () => {
        try {
            if (JSON.stringify(linkedChain(node)) !== original || JSON.stringify(node.properties.eclipseSyncChain) !== saved) throw Error('The chain changed. Cancel and reopen member options.');
            applyChainEdit(node, chain.variables, { memberOverrides: new Map([[node, draft()]]) }); ui.root.close();
        } catch (error) { ui.status.textContent = error.message; }
    });
    select.addEventListener('change', preview); preview();
}

function openJoin(node) {
    const ui = dialog('Join existing sync chain');
    const select = field(ui.body, 'Chain', document.createElement('select'));
    for (const chain of sharedChains(node)) { const option = document.createElement('option'); option.value = chain.id; option.textContent = chain.name; select.append(option); }
    let apply;
    const preview = () => {
        try {
            const chain = sharedChains(node).find(c => c.id === select.value);
            if (!chain) throw Error('There are no shared chains in this workflow.');
            const member = inferChainMember(node, chain);
            ui.status.textContent = previewText([{ node, member, next: chainValues(node) }]); apply.disabled = false;
        } catch (error) { ui.status.textContent = error.message; apply.disabled = true; }
    };
    apply = ui.button('Apply', () => {
        try { joinSharedChain(node, select.value); ui.root.close(); }
        catch (error) { ui.status.textContent = error.message; }
    });
    select.addEventListener('change', preview); preview();
}

export function syncChainMenu(node, canvas) {
    const linked = linkedChain(node);
    return createRendererAwareSubmenuEntry({ content: 'Sync chain', has_submenu: true, submenu: { title: linked?.name || 'Sync chain', options: [
        { content: 'Create from selected getters', disabled: !!linked, callback: () => {
            const nodes = [...new Set([node, ...Object.values(canvas?.selected_nodes || app.canvas?.selected_nodes || {})])]
                .filter(n => n.type === 'GetAllActiveNode');
            openChainEditor(node, nodes);
        } },
        { content: 'Join existing', disabled: !!linked, callback: () => openJoin(node) },
        { content: 'Edit shared chain', disabled: !linked, callback: () => openChainEditor(node) },
        { content: 'Member options', disabled: !linked, callback: () => openChainMemberOptions(node) },
        { content: 'Unlink', disabled: !linked, callback: () => unlinkSharedChain(node) },
    ] } });
}
