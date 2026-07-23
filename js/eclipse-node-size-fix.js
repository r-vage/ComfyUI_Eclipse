import {
    app
} from './comfy/index.js';
import {
    isVueMode,
    onVueModeChange
} from './eclipse-widget-performance-utils.js';

const NODE_COLLAPSED_WIDTH = 80;
const COLLAPSED_HEADER_EXTRA = 68;
const MAX_SYNC_FRAMES = 5;
const POST_MEASUREMENT_SYNC_FRAMES = 2;
const POST_NAVIGATION_WAIT_FRAMES = 2;
const DEFAULT_NODE_TITLE_HEIGHT = 30;
// Vue rewrites the node's class and inline style bindings. Dedicated data
// attributes remain stable while those bindings change during interaction.
const COLLAPSED_WIDTH_ATTRIBUTE = 'data-eclipse-collapsed-width';
const COMPACT_MIN_WIDTH_ATTRIBUTE = 'data-eclipse-compact-min-width';
// Frontend 1.41+ hardcodes --min-node-width: 225px inline on the outer
// .lg-node container. For narrow pill/utility nodes (Set, Get, compact
// nodes) this leaves empty clickable area past the rendered body. We
// override inline so it shrinks to match the node's actual width.
const VUE_DEFAULT_MIN_NODE_WIDTH = 225;
const nodeElementCache = new WeakMap();
const pendingNodeSyncs = new WeakMap();
const expandedNodeSizes = new WeakMap();
let activeGraph = null;
let navigationGeneration = 0;
let pendingGraphSyncFrame = null;

function injectStyles() {
    if (document.getElementById('eclipse-node-size-fix-styles')) return;
    const style = document.createElement('style');
    style.id = 'eclipse-node-size-fix-styles';
    style.textContent = [
        `[data-node-id][${COLLAPSED_WIDTH_ATTRIBUTE}] {`,
        '  min-width: var(--eclipse-cw) !important;',
        '  max-width: var(--eclipse-cw) !important;',
        '  width: var(--eclipse-cw) !important;',
        '}',
        // Keep collapsed nodes below every expanded node while leaving the
        // frontend's native z-order untouched for expanded nodes. Vue exposes
        // collapsed state directly on the node container.
        '[data-node-id][data-collapsed] {',
        '  z-index: -1 !important;',
        '}',
        // Alias --eclipse-min-w → --min-node-width with !important.
        // Vue's reactive template rewrites --min-node-width: 225px inline
        // on every patch cycle, so a plain inline override is clobbered.
        // Stylesheet !important beats Vue's non-important inline binding.
        `[data-node-id][${COMPACT_MIN_WIDTH_ATTRIBUTE}] {`,
        '  --min-node-width: var(--eclipse-min-w) !important;',
        '}',
    ].join('\n');
    document.head.appendChild(style);
}

