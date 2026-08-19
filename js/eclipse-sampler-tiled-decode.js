import { app, api } from './comfy/index.js';
import { isVueMode, onVueModeChange } from './eclipse-widget-performance-utils.js';

const SAMPLER_NODE_TYPES = ['Eclipse KSampler (Kargim) [Eclipse]'];
const PREVIEW_PHASE_ATTRIBUTE = 'data-eclipse-ksampler-preview-phase';
const VUE_NODE_SELECTOR = '.lg-node[data-node-id]';
const PREVIEW_PHASE = Object.freeze({
    LIVE: 'live',
    FINAL: 'final',
    NONE: 'none',
});
const MAX_PHASE_SYNC_FRAMES = 5;
const POST_NAVIGATION_WAIT_FRAMES = 2;
const samplerNodes = new Set();
const pendingPhaseSyncs = new WeakMap();
let activeGraph = null;
let navigationGeneration = 0;
let readyGraph = null;
let readyGraphGeneration = -1;
let pendingGraphSyncFrame = null;
let previewRemountObserver = null;
let previewRemountObserverTarget = null;

function injectPreviewPhaseStyles() {
    if (document.getElementById('eclipse-ksampler-live-preview-styles')) return;
    const style = document.createElement('style');
    style.id = 'eclipse-ksampler-live-preview-styles';
    const liveBody = `.lg-node[${PREVIEW_PHASE_ATTRIBUTE}="${PREVIEW_PHASE.LIVE}"] [data-testid^="node-body-"]`;
    const finalBody = `.lg-node[${PREVIEW_PHASE_ATTRIBUTE}="${PREVIEW_PHASE.FINAL}"] [data-testid^="node-body-"]`;
    const noneBody = `.lg-node[${PREVIEW_PHASE_ATTRIBUTE}="${PREVIEW_PHASE.NONE}"] [data-testid^="node-body-"]`;
    style.textContent = [
        `${liveBody} > div:has(> .lg-node-content),`,
        `${noneBody} > div:has(> .lg-node-content) {`,
        '  display: none !important;',
        '}',
        `${liveBody} .lg-node-content,`,
        `${noneBody} .lg-node-content,`,
        `${finalBody} > img,`,
        `${finalBody} > .text-node-component-header-text.text-center.text-xs,`,
        `${finalBody} > .text-pure-white.text-center,`,
        `${noneBody} > img,`,
        `${noneBody} > .text-node-component-header-text.text-center.text-xs,`,
        `${noneBody} > .text-pure-white.text-center {`,
        '  display: none !important;',
        '}',
    ].join('\n');
    document.head.appendChild(style);
}

function isNodeInActiveGraph(node, graph, generation) {
    return !!graph &&
        generation === navigationGeneration &&
        graph === activeGraph &&
        graph === readyGraph &&
        generation === readyGraphGeneration &&
        graph === app.canvas?.graph &&
        node.graph === graph &&
        graph._nodes?.includes(node);
}

