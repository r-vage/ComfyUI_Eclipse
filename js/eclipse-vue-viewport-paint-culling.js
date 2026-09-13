/**
 * Paint-only viewport culling for Nodes 2.0.
 *
 * Copyright (c) 2026 r-vage. MIT License.
 */
import {
    app,
    api
} from './comfy/index.js';
import {
    isVueMode,
    onVueModeChange
} from './eclipse-widget-performance-utils.js';

const SETTING_ID = 'Eclipse.VueViewportPaintCulling';
const CONFIG_KEY = 'vue_viewport_paint_culling';
const CULLED_ATTRIBUTE = 'data-eclipse-viewport-culled';
const NODE_SELECTOR = '.lg-node[data-node-id]';
const TRANSFORM_PANE_SELECTOR = '[data-testid="transform-pane"]';
const OVERSCAN_RATIO = 0.25;
const HYDRATION_FRAMES = 2;

function isValidArea(area) {
    return area != null &&
        typeof area.length === 'number' &&
        area.length >= 4 &&
        [area[0], area[1], area[2], area[3]].every(Number.isFinite) &&
        area[2] > 0 &&
        area[3] > 0;
}

function expandViewport(area) {
    const horizontal = area[2] * OVERSCAN_RATIO;
    const vertical = area[3] * OVERSCAN_RATIO;
    return [
        area[0] - horizontal,
        area[1] - vertical,
        area[2] + horizontal * 2,
        area[3] + vertical * 2,
    ];
}

function areasIntersect(first, second) {
    return !(
        first[0] + first[2] < second[0] ||
        second[0] + second[2] < first[0] ||
        first[1] + first[3] < second[1] ||
        second[1] + second[3] < first[1]
    );
}

function nodeIsSelected(canvas, node, element) {
    const selected = canvas?.selected_nodes;
    if (selected instanceof Set || selected instanceof Map) {
        if (selected.has(node) || selected.has(node.id) ||
            selected.has(String(node.id))) return true;
    } else if (selected && typeof selected === 'object') {
        if (selected[node.id] === node || selected[node.id] === true ||
            Object.values(selected).includes(node)) return true;
    }
    const selectedItems = canvas?.selectedItems;
    if (selectedItems instanceof Set || selectedItems instanceof Map) {
        if (selectedItems.has(node) || selectedItems.has(node.id) ||
            selectedItems.has(String(node.id))) return true;
    }
    return Boolean(
        node?.is_selected ||
        element?.matches?.('.outline-node-component-outline')
    );
}

function interactionReferencesNode(value, node) {
    return value === node || value?.node === node || value?.node_data === node;
}

function injectStyles() {
    if (document.getElementById('eclipse-vue-viewport-paint-culling-styles')) {
        return;
    }
    const style = document.createElement('style');
    style.id = 'eclipse-vue-viewport-paint-culling-styles';
    const target = `${NODE_SELECTOR}[${CULLED_ATTRIBUTE}]`;
    style.textContent = [
        `${target},`,
        `${target} * {`,
        '  visibility: hidden !important;',
        '  pointer-events: none !important;',
        '}',
    ].join('\n');
    document.head.appendChild(style);
}

