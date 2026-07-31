/**
 * Eclipse viewport virtualization for Nodes 2.0.
 *
 * Copyright (c) 2026 r-vage. MIT License.
 */
import {
    app
} from './comfy/index.js';
import {
    createCanonicalVueNodeSetting,
    hasNativeVueNodeSetting,
    persistCanonicalVueNodeSetting,
    resolveCanonicalVueNodeSetting,
    VUE_NODE_SETTING_DEFINITIONS
} from './eclipse-vue-node-settings.js';

const CONTROLLER_OWNER = 'eclipse:viewport-virtualization';
const VIEWPORT_SETTLE_DELAY = 150;
const viewportVirtualizationSetting =
    VUE_NODE_SETTING_DEFINITIONS.viewportVirtualization;

function isRenderingApi(rendering) {
    return Boolean(
        rendering &&
        typeof rendering.getSnapshot === 'function' &&
        typeof rendering.subscribe === 'function' &&
        typeof rendering.createPushController === 'function'
    );
}

function isValidArea(area) {
    return Array.isArray(area) &&
        area.length >= 4 &&
        area.every((value) => Number.isFinite(value)) &&
        area[2] > 0 &&
        area[3] > 0;
}

function areasIntersect(first, second) {
    return !(
        first[0] + first[2] < second[0] ||
        second[0] + second[2] < first[0] ||
        first[1] + first[3] < second[1] ||
        second[1] + second[3] < first[1]
    );
}

function snapshotGeometryKey(snapshot) {
    const renderAreas = snapshot.renderAreas.map((entry, index) => {
        const id = entry?.id ?? `invalid-${index}`;
        const area = Array.isArray(entry?.area)
            ? entry.area.join(',')
            : 'invalid';
        return `${id}:${area}`;
    });
    return [
        snapshot.graphRevision,
        snapshot.managerAvailable ? 1 : 0,
        snapshot.nodeIds.join(','),
        renderAreas.join(';'),
        snapshot.visibleCanvasArea?.join(',') ?? 'missing',
    ].join('|');
}

