import {
    app
} from './comfy/index.js';
const CANVAS_CULLED_NAMES = new Set([
    "Preview Image [Eclipse]",
    "Preview Image (DOM) [Eclipse]",
    "Preview Mask [Eclipse]",
    "Load Image (Metadata Pipe) [Eclipse]",
    "Load Image (Pipe) [Eclipse]",
    "Load Image From Folder [Eclipse]",
    "Load Image From Folder (Pipe) [Eclipse]",
    "Save Images [Eclipse]",
    "Save Images v2 [Eclipse]",
    "Show Any [Eclipse]",
    "String Dual [Eclipse]",
    "String Multiline [Eclipse]",
    "String Multiline List [Eclipse]",
    "Wildcard Processor [Eclipse]",
    "Read Prompt Files [Eclipse]",
    "Smart LM Loader [Eclipse]",
    "Smart Detection [Eclipse]",
    "LoadImage",
    "SaveImage",
    "PreviewImage",
    "PreviewMask",
    "KSampler",
    "KSamplerAdvanced",
    "SamplerCustom",
    "SEGSPreview",
    "SEGS Preview [Eclipse]",
]);
const THROTTLE_MS = 700;
const CULL_MARGIN = 50;
const SELECTED_Z_BOOST = 10000;
const LOAD_SETTLE_MS = 1200;

let _cullingReady = false;
let _cullingEnabled = true;
// Fingerprint of the last completed scan — when nothing relevant has
// changed (node count, positions, sizes, collapse/mode flags, selection)
// we early-exit without re-running the O(n²) occlusion pass. Culling is
// purely graph-space, so pan/zoom do not invalidate it.
let _lastScanHash = '';

function computeScanHash(canvas, visibleNodes, graphNodes) {
    let h = graphNodes.length + '|' + visibleNodes.length + '|';
    for (let i = 0; i < visibleNodes.length; i++) {
        const n = visibleNodes[i];
        h += (n.id ?? i) + ',' + n.pos[0] + ',' + n.pos[1] + ',' +
             n.size[0] + ',' + n.size[1] + ',' +
             (n.flags?.collapsed ? 1 : 0) + ',' + (n.mode || 0) + ';';
    }
    const sel = canvas.selected_nodes;
    if (sel) {
        const keys = Object.keys(sel);
        if (keys.length) h += '|s:' + keys.sort().join(',');
    }
    return h;
}

function runCullingScan() {
    if (!_cullingReady) return;
    const canvas = app.canvas;
    if (!canvas) return;
    const visibleNodes = canvas.visible_nodes;
    if (!visibleNodes || visibleNodes.length === 0) return;
    const graphNodes = canvas.graph?._nodes;
    if (!graphNodes) return;
    // Skip scan when graph geometry + selection are unchanged since last pass.
    // New widgets on existing nodes get wrapped on the next real change —
    // wrapNodeWidgets is idempotent via the _eclipseCullWrapped flag.
    const hash = computeScanHash(canvas, visibleNodes, graphNodes);
    if (hash === _lastScanHash) return;
    _lastScanHash = hash;
    const zMap = new Map();
    for (let i = 0; i < graphNodes.length; i++) {
        zMap.set(graphNodes[i], i);
    }
    const titleH = LiteGraph.NODE_TITLE_HEIGHT || 20;
    for (let n = 0; n < visibleNodes.length; n++) {
        const node = visibleNodes[n];
        if (node.flags?.collapsed) {
            node._eclipseIsCulled = false;
            continue;
        }
        const nz = zMap.get(node) ?? -1;
        const nx = node.pos[0];
        const ny = node.pos[1] - titleH;
        const nr = nx + node.size[0];
        const nb = ny + node.size[1] + titleH;
        let culled = false;
        for (let o = 0; o < visibleNodes.length; o++) {
            const other = visibleNodes[o];
            if (other === node || other.flags?.collapsed) continue;
            const oz = zMap.get(other) ?? -1;
            if (oz <= nz) continue;
            const ox = other.pos[0];
            const oy = other.pos[1] - titleH;
            const or_ = ox + other.size[0];
            const ob = oy + other.size[1] + titleH;
            if (ox <= nx + CULL_MARGIN && oy <= ny + CULL_MARGIN && or_ >= nr - CULL_MARGIN && ob >= nb - CULL_MARGIN) {
                culled = true;
                break;
            }
        }
        node._eclipseIsCulled = culled;
    }
    const selectedNodes = canvas.selected_nodes;
    for (let n = 0; n < visibleNodes.length; n++) {
        const node = visibleNodes[n];
        // Lazy-patch onDrawBackground for nodes not covered by beforeRegisterNodeDef
        // (e.g. group/subgraph nodes with dynamic type names)
        if (!node._eclipseBgCullPatched && node.onDrawBackground) {
            const origBg = node.onDrawBackground;
            node.onDrawBackground = function () {
                if (this._eclipseIsCulled) return;
                origBg.apply(this, arguments);
            };
            node._eclipseBgCullPatched = true;
            wrapNodeWidgets(node);
        }
        // Handle widgets added after the initial patch (e.g. dynamic-input
        // nodes, subgraph promoted widgets). wrapNodeWidgets is idempotent
        // per-widget via _eclipseCullWrapped.
        if (node.widgets && node._eclipseBgCullPatched) wrapNodeWidgets(node);
        if (!node.widgets) continue;
        const baseZ = zMap.get(node) ?? 0;
        const isSelected = selectedNodes && selectedNodes[node.id];
        const effectiveZ = String(isSelected ? baseZ + SELECTED_Z_BOOST : baseZ);
        const isHostCulled = !!node._eclipseIsCulled;
        for (let w = 0; w < node.widgets.length; w++) {
            const widget = node.widgets[w];
            // Subgraph promoted widget — propagate host culled flag to the
            // resolved inner widget (so isVisible patch can suppress its DOM
            // element) and forward zIndex to the inner widget's element.
            if (isPromotedView(widget)) {
                let inner;
                try { inner = widget.resolveDeepest?.()?.widget; } catch (_) {}
                if (inner) {
                    inner._eclipseHostCulled = isHostCulled;
                    const innerWrap = inner.element?.parentElement;
                    if (innerWrap && innerWrap.style.zIndex !== effectiveZ) {
                        innerWrap.style.zIndex = effectiveZ;
                    }
                }
                continue;
            }
            const wrapper = widget.element?.parentElement;
            if (wrapper && wrapper.style.zIndex !== effectiveZ) {
                wrapper.style.zIndex = effectiveZ;
            }
        }
    }
}

