import {
    app
} from './comfy/index.js';
import {
    isVueMode,
    onVueModeChange
} from './eclipse-widget-performance-utils.js';
import {
    createCanonicalVueNodeSetting,
    hasNativeVueNodeSetting,
    loadLegacyVueNodeConfig,
    persistCanonicalVueNodeSetting,
    resolveCanonicalVueNodeSetting,
    VUE_NODE_SETTING_DEFINITIONS
} from './eclipse-vue-node-settings.js';

const NODE_COLLAPSED_WIDTH = 80;
const MAX_SYNC_FRAMES = 5;
const POST_MEASUREMENT_SYNC_FRAMES = 2;
const POST_NAVIGATION_WAIT_FRAMES = 2;
const DEFAULT_NODE_TITLE_HEIGHT = 30;
const VUE_DEFAULT_MIN_NODE_WIDTH = 225;
const DEDICATED_DISPLAY_MIN_SIZE = 225;
const DEDICATED_DISPLAY_PRIMARY_INPUT_TYPES = new Set([
    'IMAGE',
    'VIDEO',
    'AUDIO',
    'STRING',
    'SEGS',
]);
const MATCH_TYPE_INPUT = 'COMFY_MATCHTYPE_V3';
const PAINT_TIER_OFFSET = 1_000_000_000;
const VUE_NODE_SELECTOR = '.lg-node[data-node-id]';
const COMPACT_COLLAPSED_ATTRIBUTE = 'data-eclipse-compact-collapsed';
const EXPANDED_WIDTH_ATTRIBUTE = 'data-eclipse-expanded-width';
const compactSetting = VUE_NODE_SETTING_DEFINITIONS.compactCollapsedNodes;
const nodeElementCache = new WeakMap();
const pendingNodeSyncs = new WeakMap();
const expandedNodeSizes = new WeakMap();
const collapseStates = new WeakMap();
const boundingStates = new WeakMap();
const observedElementsByNode = new WeakMap();
const collapsedElementStates = new WeakMap();
const paintStates = new WeakMap();
const paintedElements = new Set();
const graphNavigationListeners = new WeakMap();
let activeGraph = null;
let navigationGeneration = 0;
let pendingGraphSyncFrame = null;
let pendingGraphSyncJob = null;
let pendingPaintFrame = null;
let nativeDisplayCapability = false;
let compactCollapsedNodesEnabled = false;
let collapsedResizeObserver = null;
let paintMutationObserver = null;
let originalCreateNode = null;

function usesEclipseDisplayFallback() {
    return !nativeDisplayCapability;
}

function getInputType(input) {
    return Array.isArray(input) ? input[0] : input?.type;
}

function getInputOptions(input) {
    return Array.isArray(input) ? input[1] : input;
}

function getPrimaryRequiredInput(nodeData) {
    const orderedNames = nodeData?.input_order?.required || [];
    const requiredInputs = nodeData?.input?.required;
    if (requiredInputs && typeof requiredInputs === 'object') {
        const inputName = orderedNames.find((name) =>
            Object.hasOwn(requiredInputs, name)
        ) || Object.keys(requiredInputs)[0];
        return inputName ? requiredInputs[inputName] : null;
    }

    const inputs = nodeData?.inputs;
    if (!inputs || typeof inputs !== 'object') return null;
    const inputName = orderedNames.find((name) =>
        inputs[name] && inputs[name].isOptional !== true
    );
    if (inputName) return inputs[inputName];
    return Object.values(inputs).find(
        (input) => input?.isOptional !== true
    ) || null;
}

function getOptionalInputs(nodeData) {
    const optionalInputs = nodeData?.input?.optional;
    if (optionalInputs && typeof optionalInputs === 'object') {
        return Object.values(optionalInputs);
    }
    return Object.values(nodeData?.inputs || {}).filter(
        (input) => input?.isOptional === true
    );
}

