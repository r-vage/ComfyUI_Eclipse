import { app } from './comfy/index.js';

export const SETTER_TYPES = new Set(['SetNode', 'SetNode [Eclipse]']);

// Global flag: true while convertToSubgraph or unpackSubgraph is executing.
// Used by Bridge and Set/Get nodes to skip paste-rename logic during subgraph operations.
// Wrapped in an object so live mutations are visible to all importers.
export const subgraphOpState = { active: false };

// Shared ref so eclipse-getallactive.js and eclipse-getfirst.js can trigger
// the central paste-rename scan without a circular import back to eclipse-set-get.js.
// eclipse-set-get.js assigns pasteRenameScheduler.schedule = schedulePasteRenamePass
// during its module initialisation.
export const pasteRenameScheduler = { schedule: null };

// Patch LGraph.prototype.convertToSubgraph and unpackSubgraph to set the flag.
// Called once from setup().
let _subgraphOpPatched = false;
export function patchSubgraphOps() {
    if (_subgraphOpPatched) return;
    _subgraphOpPatched = true;
    // LGraph class is accessible via any graph instance's constructor
    const graphProto = app?.graph?.constructor?.prototype;
    if (!graphProto) return;
    for (const method of ['convertToSubgraph', 'unpackSubgraph']) {
        const orig = graphProto[method];
        if (typeof orig !== 'function') continue;
        graphProto[method] = function (...args) {
            subgraphOpState.active = true;
            try { return orig.apply(this, args); }
            finally { subgraphOpState.active = false; }
        };
    }
}

export function getLink(graph, linkId) {
    if (linkId == null) return null;
    if (graph.getLink) return graph.getLink(linkId);
    const links = graph.links ?? graph._links;
    if (links instanceof Map) return links.get(linkId);
    return links?.[linkId] ?? null;
}
export function findRootGraph(graph) {
    if (!graph) return null;
    return graph.rootGraph || graph;
}
export function findSubgraphNodeFor(parentGraph, innerNode) {
    if (!parentGraph?._nodes || !innerNode?.graph) return null;
    for (const n of parentGraph._nodes) {
        if (n.subgraph && n.subgraph === innerNode.graph) return n;
    }
    return null;
}
export function getGraphAncestors(graph) {
    if (!graph) return [];
    const root = findRootGraph(graph);
    if (!root || graph === root) return [root];
    const chain = [graph];
    const visited = new Set([graph]);
    let current = graph;
    while (current !== root) {
        let found = false;
        for (const n of root._nodes) {
            if (n.subgraph === current) {
                chain.push(root);
                current = root;
                found = true;
                break;
            }
        }
        if (found) break;
        const subgraphs = root._subgraphs || root.subgraphs;
        if (subgraphs) {
            for (const sg of subgraphs.values()) {
                if (sg === current || !sg._nodes) continue;
                for (const n of sg._nodes) {
                    if (n.subgraph === current) {
                        if (visited.has(sg)) {
                            found = false;
                            break;
                        }
                        visited.add(sg);
                        chain.push(sg);
                        current = sg;
                        found = true;
                        break;
                    }
                }
                if (found) break;
            }
        }
        if (!found) {
            if (!chain.includes(root)) chain.push(root);
            break;
        }
    }
    return chain;
}
export function getGraphDescendants(graph, _visited) {
    if (!graph?._nodes) return [];
    const visited = _visited || new Set();
    if (visited.has(graph)) return [];
    visited.add(graph);
    const descendants = [];
    for (const n of graph._nodes) {
        if (n.subgraph && !visited.has(n.subgraph)) {
            descendants.push(n.subgraph);
            descendants.push(...getGraphDescendants(n.subgraph, visited));
        }
    }
    return descendants;
}
// Verify that setterGraph is a REAL descendant of ancestorGraph (reachable via actual
// SubgraphNode instances) AND that every SubgraphNode along the path is active
// (neither muted — mode 2 — nor bypassed — mode 4).
//
// Why both modes matter: ComfyUI's graphToPrompt only populates inner-node DTOs when
// the top-level root SubgraphNode is NOT muted/bypassed (see line:
//   `if (!(t.mode === NEVER || t.mode === BYPASS)) for (t of e.getInnerNodes()) ...`).
// If any wrapper along the chain is muted/bypassed, returning a source node inside it
// produces a "No DTO found for virtual source node" crash when the frontend looks up
// its DTO by identity in `nodesByExecutionId`.
//
// Why path verification matters: getGraphAncestors() falls back to pushing root even
// when no real SubgraphNode instantiates setterGraph — returning a chain for orphan
// subgraph definitions. This function instead walks via actual `n.subgraph === current`
// wrapper lookups, returning false if any link is missing.
export function isDescendantPathActive(setterGraph, ancestorGraph) {
    if (!setterGraph || !ancestorGraph) return false;
    if (setterGraph === ancestorGraph) return true;
    const searchPool = [ancestorGraph, ...getGraphDescendants(ancestorGraph)];
    const visited = new Set([setterGraph]);
    let current = setterGraph;
    for (let hops = 0; hops < 32; hops++) {
        if (current === ancestorGraph) return true;
        let wrapperNode = null;
        for (const g of searchPool) {
            if (!g?._nodes || g === current) continue;
            const found = g._nodes.find(n => n.subgraph === current);
            if (found) { wrapperNode = found; break; }
        }
        if (!wrapperNode) return false;
        if (wrapperNode.mode === 2 || wrapperNode.mode === 4) return false;
        const next = wrapperNode.graph;
        if (!next || visited.has(next)) return false;
        visited.add(next);
        current = next;
    }
    return false;
}
// Verify that the frontend's graphToPrompt will create a DTO for nodes inside setterGraph.
// This is true iff every SubgraphNode from setterGraph up to root is active (not mode 2/4)
// AND every link in the chain is a real instantiated SubgraphNode (not an orphan definition).
//
// This works for ANY relationship — same-graph, ancestor, descendant, or sibling — because
// DTO creation depends only on whether the setter's own execution path is active, not on
// its relationship to the getter's graph.
export function isSetterPathToRootActive(setterGraph) {
    if (!setterGraph) return false;
    const root = findRootGraph(setterGraph);
    if (!root) return false;
    if (setterGraph === root) return true;
    const searchPool = [root, ...getGraphDescendants(root)];
    const visited = new Set([setterGraph]);
    let current = setterGraph;
    for (let hops = 0; hops < 32; hops++) {
        if (current === root) return true;
        let wrapperNode = null;
        for (const g of searchPool) {
            if (!g?._nodes || g === current) continue;
            const found = g._nodes.find(n => n.subgraph === current);
            if (found) { wrapperNode = found; break; }
        }
        if (!wrapperNode) return false;
        if (wrapperNode.mode === 2 || wrapperNode.mode === 4) return false;
        const next = wrapperNode.graph;
        if (!next || visited.has(next)) return false;
        visited.add(next);
        current = next;
    }
    return false;
}

