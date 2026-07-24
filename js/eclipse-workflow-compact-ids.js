/**
 * ComfyUI Eclipse action-bar command for compacting workflow IDs.
 * SPDX-License-Identifier: Apache-2.0
 */

import { app } from './comfy/index.js';
import { normalizeWorkflowIds } from './eclipse-workflow-id-utils.js';

const COMMAND_ID = 'Eclipse.Workflow.CompactIds';
const SUMMARY = 'Eclipse Compact IDs';

function showToast(severity, detail, life = 5000) {
    app.extensionManager?.toast?.add?.({
        severity,
        summary: SUMMARY,
        detail,
        life,
    });
}

function getRootGraph() {
    return app.rootGraph ?? app.graph?.rootGraph ?? app.graph ?? null;
}

function snapshotGraph(rootGraph) {
    if (typeof rootGraph?.serialize !== 'function') {
        throw new Error('The active workflow cannot be serialized by this ComfyUI version.');
    }
    return JSON.parse(JSON.stringify(rootGraph.serialize()));
}

function captureView(rootGraph) {
    const canvas = app.canvas;
    const activeGraph = canvas?.subgraph ?? canvas?.graph;
    const subgraphId = activeGraph && activeGraph !== rootGraph ? activeGraph.id : null;
    const scale = canvas?.ds?.scale;
    const offset = canvas?.ds?.offset;
    return {
        subgraphId,
        scale: Number.isFinite(scale) ? scale : null,
        offset: Array.isArray(offset) && offset.length >= 2 ? [offset[0], offset[1]] : null,
    };
}

function restoreView(rootGraph, view) {
    const canvas = app.canvas;
    if (!canvas) return;

    if (typeof canvas.setGraph === 'function') {
        const activeGraph = view.subgraphId ? rootGraph.subgraphs?.get?.(view.subgraphId) : rootGraph;
        canvas.setGraph(activeGraph ?? rootGraph);
    }
    if (view.scale !== null && canvas.ds) canvas.ds.scale = view.scale;
    if (view.offset && canvas.ds?.offset) {
        canvas.ds.offset[0] = view.offset[0];
        canvas.ds.offset[1] = view.offset[1];
    }
    canvas.setDirty?.(true, true);
}

function formatSuccess(result) {
    const nodeLabel = result.nodeCount === 1 ? 'node ID' : 'node IDs';
    const linkLabel = result.linkCount === 1 ? 'link ID' : 'link IDs';
    const preserved = result.stringNodeCount
        ? ` Preserved ${result.stringNodeCount} special string node ${result.stringNodeCount === 1 ? 'ID' : 'IDs'}.`
        : '';
    const repairs = [];
    if (result.duplicateLinkCount) repairs.push(`repaired ${result.duplicateLinkCount} duplicate link IDs`);
    if (result.removedLinkCount) repairs.push(`removed ${result.removedLinkCount} orphaned links`);
    if (result.removedLinkReferenceCount) {
        repairs.push(`removed ${result.removedLinkReferenceCount} stale slot references`);
    }
    if (result.removedRerouteCount) repairs.push(`removed ${result.removedRerouteCount} orphaned reroutes`);
    const repaired = repairs.length ? ` Also ${repairs.join(', ')}.` : '';
    return `Compacted ${result.nodeCount} ${nodeLabel} in depth-first saved canvas order and ${result.linkCount} ${linkLabel}.${preserved}${repaired} Use Save to write the workflow file.`;
}

export function compactActiveWorkflowIds() {
    const rootGraph = getRootGraph();
    if (!rootGraph) {
        showToast('error', 'No active workflow graph is available. No changes were applied.');
        return false;
    }

    let original;
    let normalized;
    try {
        if (typeof rootGraph.configure !== 'function' ||
            typeof rootGraph.beforeChange !== 'function' ||
            typeof rootGraph.afterChange !== 'function') {
            throw new Error('This ComfyUI version does not expose the required graph change transaction API');
        }
        original = snapshotGraph(rootGraph);
        normalized = normalizeWorkflowIds(original);
    } catch (error) {
        console.error('[Eclipse Compact IDs] Workflow validation failed:', error);
        showToast('error', `Validation failed: ${error.message}. No changes were applied.`, 8000);
        return false;
    }

    if (!normalized.changed) {
        showToast('info', 'Node IDs already follow depth-first saved canvas order; links, counters, and references are clean and compact.');
        return false;
    }

    const view = captureView(rootGraph);
    let transactionStarted = false;
    try {
        rootGraph.beforeChange();
        transactionStarted = true;
        rootGraph.configure(normalized.workflow);
        restoreView(rootGraph, view);

        const postResult = normalizeWorkflowIds(snapshotGraph(rootGraph));
        if (postResult.changed ||
            postResult.nodeCount !== normalized.nodeCount ||
            postResult.linkCount !== normalized.linkCount) {
            throw new Error('post-configuration validation did not reproduce the compact workflow');
        }
    } catch (error) {
        console.error('[Eclipse Compact IDs] Apply failed; restoring the original workflow:', error);
        let rollbackError = null;
        try {
            rootGraph.configure(original);
            restoreView(rootGraph, view);
        } catch (caught) {
            rollbackError = caught;
            console.error('[Eclipse Compact IDs] Rollback failed:', caught);
        }

        if (transactionStarted) {
            try {
                rootGraph.afterChange();
            } catch (caught) {
                console.error('[Eclipse Compact IDs] Failed to close change transaction:', caught);
            }
        }
        const rollbackDetail = rollbackError
            ? ` Rollback also failed: ${rollbackError.message}`
            : ' The original workflow was restored.';
        showToast('error', `Compaction failed: ${error.message}.${rollbackDetail}`, 10000);
        return false;
    }

    if (transactionStarted) rootGraph.afterChange();
    app.canvas?.setDirty?.(true, true);
    showToast('success', formatSuccess(normalized));
    return true;
}

app.registerExtension({
    name: 'Eclipse.Workflow.CompactIds',
    commands: [{
        id: COMMAND_ID,
        label: 'Compact workflow IDs',
        icon: 'pi pi-sort-numeric-down',
        tooltip: 'Compact workflow IDs by depth-first saved canvas order and repair stale links',
        function: compactActiveWorkflowIds,
    }],
    actionBarButtons: [{
        icon: 'pi pi-sort-numeric-down',
        label: 'Compact IDs',
        tooltip: 'Compact workflow IDs by depth-first saved canvas order and repair duplicate or stale links',
        onClick: compactActiveWorkflowIds,
    }],
});
