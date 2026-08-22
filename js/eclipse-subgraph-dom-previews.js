/**
 * Eclipse — host-owned image and text previews for current ComfyUI subgraphs.
 */

import { api, app } from './comfy/index.js';
import { registerPreviewWidgetForCulling } from './eclipse-preview-culling.js';
import { onVueModeChange } from './eclipse-widget-performance-utils.js';

export const ECLIPSE_SUBGRAPH_PREVIEW_PROPERTY = 'eclipseDomPreviewExposures';

const DOM_IMAGE_PREVIEW_NAME = '_eclipse_dom_preview';
const providersByNode = new WeakMap();
const projectionsByHost = new WeakMap();
const legacyMountObserversByHost = new WeakMap();
const legacyRestoreValuesByHost = new WeakMap();
let executionListenerInstalled = false;
let vueModeListenerInstalled = false;

function getAppGraph() {
    try {
        return app.graph;
    } catch {
        return null;
    }
}

function getProviderMap(node, create = false) {
    let providers = providersByNode.get(node);
    if (!providers && create) {
        providers = new Map();
        providersByNode.set(node, providers);
    }
    return providers;
}

function findNode(graph, nodeId) {
    const direct = graph?.getNodeById?.(nodeId);
    if (direct) return direct;
    return graph?._nodes?.find(node => String(node.id) === String(nodeId)) ?? null;
}

function supportsDirectSubgraphWidgets(host) {
    let prototype = Object.getPrototypeOf(host);
    while (prototype) {
        if (Object.prototype.hasOwnProperty.call(prototype, 'isSubgraphNode')) {
            return Object.prototype.hasOwnProperty.call(prototype, 'addCustomWidget');
        }
        prototype = Object.getPrototypeOf(prototype);
    }
    return false;
}

function isExposure(entry) {
    return !!entry && typeof entry === 'object'
        && typeof entry.sourceNodeId === 'string'
        && typeof entry.sourcePreviewName === 'string'
        && (entry.kind === 'image' || entry.kind === 'text');
}

function exposureKey(entry) {
    return `${entry.kind}\u0000${entry.sourceNodeId}\u0000${entry.sourcePreviewName}`;
}

function projectionName(entry) {
    const source = encodeURIComponent(entry.sourceNodeId);
    const preview = encodeURIComponent(entry.sourcePreviewName);
    return `_eclipse_subgraph_${entry.kind}_${source}_${preview}`;
}

function getExposureEntries(host) {
    const value = host.properties?.[ECLIPSE_SUBGRAPH_PREVIEW_PROPERTY];
    return Array.isArray(value) ? value : [];
}

function getLegacyProxyEntries(host) {
    let value = host.properties?.proxyWidgets;
    if (typeof value === 'string') {
        try {
            value = JSON.parse(value);
        } catch {
            return [];
        }
    }
    return Array.isArray(value) ? value.filter(entry => Array.isArray(entry)) : [];
}

function getLegacyPromotedWidgets(host, sourceNodeId) {
    const result = {};
    for (const widget of host.widgets || []) {
        if (String(widget?.sourceNodeId) !== sourceNodeId) continue;
        if (!['folder_source', 'image', 'output_image'].includes(widget.sourceWidgetName)) continue;
        result[widget.sourceWidgetName] = widget;
    }
    return result;
}

function disambiguateLegacyPreviewViews(host) {
    const views = (host.widgets || []).filter(widget => (
        widget?.sourceNodeId != null
        && widget.sourceWidgetName === DOM_IMAGE_PREVIEW_NAME
    ));
    if (views.length < 2) return views;
    for (const view of views) {
        const sourceNodeId = encodeURIComponent(String(view.sourceNodeId));
        const runtimeName = `${DOM_IMAGE_PREVIEW_NAME}__source_${sourceNodeId}`;
        if (view.name === runtimeName) continue;
        // Frontend 1.46.2 exposes name through a prototype getter. An own,
        // non-enumerable value changes only this runtime view's Vue identity.
        Object.defineProperty(view, 'name', {
            configurable: true,
            enumerable: false,
            value: runtimeName,
            writable: true,
        });
    }
    return views;
}

function disconnectLegacyMountObserver(host) {
    legacyMountObserversByHost.get(host)?.disconnect();
    legacyMountObserversByHost.delete(host);
}

function isLegacyVueMode() {
    return !!globalThis.LiteGraph?.vueNodesMode;
}