function collectNodesOfType(graphs, type) {
    const results = [];
    for (const g of graphs) {
        if (!g?._nodes) continue;
        for (const node of g._nodes) {
            if (node.type === type) results.push({
                node,
                graph: g
            });
        }
    }
    return results;
}

function collectSetterNodes(graphs) {
    const results = [];
    for (const g of graphs) {
        if (!g?._nodes) continue;
        for (const node of g._nodes) {
            if (SETTER_TYPES.has(node.type)) results.push({
                node,
                graph: g
            });
        }
    }
    return results;
}
export function findSetterByName(graph, name) {
    if (!name) return null;
    const root = findRootGraph(graph);
    
    // Search in priority order:
    //   1. local graph
    //   2. ancestors (parent subgraphs up to root)
    //   3. descendants of the current graph (own nested subgraphs)
    //   4. remaining root descendants — siblings and their subtrees (last resort)
    //
    // Priority 1-3 ensures that when a subgraph is duplicated, GetNode/GetAllActive/GetFirst
    // find setters from their own subgraph first, avoiding cross-subgraph mismatches.
    // Priority 4 enables sibling subgraph resolution: a Get in SG-B can reach a Set in
    // sibling SG-A as long as the combo's getVisibleSetNames also shows it, which it does
    // (both functions now have matching scope).
    const ancestors = new Set(getGraphAncestors(graph));
    const descendants = getGraphDescendants(graph);
    
    const searchOrder = [graph];
    for (const ancestor of ancestors) {
        if (ancestor !== graph) searchOrder.push(ancestor);
    }
    for (const descendant of descendants) {
        searchOrder.push(descendant);
    }
    // 4th tier — siblings: all root-reachable graphs not already in the order above
    if (root) {
        const alreadyQueued = new Set(searchOrder);
        for (const g of [root, ...getGraphDescendants(root)]) {
            if (!alreadyQueued.has(g)) searchOrder.push(g);
        }
    }
    
    const visited = new Set();
    for (const g of searchOrder) {
        if (!g || visited.has(g)) continue;
        visited.add(g);
        if (!g._nodes) continue;
        for (const node of g._nodes) {
            if (SETTER_TYPES.has(node.type) && node.widgets?.[0]?.value === name) {
                return {
                    node,
                    graph: g
                };
            }
        }
    }
    return null;
}
export function findGettersByName(graph, name, getterType) {
    if (!name) return [];
    const graphs = [graph, ...getGraphDescendants(graph)];
    return collectNodesOfType(graphs, getterType).filter(entry => entry.node.widgets?.[0]?.value === name);
}
let _setNameSourceMap = new Map();
export function getSetNameSourceMap() {
    return _setNameSourceMap;
}
export function getVisibleSetNames(graph, filterType) {
    const sourceMap = new Map();
    const root = findRootGraph(graph);
    const allGraphs = [root, ...getGraphDescendants(root)];
    const entries = collectSetterNodes(allGraphs);
    const ancestors = new Set(getGraphAncestors(graph));
    for (const e of entries) {
        const name = e.node.widgets?.[0]?.value;
        if (!name) continue;
        if (filterType && filterType !== '*') {
            const setType = e.node.inputs?.[0]?.type;
            if (setType && setType !== '*') {
                const filterTypes = String(filterType).split(',');
                if (!filterTypes.some(ft => ft === setType || setType.split(',').includes(ft))) continue;
            }
        }
        if (!sourceMap.has(name)) {
            const source = e.graph === graph ? 'local' : (ancestors.has(e.graph) ? 'parent' : 'child');
            sourceMap.set(name, source);
        }
    }
    _setNameSourceMap = sourceMap;
    return [...sourceMap.keys()].sort();
}
const MAX_BYPASS_DEPTH = 4;
export function isSetterActive(graph, setter) {
    if (!setter) return false;
    if (setter.mode === 2 || setter.mode === 4) return false;
    if (!setter.inputs?.[0]?.link) return false;
    const g = setter.graph || graph;
    let link = getLink(g, setter.inputs[0].link);
    if (!link) return false;
    let originNode = g.getNodeById?.(link.origin_id);
    let depth = 0;
    while (originNode && originNode.mode === 4 && depth < MAX_BYPASS_DEPTH) {
        depth++;
        const outType = originNode.outputs?.[link.origin_slot]?.type;
        let matchedInput = null;
        if (originNode.inputs) {
            for (const inp of originNode.inputs) {
                if (inp.link != null && (inp.type === outType || inp.type === '*' || outType === '*')) {
                    matchedInput = inp;
                    break;
                }
            }
        }
        if (!matchedInput || matchedInput.link == null) return false;
        link = getLink(g, matchedInput.link);
        if (!link) return false;
        originNode = g.getNodeById?.(link.origin_id);
    }
    return originNode != null;
}
export function resolveBypassedLink(graph, setter) {
    if (!setter?.inputs?.[0]?.link) return null;
    const g = setter.graph || graph;
    let link = getLink(g, setter.inputs[0].link);
    if (!link) return null;
    let originNode = g.getNodeById?.(link.origin_id);
    let depth = 0;
    while (originNode && originNode.mode === 4 && depth < MAX_BYPASS_DEPTH) {
        depth++;
        const outType = originNode.outputs?.[link.origin_slot]?.type;
        let matchedInput = null;
        if (originNode.inputs) {
            for (const inp of originNode.inputs) {
                if (inp.link != null && (inp.type === outType || inp.type === '*' || outType === '*')) {
                    matchedInput = inp;
                    break;
                }
            }
        }
        if (!matchedInput || matchedInput.link == null) break;
        link = getLink(g, matchedInput.link);
        if (!link) break;
        originNode = g.getNodeById?.(link.origin_id);
    }
    return link;
}

// Shared paste rename map for coordinating variable name changes during subgraph duplication.
// Pattern: oldName → newName. Cleared ONCE after graph finishes loading (not per-entry).
// Used by SetNode, GetNode, GetAllActiveNode, GetFirstNode to update references during paste operations.
// Entries persist until the map is explicitly cleared to allow all nodes to read it regardless of
// node ordering. This avoids setTimeout race conditions where early-firing setTimeouts clear
// entries before late-firing nodes read them.
export const _pasteRenameMap = new Map();

export function clearPasteRenameMap() {
    _pasteRenameMap.clear();
}

