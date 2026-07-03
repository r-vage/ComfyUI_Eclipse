/**
 * Centralized seed resolution utilities for Eclipse nodes.
 *
 * Consumer: getResolvedSeedFromGraph() — traces graph links to find the
 *           resolved seed from an upstream seed-source node.
 *
 * Producer: clearQueuedSeeds()  — nulls stale _Eclipse_queued* before inner hooks
 *           storeQueuedSeed()   — stores resolved seed for downstream consumers
 */
import { app } from './comfy/index.js';

// Trace upstream graph links from `node` to find the resolved seed value.
// inputName defaults to 'seed_input'; WildcardProcessor passes 'seed'.
export function getResolvedSeedFromGraph(node, inputName = 'seed_input') {
    const seedInputIdx = node.inputs?.findIndex(
        (e) => e.name === inputName || e.widget?.name === inputName
    );
    if (seedInputIdx < 0 || null == node.inputs[seedInputIdx]?.link) return;
    let curNode = node,
        curIdx = seedInputIdx,
        depth = 10;
    while (depth-- > 0) {
        let linkInfo;
        const linkId = curNode.inputs?.[curIdx]?.link;
        if (null != linkId) linkInfo = app.graph.links[linkId];
        else if (curNode.getInputLink) linkInfo = curNode.getInputLink(curIdx);
        if (!linkInfo) return;
        const src = app.graph.getNodeById(linkInfo.origin_id);
        if (!src) return;
        // Queued seed from current execution (set by producer before cache clear)
        if (src._Eclipse_queuedSeed != null) return src._Eclipse_queuedSeed;
        // Direct seed resolution methods
        if (src.getSeedToUse) return src.getSeedToUse();
        if (src._Eclipse_seedWidget) return Number(src._Eclipse_seedWidget.value);
        // Smart Sampler Settings v2 — dual-seed via _resolveSeed
        if (src._resolveSeed) {
            if (src._Eclipse_PromptSeedWidget) return src._Eclipse_queuedPromptSeed ?? src._resolveSeed('PromptSeed');
            if (src._Eclipse_ImageSeedWidget) return src._Eclipse_queuedImageSeed ?? src._resolveSeed('ImageSeed');
        }
        // Passthrough nodes — follow single-input or pipe connections
        if (src.getInputLink) { curNode = src; curIdx = 0; continue; }
        if (src.inputs?.length === 1 && src.outputs?.length >= 1) { curNode = src; curIdx = 0; continue; }
        const pipeIdx = src.inputs?.findIndex(i => (i.name || '').toLowerCase() === 'pipe');
        if (pipeIdx >= 0) { curNode = src; curIdx = pipeIdx; continue; }
        // Fallback: look for seed/value widget on source node
        for (const w of src.widgets || []) {
            const wn = (w.name || '').toLowerCase();
            if (wn === 'seed' || wn === 'value') return Number(w.value);
        }
        return;
    }
}

// Clear stale queued seeds for matching nodes before calling inner graphToPrompt.
// filter(node) → boolean determines which nodes to clear.
export function clearQueuedSeeds(nodes, filter) {
    for (const n of nodes) {
        if (!filter(n)) continue;
        n._Eclipse_queuedSeed = null;
        // Also handle dual-seed nodes (Sampler Settings v2)
        if (n._Eclipse_queuedImageSeed !== undefined) n._Eclipse_queuedImageSeed = null;
        if (n._Eclipse_queuedPromptSeed !== undefined) n._Eclipse_queuedPromptSeed = null;
    }
}

// Store the resolved seed on a node so downstream consumers can read it.
// For dual-seed (SSv2), pass prefix ('ImageSeed' or 'PromptSeed').
export function storeQueuedSeed(node, resolved, prefix) {
    if (prefix) {
        node[`_Eclipse_queued${prefix}`] = resolved;
    } else {
        node._Eclipse_queuedSeed = resolved;
    }
}

// ─── Shared per-call node list ───────────────────────────────────────────────
// All Eclipse graphToPrompt hooks share one recursive walk per queue call.
// The first hook to call enterGraphToPromptHook() triggers the build;
// subsequent hooks reuse the cached list.
// exitGraphToPromptHook() clears it when the outermost hook finishes.
let _cachedNodeList = null;
let _hookDepth = 0;

// Call at the entry of each graphToPrompt wrapper (before clearing seeds).
export function enterGraphToPromptHook() {
    _hookDepth++;
}

// Call after the hook's post-processing is done (normal return path).
// Also call in a catch block before re-throwing to handle errors.
// Clears the shared node list when the outermost hook finishes.
export function exitGraphToPromptHook() {
    if (--_hookDepth <= 0) {
        _hookDepth = 0;
        _cachedNodeList = null;
    }
}

// Returns the flat list of all nodes (root + all nested subgraphs) for the
// current queue call. Each entry: { node, outputKey } where outputKey is
// the colon-path string (e.g. "42:7") matching promptData.output keys.
// Built once on the first call, then cached for the duration of the call.
export function getGraphNodeList(rootGraph) {
    if (_cachedNodeList) return _cachedNodeList;
    const results = [];
    function walk(graph, prefix) {
        if (!graph?._nodes) return;
        for (const node of graph._nodes) {
            const outputKey = prefix ? `${prefix}:${node.id}` : String(node.id);
            results.push({ node, outputKey });
            if (node.subgraph) walk(node.subgraph, outputKey);
        }
    }
    walk(rootGraph, '');
    _cachedNodeList = results;
    return results;
}

// Clear stale queued seed fields on a single node.
export function clearNodeQueuedSeed(node) {
    node._Eclipse_queuedSeed = null;
    if (node._Eclipse_queuedImageSeed !== undefined) node._Eclipse_queuedImageSeed = null;
    if (node._Eclipse_queuedPromptSeed !== undefined) node._Eclipse_queuedPromptSeed = null;
}

// Find a node in workflow JSON data by its colon-path outputKey (e.g. "42:7").
// Handles nested subgraphs via workflow.definitions.subgraphs.
// Mirrors rgthree's get_worflow_node() server-side logic on the client side.
export function findWorkflowNode(workflow, outputKey) {
    if (!workflow) return null;
    const parts = String(outputKey).split(':');
    let nodesList = workflow.nodes || [];
    const subgraphDefs = workflow.definitions?.subgraphs || [];
    let found = null;
    for (let i = 0; i < parts.length; i++) {
        const partId = parts[i];
        found = nodesList.find(n => String(n.id) === partId) ?? null;
        if (!found) return null;
        if (i < parts.length - 1) {
            // found is a SubgraphNode wrapper — its type is the subgraph UUID/id
            const sgDef = subgraphDefs.find(sg => String(sg.id) === String(found.type));
            if (!sgDef?.nodes) return null;
            nodesList = sgDef.nodes;
        }
    }
    return found;
}