function getVisibleLegacyWidgets(host) {
    const showAdvanced = !!host.showAdvanced
        || !!app.ui?.settings?.getSettingValue?.('Comfy.Node.AlwaysShowAdvancedWidgets');
    return (host.widgets || []).filter(widget => {
        if (!widget?.type || widget.options?.canvasOnly) return false;
        if (widget.options?.hidden ?? widget.hidden ?? false) return false;
        return !(widget.options?.advanced ?? widget.advanced ?? false) || showAdvanced;
    });
}

function getVisibleLegacyDOMWidgets(host) {
    const result = [];
    for (const widget of getVisibleLegacyWidgets(host)) {
        const sourceNode = widget?.sourceNodeId == null
            ? null
            : findNode(host.subgraph, widget.sourceNodeId);
        const sourceWidget = sourceNode?.widgets?.find(candidate => (
            candidate.name === widget.sourceWidgetName
        ));
        if (sourceWidget?.element?.nodeType === 1) {
            result.push({ sourceWidget, view: widget });
        }
    }
    return result;
}

function findLegacyVueDOMMountPoints(host) {
    if (typeof document === 'undefined') return [];
    const widgetsRoot = [...document.querySelectorAll('[data-testid="node-widgets"]')]
        .find(element => (
            element.closest('[data-node-id]')?.dataset.nodeId === String(host.id)
        ));
    if (!widgetsRoot) return [];
    return [...widgetsRoot.querySelectorAll('[data-testid="node-widget"]')]
        .filter(element => (
            element.closest?.('[data-testid="node-widgets"]') === widgetsRoot
        ))
        .map(row => row.lastElementChild)
        .filter(element => (
            element?.classList?.contains('flex')
            && element.classList.contains('flex-col')
        ));
}

function mountLegacyPreviewViews(host, views) {
    if (!isLegacyVueMode() || app.canvas?.graph !== host.graph) return false;
    const visibleDOMWidgets = getVisibleLegacyDOMWidgets(host);
    const mountPoints = findLegacyVueDOMMountPoints(host);
    if (!mountPoints.length || mountPoints.length !== visibleDOMWidgets.length) return false;

    // 1.46.2's Vue adapter passes the shared concrete widget name back into
    // its host lookup. Bind the already disambiguated rows by source instead.
    const mounts = [];
    for (const view of views) {
        const index = visibleDOMWidgets.findIndex(entry => entry.view === view);
        const resolved = visibleDOMWidgets[index];
        const mountPoint = mountPoints[index];
        const sourceWidget = resolved?.sourceWidget;
        if (!mountPoint || sourceWidget?.element?.nodeType !== 1) return false;
        mounts.push([mountPoint, sourceWidget.element]);
    }

    for (const [mountPoint, element] of mounts) {
        if (!mountPoint.contains(element)) mountPoint.replaceChildren(element);
    }
    return true;
}

function scheduleLegacyPreviewMount(host, views) {
    if (typeof MutationObserver === 'undefined' || typeof document === 'undefined'
        || !isLegacyVueMode()) {
        disconnectLegacyMountObserver(host);
        return;
    }
    mountLegacyPreviewViews(host, views);
    if (legacyMountObserversByHost.has(host)) return;
    const observer = new MutationObserver(() => {
        if (!isLegacyVueMode() || app.canvas?.graph !== host.graph
            || !host.graph?._nodes?.includes(host)) {
            disconnectLegacyMountObserver(host);
            return;
        }
        const currentViews = disambiguateLegacyPreviewViews(host);
        if (currentViews.length < 2) {
            disconnectLegacyMountObserver(host);
            return;
        }
        mountLegacyPreviewViews(host, currentViews);
    });
    legacyMountObserversByHost.set(host, observer);
    observer.observe(document.body || document.documentElement, {
        childList: true,
        subtree: true,
    });
}

function reconcileLegacyPreviewHost(host) {
    if (!host?.isSubgraphNode?.() || !host.subgraph
        || supportsDirectSubgraphWidgets(host)) {
        disconnectLegacyMountObserver(host);
        return [];
    }
    const proxyEntries = getLegacyProxyEntries(host);
    if (proxyEntries.length) {
        const views = disambiguateLegacyPreviewViews(host);
        if (views.length > 1) scheduleLegacyPreviewMount(host, views);
        else disconnectLegacyMountObserver(host);
    } else {
        disconnectLegacyMountObserver(host);
    }
    return proxyEntries;
}

