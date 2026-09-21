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
const setGetIndexes = new WeakMap();
let setGetGraphOpsPatched = false;

export function invalidateSetGetIndex(graph) {
    const root = findRootGraph(graph);
    if (root) setGetIndexes.delete(root);
}

export function patchSetGetIndexInvalidation() {
    if (setGetGraphOpsPatched) return;
    const graphProto = app?.graph?.constructor?.prototype;
    if (!graphProto) return;
    setGetGraphOpsPatched = true;
    for (const method of ['add', 'remove']) {
        const original = graphProto[method];
        if (typeof original !== 'function') continue;
        graphProto[method] = function () {
            const previousRoot = findRootGraph(this);
            const result = original.apply(this, arguments);
            invalidateSetGetIndex(previousRoot);
            invalidateSetGetIndex(this);
            return result;
        };
    }
}

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
            const previousRoot = findRootGraph(this);
            subgraphOpState.active = true;
            try { return orig.apply(this, args); }
            finally {
                subgraphOpState.active = false;
                invalidateSetGetIndex(previousRoot);
                invalidateSetGetIndex(this);
            }
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

function buildSetGetIndex(root) {
    const graphs = [];
    const parentByGraph = new Map();
    const childrenByGraph = new Map();
    const setters = [];
    const settersByName = new Map();
    const gettersByTypeAndName = new Map();
    const visited = new Set();

    const visit = (graph, parent = null) => {
        if (!graph || visited.has(graph)) return;
        visited.add(graph);
        graphs.push(graph);
        if (parent) parentByGraph.set(graph, parent);
        const children = [];
        childrenByGraph.set(graph, children);
        for (const node of graph._nodes || []) {
            const entry = { node, graph };
            const name = node.widgets?.[0]?.value;
            if (SETTER_TYPES.has(node.type)) {
                setters.push(entry);
                if (name) {
                    const named = settersByName.get(name) || [];
                    named.push(entry);
                    settersByName.set(name, named);
                }
            } else if (name) {
                let byName = gettersByTypeAndName.get(node.type);
                if (!byName) {
                    byName = new Map();
                    gettersByTypeAndName.set(node.type, byName);
                }
                const named = byName.get(name) || [];
                named.push(entry);
                byName.set(name, named);
            }
            if (node.subgraph && !visited.has(node.subgraph)) {
                children.push(node.subgraph);
                visit(node.subgraph, graph);
            }
        }
    };
    visit(root);
    return {
        childrenByGraph,
        gettersByTypeAndName,
        graphs,
        parentByGraph,
        setters,
        settersByName,
        setterResolutionByGraph: new WeakMap(),
        visibleNamesByGraph: new WeakMap(),
    };
}

function getSetGetIndex(graph) {
    const root = findRootGraph(graph);
    if (!root) return null;
    let index = setGetIndexes.get(root);
    if (!index) {
        index = buildSetGetIndex(root);
        setGetIndexes.set(root, index);
    }
    return index;
}

function getIndexedDescendants(index, graph) {
    const descendants = [];
    const visit = (current) => {
        for (const child of index.childrenByGraph.get(current) || []) {
            descendants.push(child);
            visit(child);
        }
    };
    visit(graph);
    return descendants;
}

function getIndexedAncestors(index, graph) {
    const ancestors = [graph];
    const visited = new Set(ancestors);
    let current = graph;
    while (index.parentByGraph.has(current)) {
        current = index.parentByGraph.get(current);
        if (visited.has(current)) break;
        visited.add(current);
        ancestors.push(current);
    }
    return ancestors;
}

function getSetterSearchOrder(index, graph) {
    const searchOrder = [];
    const queued = new Set();
    const append = (candidate) => {
        if (candidate && !queued.has(candidate)) {
            queued.add(candidate);
            searchOrder.push(candidate);
        }
    };
    for (const ancestor of getIndexedAncestors(index, graph)) append(ancestor);
    for (const descendant of getIndexedDescendants(index, graph)) append(descendant);
    for (const candidate of index.graphs) append(candidate);
    return searchOrder;
}

export function findSetterByName(graph, name) {
    if (!name) return null;
    const index = getSetGetIndex(graph);
    if (!index) return null;
    if (!index.childrenByGraph.has(graph)) {
        const local = graph?._nodes?.find(node =>
            SETTER_TYPES.has(node.type) && node.widgets?.[0]?.value === name
        );
        if (local) return { node: local, graph };
    }
    let resolvedByName = index.setterResolutionByGraph.get(graph);
    if (!resolvedByName) {
        resolvedByName = new Map();
        index.setterResolutionByGraph.set(graph, resolvedByName);
    }
    if (resolvedByName.has(name)) return resolvedByName.get(name);
    const entries = index.settersByName.get(name) || [];
    if (!entries.length) {
        resolvedByName.set(name, null);
        return null;
    }
    for (const candidate of getSetterSearchOrder(index, graph)) {
        const entry = entries.find(item => item.graph === candidate);
        if (entry) {
            resolvedByName.set(name, entry);
            return entry;
        }
    }
    resolvedByName.set(name, null);
    return null;
}
export function findGettersByName(graph, name, getterType) {
    if (!name) return [];
    const index = getSetGetIndex(graph);
    if (!index) return [];
    if (!index.childrenByGraph.has(graph)) {
        return [graph, ...getGraphDescendants(graph)].flatMap(current =>
            (current?._nodes || [])
                .filter(node => node.type === getterType && node.widgets?.[0]?.value === name)
                .map(node => ({ node, graph: current }))
        );
    }
    const graphs = new Set([graph, ...getIndexedDescendants(index, graph)]);
    return (index.gettersByTypeAndName.get(getterType)?.get(name) || [])
        .filter(entry => graphs.has(entry.graph));
}
let _setNameSourceMap = new Map();
export function getSetNameSourceMap() {
    return _setNameSourceMap;
}
export function getVisibleSetNames(graph, filterType) {
    const index = getSetGetIndex(graph);
    if (!index) return [];
    let byFilter = index.visibleNamesByGraph.get(graph);
    if (!byFilter) {
        byFilter = new Map();
        index.visibleNamesByGraph.set(graph, byFilter);
    }
    const filterKey = filterType || '';
    const cached = byFilter.get(filterKey);
    if (cached) {
        _setNameSourceMap = cached.sourceMap;
        return [...cached.names];
    }
    const sourceMap = new Map();
    const ancestors = new Set(
        index.childrenByGraph.has(graph)
            ? getIndexedAncestors(index, graph)
            : getGraphAncestors(graph)
    );
    for (const e of index.setters) {
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
    const names = [...sourceMap.keys()].sort();
    byFilter.set(filterKey, { names, sourceMap });
    return [...names];
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
    // A muted source (including the source reached through bypassed nodes)
    // cannot supply an execution value, even while its Set remains active.
    return originNode != null && originNode.mode !== 2;
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