function isUnconstrainedMatchTypeInput(input) {
    if (getInputType(input) !== MATCH_TYPE_INPUT) return false;
    const allowedTypes = getInputOptions(input)?.template?.allowed_types;
    if (typeof allowedTypes !== 'string') return false;
    return allowedTypes.split(',').some((type) => type.trim() === '*');
}

function hasOptionalWildcardDisplayInput(nodeData) {
    return getOptionalInputs(nodeData).some((input) =>
        getInputType(input) === '*' || isUnconstrainedMatchTypeInput(input)
    );
}

function isDedicatedDisplayNode(node, nodeData) {
    if (!nodeData?.output_node || node?.subgraph) return false;
    const primaryInputType = getInputType(
        getPrimaryRequiredInput(nodeData)
    );
    return DEDICATED_DISPLAY_PRIMARY_INPUT_TYPES.has(primaryInputType) ||
        hasOptionalWildcardDisplayInput(nodeData);
}

function applyDedicatedDisplayInitialSize(node, nodeData) {
    if (!isVueMode() || !isDedicatedDisplayNode(node, nodeData)) return;
    const computedSize = node.computeSize?.();
    if (!computedSize) return;
    const width = Math.max(
        DEDICATED_DISPLAY_MIN_SIZE,
        Number(node.size?.[0]) || 0,
        Number(computedSize[0]) || 0
    );
    const height = Math.max(
        DEDICATED_DISPLAY_MIN_SIZE,
        Number(node.size?.[1]) || 0,
        Number(computedSize[1]) || 0
    );
    if (node.size?.[0] === width && node.size?.[1] === height) return;
    node.setSize?.([width, height]);
}

function initializeDedicatedDisplaySize(node, nodeData) {
    if (!isVueMode() || !isDedicatedDisplayNode(node, nodeData)) return;
    const hadOwnConfigure = Object.hasOwn(node, 'configure');
    const originalConfigure = node.configure;
    let configured = false;
    const trackedConfigure = function () {
        configured = true;
        return originalConfigure?.apply(this, arguments);
    };
    node.configure = trackedConfigure;
    applyDedicatedDisplayInitialSize(node, nodeData);

    let settlingFramesLeft = 2;
    const settleInitialSize = () => {
        if (!configured) applyDedicatedDisplayInitialSize(node, nodeData);
        if (--settlingFramesLeft > 0) {
            requestAnimationFrame(settleInitialSize);
            return;
        }
        if (node.configure !== trackedConfigure) return;
        if (hadOwnConfigure) node.configure = originalConfigure;
        else delete node.configure;
    };
    requestAnimationFrame(settleInitialSize);
}

function installDedicatedDisplayInitialSize() {
    const liteGraph = globalThis.LiteGraph;
    if (originalCreateNode || typeof liteGraph?.createNode !== 'function') return;
    originalCreateNode = liteGraph.createNode;
    liteGraph.createNode = function () {
        const node = originalCreateNode.apply(this, arguments);
        if (node) {
            initializeDedicatedDisplaySize(
                node,
                node.constructor?.nodeData
            );
        }
        return node;
    };
}

function injectStyles() {
    if (!usesEclipseDisplayFallback()) return;
    if (document.getElementById('eclipse-node-size-fix-styles')) return;
    const style = document.createElement('style');
    style.id = 'eclipse-node-size-fix-styles';
    style.textContent = [
        `[data-node-id][${EXPANDED_WIDTH_ATTRIBUTE}] {`,
        '  --min-node-width: var(--eclipse-expanded-width) !important;',
        '}',
        `[data-node-id][${COMPACT_COLLAPSED_ATTRIBUTE}] {`,
        '  min-width: 0 !important;',
        '  width: fit-content !important;',
        '  max-width: var(--eclipse-expanded-width) !important;',
        '}',
        `[data-node-id][${COMPACT_COLLAPSED_ATTRIBUTE}] > [data-testid="node-inner-wrapper"] {`,
        '  min-width: 0 !important;',
        '  width: fit-content !important;',
        '  max-width: var(--eclipse-expanded-width) !important;',
        '}',
        `[data-node-id][${COMPACT_COLLAPSED_ATTRIBUTE}] .lg-node-header {`,
        '  padding-right: 1rem !important;',
        '}',
        `[data-node-id][${COMPACT_COLLAPSED_ATTRIBUTE}] .lg-node-header > div > :not(:first-child):not([data-testid="node-pin-indicator"]) {`,
        '  display: none !important;',
        '}',
    ].join('\n');
    document.head.appendChild(style);
}