function getNodeElement(node, graph, generation) {
    const cached = nodeElementCache.get(node);
    if (cached?.el?.isConnected && cached.graph === graph && cached.generation === generation) {
        return cached.el;
    }
    if (null == node.id) return null;
    const escapedId = globalThis.CSS?.escape
        ? CSS.escape(String(node.id))
        : String(node.id).replace(/["\\]/g, '\\$&');
    const el = document.querySelector(`.lg-node[data-node-id="${escapedId}"]`);
    if (el) nodeElementCache.set(node, { el, graph, generation });
    return el;
}

function getRenderedTitle(node, el) {
    const titleEl = el.querySelector('[data-testid="node-title"]');
    const renderedTitle = titleEl?.textContent?.trim();
    if (renderedTitle) return { title: renderedTitle, titleEl };
    let fallback = '';
    try {
        fallback = node.getTitle?.() || node.title || node.type || '';
    } catch (_) {
        fallback = node.title || node.type || '';
    }
    return { title: fallback, titleEl };
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
    // A collapsed Vue measurement normalizes the title-only DOM height to 0.
    // Never replace the saved expanded dimensions with that observer result.
    if (node.flags?.collapsed && height <= 0) return false;
    expandedNodeSizes.set(node, [width, height]);
    return true;
}

function restoreExpandedNodeSize(node) {
    const expandedSize = expandedNodeSizes.get(node);
    if (!expandedSize) return;
    if (node.size?.[0] === expandedSize[0] && node.size?.[1] === expandedSize[1]) return;
    node.size = [expandedSize[0], expandedSize[1]];
}

function applyCollapsedLogicalBounds(node, collapsedWidth, bounds = node.boundingRect) {
    if (!eclipseSizeFixEnabled || !isVueMode() || !node.flags?.collapsed) return;
    if (!bounds || bounds.length < 4) return;
    const width = Number(collapsedWidth ?? node._collapsed_width);
    bounds[2] = Number.isFinite(width) && width > 0 ? width : NODE_COLLAPSED_WIDTH;
    bounds[3] = getNodeTitleHeight();
}

function patchNodeBounding(node) {
    if (node._eclipseBoundingWrapper) return;
    const origBounding = node.onBounding;
    const wrapper = function (bounds) {
        origBounding?.apply(this, arguments);
        applyCollapsedLogicalBounds(this, this._collapsed_width, bounds);
    };
    node._eclipseBoundingWrapper = wrapper;
    node.onBounding = wrapper;
}

function syncNodeSizeToCSS(node, graph, generation) {
    if (!isVueMode()) return false;
    if (graph !== activeGraph || node.graph !== graph || generation !== navigationGeneration) return false;
    const el = getNodeElement(node, graph, generation);
    if (!el) return false;
    if (node.flags?.collapsed) {
        const collapsedW = computeCollapsedWidth(node, el);
        node._collapsed_width = collapsedW;
        el.style.setProperty('--eclipse-cw', `${collapsedW}px`);
        // Use --eclipse-min-w sidecar var (aliased via !important CSS rule)
        // because Vue reactively rewrites --min-node-width on every patch.
        el.style.setProperty('--eclipse-min-w', `${collapsedW}px`);
        el.setAttribute(COLLAPSED_WIDTH_ATTRIBUTE, '');
        el.setAttribute(COMPACT_MIN_WIDTH_ATTRIBUTE, '');
        applyCollapsedLogicalBounds(node, collapsedW);
        // ResizeObserver writes the title-only DOM dimensions into node.size.
        // Put the expanded dimensions back after that native measurement so
        // expansion restores the node instead of adopting its collapsed box.
        restoreExpandedNodeSize(node);
    } else {
        rememberExpandedNodeSize(node);
        el.removeAttribute(COLLAPSED_WIDTH_ATTRIBUTE);
        el.style.removeProperty('--eclipse-cw');
        // Shrink min-node-width via sidecar var + !important CSS alias
        // so Vue's reactive inline rewrites of --min-node-width can't
        // clobber it. Only applies when node is narrower than default.
        const expandedWidth = Number(node.size?.[0]);
        if (Number.isFinite(expandedWidth) &&
            expandedWidth >= NODE_COLLAPSED_WIDTH &&
            expandedWidth < VUE_DEFAULT_MIN_NODE_WIDTH) {
            el.style.setProperty('--eclipse-min-w', `${expandedWidth}px`);
            el.setAttribute(COMPACT_MIN_WIDTH_ATTRIBUTE, '');
        } else {
            el.removeAttribute(COMPACT_MIN_WIDTH_ATTRIBUTE);
            el.style.removeProperty('--eclipse-min-w');
        }
    }
    return true;
}

function computeCollapsedWidth(node, el) {
    const { title, titleEl } = getRenderedTitle(node, el);
    const nodeWidth = Number(expandedNodeSizes.get(node)?.[0] ?? node.size?.[0]);
    const expandedWidth = Number.isFinite(nodeWidth) && nodeWidth >= NODE_COLLAPSED_WIDTH
        ? nodeWidth
        : 200;
    try {
        if (!computeCollapsedWidth._canvas) {
            computeCollapsedWidth._canvas = document.createElement('canvas');
        }
        const ctx = computeCollapsedWidth._canvas.getContext('2d');
        if (ctx) {
            const renderedFont = titleEl ? getComputedStyle(titleEl).font : '';
            ctx.font = renderedFont || '600 14px Inter, Arial, sans-serif';
            const measuredWidth = ctx.measureText(title).width + COLLAPSED_HEADER_EXTRA;
            return Math.min(expandedWidth, Math.max(measuredWidth, NODE_COLLAPSED_WIDTH));
        }
    } catch (_) {}
    return node._collapsed_width ? node._collapsed_width : NODE_COLLAPSED_WIDTH;
}

function patchNodeCollapse(node, refreshExpandedSize = false) {
    if (refreshExpandedSize || !expandedNodeSizes.has(node)) rememberExpandedNodeSize(node);
    patchNodeBounding(node);
    const origCollapse = node.collapse;
    if (typeof origCollapse !== 'function' || origCollapse === node._eclipseCollapseWrapper) return;
    const wrapper = function () {
        const wasCollapsed = !!this.flags?.collapsed;
        if (!wasCollapsed) rememberExpandedNodeSize(this);
        const result = origCollapse.apply(this, arguments);
        if (wasCollapsed && !this.flags?.collapsed) restoreExpandedNodeSize(this);
        scheduleNodeSync(this);
        return result;
    };
    node._eclipseCollapseWrapper = wrapper;
    node.collapse = wrapper;
}

function finishNodeSync(node, job) {
    if (pendingNodeSyncs.get(node) === job) pendingNodeSyncs.delete(node);
}

function scheduleNodeSync(node, graph = node.graph, generation = navigationGeneration, force = false) {
    if (!eclipseSizeFixEnabled || !isVueMode() || !graph) return;
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
        if (!eclipseSizeFixEnabled || !isVueMode() ||
            job.graph !== activeGraph || node.graph !== job.graph ||
            job.generation !== navigationGeneration) {
            finishNodeSync(node, job);
            return;
        }
        if (syncNodeSizeToCSS(node, job.graph, job.generation)) {
            if (!node.flags?.collapsed) {
                finishNodeSync(node, job);
                return;
            }
            // CSS changes are observed after layout and can write collapsed DOM
            // dimensions back into node.size. A couple of follow-up frames put
            // the expanded dimensions and logical bounds back after that pass.
            if (postMeasurementFramesLeft-- <= 0) {
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

function prepareAllNodes(graph = activeGraph, generation = navigationGeneration, force = false) {
    if (!graph || graph !== activeGraph || generation !== navigationGeneration) return;
    const nodes = graph._nodes;
    if (!nodes?.length) return;
    for (let idx = 0; idx < nodes.length; idx++) {
        const node = nodes[idx];
        patchNodeCollapse(node);
        scheduleNodeSync(node, graph, generation, force);
    }
}

function invalidateGraphNodeSyncs(graph) {
    for (const node of graph?._nodes || []) {
        nodeElementCache.delete(node);
        const pending = pendingNodeSyncs.get(node);
        if (pending) cancelAnimationFrame(pending.frameId);
        pendingNodeSyncs.delete(node);
    }
}

function schedulePostNavigationSizeSync(graph, oldGraph = null) {
    if (!eclipseSizeFixEnabled || !graph) return;
    activeGraph = graph;
    const generation = ++navigationGeneration;
    if (pendingGraphSyncFrame !== null) {
        cancelAnimationFrame(pendingGraphSyncFrame);
        pendingGraphSyncFrame = null;
    }
    if (oldGraph && oldGraph !== graph) invalidateGraphNodeSyncs(oldGraph);
    invalidateGraphNodeSyncs(graph);
    let framesLeft = POST_NAVIGATION_WAIT_FRAMES;
    const waitForRemount = () => {
        pendingGraphSyncFrame = null;
        if (generation !== navigationGeneration || graph !== activeGraph ||
            graph !== app.canvas?.graph) return;
        if (--framesLeft > 0) {
            pendingGraphSyncFrame = requestAnimationFrame(waitForRemount);
            return;
        }
        pendingGraphSyncFrame = null;
        prepareAllNodes(graph, generation, true);
    };
    pendingGraphSyncFrame = requestAnimationFrame(waitForRemount);
}

function installGraphNavigationListener() {
    const canvasEl = app.canvas?.canvas;
    if (!canvasEl || canvasEl._eclipseSizeFixGraphListener) return;
    const listener = (event) => {
        const graph = event.detail?.newGraph;
        if (graph) schedulePostNavigationSizeSync(graph, event.detail?.oldGraph);
    };
    canvasEl._eclipseSizeFixGraphListener = listener;
    canvasEl.addEventListener('litegraph:set-graph', listener);
}

let eclipseSizeFixEnabled = true;
let unsubscribeModeChange = null;
app.registerExtension({
    name: 'Eclipse.nodeSizeFix',
    async init() {
        try {
            const resp = await fetch('/eclipse/config/all');
            if (resp.ok) {
                const config = await resp.json();
                if (config.vue_size_fix === false) {
                    eclipseSizeFixEnabled = false;
                    return;
                }
            }
        } catch (_) {}
        injectStyles();
        activeGraph = app.canvas?.graph || app.graph || null;
        installGraphNavigationListener();
        unsubscribeModeChange?.();
        unsubscribeModeChange = onVueModeChange((vueModeEnabled) => {
            if (vueModeEnabled) {
                schedulePostNavigationSizeSync(app.canvas?.graph || activeGraph || app.graph);
            }
        });
        if (isVueMode()) schedulePostNavigationSizeSync(activeGraph);
    },
    nodeCreated(node) {
        if (!eclipseSizeFixEnabled) return;
        patchNodeCollapse(node);
        scheduleNodeSync(node);
    },
    loadedGraphNode(node) {
        if (!eclipseSizeFixEnabled) return;
        // nodeCreated can run before configure restores the serialized size.
        // Refresh here so already-collapsed workflows retain that final size.
        patchNodeCollapse(node, true);
        scheduleNodeSync(node);
    },
    async afterConfigureGraph() {
        if (!eclipseSizeFixEnabled) return;
        schedulePostNavigationSizeSync(app.canvas?.graph || activeGraph || app.graph);
    },
});