function createVueViewportPaintCulling(appRef) {
    let enabled = false;
    let disposed = false;
    let failed = false;
    let activeGraph = null;
    let graphGeneration = 0;
    let graphScanFrame = null;
    let evaluationFrame = null;
    let hydrationFrame = null;
    let mutationObserver = null;
    let canvasElement = null;
    let dragAndScale = null;
    let nativeOnChanged = null;
    let transformWrapper = null;
    let unsubscribeModeChange = null;
    const records = new Map();
    const markedElements = new Set();
    const hydratingElements = new Map();
    const pointerElements = new Map();

    function setElementCulled(element, culled) {
        if (!element) return;
        const currentlyCulled = element.hasAttribute?.(CULLED_ATTRIBUTE) === true;
        if (currentlyCulled === culled) return;
        if (culled) {
            element.setAttribute?.(CULLED_ATTRIBUTE, '');
            markedElements.add(element);
        } else {
            element.removeAttribute?.(CULLED_ATTRIBUTE);
            markedElements.delete(element);
        }
    }

    function clearAllMarkers() {
        for (const element of [...markedElements]) {
            setElementCulled(element, false);
        }
    }

    function cancelFrame(frameId) {
        if (frameId !== null) cancelAnimationFrame(frameId);
    }

    function stopScheduledWork() {
        cancelFrame(graphScanFrame);
        cancelFrame(evaluationFrame);
        cancelFrame(hydrationFrame);
        graphScanFrame = null;
        evaluationFrame = null;
        hydrationFrame = null;
        hydratingElements.clear();
    }

    function failOpen(error) {
        if (failed || disposed) return;
        failed = true;
        stopScheduledWork();
        clearAllMarkers();
        console.error(
            '[Eclipse] Nodes 2 viewport paint culling failed open; all nodes remain visible.',
            error
        );
    }

    function currentViewport() {
        const canvas = appRef.canvas;
        if (!canvas || canvas.graph !== activeGraph) return null;
        const area = canvas.ds?.visible_area ?? canvas.visible_area;
        return isValidArea(area)
            ? [area[0], area[1], area[2], area[3]]
            : null;
    }

    function isProtected(record) {
        const { element, node } = record;
        if (hydratingElements.has(element)) return true;
        if ([...pointerElements.values()].includes(element)) return true;
        const activeElement = document.activeElement;
        if (activeElement &&
            (activeElement === element || element.contains?.(activeElement))) {
            return true;
        }
        const canvas = appRef.canvas;
        if (nodeIsSelected(canvas, node, element)) return true;
        return [
            canvas?.node_dragged,
            canvas?.resizing_node,
            canvas?.connecting_node,
            canvas?.node_over,
        ].some((value) => interactionReferencesNode(value, node));
    }

    function evaluateActiveGraph() {
        evaluationFrame = null;
        if (disposed || failed) return;
        if (!enabled || !isVueMode() || !activeGraph ||
            appRef.canvas?.graph !== activeGraph) {
            clearAllMarkers();
            return;
        }
        try {
            const viewport = currentViewport();
            if (!viewport) {
                clearAllMarkers();
                return;
            }
            const overscanViewport = expandViewport(viewport);
            for (const [nodeId, record] of [...records]) {
                const { element, node } = record;
                if (!element?.isConnected || node?.graph !== activeGraph) {
                    setElementCulled(element, false);
                    records.delete(nodeId);
                    hydratingElements.delete(element);
                    continue;
                }
                const area = node.renderArea;
                const culled = isValidArea(area) &&
                    !isProtected(record) &&
                    !areasIntersect(overscanViewport, area);
                setElementCulled(element, culled);
            }
        } catch (error) {
            failOpen(error);
        }
    }

    function scheduleEvaluation() {
        if (disposed || failed || evaluationFrame !== null) return;
        evaluationFrame = requestAnimationFrame(evaluateActiveGraph);
    }

    function scheduleHydrationTick() {
        if (hydrationFrame !== null || !hydratingElements.size) return;
        hydrationFrame = requestAnimationFrame(() => {
            hydrationFrame = null;
            let released = false;
            for (const [element, framesLeft] of [...hydratingElements]) {
                if (!element?.isConnected) {
                    hydratingElements.delete(element);
                } else if (framesLeft <= 1) {
                    hydratingElements.delete(element);
                    released = true;
                } else {
                    hydratingElements.set(element, framesLeft - 1);
                }
            }
            if (hydratingElements.size) scheduleHydrationTick();
            if (released) scheduleEvaluation();
        });
    }

    function hydrateElement(element) {
        if (!element?.matches?.(NODE_SELECTOR)) return false;
        const nodeId = element.getAttribute?.('data-node-id');
        if (nodeId == null || nodeId.startsWith('preview-')) return false;
        for (const [knownId, knownRecord] of records) {
            if (knownId !== nodeId && knownRecord.element === element) {
                records.delete(knownId);
            }
        }
        let record = records.get(nodeId);
        if (!record) {
            const node = activeGraph?._nodes?.find?.(
                (candidate) => String(candidate.id) === nodeId &&
                    candidate.graph === activeGraph
            );
            if (!node) return false;
            record = { node, element: null };
            records.set(nodeId, record);
        }
        if (record.element && record.element !== element) {
            setElementCulled(record.element, false);
            hydratingElements.delete(record.element);
        }
        record.element = element;
        setElementCulled(element, false);
        hydratingElements.set(element, HYDRATION_FRAMES);
        scheduleHydrationTick();
        return true;
    }

    function collectNodeElements(root) {
        const elements = [];
        if (root?.matches?.(NODE_SELECTOR)) elements.push(root);
        for (const element of root?.querySelectorAll?.(NODE_SELECTOR) || []) {
            elements.push(element);
        }
        return elements;
    }

    function forgetElement(element) {
        const nodeId = element?.getAttribute?.('data-node-id');
        const record = nodeId == null ? null : records.get(nodeId);
        if (record?.element === element) records.delete(nodeId);
        hydratingElements.delete(element);
        setElementCulled(element, false);
        for (const [pointerId, pointerElement] of [...pointerElements]) {
            if (pointerElement === element) pointerElements.delete(pointerId);
        }
    }

    function hydrateActiveGraph(generation) {
        graphScanFrame = null;
        if (disposed || failed || !enabled || !isVueMode() ||
            generation !== graphGeneration || !activeGraph ||
            appRef.canvas?.graph !== activeGraph) {
            clearAllMarkers();
            return;
        }
        try {
            clearAllMarkers();
            records.clear();
            hydratingElements.clear();
            if (!Array.isArray(activeGraph._nodes)) {
                clearAllMarkers();
                return;
            }
            for (const node of activeGraph._nodes) {
                if (node?.id == null || node.graph !== activeGraph) continue;
                records.set(String(node.id), { node, element: null });
            }
            for (const element of document.querySelectorAll(NODE_SELECTOR)) {
                hydrateElement(element);
            }
            scheduleEvaluation();
        } catch (error) {
            failOpen(error);
        }
    }

    function scheduleGraphHydration(graph) {
        cancelFrame(graphScanFrame);
        cancelFrame(evaluationFrame);
        cancelFrame(hydrationFrame);
        graphScanFrame = null;
        evaluationFrame = null;
        hydrationFrame = null;
        clearAllMarkers();
        records.clear();
        hydratingElements.clear();
        activeGraph = graph || null;
        const generation = ++graphGeneration;
        if (!enabled || !isVueMode() || !activeGraph) return;
        graphScanFrame = requestAnimationFrame(
            () => hydrateActiveGraph(generation)
        );
    }

    function handleMutations(mutations) {
        if (!enabled || !isVueMode() || !activeGraph || failed) return;
        let changed = false;
        for (const mutation of mutations) {
            if (mutation.type === 'attributes') {
                const target = mutation.target;
                if (target?.matches?.(NODE_SELECTOR)) {
                    if (mutation.attributeName === 'data-node-id') {
                        changed = hydrateElement(target) || changed;
                    } else {
                        changed = true;
                    }
                } else if (mutation.attributeName === 'style' &&
                    target?.matches?.(TRANSFORM_PANE_SELECTOR)) {
                    changed = true;
                }
            }
            for (const root of mutation.removedNodes || []) {
                for (const element of collectNodeElements(root)) {
                    forgetElement(element);
                    changed = true;
                }
            }
            for (const root of mutation.addedNodes || []) {
                for (const element of collectNodeElements(root)) {
                    changed = hydrateElement(element) || changed;
                }
            }
        }
        if (changed) scheduleEvaluation();
    }

    function unbindCanvas() {
        if (canvasElement) {
            canvasElement.removeEventListener?.(
                'litegraph:set-graph',
                handleGraphChange
            );
        }
        if (dragAndScale?.onChanged === transformWrapper) {
            dragAndScale.onChanged = nativeOnChanged;
        }
        canvasElement = null;
        dragAndScale = null;
        nativeOnChanged = null;
        transformWrapper = null;
    }

    function bindCanvas() {
        const nextCanvasElement = appRef.canvas?.canvas || null;
        const nextDragAndScale = appRef.canvas?.ds || null;
        if (canvasElement === nextCanvasElement &&
            nextDragAndScale === dragAndScale && transformWrapper) return;
        unbindCanvas();
        canvasElement = nextCanvasElement;
        canvasElement?.addEventListener?.(
            'litegraph:set-graph',
            handleGraphChange
        );
        dragAndScale = nextDragAndScale;
        if (!dragAndScale) return;
        const boundDragAndScale = dragAndScale;
        const wrappedOnChanged = dragAndScale.onChanged;
        nativeOnChanged = wrappedOnChanged;
        transformWrapper = function () {
            let result;
            try {
                result = wrappedOnChanged?.apply(this, arguments);
            } finally {
                if (!disposed && enabled && isVueMode() &&
                    dragAndScale === boundDragAndScale) scheduleEvaluation();
            }
            return result;
        };
        dragAndScale.onChanged = transformWrapper;
    }

    function handleGraphChange(event) {
        bindCanvas();
        scheduleGraphHydration(
            event?.detail?.newGraph ?? appRef.canvas?.graph ?? null
        );
    }

    function closestNodeElement(target) {
        return target?.matches?.(NODE_SELECTOR)
            ? target
            : target?.closest?.(NODE_SELECTOR);
    }

    function handlePointerDown(event) {
        if (!enabled || !isVueMode()) return;
        const element = closestNodeElement(event.target);
        if (!element) return;
        pointerElements.set(event.pointerId, element);
        setElementCulled(element, false);
        scheduleEvaluation();
    }

    function handlePointerEnd(event) {
        if (pointerElements.delete(event.pointerId)) scheduleEvaluation();
    }

    function handleFocus(event) {
        const element = closestNodeElement(event.target);
        if (element) setElementCulled(element, false);
        scheduleEvaluation();
    }

    function installObserver() {
        if (mutationObserver || typeof MutationObserver !== 'function') return;
        mutationObserver = new MutationObserver(handleMutations);
        mutationObserver.observe(document.documentElement, {
            subtree: true,
            childList: true,
            attributes: true,
            attributeFilter: ['style', 'class', 'data-node-id', 'data-collapsed'],
        });
    }

    function setEnabled(value) {
        if (disposed) return;
        enabled = value === true;
        if (!enabled || !isVueMode()) {
            scheduleGraphHydration(null);
            return;
        }
        failed = false;
        bindCanvas();
        scheduleGraphHydration(appRef.canvas?.graph ?? null);
    }

    function refreshGraph() {
        if (disposed) return;
        bindCanvas();
        scheduleGraphHydration(
            enabled && isVueMode() ? appRef.canvas?.graph ?? null : null
        );
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        stopScheduledWork();
        clearAllMarkers();
        records.clear();
        pointerElements.clear();
        mutationObserver?.disconnect?.();
        mutationObserver = null;
        unsubscribeModeChange?.();
        unsubscribeModeChange = null;
        unbindCanvas();
        document.removeEventListener?.('pointerdown', handlePointerDown, true);
        document.removeEventListener?.('pointerup', handlePointerEnd, true);
        document.removeEventListener?.('pointercancel', handlePointerEnd, true);
        document.removeEventListener?.('lostpointercapture', handlePointerEnd, true);
        document.removeEventListener?.('focusin', handleFocus, true);
        document.removeEventListener?.('focusout', handleFocus, true);
    }

    injectStyles();
    installObserver();
    document.addEventListener?.('pointerdown', handlePointerDown, true);
    document.addEventListener?.('pointerup', handlePointerEnd, true);
    document.addEventListener?.('pointercancel', handlePointerEnd, true);
    document.addEventListener?.('lostpointercapture', handlePointerEnd, true);
    document.addEventListener?.('focusin', handleFocus, true);
    document.addEventListener?.('focusout', handleFocus, true);
    unsubscribeModeChange = onVueModeChange((vueModeEnabled) => {
        if (vueModeEnabled && enabled) {
            failed = false;
            bindCanvas();
            scheduleGraphHydration(appRef.canvas?.graph ?? null);
        } else {
            scheduleGraphHydration(null);
        }
    });
    bindCanvas();

    return Object.freeze({ dispose, refreshGraph, setEnabled });
}