function hasPopulatedLegacyFilename(widgets) {
    return ['image', 'output_image'].some(name => {
        const value = widgets[name]?.value;
        return typeof value === 'string' && value !== '' && value !== 'none';
    });
}

function legacyRestoreValue(widgets) {
    return JSON.stringify(['folder_source', 'image', 'output_image'].map(name => (
        widgets[name]?.value ?? null
    )));
}

function findExposure(host, sourceNode, provider) {
    return getExposureEntries(host).find(entry => isExposure(entry)
        && entry.sourceNodeId === String(sourceNode.id)
        && entry.sourcePreviewName === provider.name
        && entry.kind === provider.kind);
}

function removeProjection(host, key, runtime) {
    const providers = getProviderMap(host);
    if (providers?.get(runtime.exposedProvider.name) === runtime.exposedProvider) {
        providers.delete(runtime.exposedProvider.name);
    }
    if (runtime.controller.widget && host.removeWidget) {
        host.removeWidget(runtime.controller.widget);
    } else {
        runtime.controller.dispose?.();
    }
    projectionsByHost.get(host)?.delete(key);
}

function setProjectionValue(runtime, value) {
    runtime.value = value;
    runtime.controller.setValue(value);
}

function createProjection(host, entry, sourceNode, provider) {
    const name = projectionName(entry);
    const controller = provider.createProjection(host, {
        kind: provider.kind,
        label: provider.label,
        name,
    });
    if (!controller?.widget || typeof controller.setValue !== 'function') return null;

    registerPreviewWidgetForCulling(host, controller.widget);
    const runtime = {
        controller,
        entry,
        exposedProvider: null,
        provider,
        sourceNode,
        value: undefined,
    };
    runtime.exposedProvider = {
        createProjection: provider.createProjection,
        getCurrentValue: () => runtime.value,
        kind: provider.kind,
        label: `${sourceNode.title || sourceNode.type || sourceNode.id}: ${provider.label}`,
        name,
        readOutput: provider.readOutput,
    };
    getProviderMap(host, true).set(name, runtime.exposedProvider);

    const currentValue = provider.getCurrentValue?.();
    if (currentValue !== undefined) setProjectionValue(runtime, currentValue);
    return runtime;
}

export function reconcileSubgraphDOMPreviewHost(host, options = {}) {
    if (!host?.isSubgraphNode?.() || !host.subgraph
        || !supportsDirectSubgraphWidgets(host)) return;
    const preservedSize = options.preserveSize === false || !host.size
        ? null
        : [host.size[0], host.size[1]];
    const runtimes = projectionsByHost.get(host) ?? new Map();
    projectionsByHost.set(host, runtimes);
    const desired = new Map();

    for (const entry of getExposureEntries(host)) {
        if (!isExposure(entry)) continue;
        const sourceNode = findNode(host.subgraph, entry.sourceNodeId);
        const provider = getProviderMap(sourceNode)?.get(entry.sourcePreviewName);
        if (!sourceNode || !provider || provider.kind !== entry.kind) continue;
        desired.set(exposureKey(entry), { entry, provider, sourceNode });
    }

    for (const [key, runtime] of [...runtimes]) {
        const match = desired.get(key);
        if (!match || match.provider !== runtime.provider || match.sourceNode !== runtime.sourceNode) {
            removeProjection(host, key, runtime);
        }
    }

    for (const [key, value] of desired) {
        if (runtimes.has(key)) continue;
        const runtime = createProjection(host, value.entry, value.sourceNode, value.provider);
        if (runtime) runtimes.set(key, runtime);
    }
    if (preservedSize) {
        host.size[0] = preservedSize[0];
        host.size[1] = preservedSize[1];
    }
}

function reconcileGraph(graph, visited = new Set()) {
    if (!graph || visited.has(graph)) return;
    visited.add(graph);
    for (const node of graph._nodes || []) {
        if (node.subgraph) reconcileGraph(node.subgraph, visited);
    }
    for (const node of graph._nodes || []) {
        if (node.isSubgraphNode?.()) reconcileSubgraphDOMPreviewHost(node);
    }
}

function reconcileLegacyGraph(graph, visited = new Set()) {
    if (!graph || visited.has(graph)) return;
    visited.add(graph);
    for (const node of graph._nodes || []) {
        reconcileLegacyGraph(node.subgraph, visited);
    }
    for (const node of graph._nodes || []) reconcileLegacyPreviewHost(node);
}