function createViewportVirtualization(rendering) {
    let controller;
    let unsubscribe;
    let latestSnapshot = null;
    let graphRevision;
    let lastGeometryKey;
    let settledGeometryKey;
    let lastContributionKey = '';
    let settleTimer;
    let hydrationFrame;
    let disposed = false;
    let failed = false;
    const hydratedNodeIds = new Set();
    const pendingHydrationNodeIds = new Set();

    function cancelSettleTimer() {
        if (settleTimer === undefined) return;
        clearTimeout(settleTimer);
        settleTimer = undefined;
    }

    function cancelHydrationFrame() {
        if (hydrationFrame === undefined) return;
        cancelAnimationFrame(hydrationFrame);
        hydrationFrame = undefined;
    }

    function stopScheduledWork() {
        cancelSettleTimer();
        cancelHydrationFrame();
    }

    function runCleanup(action, operation) {
        try {
            action?.();
        } catch (error) {
            console.error(
                `[Eclipse] Failed to ${operation} Vue node viewport virtualization.`,
                error
            );
        }
    }

    function failOpen(error) {
        if (failed || disposed) return;
        failed = true;
        stopScheduledWork();
        const stop = unsubscribe;
        unsubscribe = undefined;
        runCleanup(stop, 'unsubscribe');
        runCleanup(() => controller?.clear(), 'clear');
        runCleanup(() => controller?.dispose(), 'dispose');
        console.error(
            '[Eclipse] Vue node viewport virtualization failed open; all nodes will render.',
            error
        );
    }

    function setSuppressedNodeIds(nodeIds) {
        const contributionKey = JSON.stringify(nodeIds);
        if (contributionKey === lastContributionKey) return;
        lastContributionKey = contributionKey;
        try {
            if (nodeIds.length === 0) controller.clear();
            else controller.update({ suppress: nodeIds });
        } catch (error) {
            failOpen(error);
        }
    }

    function applySnapshot(snapshot) {
        if (failed || disposed) return;
        if (!snapshot.managerAvailable ||
            !isValidArea(snapshot.visibleCanvasArea)) {
            setSuppressedNodeIds([]);
            return;
        }
        if (snapshot.renderFrozen) return;
        const knownNodeIds = new Set(snapshot.nodeIds.map(String));
        const areasByNodeId = new Map();
        for (const entry of snapshot.renderAreas) {
            if (entry?.id == null) continue;
            areasByNodeId.set(String(entry.id), entry.area);
        }
        const suppressedNodeIds = [];
        for (const nodeId of snapshot.nodeIds.map(String)) {
            const area = areasByNodeId.get(nodeId);
            if (!hydratedNodeIds.has(nodeId) || !isValidArea(area)) continue;
            if (!areasIntersect(snapshot.visibleCanvasArea, area)) {
                suppressedNodeIds.push(nodeId);
            }
        }
        for (const nodeId of hydratedNodeIds) {
            if (!knownNodeIds.has(nodeId)) hydratedNodeIds.delete(nodeId);
        }
        setSuppressedNodeIds(suppressedNodeIds);
    }

    function applyLatestSnapshot() {
        if (!latestSnapshot) return;
        try {
            applySnapshot(latestSnapshot);
        } catch (error) {
            failOpen(error);
        }
    }

    function scheduleSettledApply(geometryKey) {
        cancelSettleTimer();
        settleTimer = setTimeout(() => {
            settleTimer = undefined;
            if (geometryKey !== lastGeometryKey) return;
            settledGeometryKey = geometryKey;
            applyLatestSnapshot();
        }, VIEWPORT_SETTLE_DELAY);
    }

    function scheduleHydration() {
        cancelHydrationFrame();
        hydrationFrame = requestAnimationFrame(() => {
            hydrationFrame = requestAnimationFrame(() => {
                hydrationFrame = undefined;
                for (const nodeId of pendingHydrationNodeIds) {
                    hydratedNodeIds.add(nodeId);
                }
                pendingHydrationNodeIds.clear();
                applyLatestSnapshot();
            });
        });
    }

    function pruneHydrationState(nodeIds) {
        const knownNodeIds = new Set(nodeIds.map(String));
        for (const nodeId of hydratedNodeIds) {
            if (!knownNodeIds.has(nodeId)) hydratedNodeIds.delete(nodeId);
        }
        for (const nodeId of pendingHydrationNodeIds) {
            if (!knownNodeIds.has(nodeId)) {
                pendingHydrationNodeIds.delete(nodeId);
            }
        }
    }

    function queueInitializedNodes(snapshot) {
        let changed = false;
        const knownNodeIds = new Set(snapshot.nodeIds.map(String));
        for (const rawNodeId of snapshot.initializedNodeIds) {
            const nodeId = String(rawNodeId);
            if (!knownNodeIds.has(nodeId)) continue;
            if (hydratedNodeIds.has(nodeId) ||
                pendingHydrationNodeIds.has(nodeId)) continue;
            pendingHydrationNodeIds.add(nodeId);
            changed = true;
        }
        if (changed) scheduleHydration();
    }

    function resetGraphState(nextGraphRevision) {
        stopScheduledWork();
        graphRevision = nextGraphRevision;
        lastGeometryKey = undefined;
        settledGeometryKey = undefined;
        lastContributionKey = '';
        hydratedNodeIds.clear();
        pendingHydrationNodeIds.clear();
    }

    function handleSnapshot(snapshot) {
        if (failed || disposed) return;
        try {
            if (!snapshot ||
                !Array.isArray(snapshot.nodeIds) ||
                !Array.isArray(snapshot.renderAreas) ||
                !Array.isArray(snapshot.initializedNodeIds)) {
                throw new Error('Invalid Vue node rendering snapshot');
            }
            const wasFrozen = latestSnapshot?.renderFrozen === true;
            latestSnapshot = snapshot;
            if (graphRevision !== snapshot.graphRevision) {
                resetGraphState(snapshot.graphRevision);
            }
            pruneHydrationState(snapshot.nodeIds);
            queueInitializedNodes(snapshot);
            const geometryKey = snapshotGeometryKey(snapshot);
            if (!snapshot.managerAvailable ||
                !isValidArea(snapshot.visibleCanvasArea)) {
                cancelSettleTimer();
                lastGeometryKey = geometryKey;
                settledGeometryKey = geometryKey;
                applyLatestSnapshot();
                return;
            }
            if (geometryKey !== lastGeometryKey) {
                lastGeometryKey = geometryKey;
                settledGeometryKey = undefined;
                scheduleSettledApply(geometryKey);
            }
            if (wasFrozen && !snapshot.renderFrozen &&
                settledGeometryKey === geometryKey) {
                applyLatestSnapshot();
            }
        } catch (error) {
            failOpen(error);
        }
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        stopScheduledWork();
        const stop = unsubscribe;
        unsubscribe = undefined;
        runCleanup(stop, 'unsubscribe');
        runCleanup(() => controller?.clear(), 'clear');
        runCleanup(() => controller?.dispose(), 'dispose');
    }

    try {
        controller = rendering.createPushController(CONTROLLER_OWNER);
        const stop = rendering.subscribe(handleSnapshot);
        if (failed) stop();
        else unsubscribe = stop;
    } catch (error) {
        failOpen(error);
    }

    return Object.freeze({ dispose });
}

let activeVirtualization = null;

function disableViewportVirtualization() {
    activeVirtualization?.dispose();
    activeVirtualization = null;
}

function enableViewportVirtualization(rendering) {
    if (activeVirtualization) return;
    try {
        activeVirtualization = createViewportVirtualization(rendering);
    } catch (error) {
        console.error(
            '[Eclipse] Failed to initialize Vue node viewport virtualization.',
            error
        );
    }
}

app.registerExtension({
    name: 'Eclipse.VueNodeViewportVirtualization',
    async init(appRef) {
        const rendering = appRef.extensionManager?.vueNodes?.rendering;
        if (!isRenderingApi(rendering) ||
            hasNativeVueNodeSetting(appRef, viewportVirtualizationSetting)) {
            return;
        }
        try {
            const resolved = resolveCanonicalVueNodeSetting(
                appRef,
                viewportVirtualizationSetting,
                {}
            );
            appRef.ui.settings.addSetting(createCanonicalVueNodeSetting(
                viewportVirtualizationSetting,
                (value) => {
                    if (value === true) {
                        enableViewportVirtualization(rendering);
                    } else {
                        disableViewportVirtualization();
                    }
                }
            ));
            if (resolved.value === true) {
                enableViewportVirtualization(rendering);
            } else {
                disableViewportVirtualization();
            }
            await persistCanonicalVueNodeSetting(
                appRef,
                viewportVirtualizationSetting,
                resolved
            );
        } catch (error) {
            disableViewportVirtualization();
            console.error(
                '[Eclipse] Failed to initialize Vue node viewport virtualization.',
                error
            );
        }
    },
});