function getVueNodeElement(node, graph, generation) {
    if (!isNodeInActiveGraph(node, graph, generation)) return null;
    const cached = node._eclipseSamplerVueElement;
    if (cached?.element?.isConnected && cached.graph === graph && cached.generation === generation) {
        return cached.element;
    }
    if (node.id == null) return null;
    const escapedId = globalThis.CSS?.escape
        ? CSS.escape(String(node.id))
        : String(node.id).replace(/["\\]/g, '\\$&');
    const element = document.querySelector(`.lg-node[data-node-id="${escapedId}"]`);
    if (element) node._eclipseSamplerVueElement = { element, graph, generation };
    return element;
}

function finishPhaseSync(node, job) {
    if (pendingPhaseSyncs.get(node) === job) pendingPhaseSyncs.delete(node);
}

function schedulePreviewPhaseSync(node, graph = node.graph || activeGraph, generation = navigationGeneration, force = false) {
    if (!isVueMode() || !graph || graph !== activeGraph || graph !== readyGraph || generation !== readyGraphGeneration) {
        return;
    }
    const existing = pendingPhaseSyncs.get(node);
    if (existing) {
        if (!force) return;
        cancelAnimationFrame(existing.frameId);
    }
    const job = { frameId: null, graph, generation };
    pendingPhaseSyncs.set(node, job);
    let framesLeft = MAX_PHASE_SYNC_FRAMES;
    const trySync = () => {
        if (pendingPhaseSyncs.get(node) !== job) return;
        if (!isVueMode() || !samplerNodes.has(node) ||
            job.generation !== navigationGeneration || job.graph !== activeGraph ||
            job.graph !== readyGraph || job.generation !== readyGraphGeneration ||
            job.graph !== app.canvas?.graph || (node.graph && node.graph !== job.graph)) {
            finishPhaseSync(node, job);
            return;
        }
        const element = getVueNodeElement(node, job.graph, job.generation);
        if (element) {
            element.setAttribute(PREVIEW_PHASE_ATTRIBUTE, node._eclipseSamplerPreviewPhase ?? PREVIEW_PHASE.FINAL);
            finishPhaseSync(node, job);
            return;
        }
        if (--framesLeft <= 0) {
            finishPhaseSync(node, job);
            return;
        }
        job.frameId = requestAnimationFrame(trySync);
    };
    trySync();
}

function setPreviewPhase(node, phase) {
    node._eclipseSamplerPreviewPhase = phase;
    schedulePreviewPhaseSync(node, node.graph || activeGraph, navigationGeneration, true);
}

function invalidateSamplerNodeElement(node) {
    const pending = pendingPhaseSyncs.get(node);
    if (pending) cancelAnimationFrame(pending.frameId);
    pendingPhaseSyncs.delete(node);
    node._eclipseSamplerVueElement?.element?.removeAttribute(PREVIEW_PHASE_ATTRIBUTE);
    delete node._eclipseSamplerVueElement;
}

function invalidateGraphElements(graph) {
    for (const node of graph?._nodes || []) {
        if (samplerNodes.has(node)) invalidateSamplerNodeElement(node);
    }
}

function findActiveSamplerNode(nodeId) {
    const graph = app.canvas?.graph;
    if (!isVueMode() || graph !== activeGraph || graph !== readyGraph) return null;
    return graph?._nodes?.find(node =>
        samplerNodes.has(node) &&
        node.graph === graph &&
        String(node.id) === nodeId
    ) || null;
}

function reapplyMountedPreviewPhase(element) {
    if (!element.isConnected) return;
    const nodeId = element.getAttribute?.('data-node-id');
    if (nodeId == null) return;
    const node = findActiveSamplerNode(nodeId);
    if (!node) return;

    const cached = node._eclipseSamplerVueElement;
    if (cached?.element !== element) {
        node._eclipseSamplerVueElement = {
            element,
            graph: activeGraph,
            generation: navigationGeneration,
        };
    }
    element.setAttribute(
        PREVIEW_PHASE_ATTRIBUTE,
        node._eclipseSamplerPreviewPhase ?? PREVIEW_PHASE.FINAL
    );
}

function handleMountedPreviewNodes(records) {
    for (const record of records) {
        for (const addedNode of record.addedNodes || []) {
            if (addedNode.matches?.(VUE_NODE_SELECTOR)) {
                reapplyMountedPreviewPhase(addedNode);
            }
            for (const element of addedNode.querySelectorAll?.(VUE_NODE_SELECTOR) || []) {
                reapplyMountedPreviewPhase(element);
            }
        }
    }
}

function startPreviewRemountObserver() {
    if (!isVueMode() || typeof MutationObserver !== 'function') return;
    const observerTarget = document.documentElement;
    if (!observerTarget || observerTarget === previewRemountObserverTarget) return;
    if (!previewRemountObserver) {
        previewRemountObserver = new MutationObserver(handleMountedPreviewNodes);
    } else {
        previewRemountObserver.disconnect();
    }
    previewRemountObserver.observe(observerTarget, { childList: true, subtree: true });
    previewRemountObserverTarget = observerTarget;
}

function stopPreviewRemountObserver() {
    previewRemountObserver?.disconnect();
    previewRemountObserverTarget = null;
}

function reapplyGraphPreviewPhases(graph, generation) {
    if (graph !== activeGraph || graph !== readyGraph ||
        generation !== navigationGeneration || generation !== readyGraphGeneration) return;
    for (const node of graph._nodes || []) {
        if (!samplerNodes.has(node)) continue;
        schedulePreviewPhaseSync(node, graph, generation, true);
    }
}

function schedulePostNavigationPhaseSync(graph, oldGraph = null) {
    startPreviewRemountObserver();
    if (!graph) return;
    activeGraph = graph;
    readyGraph = null;
    readyGraphGeneration = -1;
    const generation = ++navigationGeneration;
    if (pendingGraphSyncFrame !== null) {
        cancelAnimationFrame(pendingGraphSyncFrame);
        pendingGraphSyncFrame = null;
    }
    if (oldGraph && oldGraph !== graph) invalidateGraphElements(oldGraph);
    invalidateGraphElements(graph);
    let framesLeft = POST_NAVIGATION_WAIT_FRAMES;
    const waitForRemount = () => {
        pendingGraphSyncFrame = null;
        if (generation !== navigationGeneration || graph !== activeGraph || graph !== app.canvas?.graph) return;
        startPreviewRemountObserver();
        if (--framesLeft > 0) {
            pendingGraphSyncFrame = requestAnimationFrame(waitForRemount);
            return;
        }
        readyGraph = graph;
        readyGraphGeneration = generation;
        reapplyGraphPreviewPhases(graph, generation);
    };
    pendingGraphSyncFrame = requestAnimationFrame(waitForRemount);
}

function installGraphNavigationListener() {
    const canvasElement = app.canvas?.canvas;
    if (!canvasElement || canvasElement._eclipseSamplerPreviewGraphListener) return;
    const listener = (event) => {
        const graph = event.detail?.newGraph;
        if (graph) schedulePostNavigationPhaseSync(graph, event.detail?.oldGraph);
    };
    canvasElement._eclipseSamplerPreviewGraphListener = listener;
    canvasElement.addEventListener('litegraph:set-graph', listener);
}

function clearTransientPreview(node) {
    if (node.id == null) return;
    const graph = node.graph;
    const isRootNode = !graph || graph === app.graph || graph === app.rootGraph;
    const previewKey = !isRootNode && graph.id != null
        ? `${graph.id}:${node.id}`
        : String(node.id);
    if (app.nodePreviewImages?.[previewKey]) {
        delete app.nodePreviewImages[previewKey];
    }
}

function isPreviewEventForNode(detail, node) {
    if (detail?.displayNodeId == null || node.id == null) return false;
    const displayNodeId = String(detail.displayNodeId);
    return displayNodeId === String(node.id) || displayNodeId.split(':').at(-1) === String(node.id);
}

function isExecutionFailureForNode(detail, node) {
    if (node._eclipseSamplerPreviewPhase !== PREVIEW_PHASE.LIVE || detail?.node_id == null || node.id == null) {
        return false;
    }
    const executionNodeId = String(detail.node_id);
    const matchesNode = executionNodeId === String(node.id) || executionNodeId.split(':').at(-1) === String(node.id);
    if (!matchesNode) return false;

    const activeJobId = node._eclipseSamplerPreviewJobId;
    return activeJobId == null || detail.prompt_id == null || String(detail.prompt_id) === activeJobId;
}

function restoreFinalAfterFailure(detail) {
    for (const node of samplerNodes) {
        if (!isExecutionFailureForNode(detail, node)) continue;
        delete node._eclipseSamplerPreviewJobId;
        clearTransientPreview(node);
        const previewModeWidget = node.widgets?.find(w => w.name === 'preview_mode');
        setPreviewPhase(node, previewModeWidget?.value === 'None' ? PREVIEW_PHASE.NONE : PREVIEW_PHASE.FINAL);
        node.setDirtyCanvas?.(true, true);
    }
}

injectPreviewPhaseStyles();

api.addEventListener('b_preview_with_metadata', ({ detail }) => {
    for (const node of samplerNodes) {
        if (!isPreviewEventForNode(detail, node)) continue;
        const previewModeWidget = node.widgets?.find(w => w.name === 'preview_mode');
        if (previewModeWidget?.value !== 'None') {
            node._eclipseSamplerPreviewJobId = detail.jobId == null ? undefined : String(detail.jobId);
            setPreviewPhase(node, PREVIEW_PHASE.LIVE);
        }
    }
});

api.addEventListener('execution_error', ({ detail }) => restoreFinalAfterFailure(detail));
api.addEventListener('execution_interrupted', ({ detail }) => restoreFinalAfterFailure(detail));

onVueModeChange((vueModeEnabled) => {
    if (vueModeEnabled) {
        startPreviewRemountObserver();
        schedulePostNavigationPhaseSync(app.canvas?.graph || activeGraph || app.graph);
    } else {
        stopPreviewRemountObserver();
    }
});

app.registerExtension({
    name: 'Eclipse.SamplerTiledDecodeVisibility',
    async init() {
        activeGraph = app.canvas?.graph || app.graph || null;
        installGraphNavigationListener();
        if (isVueMode()) {
            startPreviewRemountObserver();
            schedulePostNavigationPhaseSync(activeGraph);
        }
    },
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (!SAMPLER_NODE_TYPES.includes(nodeData.name)) return;

        const origOnExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (output) {
            const res = origOnExecuted ? origOnExecuted.apply(this, arguments) : undefined;
            const previewModeWidget = this.widgets?.find(w => w.name === 'preview_mode');
            delete this._eclipseSamplerPreviewJobId;
            if (previewModeWidget?.value === "None") {
                setPreviewPhase(this, PREVIEW_PHASE.NONE);
                this.imgs = null;
                this.images = null;
                this.preview = null;
                if (app.nodeOutputs?.[this.id]) {
                    delete app.nodeOutputs[this.id].images;
                }
                clearTransientPreview(this);
                const previewWidgetIdx = this.widgets.findIndex(w => w.name === '$$canvas-image-preview' || w.type === 'IMAGE_PREVIEW');
                if (previewWidgetIdx > -1) {
                    const widget = this.widgets[previewWidgetIdx];
                    widget.onRemove?.();
                    this.widgets.splice(previewWidgetIdx, 1);
                }
                const size = this.computeSize();
                const width = this.size ? this.size[0] : size[0];
                this.setSize([width, size[1]]);
                this.setDirtyCanvas(true, true);
            } else {
                if (output?.images) {
                    this.imgs = output.images.map(img => {
                        const image = new Image();
                        image.src = api.apiURL(`/view?filename=${encodeURIComponent(img.filename)}&type=${encodeURIComponent(img.type)}&subfolder=${encodeURIComponent(img.subfolder)}`);
                        return image;
                    });
                }
                clearTransientPreview(this);
                setPreviewPhase(this, PREVIEW_PHASE.FINAL);
                this.setDirtyCanvas(true, true);
            }
            return res;
        };

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const origResult = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            samplerNodes.add(node);
            const initialPreviewMode = node.widgets?.find(w => w.name === 'preview_mode')?.value;
            setPreviewPhase(node, initialPreviewMode === 'None' ? PREVIEW_PHASE.NONE : PREVIEW_PHASE.FINAL);

            const origOnRemoved = node.onRemoved;
            node.onRemoved = function () {
                samplerNodes.delete(node);
                invalidateSamplerNodeElement(node);
                delete node._eclipseSamplerPreviewPhase;
                delete node._eclipseSamplerPreviewJobId;
                return origOnRemoved?.apply(this, arguments);
            };

            const tiledDecodeWidget = this.widgets.find(w => w.name === 'tiled_decode');
            const tileSizeWidget = this.widgets.find(w => w.name === 'tile_size');

            if (tiledDecodeWidget && tileSizeWidget) {
                const updateVisibility = () => {
                    const isTiled = !!tiledDecodeWidget.value;

                    if (isTiled) {
                        if (tileSizeWidget.type === "hidden" && tileSizeWidget.origType) {
                            tileSizeWidget.type = tileSizeWidget.origType;
                            if (tileSizeWidget.origComputeSize) {
                                tileSizeWidget.computeSize = tileSizeWidget.origComputeSize;
                            } else {
                                delete tileSizeWidget.computeSize;
                            }
                        }
                    } else {
                        if (tileSizeWidget.type !== "hidden") {
                            tileSizeWidget.origType = tileSizeWidget.type;
                            tileSizeWidget.origComputeSize = tileSizeWidget.computeSize;
                            tileSizeWidget.type = "hidden";
                            tileSizeWidget.computeSize = () => [0, -4];
                        }
                    }
                    const size = this.computeSize();
                    const width = this.size ? this.size[0] : size[0];
                    this.setSize([width, size[1]]);
                    this.setDirtyCanvas(true, true);
                };

                // Add listener / callback on tiled_decode value change
                const origCallback = tiledDecodeWidget.callback;
                tiledDecodeWidget.callback = function (val) {
                    const res = origCallback ? origCallback.apply(this, arguments) : undefined;
                    updateVisibility();
                    return res;
                };

                // Run initially to set correct state on load/creation
                setTimeout(updateVisibility, 0);
            }

            const previewModeWidget = this.widgets.find(w => w.name === 'preview_mode');
            if (previewModeWidget) {
                const updatePreviewVisibility = (val) => {
                    if (val === "None") {
                        delete node._eclipseSamplerPreviewJobId;
                        setPreviewPhase(node, PREVIEW_PHASE.NONE);
                        node.imgs = null;
                        node.images = null;
                        node.preview = null;
                        if (app.nodeOutputs?.[node.id]) {
                            delete app.nodeOutputs[node.id].images;
                        }
                        clearTransientPreview(node);
                        const previewWidgetIdx = node.widgets.findIndex(w => w.name === '$$canvas-image-preview' || w.type === 'IMAGE_PREVIEW');
                        if (previewWidgetIdx > -1) {
                            const widget = node.widgets[previewWidgetIdx];
                            widget.onRemove?.();
                            node.widgets.splice(previewWidgetIdx, 1);
                        }
                        const size = node.computeSize();
                        const width = node.size ? node.size[0] : size[0];
                        node.setSize([width, size[1]]);
                        node.setDirtyCanvas(true, true);
                    } else if (node._eclipseSamplerPreviewPhase === PREVIEW_PHASE.NONE) {
                        setPreviewPhase(node, PREVIEW_PHASE.FINAL);
                    }
                };

                const origPreviewCallback = previewModeWidget.callback;
                previewModeWidget.callback = function (val) {
                    const res = origPreviewCallback ? origPreviewCallback.apply(this, arguments) : undefined;
                    updatePreviewVisibility(val);
                    return res;
                };

                // Run initially to clear if default is None
                setTimeout(() => updatePreviewVisibility(previewModeWidget.value), 0);
            }

            return origResult;
        };
    }
});