async function restoreLegacyHost(host) {
    const proxyEntries = reconcileLegacyPreviewHost(host);
    if (!proxyEntries.length) return;
    const restores = legacyRestoreValuesByHost.get(host) ?? new Map();
    legacyRestoreValuesByHost.set(host, restores);
    const pending = [];

    for (const entry of proxyEntries) {
        const [rawSourceNodeId, previewName] = entry;
        if (previewName !== DOM_IMAGE_PREVIEW_NAME) continue;
        const sourceNodeId = String(rawSourceNodeId);
        const sourceNode = findNode(host.subgraph, sourceNodeId);
        const provider = getProviderMap(sourceNode)?.get(previewName);
        if (!sourceNode || typeof provider?.restoreLegacyHost !== 'function') continue;
        const promotedWidgets = getLegacyPromotedWidgets(host, sourceNodeId);
        if (!hasPopulatedLegacyFilename(promotedWidgets)) continue;
        const key = `${sourceNodeId}\u0000${previewName}`;
        const value = legacyRestoreValue(promotedWidgets);
        const previous = restores.get(key);
        if (previous?.provider === provider && previous.value === value) continue;
        restores.set(key, { provider, value });
        pending.push(Promise.resolve(provider.restoreLegacyHost({
            host,
            promotedWidgets,
            sourceNode,
        })).then(() => {
            host.setDirtyCanvas?.(true, true);
            host.graph?.setDirtyCanvas?.(true, true);
        }).catch(error => {
            const current = restores.get(key);
            if (current?.provider === provider && current.value === value) restores.delete(key);
            console.error('[Eclipse] Failed to restore a legacy subgraph DOM preview:', error);
        }));
    }
    await Promise.all(pending);
}

async function restoreLegacyGraph(graph, visited = new Set()) {
    if (!graph || visited.has(graph)) return;
    visited.add(graph);
    for (const node of graph._nodes || []) {
        await restoreLegacyGraph(node.subgraph, visited);
    }
    await Promise.all((graph._nodes || []).map(node => restoreLegacyHost(node)));
}

export function reconcileSubgraphDOMPreviews() {
    const graph = getAppGraph();
    const root = graph?.rootGraph || graph;
    reconcileGraph(root);
    reconcileLegacyGraph(root);
}

function collectGraphs(graph, result = [], visited = new Set()) {
    if (!graph || visited.has(graph)) return result;
    visited.add(graph);
    result.push(graph);
    for (const node of graph._nodes || []) collectGraphs(node.subgraph, result, visited);
    return result;
}

function parentHostsFor(node) {
    const appGraph = getAppGraph();
    const root = node?.graph?.rootGraph || appGraph?.rootGraph || appGraph;
    if (!node?.graph || !root) return [];
    const hosts = [];
    for (const graph of collectGraphs(root)) {
        for (const candidate of graph._nodes || []) {
            if (candidate.subgraph === node.graph) hosts.push(candidate);
        }
    }
    return hosts;
}

function broadcastProviderValue(sourceNode, provider, value, visited = new Set()) {
    if (visited.has(provider)) return;
    visited.add(provider);
    for (const host of parentHostsFor(sourceNode)) {
        const entry = findExposure(host, sourceNode, provider);
        if (!entry) continue;
        reconcileSubgraphDOMPreviewHost(host);
        const runtime = projectionsByHost.get(host)?.get(exposureKey(entry));
        if (!runtime) continue;
        setProjectionValue(runtime, value);
        broadcastProviderValue(host, runtime.exposedProvider, value, visited);
    }
}

export function publishSubgraphDOMPreview(node, previewName, value) {
    const provider = getProviderMap(node)?.get(previewName);
    if (provider) broadcastProviderValue(node, provider, value);
}

export function registerSubgraphDOMPreviewProvider(node, provider) {
    if (!node || !provider?.name || !provider?.label
        || !['image', 'text'].includes(provider.kind)
        || typeof provider.createProjection !== 'function'
        || typeof provider.readOutput !== 'function'
        || (provider.restoreLegacyHost !== undefined
            && typeof provider.restoreLegacyHost !== 'function')) {
        throw new Error('Invalid Eclipse subgraph DOM preview provider');
    }
    const providers = getProviderMap(node, true);
    providers.set(provider.name, provider);
    queueMicrotask(reconcileSubgraphDOMPreviews);
    return () => {
        if (providers.get(provider.name) === provider) providers.delete(provider.name);
        queueMicrotask(reconcileSubgraphDOMPreviews);
    };
}

