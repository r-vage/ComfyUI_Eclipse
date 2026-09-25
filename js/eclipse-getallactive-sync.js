/**
 * Shared priority chains for Get All Active.
 * Copyright (c) 2026 r-vage. MIT License.
 */
import { app } from './comfy/index.js';
import {
    findRootGraph, getGraphDescendants, findSetterByName, getLink,
    editMultiGetterGraph, multiGetterLifecycle, subgraphOpState,
} from './eclipse-set-get-utils.js';

const STORE = 'eclipseSyncChains';
const MEMBER = 'eclipseSyncChain';
export const chainValues = node => node.widgets.slice(2).map(w => w.value);
const same = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);
const nodesIn = root => [root, ...getGraphDescendants(root)].flatMap(g => g?._nodes || []);
export const sharedChains = node => findRootGraph(node.graph)?.extra?.[STORE]?.chains || [];
export function linkedChain(node) {
    const id = node.properties?.[MEMBER]?.id;
    return id && sharedChains(node).find(chain => chain.id === id);
}
const membersOf = (root, id) => nodesIn(root).filter(n =>
    n.type === 'GetAllActiveNode' && n.properties?.[MEMBER]?.id === id);

function validateVariables(variables) {
    if (!Array.isArray(variables) || variables.length > 20 ||
        variables.some(v => typeof v !== 'string' || !v.trim()) || new Set(variables).size !== variables.length) {
        throw Error('Use at most 20 distinct, non-empty variables.');
    }
}

// Stable topological merge: every member contributes ordering constraints. The
// first occurrence in node-ID order breaks ties without inventing constraints.
export function mergeChainLists(nodes) {
    const edges = new Map(), indegree = new Map();
    const sorted = [...nodes].sort((a, b) => String(a.id).localeCompare(String(b.id), undefined, { numeric: true }));
    for (const node of sorted) {
        const list = chainValues(node).filter(Boolean);
        if (new Set(list).size !== list.length) throw Error(`Getter ${node.id} contains duplicate variables.`);
        for (const name of list) if (!edges.has(name)) { edges.set(name, new Set()); indegree.set(name, 0); }
        for (let i = 1; i < list.length; i++) {
            if (edges.get(list[i - 1]).has(list[i])) continue;
            edges.get(list[i - 1]).add(list[i]); indegree.set(list[i], indegree.get(list[i]) + 1);
        }
    }
    const result = [];
    while (result.length < edges.size) {
        const next = [...indegree.keys()].find(name => indegree.get(name) === 0);
        if (next === undefined) throw Error('The selected getters have contradictory variable ordering.');
        result.push(next); indegree.set(next, -1);
        for (const name of edges.get(next)) indegree.set(name, indegree.get(name) - 1);
    }
    validateVariables(result);
    return result;
}

export function inferChainMember(node, chain) {
    const list = chainValues(node).filter(Boolean);
    if (list.some(v => !chain.variables.includes(v))) throw Error(`Getter ${node.id} has variables outside this chain.`);
    const start = list[0] || null;
    const member = { id: chain.id, start, exclusions: start
        ? chain.variables.slice(chain.variables.indexOf(start)).filter(v => !list.includes(v)) : [] };
    if (!same(deriveChainList(chain.variables, member), list)) throw Error(`Getter ${node.id} has a different variable order.`);
    return member;
}

export function deriveChainList(variables, member) {
    const index = variables.indexOf(member.start);
    return index < 0 ? [] : variables.slice(index).filter(v => !member.exclusions.includes(v));
}

function advanceMember(member, previous, next) {
    const result = { ...member, exclusions: member.exclusions.filter(v => next.includes(v)) };
    if (member.start && !next.includes(member.start)) {
        const range = previous.slice(previous.indexOf(member.start));
        result.start = range.find(v => next.includes(v) && !result.exclusions.includes(v)) || null;
    }
    return result;
}

const concrete = type => typeof type === 'string' && type.trim() && type !== '*' &&
    !type.includes(',') && !type.includes('COMFY_MATCHTYPE_V3');
const floating = node => node.outputs.some(o => o._floatingLinks?.size || o._floatingLinks?.length) ||
    [...(node.graph?.floatingLinks?.values?.() || [])].some(l => l.origin_id === node.id);