// Detect a promoted-widget view on a subgraph host node. PromotedWidgetView
// instances expose `sourceNodeId` + `sourceWidgetName` and proxy to a widget
// owned by an inner (hidden) node.
function isPromotedView(w) {
    return !!w && typeof w === 'object'
        && 'sourceNodeId' in w && 'sourceWidgetName' in w;
}

function wrapNodeWidgets(node) {
    if (!node.widgets) return;
    for (const w of node.widgets) {
        if (w._eclipseCullWrapped) continue;
        // Classic LiteGraph widgets implement drawWidget; new BaseDOMWidgetImpl
        // and PromotedWidgetView implement draw(ctx, node, width, y, h, lq).
        // Wrap whichever is present — for PromotedWidgetView, `this.node` is
        // the subgraph host (so the host's _eclipseIsCulled flag applies).
        if (typeof w.drawWidget === 'function') {
            const origDraw = w.drawWidget;
            w.drawWidget = function (...args) {
                if (this.node?._eclipseIsCulled) return;
                return origDraw.apply(this, args);
            };
        }
        if (typeof w.draw === 'function') {
            const origDrawFn = w.draw;
            w.draw = function (...args) {
                if (this.node?._eclipseIsCulled) return;
                return origDrawFn.apply(this, args);
            };
        }
        w._eclipseCullWrapped = true;
    }
}

function patchDOMWidgetVisibility() {
    const domAPI = window.comfyAPI?.domWidget;
    if (!domAPI?.ComponentWidgetImpl) return;
    const baseProto = Object.getPrototypeOf(domAPI.ComponentWidgetImpl.prototype);
    if (!baseProto?.isVisible) return;
    const origIsVisible = baseProto.isVisible;
    baseProto.isVisible = function () {
        // Direct cull (widget's own node is culled).
        if (this.node?._eclipseIsCulled) return false;
        // Subgraph promotion cull — widget is owned by an inner (hidden)
        // node, but its DOM element is positioned over a culled subgraph
        // host. Flag is set during runCullingScan.
        if (this._eclipseHostCulled) return false;
        return origIsVisible.call(this);
    };
}
app.registerExtension({
    name: "Eclipse.PreviewCulling",
    async init() {
        // Fetch config before beforeRegisterNodeDef runs
        try {
            const resp = await fetch('/eclipse/config/all');
            if (resp.ok) {
                const cfg = await resp.json();
                if (cfg.preview_culling === false) {
                    _cullingEnabled = false;
                }
            }
        } catch (_) {}
    },
    async setup() {
        if (!_cullingEnabled) return;

        patchDOMWidgetVisibility();

        // Patch loadGraphData to pause culling during workflow load
        const origLoad = app.loadGraphData?.bind(app);
        if (origLoad) {
            app.loadGraphData = async function (...args) {
                _cullingReady = false;
                _lastScanHash = '';
                const result = await origLoad(...args);
                setTimeout(() => { _cullingReady = true; }, LOAD_SETTLE_MS);
                return result;
            };
        }

        // Enable culling after initial page load settles
        setTimeout(() => { _cullingReady = true; }, LOAD_SETTLE_MS * 2);

        setInterval(runCullingScan, THROTTLE_MS);
    },
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (!_cullingEnabled) return;
        if (!CANVAS_CULLED_NAMES.has(nodeData.name)) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origOnNodeCreated?.apply(this, arguments);
            wrapNodeWidgets(this);
        };
        const origOnDrawBg = nodeType.prototype.onDrawBackground;
        nodeType.prototype.onDrawBackground = function () {
            if (this._eclipseIsCulled) return;
            origOnDrawBg?.apply(this, arguments);
        };
        nodeType.prototype._eclipseBgCullPatched = true;
    },
});