function resolveExecutionPath(locator) {
    const parts = String(locator ?? '').split(':').filter(Boolean);
    if (parts.length < 2) return null;
    const appGraph = getAppGraph();
    let graph = appGraph?.rootGraph || appGraph;
    const hosts = [];
    for (const part of parts.slice(0, -1)) {
        const host = findNode(graph, part);
        if (!host?.subgraph) return null;
        hosts.push(host);
        graph = host.subgraph;
    }
    const sourceNode = findNode(graph, parts.at(-1));
    return sourceNode ? { hosts, sourceNode } : null;
}

function routeExecution(detail) {
    const path = resolveExecutionPath(detail?.display_node ?? detail?.node);
    if (!path) return;
    const sourceProviders = getProviderMap(path.sourceNode);
    if (!sourceProviders) return;

    for (const sourceProvider of sourceProviders.values()) {
        let provider = sourceProvider;
        let sourceNode = path.sourceNode;
        const value = provider.readOutput(detail.output);
        for (let index = path.hosts.length - 1; index >= 0; index--) {
            const host = path.hosts[index];
            const entry = findExposure(host, sourceNode, provider);
            if (!entry) break;
            reconcileSubgraphDOMPreviewHost(host);
            const runtime = projectionsByHost.get(host)?.get(exposureKey(entry));
            if (!runtime) break;
            setProjectionValue(runtime, value);
            sourceNode = host;
            provider = runtime.exposedProvider;
        }
    }
}

function toggleExposure(host, sourceNode, provider) {
    const current = getExposureEntries(host);
    const existing = findExposure(host, sourceNode, provider);
    const next = existing
        ? current.filter(entry => entry !== existing)
        : [...current, {
            sourceNodeId: String(sourceNode.id),
            sourcePreviewName: provider.name,
            kind: provider.kind,
        }];
    const graph = host.graph;
    graph?.beforeChange?.();
    host.properties ??= {};
    host.properties[ECLIPSE_SUBGRAPH_PREVIEW_PROPERTY] = next;
    reconcileSubgraphDOMPreviewHost(host, { preserveSize: false });
    graph?.afterChange?.();
    host.setDirtyCanvas?.(true, true);
}

function getMenuItems(host) {
    if (!host?.isSubgraphNode?.() || !host.subgraph
        || !supportsDirectSubgraphWidgets(host)) return [];
    reconcileGraph(host.subgraph);
    const options = [];
    for (const sourceNode of host.subgraph._nodes || []) {
        for (const provider of getProviderMap(sourceNode)?.values() || []) {
            const selected = !!findExposure(host, sourceNode, provider);
            options.push({
                content: `${selected ? '✓' : '\u2003'} ${sourceNode.title || sourceNode.type || sourceNode.id}: ${provider.label}`,
                callback: () => toggleExposure(host, sourceNode, provider),
            });
        }
    }
    options.sort((left, right) => left.content.localeCompare(right.content));
    if (!options.length) return [];
    return [null, {
        content: 'DOM previews',
        has_submenu: true,
        submenu: {
            title: 'Eclipse DOM previews',
            options,
        },
    }];
}

(window._eclipseMenuProviders ??= []).push(getMenuItems);

app.registerExtension({
    name: 'Eclipse.SubgraphDOMPreviews',
    setup() {
        if (!executionListenerInstalled) {
            executionListenerInstalled = true;
            api.addEventListener('executed', event => routeExecution(event.detail));
        }
        if (!vueModeListenerInstalled) {
            vueModeListenerInstalled = true;
            onVueModeChange(() => queueMicrotask(reconcileSubgraphDOMPreviews));
        }
    },
    nodeCreated(node) {
        if (node.isSubgraphNode?.()) queueMicrotask(reconcileSubgraphDOMPreviews);
    },
    loadedGraphNode(node) {
        if (node.isSubgraphNode?.()) queueMicrotask(reconcileSubgraphDOMPreviews);
    },
    async afterConfigureGraph() {
        reconcileSubgraphDOMPreviews();
        const graph = getAppGraph();
        await restoreLegacyGraph(graph?.rootGraph || graph);
    },
});