// Removal is deliberately decided before any positional/type/wiring preflight.
// A subsequence can always be applied by discarding only its own outputs.
export function preflightChainMember(node, next) {
    const current = chainValues(node);
    const retained = current.filter(v => next.includes(v));
    if (same(retained, next)) return { node, next, removal: true, targets: [] };
    const wired = floating(node) || node.outputs.some(o => o.links?.length);
    if (!wired) return { node, next, targets: [] };
    if (floating(node)) throw Error('An affected connection is incomplete or floating.');
    let first = 0;
    while (first < Math.min(current.length, next.length) && current[first] === next[first]) first++;
    const affected = [...current.slice(first), ...next.slice(first)];
    const types = affected.map(v => findSetterByName(node.graph, v)?.node.inputs?.[0]?.type);
    const common = types[0];
    if (!types.every(t => concrete(t) && t === common)) throw Error('Affected variables need matching concrete types for positional updates.');
    if (node.widgets[0].value !== '*' && node.widgets[0].value !== common) throw Error('The type filter does not match the variables.');
    for (let i = 0; i < node.outputs.length; i++) for (const id of node.outputs[i].links || []) {
        const link = getLink(node.graph, id);
        const input = node.graph.getNodeById(link?.target_id)?.inputs?.[link?.target_slot];
        if (!link || link.origin_id !== node.id || link.origin_slot !== i || !input || input.link !== id) {
            throw Error('An affected connection is incomplete.');
        }
        if (i >= first && i < next.length && !LiteGraph.isValidConnection(common, input.type)) {
            throw Error('A retained target input does not accept the variable type.');
        }
    }
    const targets = [];
    if (next.length > current.length) {
        // Only a complete ordered fan-out has an unambiguous extension. Never
        // reconnect an existing wire, fill a gap, or displace another source.
        for (let i = 0; i < current.length; i++) {
            if (!node.outputs[i].links?.length) throw Error('Automatic extension requires every output to be connected.');
            for (const id of node.outputs[i].links) {
                const link = getLink(node.graph, id), target = node.graph.getNodeById(link.target_id);
                if (target.type !== 'Any Multi-Switch [Eclipse]' || target.inputs[link.target_slot].name !== `any_${i + 1}`) {
                    throw Error('Automatic extension requires consecutive any_1 … any_N inputs on Any Multi-Switch [Eclipse].');
                }
                if (!targets.includes(target)) targets.push(target);
            }
        }
        for (const target of targets) {
            for (let i = 0; i < current.length; i++) {
                const input = target.inputs.find(v => v.name === `any_${i + 1}`);
                const link = input && getLink(node.graph, input.link);
                if (!link || link.origin_id !== node.id || link.origin_slot !== i) throw Error('A switch has partial or mixed-source wiring.');
            }
            for (const input of target.inputs) {
                if (/^any_\d+$/.test(input.name) && Number(input.name.slice(4)) > current.length && input.link != null) {
                    throw Error('A switch has another source after this member.');
                }
                if (/^any_\d+$/.test(input.name) && !LiteGraph.isValidConnection(common, input.type)) {
                    throw Error('A switch input does not accept the variable type.');
                }
            }
        }
    }
    return { node, next, targets };
}

function applyMemberPlan(plan, resolvedTypes) {
    const { node, next, removal, targets } = plan;
    if (removal) {
        const current = chainValues(node);
        const first = current.findIndex(v => !next.includes(v));
        // Select one strategy for the whole member. Otherwise removing two
        // rows could compact a survivor before a later removal needs gaps.
        const reason = first < 0 ? null : node.positionalEditReason(first, current.length - 1, true, resolvedTypes);
        for (let i = node.widgets.length - 3; i >= 0; i--) {
            if (!next.includes(node.widgets[i + 2].value)) node.removeVar(i, {
                automatic: true, keepConnectionsInPosition: !reason, resolvedTypes,
            });
        }
        if (reason && current.some(Boolean)) app.extensionManager.toast.add({
            severity: 'warn', summary: 'Eclipse Sync chain',
            detail: `Getter ${node.id}: variables removed; surviving connections kept their destinations. ${reason}`, life: 7000,
        });
    } else {
        const previousCount = node.outputs.length;
        node.properties.varCount = Math.max(1, next.length);
        node.widgets[1].value = String(node.properties.varCount);
        node.syncVarWidgets();
        node.widgets.slice(2).forEach((w, i) => { w.value = next[i] || ''; });
        node.updateOutputTypes();
        for (const target of targets) for (let i = previousCount; i < next.length; i++) {
            let slot = target.inputs.findIndex(input => input.name === `any_${i + 1}`);
            if (slot < 0) { target.addInput(`any_${i + 1}`, node.outputs[i].type); slot = target.inputs.length - 1; }
            node.connect(i, target, slot);
        }
    }
    node.refreshChainControls();
    node.setDirtyCanvas(true, true);
}

export function previewChainEdit(node, variables, memberOverrides = new Map()) {
    validateVariables(variables);
    const chain = linkedChain(node);
    if (!chain) throw Error('This getter is no longer linked to a chain.');
    const members = membersOf(findRootGraph(node.graph), chain.id);
    return members.map(memberNode => {
        const member = memberOverrides.get(memberNode) || advanceMember(memberNode.properties[MEMBER], chain.variables, variables);
        const next = deriveChainList(variables, member);
        try { return { ...preflightChainMember(memberNode, next), member }; }
        catch (error) { throw Error(`Getter ${memberNode.id} (${memberNode.title}): ${error.message}`); }
    });
}

