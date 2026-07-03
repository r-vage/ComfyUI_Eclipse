import {
    app
} from './comfy/index.js';
import { isVueMode } from './eclipse-widget-performance-utils.js';
const NODE_COLLAPSED_WIDTH = 80;
const COLLAPSED_HEADER_EXTRA = 68;
// Frontend 1.41+ hardcodes --min-node-width: 225px inline on the outer
// .lg-node container. For narrow pill/utility nodes (Set, Get, compact
// nodes) this leaves empty clickable area past the rendered body. We
// override inline so it shrinks to match the node\'s actual width.
const VUE_DEFAULT_MIN_NODE_WIDTH = 225;

function injectStyles() {
    if (document.getElementById('eclipse-node-size-fix-styles')) return;
    const style = document.createElement('style');
    style.id = 'eclipse-node-size-fix-styles';
    style.textContent = [
        '[data-node-id][style*="--eclipse-cw"] {',
        '  min-width: var(--eclipse-cw) !important;',
        '  max-width: var(--eclipse-cw) !important;',
        '  width: var(--eclipse-cw) !important;',
        '}',
        '[data-node-id][style*="--eclipse-z"] {',
        '  z-index: var(--eclipse-z) !important;',
        '}',
        // Alias --eclipse-min-w → --min-node-width with !important.
        // Vue\'s reactive template rewrites --min-node-width: 225px inline
        // on every patch cycle, so a plain inline override is clobbered.
        // Stylesheet !important beats Vue\'s non-important inline binding.
        '[data-node-id][style*="--eclipse-min-w"] {',
        '  --min-node-width: var(--eclipse-min-w) !important;',
        '}',
    ].join('\n');
    document.head.appendChild(style);
}

function getNodeElement(node) {
    if (node._eclipseCachedEl?.isConnected) return node._eclipseCachedEl;
    if (null == node.id) return null;
    const el = document.querySelector(`[data-node-id="${node.id}"]`);
    if (el) node._eclipseCachedEl = el;
    return el;
}

function syncNodeSizeToCSS(node, includeExpanded = false) {
    const el = getNodeElement(node);
    if (!el) return;
    if (node.flags?.collapsed) {
        const collapsedW = computeCollapsedWidth(node);
        el.style.setProperty('--eclipse-cw', `${collapsedW}px`);
        el.style.setProperty('--eclipse-z', '-1');
        // Use --eclipse-min-w sidecar var (aliased via !important CSS rule)
        // because Vue reactively rewrites --min-node-width on every patch.
        el.style.setProperty('--eclipse-min-w', `${collapsedW}px`);
    } else {
        // Fallback: write --node-width/--node-height only if the frontend
        // hasn\'t set them yet. Frontend 1.37+ sets these natively via
        // initSizeStyles(); writing unconditionally races Vue\'s reactive
        // updates and caused right-edge drift in 1.42.11.
        if (includeExpanded && node.size && node.size[0] >= 10 && node.size[1] >= 10) {
            if (!el.style.getPropertyValue('--node-width')) {
                el.style.setProperty('--node-width', `${node.size[0]}px`);
            }
            if (!el.style.getPropertyValue('--node-height')) {
                const totalHeight = node.size[1] + 30;
                el.style.setProperty('--node-height', `${totalHeight}px`);
            }
        }
        // Shrink min-node-width via sidecar var + !important CSS alias
        // so Vue\'s reactive inline rewrites of --min-node-width can\'t
        // clobber it. Only applies when node is narrower than default.
        if (node.size && node.size[0] < VUE_DEFAULT_MIN_NODE_WIDTH) {
            el.style.setProperty('--eclipse-min-w', `${node.size[0]}px`);
        } else {
            el.style.removeProperty('--eclipse-min-w');
        }
        el.style.removeProperty('--eclipse-cw');
        el.style.removeProperty('--eclipse-z');
    }
}

function computeCollapsedWidth(node) {
    const title = node.title || node.type || '';
    const expandedWidth = node.size ? node.size[0] : 200;
    try {
        if (!computeCollapsedWidth._canvas) {
            computeCollapsedWidth._canvas = document.createElement('canvas');
        }
        const ctx = computeCollapsedWidth._canvas.getContext('2d');
        if (ctx) {
            ctx.font = '600 14px Inter, Arial, sans-serif';
            const measuredWidth = ctx.measureText(title).width + COLLAPSED_HEADER_EXTRA;
            return Math.min(expandedWidth, Math.max(measuredWidth, NODE_COLLAPSED_WIDTH));
        }
    } catch (_) {}
    return node._collapsed_width ? node._collapsed_width : NODE_COLLAPSED_WIDTH;
}

function patchNodeCollapse(node) {
    if (node._eclipseCollapsePatched) return;
    node._eclipseCollapsePatched = true;
    const origCollapse = node.collapse;
    if (!origCollapse) return;
    node.collapse = function (force) {
        origCollapse.call(this, force);
        requestAnimationFrame(() => {
            syncNodeSizeToCSS(this);
        });
    };
}

function fixAllNodes() {
    if (!app.graph) return;
    const nodes = app.graph._nodes;
    if (!nodes?.length) return;
    for (let idx = 0; idx < nodes.length; idx++) {
        const node = nodes[idx];
        patchNodeCollapse(node);
        if (getNodeElement(node)) syncNodeSizeToCSS(node, true);
    }
}
let eclipseSizeFixEnabled = true;
app.registerExtension({
    name: 'Eclipse.nodeSizeFix',
    async init() {
        // Classic canvas mode has no per-node DOM elements — every path
        // in this extension is a no-op there. Short-circuit the whole
        // extension to avoid patching collapse + rAF loops per node.
        if (!isVueMode()) {
            eclipseSizeFixEnabled = false;
            return;
        }
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
    },
    nodeCreated(node) {
        if (eclipseSizeFixEnabled) patchNodeCollapse(node);
    },
    loadedGraphNode(node) {
        if (eclipseSizeFixEnabled) patchNodeCollapse(node);
    },
    async afterConfigureGraph() {
        if (!eclipseSizeFixEnabled) return;
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                fixAllNodes();
            });
        });
    },
});