let configuredEnabled = true;
let activeController = null;

async function persistEnabled(value) {
    try {
        const response = await api.fetchApi('/eclipse/config/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ [CONFIG_KEY]: value }),
        });
        if (!response.ok || (await response.json()).success !== true) {
            throw new Error(`HTTP ${response.status}`);
        }
    } catch (error) {
        console.error('[Eclipse] Failed to save Nodes 2 viewport paint culling:', error);
    }
}

app.registerExtension({
    name: 'Eclipse.VueViewportPaintCulling',
    async init(appRef) {
        try {
            const response = await api.fetchApi('/eclipse/config/all');
            const config = response.ok ? await response.json() : {};
            configuredEnabled = config[CONFIG_KEY] !== false;
        } catch (error) {
            configuredEnabled = true;
            console.error('[Eclipse] Failed to load Nodes 2 viewport paint culling:', error);
        }
        let initialized = false;
        appRef.ui.settings.addSetting({
            id: SETTING_ID,
            category: ['Eclipse', 'Nodes 2.0', 'VueViewportPaintCulling'],
            name: 'Cull offscreen nodes',
            type: 'boolean',
            tooltip: 'Hide complete Nodes 2 node trees outside the viewport plus 25% overscan while keeping them mounted, reactive, and serialized.',
            defaultValue: configuredEnabled,
            sortOrder: 65,
            onChange(value) {
                if (!initialized) {
                    initialized = true;
                    return;
                }
                configuredEnabled = value === true;
                activeController?.setEnabled(configuredEnabled);
                void persistEnabled(configuredEnabled);
            },
        });
    },
    setup(appRef) {
        activeController?.dispose();
        activeController = createVueViewportPaintCulling(appRef);
        activeController.setEnabled(configuredEnabled);
    },
    afterConfigureGraph() {
        activeController?.refreshGraph();
    },
});

export {
    areasIntersect,
    createVueViewportPaintCulling,
    expandViewport,
    isValidArea
};