export function applyChainEdit(node, variables, { name, memberOverrides, automatic = false, types } = {}) {
    const plans = previewChainEdit(node, variables, memberOverrides);
    const chain = linkedChain(node);
    const edit = () => {
        chain.variables = [...variables];
        if (name !== undefined) chain.name = name.trim() || chain.name;
        for (const plan of plans) {
            plan.node.properties[MEMBER] = plan.member;
            applyMemberPlan(plan, types?.get(plan.node));
        }
    };
    if (automatic) edit();
    else editMultiGetterGraph(node, edit, true);
    return plans;
}

export function createSharedChain(nodes, name, variables = mergeChainLists(nodes)) {
    if (!nodes.length) throw Error('Select at least one Get All Active.');
    validateVariables(variables);
    const root = findRootGraph(nodes[0].graph);
    if (nodes.some(n => n.type !== 'GetAllActiveNode' || findRootGraph(n.graph) !== root || linkedChain(n))) {
        throw Error('Select independent Get All Active nodes in this workflow.');
    }
    const chain = { id: crypto.randomUUID(), name: name.trim() || 'Shared chain', variables: [...variables] };
    const members = nodes.map(n => inferChainMember(n, chain));
    const plans = nodes.map((n, i) => preflightChainMember(n, deriveChainList(variables, members[i])));
    editMultiGetterGraph(nodes[0], () => {
        root.extra ||= {};
        root.extra[STORE] ||= { version: 1, chains: [] };
        root.extra[STORE].chains.push(chain);
        nodes.forEach((n, i) => {
            n.properties[MEMBER] = members[i];
            applyMemberPlan(plans[i]);
        });
    }, true);
    return chain;
}

export function joinSharedChain(node, id) {
    const chain = sharedChains(node).find(c => c.id === id);
    if (!chain) throw Error('The selected chain no longer exists.');
    const member = inferChainMember(node, chain);
    const plan = preflightChainMember(node, deriveChainList(chain.variables, member));
    editMultiGetterGraph(node, () => { node.properties[MEMBER] = member; applyMemberPlan(plan); }, true);
}

export function unlinkSharedChain(node) {
    editMultiGetterGraph(node, () => { delete node.properties[MEMBER]; node.refreshChainControls(); });
}

export function excludeChainVariable(node, index) {
    const member = node.properties[MEMBER];
    const name = chainValues(node)[index];
    if (!name) return;
    applyChainEdit(node, linkedChain(node).variables, { memberOverrides: new Map([[node,
        { ...member, exclusions: [...new Set([...member.exclusions, name])] },
    ]]) });
}

export function renameChainVariable(node, oldName, newName) {
    const chain = linkedChain(node);
    if (!chain) return false;
    if (app.configuringGraph || subgraphOpState.active ||
        app.extensionManager?.workflow?.activeWorkflow?.changeTracker?._restoringState) return true;
    if (!chain.variables.includes(oldName)) return true;
    // Renaming changes identity/labels, never priority or physical wiring.
    // A name collision cannot be represented twice: discard the old row safely.
    if (chain.variables.includes(newName)) {
        applyChainEdit(node, chain.variables.filter(v => v !== oldName), { automatic: true });
        return true;
    }
    chain.variables = chain.variables.map(v => v === oldName ? newName : v);
    for (const member of membersOf(findRootGraph(node.graph), chain.id)) {
        const state = member.properties[MEMBER];
        if (state.start === oldName) state.start = newName;
        state.exclusions = state.exclusions.map(v => v === oldName ? newName : v);
        member.widgets.slice(2).forEach(w => { if (w.value === oldName) w.value = newName; });
        member.updateOutputTypes(); member.refreshChainControls();
    }
    return true;
}

multiGetterLifecycle.cleanChains = (root, batch) => {
    const handled = new Set();
    let changed = false;
    for (const chain of root.extra?.[STORE]?.chains || []) {
        const members = membersOf(root, chain.id);
        if (!members.length) continue;
        members.forEach(n => handled.add(n));
        const variables = chain.variables.filter(name => !batch.names.has(name) ||
            members.some(n => findSetterByName(n.graph, name)));
        const overrides = new Map();
        for (const node of members) {
            const member = advanceMember(node.properties[MEMBER], chain.variables, variables);
            for (const name of batch.names) if (variables.includes(name) && !findSetterByName(node.graph, name)) {
                if (!member.exclusions.includes(name)) member.exclusions.push(name);
            }
            overrides.set(node, member);
        }
        if (!same(variables, chain.variables) || members.some(n => !same(chainValues(n).filter(Boolean), deriveChainList(variables, overrides.get(n))))) {
            applyChainEdit(members[0], variables, { memberOverrides: overrides, automatic: true, types: batch.types });
            changed = true;
        }
    }
    return { handled, changed };
};

multiGetterLifecycle.renameChains = (root, oldName, newName) => {
    for (const node of nodesIn(root)) {
        if (node.type === 'GetAllActiveNode' && linkedChain(node)) renameChainVariable(node, oldName, newName);
    }
};