function getNodeElement(node, graph, generation) {
    const cached = nodeElementCache.get(node);
    if (cached?.el?.isConnected && cached.graph === graph &&
        cached.generation === generation) {
        return cached.el;
    }
    if (node.id == null) return null;
    const escapedId = globalThis.CSS?.escape
        ? CSS.escape(String(node.id))
        : String(node.id).replace(/["\\]/g, '\\$&');
    const element = document.querySelector(
        `.lg-node[data-node-id="${escapedId}"]`
    );
    if (element) {
        nodeElementCache.set(node, {
            el: element,
            graph,
            generation,
        });
    }
    return element;
}

function getNodeTitleHeight() {
    const titleHeight = Number(globalThis.LiteGraph?.NODE_TITLE_HEIGHT);
    return Number.isFinite(titleHeight) && titleHeight > 0
        ? titleHeight
        : DEFAULT_NODE_TITLE_HEIGHT;
}

function rememberExpandedNodeSize(node) {
    const width = Number(node.size?.[0]);
    const height = Number(node.size?.[1]);
    if (!Number.isFinite(width) || !Number.isFinite(height)) return false;
    if (node.flags?.collapsed && height <= 0) return false;
    expandedNodeSizes.set(node, [width, height]);
    return true;
}

function restoreExpandedNodeSize(node) {
    const expandedSize = expandedNodeSizes.get(node);
    if (!expandedSize) return;
    if (node.size?.[0] === expandedSize[0] &&
        node.size?.[1] === expandedSize[1]) return;
    node.size = [expandedSize[0], expandedSize[1]];
}

function applyCollapsedLogicalBounds(
    node,
    collapsedWidth,
    bounds = node.boundingRect
) {
    if (!usesEclipseDisplayFallback() || !isVueMode() ||
        !node.flags?.collapsed) return;
    if (!bounds || bounds.length < 4) return;
    const width = Number(collapsedWidth ?? node._collapsed_width);
    bounds[2] = Number.isFinite(width) && width > 0
        ? width
        : NODE_COLLAPSED_WIDTH;
    bounds[3] = getNodeTitleHeight();
}

function attachNodeBounding(node) {
    const existing = boundingStates.get(node);
    if (existing) {
        existing.active = true;
        return;
    }
    const state = {
        active: true,
        original: node.onBounding,
        wrapper: null,
    };
    state.wrapper = function (bounds) {
        state.original?.apply(this, arguments);
        if (state.active) {
            applyCollapsedLogicalBounds(this, this._collapsed_width, bounds);
        }
    };
    boundingStates.set(node, state);
    node.onBounding = state.wrapper;
}

function detachNodeBounding(node) {
    const state = boundingStates.get(node);
    if (!state) return;
    state.active = false;
    if (node.onBounding === state.wrapper) {
        node.onBounding = state.original;
        boundingStates.delete(node);
    }
}

function getObservedWidth(entry) {
    const borderBox = Array.isArray(entry.borderBoxSize)
        ? entry.borderBoxSize[0]
        : entry.borderBoxSize;
    const width = Number(
        borderBox?.inlineSize ??
        entry.contentRect?.width ??
        entry.target?.getBoundingClientRect?.().width
    );
    return Number.isFinite(width) && width > 0 ? width : null;
}

function handleCollapsedResize(entries) {
    for (const entry of entries) {
        const element = entry.target;
        const state = collapsedElementStates.get(element);
        if (!state || !element.isConnected || !isVueMode() ||
            !usesEclipseDisplayFallback() ||
            state.graph !== activeGraph ||
            state.generation !== navigationGeneration ||
            state.node.graph !== state.graph ||
            !state.node.flags?.collapsed) {
            collapsedResizeObserver?.unobserve(element);
            collapsedElementStates.delete(element);
            continue;
        }
        const width = getObservedWidth(entry);
        if (width !== null) state.node._collapsed_width = width;
        applyCollapsedLogicalBounds(state.node, width);
        restoreExpandedNodeSize(state.node);
    }
    schedulePaintOrderSync();
}

function getCollapsedResizeObserver() {
    if (!collapsedResizeObserver && typeof ResizeObserver === 'function') {
        collapsedResizeObserver = new ResizeObserver(handleCollapsedResize);
    }
    return collapsedResizeObserver;
}

function observeCollapsedElement(node, element, graph, generation) {
    const previous = observedElementsByNode.get(node);
    if (previous && previous !== element) {
        collapsedResizeObserver?.unobserve(previous);
        collapsedElementStates.delete(previous);
    }
    observedElementsByNode.set(node, element);
    collapsedElementStates.set(element, { node, graph, generation });
    getCollapsedResizeObserver()?.observe(element);
}

function unobserveCollapsedElement(node) {
    const element = observedElementsByNode.get(node);
    if (!element) return;
    collapsedResizeObserver?.unobserve(element);
    collapsedElementStates.delete(element);
    observedElementsByNode.delete(node);
}

function clearSizeOverrides(element) {
    element.removeAttribute(COMPACT_COLLAPSED_ATTRIBUTE);
    element.removeAttribute(EXPANDED_WIDTH_ATTRIBUTE);
    element.style.removeProperty('--eclipse-expanded-width');
}

function syncCompactCollapsedStyle(node, element) {
    if (!compactCollapsedNodesEnabled) {
        element.removeAttribute(COMPACT_COLLAPSED_ATTRIBUTE);
        return;
    }
    const expandedWidth = Number(
        expandedNodeSizes.get(node)?.[0] ?? node.size?.[0]
    );
    if (Number.isFinite(expandedWidth) && expandedWidth > 0) {
        element.style.setProperty(
            '--eclipse-expanded-width',
            `${expandedWidth}px`
        );
    }
    element.setAttribute(COMPACT_COLLAPSED_ATTRIBUTE, '');
}

function syncExpandedStyle(node, element) {
    const expandedWidth = Number(node.size?.[0]);
    if (Number.isFinite(expandedWidth) &&
        expandedWidth >= NODE_COLLAPSED_WIDTH &&
        expandedWidth < VUE_DEFAULT_MIN_NODE_WIDTH) {
        element.style.setProperty(
            '--eclipse-expanded-width',
            `${expandedWidth}px`
        );
        element.setAttribute(EXPANDED_WIDTH_ATTRIBUTE, '');
    } else {
        element.removeAttribute(EXPANDED_WIDTH_ATTRIBUTE);
        element.style.removeProperty('--eclipse-expanded-width');
    }
}

function syncNodeSizeToCSS(node, graph, generation) {
    if (!usesEclipseDisplayFallback() || !isVueMode()) return false;
    if (graph !== activeGraph || node.graph !== graph ||
        generation !== navigationGeneration) return false;
    const element = getNodeElement(node, graph, generation);
    if (!element) return false;
    if (node.flags?.collapsed) {
        attachNodeBounding(node);
        element.removeAttribute(EXPANDED_WIDTH_ATTRIBUTE);
        syncCompactCollapsedStyle(node, element);
        observeCollapsedElement(node, element, graph, generation);
        applyCollapsedLogicalBounds(node, node._collapsed_width);
        restoreExpandedNodeSize(node);
    } else {
        unobserveCollapsedElement(node);
        detachNodeBounding(node);
        element.removeAttribute(COMPACT_COLLAPSED_ATTRIBUTE);
        rememberExpandedNodeSize(node);
        syncExpandedStyle(node, element);
    }
    return true;
}

function patchNodeCollapse(node, refreshExpandedSize = false) {
    if (!usesEclipseDisplayFallback()) return;
    if (refreshExpandedSize || !expandedNodeSizes.has(node)) {
        rememberExpandedNodeSize(node);
    }
    if (isVueMode() && node.flags?.collapsed) attachNodeBounding(node);
    const existing = collapseStates.get(node);
    if (existing) return;
    const original = node.collapse;
    if (typeof original !== 'function') return;
    const state = { original, wrapper: null };
    state.wrapper = function () {
        const wasCollapsed = Boolean(this.flags?.collapsed);
        const active = usesEclipseDisplayFallback() && isVueMode();
        if (active && !wasCollapsed) rememberExpandedNodeSize(this);
        const result = state.original.apply(this, arguments);
        if (active && this.flags?.collapsed) {
            attachNodeBounding(this);
        } else if (active && wasCollapsed) {
            restoreExpandedNodeSize(this);
            detachNodeBounding(this);
            unobserveCollapsedElement(this);
        }
        scheduleNodeSync(this, this.graph, navigationGeneration, true);
        schedulePaintOrderSync();
        return result;
    };
    collapseStates.set(node, state);
    node.collapse = state.wrapper;
}

function finishNodeSync(node, job) {
    if (pendingNodeSyncs.get(node) === job) pendingNodeSyncs.delete(node);
}

function scheduleNodeSync(
    node,
    graph = node.graph,
    generation = navigationGeneration,
    force = false
) {
    if (!usesEclipseDisplayFallback() || !isVueMode() || !graph) return;
    if (graph !== activeGraph || node.graph !== graph) return;
    const existing = pendingNodeSyncs.get(node);
    if (existing) {
        if (!force) return;
        cancelAnimationFrame(existing.frameId);
    }
    const job = { frameId: null, graph, generation };
    pendingNodeSyncs.set(node, job);
    let framesLeft = MAX_SYNC_FRAMES;
    let postMeasurementFramesLeft = POST_MEASUREMENT_SYNC_FRAMES;
    const trySync = () => {
        if (pendingNodeSyncs.get(node) !== job) return;
        if (!isVueMode() || job.graph !== activeGraph ||
            node.graph !== job.graph ||
            job.generation !== navigationGeneration) {
            finishNodeSync(node, job);
            return;
        }
        if (syncNodeSizeToCSS(node, job.graph, job.generation)) {
            schedulePaintOrderSync();
            if (!node.flags?.collapsed ||
                postMeasurementFramesLeft-- <= 0) {
                finishNodeSync(node, job);
                return;
            }
        } else if (--framesLeft <= 0) {
            finishNodeSync(node, job);
            return;
        }
        job.frameId = requestAnimationFrame(trySync);
    };
    job.frameId = requestAnimationFrame(trySync);
}

function getSelectedNodes() {
    return app.canvas?.selected_nodes;
}

function isNodeSelected(node, element) {
    const selected = getSelectedNodes();
    if (selected instanceof Set || selected instanceof Map) {
        if (selected.has(node) || selected.has(node.id) ||
            selected.has(String(node.id))) return true;
    } else if (selected && typeof selected === 'object') {
        if (selected[node.id] === node || selected[node.id] === true ||
            Object.values(selected).includes(node)) return true;
    }
    return Boolean(
        node.is_selected ||
        element.matches?.('.outline-node-component-outline')
    );
}

function readNativePaintOrder(element, graphIndex) {
    const state = paintStates.get(element);
    const current = String(element.style.zIndex ?? '');
    if (!state || current !== state.applied) {
        const numeric = Number(current);
        return {
            raw: current,
            numeric: Number.isFinite(numeric) ? numeric : graphIndex,
        };
    }
    return {
        raw: state.native,
        numeric: state.nativeNumeric,
    };
}

function getPaintTier(node, element) {
    if (!node.flags?.collapsed) return 1;
    return isNodeSelected(node, element) ? 2 : 0;
}

function syncElementPaintOrder(node, element, graphIndex = 0) {
    const nativeOrder = readNativePaintOrder(element, graphIndex);
    const tier = getPaintTier(node, element);
    const applied = tier === 1
        ? nativeOrder.raw
        : String(
            nativeOrder.numeric +
            (tier === 0 ? -PAINT_TIER_OFFSET : PAINT_TIER_OFFSET)
        );
    paintStates.set(element, {
        native: nativeOrder.raw,
        nativeNumeric: nativeOrder.numeric,
        applied,
    });
    paintedElements.add(element);
    if (String(element.style.zIndex ?? '') !== applied) {
        element.style.zIndex = applied;
    }
}

function recomputePaintOrder(graph = activeGraph) {
    if (!usesEclipseDisplayFallback() || !isVueMode() || !graph ||
        graph !== activeGraph) return;
    const nodesById = new Map(
        Array.from(graph._nodes || []).map((node, graphIndex) => [
            String(node.id),
            { node, graphIndex },
        ])
    );
    for (const element of document.querySelectorAll(VUE_NODE_SELECTOR)) {
        const nodeId = element.getAttribute('data-node-id');
        const record = nodeId == null ? null : nodesById.get(nodeId);
        if (!record || record.node.graph !== graph) continue;
        const { node, graphIndex } = record;
        nodeElementCache.set(node, {
            el: element,
            graph,
            generation: navigationGeneration,
        });
        syncElementPaintOrder(node, element, graphIndex);
    }
}

function schedulePaintOrderSync() {
    if (!usesEclipseDisplayFallback() || !isVueMode() ||
        pendingPaintFrame !== null) return;
    pendingPaintFrame = requestAnimationFrame(() => {
        pendingPaintFrame = null;
        recomputePaintOrder();
    });
}

function findActiveGraphNode(nodeId) {
    const graph = app.canvas?.graph;
    if (!usesEclipseDisplayFallback() || !isVueMode() ||
        graph !== activeGraph || nodeId.startsWith('preview-')) return null;
    return graph?._nodes?.find(node =>
        node.graph === graph && String(node.id) === nodeId
    ) || null;
}

function reapplyMountedNodeSize(element) {
    if (!element?.isConnected) return;
    const nodeId = element.getAttribute?.('data-node-id');
    if (nodeId == null) return;
    const node = findActiveGraphNode(nodeId);
    if (!node) return;
    const cached = nodeElementCache.get(node);
    if (cached?.el !== element) {
        if (cached?.el) restoreElementPaintOrder(cached.el);
        nodeElementCache.set(node, {
            el: element,
            graph: activeGraph,
            generation: navigationGeneration,
        });
    }
    syncElementPaintOrder(node, element);
    if (pendingGraphSyncJob?.graph === activeGraph &&
        pendingGraphSyncJob.generation === navigationGeneration) {
        pendingGraphSyncJob.pending.set(node, {
            attemptsLeft: MAX_SYNC_FRAMES,
            postMeasurementFramesLeft: POST_MEASUREMENT_SYNC_FRAMES,
        });
        return;
    }
    if (pendingGraphSyncFrame !== null) return;
    scheduleNodeSync(node, activeGraph, navigationGeneration, true);
}

function handleNodeMutations(records) {
    let paintOrderChanged = false;
    for (const record of records) {
        if (record.type === 'attributes') {
            const element = record.target;
            const state = paintStates.get(element);
            if (record.attributeName === 'style' && state &&
                String(element.style?.zIndex ?? '') === state.applied) continue;
            paintOrderChanged = true;
        }
        for (const addedNode of record.addedNodes || []) {
            if (addedNode.matches?.(VUE_NODE_SELECTOR)) {
                reapplyMountedNodeSize(addedNode);
            }
            const nestedElements =
                addedNode.querySelectorAll?.(VUE_NODE_SELECTOR) || [];
            for (const element of nestedElements) {
                reapplyMountedNodeSize(element);
            }
        }
    }
    if (paintOrderChanged) schedulePaintOrderSync();
}

function restoreElementPaintOrder(element) {
    const state = paintStates.get(element);
    if (state && String(element.style.zIndex ?? '') === state.applied) {
        element.style.zIndex = state.native;
    }
    paintStates.delete(element);
    paintedElements.delete(element);
}

function restoreNativePaintOrder() {
    if (pendingPaintFrame !== null) cancelAnimationFrame(pendingPaintFrame);
    pendingPaintFrame = null;
    for (const element of paintedElements) {
        restoreElementPaintOrder(element);
    }
    paintedElements.clear();
}

function installPaintMutationObserver() {
    if (paintMutationObserver || typeof MutationObserver !== 'function') return;
    paintMutationObserver = new MutationObserver(handleNodeMutations);
    paintMutationObserver.observe(document.documentElement, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: [
            'class',
            'style',
            'data-collapsed',
            'data-node-id',
        ],
    });
}

function prepareAllNodes(
    graph = activeGraph,
    generation = navigationGeneration
) {
    if (!usesEclipseDisplayFallback() || !graph || graph !== activeGraph ||
        generation !== navigationGeneration) return;
    const nodes = Array.from(graph._nodes || []);
    if (!nodes.length) return;
    const pending = new Map();
    for (const node of nodes) {
        patchNodeCollapse(node);
        const nodeJob = pendingNodeSyncs.get(node);
        if (nodeJob) cancelAnimationFrame(nodeJob.frameId);
        pendingNodeSyncs.delete(node);
        pending.set(node, {
            attemptsLeft: MAX_SYNC_FRAMES,
            postMeasurementFramesLeft: POST_MEASUREMENT_SYNC_FRAMES,
        });
    }
    const job = { graph, generation, pending, frameId: null };
    pendingGraphSyncJob = job;
    const syncBatch = () => {
        if (pendingGraphSyncJob !== job) return;
        pendingGraphSyncFrame = null;
        if (!isVueMode() || job.graph !== activeGraph ||
            job.graph !== app.canvas?.graph ||
            job.generation !== navigationGeneration) {
            pendingGraphSyncJob = null;
            return;
        }
        for (const [node, state] of job.pending) {
            if (node.graph !== job.graph) {
                job.pending.delete(node);
                continue;
            }
            if (!syncNodeSizeToCSS(node, job.graph, job.generation)) {
                if (--state.attemptsLeft <= 0) job.pending.delete(node);
                continue;
            }
            if (!node.flags?.collapsed ||
                state.postMeasurementFramesLeft-- <= 0) {
                job.pending.delete(node);
            }
        }
        recomputePaintOrder(job.graph);
        if (!job.pending.size) {
            pendingGraphSyncJob = null;
            return;
        }
        job.frameId = requestAnimationFrame(syncBatch);
        pendingGraphSyncFrame = job.frameId;
    };
    job.frameId = requestAnimationFrame(syncBatch);
    pendingGraphSyncFrame = job.frameId;
}

function clearNodeFallback(node) {
    nodeElementCache.delete(node);
    const pending = pendingNodeSyncs.get(node);
    if (pending) cancelAnimationFrame(pending.frameId);
    pendingNodeSyncs.delete(node);
    unobserveCollapsedElement(node);
    detachNodeBounding(node);
    if (node.flags?.collapsed) restoreExpandedNodeSize(node);
    else rememberExpandedNodeSize(node);
}

function clearGraphFallback(graph) {
    for (const node of graph?._nodes || []) {
        const cached = nodeElementCache.get(node);
        if (cached?.el) {
            clearSizeOverrides(cached.el);
            restoreElementPaintOrder(cached.el);
        }
        clearNodeFallback(node);
    }
}

function deactivateDisplayFallback() {
    navigationGeneration++;
    if (pendingGraphSyncFrame !== null) {
        cancelAnimationFrame(pendingGraphSyncFrame);
    }
    pendingGraphSyncFrame = null;
    pendingGraphSyncJob = null;
    clearGraphFallback(activeGraph);
    collapsedResizeObserver?.disconnect();
    paintMutationObserver?.disconnect();
    paintMutationObserver = null;
    restoreNativePaintOrder();
}

function schedulePostNavigationSizeSync(graph, oldGraph = null) {
    if (!usesEclipseDisplayFallback() || !graph) return;
    activeGraph = graph;
    const generation = ++navigationGeneration;
    if (pendingGraphSyncFrame !== null) {
        cancelAnimationFrame(pendingGraphSyncFrame);
        pendingGraphSyncFrame = null;
    }
    pendingGraphSyncJob = null;
    if (oldGraph && oldGraph !== graph) clearGraphFallback(oldGraph);
    clearGraphFallback(graph);
    installPaintMutationObserver();
    let framesLeft = POST_NAVIGATION_WAIT_FRAMES;
    const waitForRemount = () => {
        pendingGraphSyncFrame = null;
        if (generation !== navigationGeneration || graph !== activeGraph ||
            graph !== app.canvas?.graph) return;
        if (--framesLeft > 0) {
            pendingGraphSyncFrame = requestAnimationFrame(waitForRemount);
            return;
        }
        prepareAllNodes(graph, generation);
    };
    pendingGraphSyncFrame = requestAnimationFrame(waitForRemount);
}

function installGraphNavigationListener() {
    const canvasElement = app.canvas?.canvas;
    if (!canvasElement || graphNavigationListeners.has(canvasElement)) return;
    const listener = (event) => {
        const graph = event.detail?.newGraph;
        if (graph) {
            schedulePostNavigationSizeSync(
                graph,
                event.detail?.oldGraph
            );
        }
    };
    graphNavigationListeners.set(canvasElement, listener);
    canvasElement.addEventListener('litegraph:set-graph', listener);
}

let unsubscribeModeChange = null;
app.registerExtension({
    name: 'Eclipse.nodeSizeFix',
    async init() {
        installDedicatedDisplayInitialSize();
        nativeDisplayCapability = hasNativeVueNodeSetting(
            app,
            compactSetting
        );
        const legacyConfig = await loadLegacyVueNodeConfig();
        const resolved = resolveCanonicalVueNodeSetting(
            app,
            compactSetting,
            legacyConfig
        );
        compactCollapsedNodesEnabled = resolved.value;
        if (!nativeDisplayCapability) {
            app.ui.settings.addSetting(createCanonicalVueNodeSetting(
                compactSetting,
                (value) => {
                    compactCollapsedNodesEnabled = value === true;
                    if (isVueMode()) {
                        schedulePostNavigationSizeSync(
                            app.canvas?.graph || activeGraph || app.graph
                        );
                    }
                }
            ));
        }
        await persistCanonicalVueNodeSetting(app, compactSetting, resolved);
        if (nativeDisplayCapability) return;
        injectStyles();
        activeGraph = app.canvas?.graph || app.graph || null;
        installGraphNavigationListener();
        unsubscribeModeChange?.();
        unsubscribeModeChange = onVueModeChange((vueModeEnabled) => {
            if (vueModeEnabled) {
                installGraphNavigationListener();
                schedulePostNavigationSizeSync(
                    app.canvas?.graph || activeGraph || app.graph
                );
            } else {
                deactivateDisplayFallback();
            }
        });
        if (isVueMode()) schedulePostNavigationSizeSync(activeGraph);
    },
    setup() {
        installDedicatedDisplayInitialSize();
    },
    nodeCreated(node) {
        if (nativeDisplayCapability) return;
        patchNodeCollapse(node);
        scheduleNodeSync(node);
    },
    loadedGraphNode(node) {
        if (nativeDisplayCapability) return;
        patchNodeCollapse(node, true);
        scheduleNodeSync(node);
    },
    async afterConfigureGraph() {
        if (nativeDisplayCapability) return;
        installGraphNavigationListener();
        schedulePostNavigationSizeSync(
            app.canvas?.graph || activeGraph || app.graph
        );
    },
});
