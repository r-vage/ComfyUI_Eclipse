/**
 * eclipse-image-selector.js
 *
 * Image Selector [Eclipse] — interactive image picker that pauses a workflow.
 *
 * First run: node shows all images in a grid with selection overlays.
 *   - Click image = toggle selection (highlighted border + checkmark)
 *   - Ctrl+A = select all, Escape = deselect all
 *   - Toolbar: [All] [Auto select and confirm] [Discard ✕] [Confirm (N) →]
 *   - Confirm POSTs indices to /eclipse/image_selector/confirm
 *     → automatically re-queues
 *   - Discard clears the decision while keeping the grid available
 *
 * Selection order and preview references are saved in workflow properties.
 * Reload validates the backend generation without confirming or queuing work.
 */

import { app, api } from './comfy/index.js';
import { showEclipseToast } from './eclipse-notifications.js';
import { createDOMPreview } from './eclipse-dom-preview.js';
import {
    findSetterByName,
    getGraphAncestors,
    getLink,
    isSetterPathToRootActive,
    resolveBypassedLink,
} from './eclipse-set-get-utils.js';
import {
    createWidgetVisibilityManager,
    isConfiguringGraph,
    isVueMode,
    notifyVue,
    onVueModeChange,
} from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'Image Selector [Eclipse]';
const SELECTOR_MIN_HEIGHT = 220;
const SPECIAL_SEED_VALUES = Object.freeze([-1, -2, -3]);
const SPECIAL_INDEX_VALUES = Object.freeze([-1, -2, -3, -4]);
const ECLIPSE_SEED_ADAPTER = Object.freeze({
    cacheFields: Object.freeze(['_Eclipse_cachedInputSeed', '_Eclipse_cachedResolvedSeed']),
    controlInputName: 'seed',
    dynamicValues: SPECIAL_SEED_VALUES,
    promptValueName: 'seed',
    queuedValueField: '_Eclipse_queuedSeed',
    resolvedValueField: '_Eclipse_lastSeed',
    sourceProvider: true,
    valueLabel: 'seed',
    widgetField: '_Eclipse_seedWidget',
});
const CONTINUATION_VALUE_ADAPTERS = Object.freeze({
    'Seed [Eclipse]': ECLIPSE_SEED_ADAPTER,
    'Smart Sampler Settings [Eclipse]': ECLIPSE_SEED_ADAPTER,
    'Smart Sampler Settings (Legacy) [Eclipse]': ECLIPSE_SEED_ADAPTER,
    'Smart Model Loader [Eclipse]': Object.freeze({
        cacheFields: Object.freeze(['_Eclipse_cachedSeedInput', '_Eclipse_cachedSeedResolved']),
        controlInputName: 'seed',
        dynamicValues: SPECIAL_SEED_VALUES,
        promptValueName: 'seed',
        queuedValueField: '_Eclipse_queuedSeed',
        resolvedValueField: '_Eclipse_lastSeed',
        sourceProvider: true,
        valueLabel: 'seed',
        widgetField: '_Eclipse_seedWidget',
    }),
    'Smart LM Loader [Eclipse]': Object.freeze({
        cacheFields: Object.freeze(['_SmartLLM_cachedInputSeed', '_SmartLLM_cachedResolvedSeed']),
        controlInputName: 'seed',
        dynamicValues: SPECIAL_SEED_VALUES,
        promptValueName: 'seed',
        queuedValueField: '_SmartLLM_queuedSeed',
        resolvedValueField: '_SmartLLM_lastSeed',
        sourceProvider: true,
        valueLabel: 'seed',
        widgetField: '_SmartLLM_seedWidget',
    }),
    'Smart Detection [Eclipse]': Object.freeze({
        cacheFields: Object.freeze([
            '_SmartLLMDetection_cachedInputSeed',
            '_SmartLLMDetection_cachedResolvedSeed',
        ]),
        controlInputName: 'seed',
        dynamicValues: SPECIAL_SEED_VALUES,
        promptValueName: 'seed',
        queuedValueField: '_SmartLLMDetection_queuedSeed',
        resolvedValueField: '_SmartLLMDetection_lastSeed',
        sourceProvider: true,
        valueLabel: 'seed',
        widgetField: '_SmartLLMDetection_seedWidget',
    }),
    'Smart Prompt [Eclipse]': Object.freeze({
        cacheFields: Object.freeze(['_Eclipse_cachedInputSeed', '_Eclipse_cachedResolvedSeed']),
        captureOnSelectorExecution: true,
        controlInputName: 'seed_input',
        dynamicValues: SPECIAL_SEED_VALUES,
        promptValueName: 'seed',
        queuedValueField: '_Eclipse_queuedSeed',
        resolvedValueField: '_Eclipse_lastSeed',
        valueLabel: 'seed',
        widgetField: '_Eclipse_seedWidget',
    }),
    'Wildcard Processor [Eclipse]': Object.freeze({
        cacheFields: Object.freeze(['_Eclipse_cachedInputSeed', '_Eclipse_cachedResolvedSeed']),
        captureOnSelectorExecution: true,
        controlInputName: 'seed_input',
        dynamicValues: SPECIAL_SEED_VALUES,
        isApplicable(node, promptNode = null) {
            const mode = promptNode?.inputs?.mode
                ?? node.widgets?.find(widget => widget.name === 'mode')?.value;
            return mode !== 'fixed';
        },
        promptValueName: 'seed',
        queuedValueField: '_Eclipse_queuedSeed',
        resolvedValueField: '_Eclipse_lastSeed',
        valueLabel: 'seed',
        widgetField: '_Eclipse_seedWidget',
    }),
    'Read Prompt Files [Eclipse]': Object.freeze({
        cacheFields: Object.freeze(['_Eclipse_cachedInputIndex', '_Eclipse_cachedResolvedIndex']),
        captureOnSelectorExecution: true,
        controlInputName: 'seed_input',
        dynamicValues: SPECIAL_INDEX_VALUES,
        promptValueName: 'index',
        resolvedValueField: '_Eclipse_lastResolvedIndex',
        valueLabel: 'index',
        widgetField: '_Eclipse_indexWidget',
    }),
    'Load Image From Folder [Eclipse]': Object.freeze({
        cacheFields: Object.freeze([]),
        captureOnSelectorExecution: true,
        controlInputName: 'seed_input',
        dynamicValues: SPECIAL_INDEX_VALUES,
        promptValueName: 'index',
        resolvedValueField: '_Eclipse_lastResolvedIndex',
        valueLabel: 'index',
        widgetField: '_Eclipse_indexWidget',
    }),
    'Load Image From Folder (Pipe) [Eclipse]': Object.freeze({
        cacheFields: Object.freeze([]),
        captureOnSelectorExecution: true,
        controlInputName: 'seed_input',
        dynamicValues: SPECIAL_INDEX_VALUES,
        promptValueName: 'index',
        resolvedValueField: '_Eclipse_lastResolvedIndex',
        valueLabel: 'index',
        widgetField: '_Eclipse_indexWidget',
    }),
});
const _pendingContinuationDependencySnapshots = new Map();
const _earlyExecutedNodeKeys = new Map();
const _earlyTerminalPromptIds = new Set();
const MAX_EARLY_PROMPT_EVENTS = 32;
const SELECTOR_SNAPSHOT = 'eclipseSelectorSnapshot';

function _selectorNodeKey(node) {
    return _getLiveGraphNodeList(app.rootGraph || app.graph)
        .find(entry => entry.node === node)?.outputKey ?? String(node.id);
}

function _selectorInputState(selector) {
    const entries = _getLiveGraphNodeList(app.rootGraph || app.graph);
    const keys = new Map(entries.map(({ node, outputKey }) => [node, outputKey]));
    const visited = new Set();
    const inputs = [];
    const visit = node => {
        if (!node || visited.has(node)) return;
        visited.add(node);
        const links = (node.inputs || []).map(input => {
            const link = getLink(node.graph, input.link);
            const source = link && _getNodeById(node.graph, link.origin_id);
            visit(source);
            return [input.name, source ? keys.get(source) : null, link?.origin_slot ?? null];
        });
        const virtual = node.type === 'GetNode [Eclipse]' ? _resolveEclipseGetterSource(node)
            : node.type === 'GetNode' ? _resolveKJGetterSource(node) : null;
        visit(virtual?.node);
        inputs.push({
            key: keys.get(node) ?? String(node.id),
            type: node.type,
            mode: node.mode ?? 0,
            links,
            virtual: virtual ? [keys.get(virtual.node), virtual.slot] : null,
            widgets: node === selector ? [] : (node.widgets || [])
                .filter(widget => widget.serialize !== false && widget.options?.serialize !== false)
                .map(widget => [widget.name, widget.value]),
        });
    };
    visit(selector);
    // Keep upstream text/widget payloads out of the selector's saved properties.
    const serialized = JSON.stringify(inputs);
    let hash = 2166136261;
    for (let index = 0; index < serialized.length; index++) {
        hash = Math.imul(hash ^ serialized.charCodeAt(index), 16777619);
    }
    return `${serialized.length}:${hash >>> 0}`;
}

function _savedContinuationDependencies(node) {
    const keys = new Map(_getLiveGraphNodeList(app.rootGraph || app.graph)
        .map(({ node, outputKey }) => [node, outputKey]));
    return (node._eclipseSelectorContinuationDependencies || []).map(dependency => ({
        outputKey: dependency.outputKey,
        type: dependency.type,
        resolvedValue: dependency.resolvedValue,
        directBranch: dependency.directBranch,
        connections: dependency.connections.map(connection => ({
            consumerKey: keys.get(connection.consumerNode),
            sourceKey: keys.get(connection.sourceNode),
        })),
    }));
}

function _restoreContinuationDependencies(node, saved) {
    const nodes = new Map(_getLiveGraphNodeList(app.rootGraph || app.graph)
        .map(({ node, outputKey }) => [outputKey, node]));
    node._eclipseSelectorContinuationDependencies = (Array.isArray(saved) ? saved : [])
        .flatMap(dependency => {
            const provider = nodes.get(dependency?.outputKey);
            const adapter = CONTINUATION_VALUE_ADAPTERS[dependency?.type];
            if (!provider || provider.type !== dependency.type || !adapter
                || !_isResolvedContinuationValue(dependency.resolvedValue)) return [];
            return [{
                ...dependency,
                node: provider,
                adapter,
                connections: (dependency.connections || []).flatMap(connection => {
                    const consumerNode = nodes.get(connection.consumerKey);
                    const sourceNode = nodes.get(connection.sourceKey);
                    return consumerNode && sourceNode ? [{ consumerNode, sourceNode }] : [];
                }),
            }];
        });
}

function _updateSelectorSnapshot(node, changes, track = false) {
    const snapshot = node.properties?.[SELECTOR_SNAPSHOT];
    if (!snapshot || snapshot.expired) return;
    // Canvas change events feed ComfyUI's workflow persistence and undo tracker.
    if (track) {
        if (app.canvas?.emitBeforeChange) app.canvas.emitBeforeChange();
        else node.graph?.beforeChange?.();
    }
    Object.assign(snapshot, changes);
    if (track) {
        if (app.canvas?.emitAfterChange) app.canvas.emitAfterChange();
        else node.graph?.afterChange?.();
    }
}

function _disposeSelectorUI(node) {
    node._eclipseSelectorResizeObserver?.disconnect();
    node._eclipseSelectorModeUnsubscribe?.();
    node._eclipseSelectorPointerEnterCleanup?.();
    for (const name of ['ResizeObserver', 'ModeUnsubscribe', 'PointerEnterCleanup',
        'RefreshLayout', 'Dropdown']) delete node[`_eclipseSelector${name}`];
}

function _expireSelectorSnapshot(node) {
    node._eclipseSelectorRevision = (node._eclipseSelectorRevision || 0) + 1;
    delete node._eclipseSelectorContinuationDependencies;
    const snapshot = node.properties[SELECTOR_SNAPSHOT];
    node.properties[SELECTOR_SNAPSHOT] = {
        version: 1, expired: true, nodeId: snapshot?.nodeId, generation: snapshot?.generation,
    };
    _disposeSelectorUI(node);
    const container = node._eclipseDomPreview?.container;
    if (container) {
        container.replaceChildren();
        container.textContent = 'Run workflow to reload images';
    }
}

function _selectorInputsMatch(node) {
    const snapshot = node.properties?.[SELECTOR_SNAPSHOT];
    return !snapshot || (!snapshot.expired && snapshot.inputState === _selectorInputState(node));
}

async function _restoreSelectorSnapshot(node, revision) {
    const snapshot = node.properties?.[SELECTOR_SNAPSHOT];
    if (!snapshot || node._eclipseSelectorRemoved || revision !== node._eclipseSelectorRevision) return;
    const current = () => !node._eclipseSelectorRemoved && revision === node._eclipseSelectorRevision;
    if (snapshot.version !== 1 || snapshot.expired || snapshot.nodeId !== _selectorNodeKey(node)
        || typeof snapshot.generation !== 'string' || !_selectorInputsMatch(node)) {
        _expireSelectorSnapshot(node);
        return;
    }
    try {
        const response = await api.fetchApi('/eclipse/image_selector/state?'
            + `node_id=${encodeURIComponent(snapshot.nodeId)}`
            + `&generation=${encodeURIComponent(snapshot.generation)}`);
        const { ok, state } = await response.json();
        if (!current()) return;
        if (!ok || state?.generation !== snapshot.generation
            || state.total_count !== snapshot.totalCount || !_selectorInputsMatch(node)
            || !Array.isArray(snapshot.selection)
            || !snapshot.selection.every(index => Number.isInteger(index)
                && index >= 0 && index < state.total_count)) {
            _expireSelectorSnapshot(node);
            return;
        }
        _restoreContinuationDependencies(node, snapshot.dependencies);
        _buildSelectorUI(node, node._eclipseDomPreview.container,
            state.images, state.total_count, snapshot.selection);
    } catch (error) {
        if (current()) _expireSelectorSnapshot(node);
        console.warn('[Eclipse Image Selector] Could not restore the saved image grid.', error);
    }
}

function _selectorRequest(node, action, data = {}) {
    const snapshot = node.properties?.[SELECTOR_SNAPSHOT];
    const body = JSON.stringify({
        node_id: _selectorNodeKey(node),
        ...(snapshot?.generation ? { generation: snapshot.generation } : {}),
        ...data,
    });
    // Serialize user actions so a late draft reset cannot undo a newer Confirm.
    const request = (node._eclipseSelectorRequest || Promise.resolve()).catch(() => {}).then(async () => {
        const response = await api.fetchApi(`/eclipse/image_selector/${action}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body,
        });
        return response.json();
    });
    node._eclipseSelectorRequest = request;
    return request;
}

function _capEarlyPromptEvents(collection) {
    while (collection.size > MAX_EARLY_PROMPT_EVENTS) {
        collection.delete(collection.keys().next().value);
    }
}

function _isResolvedContinuationValue(value) {
    if (value == null || value === '') return false;
    const resolved = Number(value);
    return Number.isSafeInteger(resolved) && resolved >= 0;
}

function _readContinuationValue(node, adapter, promptNode = null) {
    const queued = adapter.queuedValueField ? node?.[adapter.queuedValueField] : null;
    if (_isResolvedContinuationValue(queued)) return Number(queued);
    const promptValue = promptNode?.inputs?.[adapter.promptValueName];
    if (promptNode && !_isResolvedContinuationValue(promptValue)) return null;
    const resolved = node?.[adapter.resolvedValueField];
    if (_isResolvedContinuationValue(resolved)) return Number(resolved);
    return _isResolvedContinuationValue(promptValue) ? Number(promptValue) : null;
}

function _collectPromptLinks(value, prompt, links) {
    if (!Array.isArray(value) || value.length !== 2 || typeof value[0] !== 'string') return;
    const key = value[0];
    if (Object.hasOwn(prompt, key) && Number.isInteger(Number(value[1]))) links.add(key);
}

function _collectUpstreamPromptKeys(prompt, selectorKey) {
    const visited = new Set();
    const visit = (key) => {
        key = String(key);
        if (visited.has(key)) return;
        visited.add(key);
        const promptNode = prompt[key];
        if (!promptNode) return;
        const links = new Set();
        for (const value of Object.values(promptNode.inputs || {})) {
            _collectPromptLinks(value, prompt, links);
        }
        for (const upstreamKey of links) visit(upstreamKey);
    };
    visit(selectorKey);
    return visited;
}

function _collectUpstreamContinuationProviderKeys(prompt, selectorKey, upstreamKeys = null) {
    return [...(upstreamKeys || _collectUpstreamPromptKeys(prompt, selectorKey))].filter(key =>
        key !== String(selectorKey)
        && CONTINUATION_VALUE_ADAPTERS[prompt[key]?.class_type]?.sourceProvider
    );
}

function _getLiveGraphNodeList(rootGraph) {
    const results = [];
    const walk = (graph, prefix = '') => {
        for (const node of graph?._nodes || []) {
            const outputKey = prefix ? `${prefix}:${node.id}` : String(node.id);
            results.push({ node, outputKey });
            if (node.subgraph) walk(node.subgraph, outputKey);
        }
    };
    walk(rootGraph);
    return results;
}

function _getNodeById(graph, nodeId) {
    return graph?.getNodeById?.(nodeId)
        ?? graph?._nodes?.find(node => String(node.id) === String(nodeId))
        ?? null;
}

function _resolveSetterInputSource(setter, setterGraph, resolveBypass = false) {
    if (!setter || setter.mode === 2 || setter.mode === 4) return null;
    const link = resolveBypass
        ? resolveBypassedLink(setterGraph, setter)
        : getLink(setterGraph, setter.inputs?.[0]?.link);
    if (!link) return null;
    const node = _getNodeById(setterGraph, link.origin_id);
    return node ? { node, slot: link.origin_slot } : null;
}

function _resolveEclipseGetterSource(getter) {
    const name = getter.widgets?.[0]?.value;
    const result = findSetterByName(getter.graph, name);
    if (!result) return null;
    if (result.graph !== getter.graph && !isSetterPathToRootActive(result.graph)) return null;
    return _resolveSetterInputSource(result.node, result.graph, true);
}

function _resolveKJGetterSource(getter) {
    const name = getter.widgets?.[0]?.value;
    if (!name) return null;
    const scopeGraphs = getGraphAncestors(getter.graph).filter(Boolean);
    const matches = [];
    for (const graph of scopeGraphs) {
        for (const node of graph?._nodes || []) {
            if (node.type === 'SetNode' && node.widgets?.[0]?.value === name) {
                matches.push({ graph, node });
            }
        }
    }
    const local = matches.find(match => match.graph === getter.graph);
    if (local) return _resolveSetterInputSource(local.node, local.graph);
    if (matches.length !== 1) return null;
    return _resolveSetterInputSource(matches[0].node, matches[0].graph);
}

function _resolveConsumedSeedInputSource(node, promptNode) {
    if (Object.hasOwn(promptNode?.inputs || {}, 'seed_input')) return null;
    const inputIndex = node.inputs?.findIndex(input =>
        input?.name === 'seed_input' || input?.widget?.name === 'seed_input'
    ) ?? -1;
    const link = getLink(node.graph, node.inputs?.[inputIndex]?.link);
    if (!link) return null;
    const source = _getNodeById(node.graph, link.origin_id);
    if (!source) return null;
    if (source.type === 'GetNode [Eclipse]') return _resolveEclipseGetterSource(source);
    if (source.type === 'GetNode') return _resolveKJGetterSource(source);
    return { node: source, slot: link.origin_slot };
}

function _findNodeInput(node, inputName) {
    return node.inputs?.find(input =>
        input?.name === inputName || input?.widget?.name === inputName
    ) ?? null;
}

function _hasLiveControlConnection(node, adapter) {
    return _findNodeInput(node, adapter.controlInputName)?.link != null;
}

function _isAdapterDynamic(node, adapter) {
    const widget = node?.[adapter.widgetField];
    return Boolean(widget && adapter.dynamicValues.includes(Number(widget.value)));
}

function _isAdapterApplicable(node, adapter, promptNode = null) {
    return !adapter.isApplicable || adapter.isApplicable(node, promptNode);
}

function _snapshotSelectorContinuationDependencies(prompt, rootGraph = app.graph) {
    const snapshots = new Map();
    if (!prompt || typeof prompt !== 'object') return snapshots;
    const graphNodes = _getLiveGraphNodeList(rootGraph);
    const liveNodes = new Map(graphNodes.map(({ node, outputKey }) => [String(outputKey), node]));
    const outputKeys = new Map(graphNodes.map(({ node, outputKey }) => [node, String(outputKey)]));
    for (const [rawKey, promptNode] of Object.entries(prompt)) {
        if (promptNode?.class_type !== NODE_NAME) continue;
        const selectorKey = String(rawKey);
        const selectorNode = liveNodes.get(selectorKey);
        if (!selectorNode || selectorNode.type !== NODE_NAME) continue;
        const branchKeys = _collectUpstreamPromptKeys(prompt, selectorKey);
        const dependencies = new Map();
        const addDependency = (outputKey, connection = null, directBranch = false) => {
            const node = liveNodes.get(String(outputKey));
            const promptNode = prompt[outputKey];
            const adapter = CONTINUATION_VALUE_ADAPTERS[promptNode?.class_type];
            if (!node || !adapter || node.type !== promptNode?.class_type
                || node.mode === 2 || node.mode === 4
                || !_isAdapterApplicable(node, adapter, promptNode)
                || !_isAdapterDynamic(node, adapter)
                || _hasLiveControlConnection(node, adapter)) return;
            const key = String(outputKey);
            let dependency = dependencies.get(key);
            if (!dependency) {
                dependency = {
                    adapter,
                    connections: [],
                    directBranch,
                    node,
                    outputKey: key,
                    resolvedValue: adapter.captureOnSelectorExecution
                        ? null
                        : _readContinuationValue(node, adapter, promptNode),
                    type: node.type,
                };
                dependencies.set(key, dependency);
            } else if (directBranch) {
                dependency.directBranch = true;
            }
            if (connection && !dependency.connections.some(candidate =>
                candidate.consumerNode === connection.consumerNode
                && candidate.sourceNode === connection.sourceNode
            )) {
                dependency.connections.push(connection);
            }
        };
        for (const providerKey of _collectUpstreamContinuationProviderKeys(
            prompt, selectorKey, branchKeys,
        )) {
            addDependency(providerKey, null, true);
        }
        for (const branchKey of branchKeys) {
            const branchNode = liveNodes.get(branchKey);
            const branchPromptNode = prompt[branchKey];
            if (!branchNode || branchNode.mode === 2 || branchNode.mode === 4
                || branchNode.type !== branchPromptNode?.class_type) continue;
            const branchAdapter = CONTINUATION_VALUE_ADAPTERS[branchNode.type];
            if (branchAdapter && !branchAdapter.sourceProvider
                && _isAdapterApplicable(branchNode, branchAdapter, branchPromptNode)
                && !_hasLiveControlConnection(branchNode, branchAdapter)) {
                addDependency(branchKey, null, true);
            }
            const source = _resolveConsumedSeedInputSource(branchNode, branchPromptNode);
            const sourceKey = outputKeys.get(source?.node);
            if (!sourceKey || prompt[sourceKey]?.class_type !== source.node.type) continue;
            if (!CONTINUATION_VALUE_ADAPTERS[source.node.type]?.sourceProvider) continue;
            addDependency(sourceKey, {
                consumerNode: branchNode,
                sourceNode: source.node,
            });
        }
        snapshots.set(selectorKey, { dependencies: [...dependencies.values()], node: selectorNode });
    }
    return snapshots;
}

function _attachExecutedDependencySnapshot(detail) {
    const promptId = detail?.prompt_id == null ? null : String(detail.prompt_id);
    const snapshots = promptId == null
        ? null
        : _pendingContinuationDependencySnapshots.get(promptId);
    const executionKeys = [detail?.node, detail?.display_node]
        .filter(value => value != null)
        .map(String);
    if (!snapshots) {
        if (promptId != null && executionKeys.length > 0) {
            const earlyKeys = _earlyExecutedNodeKeys.get(promptId) || new Set();
            for (const key of executionKeys) earlyKeys.add(key);
            _earlyExecutedNodeKeys.set(promptId, earlyKeys);
            _capEarlyPromptEvents(_earlyExecutedNodeKeys);
        }
        return;
    }
    for (const key of executionKeys) {
        const snapshot = snapshots.get(key);
        if (!snapshot) continue;
        for (const dependency of snapshot.dependencies) {
            if (dependency.adapter.captureOnSelectorExecution
                || !_isResolvedContinuationValue(dependency.resolvedValue)) {
                dependency.resolvedValue = _readContinuationValue(
                    dependency.node, dependency.adapter,
                );
            }
        }
        snapshot.node._eclipseSelectorContinuationDependencies = snapshot.dependencies;
        _updateSelectorSnapshot(snapshot.node, {
            dependencies: _savedContinuationDependencies(snapshot.node),
        }, true);
        snapshots.delete(key);
        if (snapshots.size === 0) _pendingContinuationDependencySnapshots.delete(promptId);
        return;
    }
}

function _connectionStillUsesProvider(connection) {
    const source = _resolveConsumedSeedInputSource(connection.consumerNode, null);
    return source?.node === connection.sourceNode;
}

function _freezeSelectorContinuationDependencies(selectorNode) {
    const dependencies = selectorNode?._eclipseSelectorContinuationDependencies;
    if (!Array.isArray(dependencies) || dependencies.length === 0) return 0;
    const liveNodes = new Map(
        _getLiveGraphNodeList(app.graph).map(({ node, outputKey }) => [String(outputKey), node]),
    );
    let frozen = 0;
    for (const dependency of dependencies) {
        const { adapter, connections, directBranch, node, outputKey, resolvedValue, type } = dependency;
        if (liveNodes.get(String(outputKey)) !== node || node.mode === 2 || node.mode === 4) continue;
        if (!_isAdapterApplicable(node, adapter) || !_isAdapterDynamic(node, adapter)
            || _hasLiveControlConnection(node, adapter)) continue;
        if (!directBranch && !connections.some(_connectionStillUsesProvider)) continue;
        const widget = node[adapter.widgetField];
        const valueToFreeze = _isResolvedContinuationValue(resolvedValue)
            ? Number(resolvedValue)
            : _readContinuationValue(node, adapter);
        if (!_isResolvedContinuationValue(valueToFreeze)) {
            console.warn(
                `[Eclipse Image Selector] Could not freeze ${type} (${outputKey}): ` +
                `no valid resolved ${adapter.valueLabel} was recorded for the displayed images.`,
            );
            continue;
        }
        widget.value = valueToFreeze;
        try {
            widget.callback?.call(widget, valueToFreeze);
        } catch (error) {
            console.warn(
                `[Eclipse Image Selector] ${type} (${outputKey}) ${adapter.valueLabel} ` +
                'callback failed after freezing.',
                error,
            );
        }
        for (const field of adapter.cacheFields) node[field] = null;
        node.setDirtyCanvas?.(true, true);
        node.graph?.setDirtyCanvas?.(true, true);
        if (isVueMode()) notifyVue(node);
        frozen++;
    }
    if (frozen > 0) app.graph?.setDirtyCanvas?.(true, true);
    return frozen;
}

async function _confirmSelectorSelection(node, indices) {
    if (!_selectorInputsMatch(node)) {
        _expireSelectorSnapshot(node);
        return { ok: false, error: 'Run workflow to reload images' };
    }
    const revision = node._eclipseSelectorRevision;
    const result = await _selectorRequest(node, 'confirm', { indices });
    if (!result.ok) return result;
    if (node._eclipseSelectorRemoved || revision !== node._eclipseSelectorRevision) {
        return { ok: false, error: 'Selector changed while confirming' };
    }
    const triggerWidget = node.widgets?.find(widget => widget.name === 'execution_trigger');
    if (triggerWidget) {
        triggerWidget.value = Date.now() % 2147483647;
        node.graph?.setDirtyCanvas(true, true);
    }
    _freezeSelectorContinuationDependencies(node);
    _updateSelectorSnapshot(node, {
        selection: indices, confirmed: true, inputState: _selectorInputState(node),
    }, true);
    app.queuePrompt(0);
    return result;
}

// ─────────────────────────────────────────────────────────────────────────────
// CSS injected once
// ─────────────────────────────────────────────────────────────────────────────

let _cssInjected = false;
function _injectCSS() {
    if (_cssInjected) return;
    _cssInjected = true;
    const style = document.createElement('style');
    style.textContent = `
.eclipse-sel-cell { position:relative; cursor:pointer; border:2px solid transparent;
    border-radius:3px; overflow:hidden; background:#222; transition:border-color 0.1s; }
.eclipse-sel-cell:hover { border-color:#aaa; }
.eclipse-sel-cell.selected { border-color:#4caf50; }
.eclipse-sel-check { position:absolute; top:3px; right:3px; width:18px; height:18px;
    border-radius:50%; background:rgba(76,175,80,0.95); display:none;
    align-items:center; justify-content:center; font-size:11px; color:#fff;
    pointer-events:none; font-weight:bold; }
.eclipse-sel-cell.selected .eclipse-sel-check { display:flex; }
.eclipse-sel-toolbar { display:flex; flex-direction:column; align-items:stretch;
    padding:6px 8px; background:#141414; border-top:1px solid #333; gap:6px; z-index:10; }
.eclipse-sel-status { font:11px sans-serif; color:#ccc; text-align:left; }
.eclipse-sel-actions { display:flex; align-items:center; justify-content:space-between; width:100%; }
.eclipse-sel-btn { font:11px sans-serif; padding:3px 10px; border:none;
    border-radius:3px; cursor:pointer; white-space:nowrap; }
.eclipse-sel-btn-all { background:#333; color:#eee; }
.eclipse-sel-btn-all:hover { background:#444; color:#fff; }
.eclipse-sel-btn-discard { background:#c62828; color:#fff; }
.eclipse-sel-btn-discard:hover { background:#e53935; }
.eclipse-sel-btn-confirm { background:#2e7d32; color:#fff; }
.eclipse-sel-btn-confirm:hover:not(:disabled) { background:#43a047; }
.eclipse-sel-btn-confirm:disabled { background:#444; color:#888; cursor:default; }
.eclipse-sel-auto { display:flex; align-items:center; gap:5px; color:#ddd;
    font:11px sans-serif; cursor:pointer; user-select:none; white-space:nowrap; }
.eclipse-sel-auto input { margin:0; cursor:pointer; accent-color:#4caf50; }
.eclipse-sel-auto input:disabled { cursor:default; }
.eclipse-sel-topbar { display:flex; align-items:center; padding:0 8px; background:#141414;
    border-bottom:1px solid rgba(255,255,255,0.1); z-index:10; gap:6px; height:30px; box-sizing:border-box; }
.eclipse-sel-label { font:11px sans-serif; color:#aaa; user-select:none; }
.eclipse-sel-select { font:11px sans-serif; background:#2a2a2a; color:#ccc;
    border:1px solid #444; border-radius:3px; padding:2px 4px; outline:none;
    cursor:pointer; transition:border-color 0.2s, color 0.2s; }
.eclipse-sel-select:hover { border-color:#666; color:#fff; }
.eclipse-sel-vue-layout { contain:size; min-height:${SELECTOR_MIN_HEIGHT}px; }
.eclipse-sel-vue-layout > .eclipse-sel-grid { min-height:0; }
.eclipse-sel-vue-layout > .eclipse-sel-topbar,
.eclipse-sel-vue-layout > .eclipse-sel-toolbar { flex-shrink:0; }
`;
    document.head.appendChild(style);
}

// ─────────────────────────────────────────────────────────────────────────────
// Build the selector widget (replaces the DOM preview's content in grid mode)
// ─────────────────────────────────────────────────────────────────────────────

function _buildSelectorUI(node, container, imageData, totalCount, initialSelection = []) {
    _injectCSS();
    _disposeSelectorUI(node);
    container.innerHTML = '';
    container.style.cssText = 'position:relative;width:100%;height:100%;overflow:hidden;' +
        'background:#1a1a1a;display:flex;flex-direction:column;border-radius:4px;';
    let selectorInteractionDisposed = false;

    // ── Top Bar (display mode select) ────────────────────────────────────────
    const topbar = document.createElement('div');
    topbar.className = 'eclipse-sel-topbar';

    const label = document.createElement('span');
    label.className = 'eclipse-sel-label';
    label.textContent = 'Preview Mode:';
    topbar.appendChild(label);

    const select = document.createElement('select');
    select.className = 'eclipse-sel-select';

    const modes = [
        { value: 'auto', label: 'Auto (Grid)' },
        { value: '1_image_per_row', label: '1 Image per Row' },
        { value: '2_images_per_row', label: '2 Images per Row' },
        { value: '3_images_per_row', label: '3 Images per Row' },
        { value: '4_images_per_row', label: '4 Images per Row' },
        { value: '5_images_per_row', label: '5 Images per Row' },
        { value: '6_images_per_row', label: '6 Images per Row' }
    ];

    modes.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.value;
        opt.textContent = m.label;
        select.appendChild(opt);
    });

    select.value = node.properties?.display_mode || 'auto';
    topbar.appendChild(select);
    container.appendChild(topbar);

    // Store dropdown on node for external (context menu) updates
    node._eclipseSelectorDropdown = select;

    // Prevent interactions with select dropdown from zooming/dragging the canvas or triggering hotkeys
    const stopPropagation = (e) => e.stopPropagation();
    select.addEventListener('click', stopPropagation);
    select.addEventListener('mousedown', stopPropagation);
    select.addEventListener('wheel', stopPropagation);
    select.addEventListener('keydown', stopPropagation);

    select.addEventListener('change', () => {
        node.properties.display_mode = select.value;
        node.setDirtyCanvas(true, true);
        applyLayout();
    });

    // Selection state
    const selected = new Set(
        initialSelection.filter(index => Number.isInteger(index) && index >= 0 && index < imageData.length),
    );
    let lastClickedIdx = null;
    let activeOverlay = null;

    function openLargePreview(initialIdx) {
        if (activeOverlay) return;

        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:absolute;top:0;left:0;right:0;bottom:0;' +
            'background:rgba(10,10,10,0.95);z-index:100;display:flex;flex-direction:column;' +
            'align-items:center;justify-content:center;outline:none;';
        overlay.tabIndex = 0;

        const overlayTopbar = document.createElement('div');
        overlayTopbar.style.cssText = 'width:100%;height:40px;' +
            'display:flex;align-items:center;justify-content:space-between;padding:0 12px;' +
            'background:#141414;border-bottom:1px solid rgba(255,255,255,0.1);z-index:110;box-sizing:border-box;';

        const selBtn = document.createElement('button');
        selBtn.style.cssText = 'font:11px sans-serif;padding:4px 10px;border:none;border-radius:3px;' +
            'cursor:pointer;color:#fff;font-weight:bold;transition:background 0.2s, color 0.2s;outline:none;';

        const overlayStatus = document.createElement('div');
        overlayStatus.style.cssText = 'font:12px sans-serif;color:#ccc;user-select:none;';

        const closeBtn = document.createElement('div');
        closeBtn.style.cssText = 'width:24px;height:24px;border-radius:50%;' +
            'background:rgba(255,255,255,0.1);color:#fff;display:flex;align-items:center;' +
            'justify-content:center;cursor:pointer;font-family:sans-serif;font-size:14px;' +
            'font-weight:bold;user-select:none;transition:background 0.2s;';
        closeBtn.textContent = '✕';
        closeBtn.title = 'Close preview (automatically selects this image)';

        closeBtn.addEventListener('mouseenter', () => closeBtn.style.background = 'rgba(255,255,255,0.25)');
        closeBtn.addEventListener('mouseleave', () => closeBtn.style.background = 'rgba(255,255,255,0.1)');

        const imageWrapper = document.createElement('div');
        imageWrapper.style.cssText = 'flex:1;width:100%;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative;';

        const largeImg = document.createElement('img');
        largeImg.draggable = false;
        largeImg.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain;user-select:none;cursor:pointer;';
        imageWrapper.appendChild(largeImg);

        let currentIdx = initialIdx;

        const getUrl = (idx) => {
            const data = imageData[idx];
            return api.apiURL(
                `/view?filename=${encodeURIComponent(data.filename)}` +
                `&type=${encodeURIComponent(data.type || 'temp')}` +
                `&subfolder=${encodeURIComponent(data.subfolder || '')}`
            );
        };

        function updateOverlay() {
            largeImg.src = getUrl(currentIdx);
            overlayStatus.textContent = `Image ${currentIdx + 1} of ${imageData.length}`;

            const isSel = selected.has(currentIdx);
            selBtn.textContent = isSel ? '✓ Selected' : 'Select';
            selBtn.style.background = isSel ? '#2e7d32' : '#444';
        }

        function toggleSelection() {
            if (selected.has(currentIdx)) {
                selected.delete(currentIdx);
                cells[currentIdx].classList.remove('selected');
            } else {
                selected.add(currentIdx);
                cells[currentIdx].classList.add('selected');
            }
            updateToolbar();
            syncSelection();
            updateOverlay();
        }

        selBtn.addEventListener('mouseenter', () => {
            const isSel = selected.has(currentIdx);
            selBtn.style.background = isSel ? '#388e3c' : '#555';
        });
        selBtn.addEventListener('mouseleave', () => {
            const isSel = selected.has(currentIdx);
            selBtn.style.background = isSel ? '#2e7d32' : '#444';
        });

        selBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleSelection();
        });

        largeImg.addEventListener('click', (e) => {
            e.stopPropagation();
            currentIdx = (currentIdx + 1) % imageData.length;
            updateOverlay();
        });

        const closeOverlay = () => {
            lastClickedIdx = currentIdx;
            overlay.remove();
            activeOverlay = null;
            container.focus({ preventScroll: true });
        };

        closeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            closeOverlay();
        });

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay || e.target === imageWrapper) {
                closeOverlay();
            }
        });

        overlay.addEventListener('keydown', (e) => {
            const key = e.key;
            if (key === 'Escape') {
                e.stopPropagation();
                e.preventDefault();
                closeOverlay();
            } else if (key === 'ArrowRight' || key === 'd' || key === 'D') {
                e.stopPropagation();
                e.preventDefault();
                currentIdx = (currentIdx + 1) % imageData.length;
                updateOverlay();
            } else if (key === 'ArrowLeft' || key === 'a' || key === 'A') {
                e.stopPropagation();
                e.preventDefault();
                currentIdx = (currentIdx - 1 + imageData.length) % imageData.length;
                updateOverlay();
            } else if (key === ' ' || key === 's' || key === 'S' || key === 'Enter') {
                e.stopPropagation();
                e.preventDefault();
                toggleSelection();
            }
        });

        overlayTopbar.appendChild(selBtn);
        overlayTopbar.appendChild(overlayStatus);
        overlayTopbar.appendChild(closeBtn);
        overlay.appendChild(overlayTopbar);
        overlay.appendChild(imageWrapper);

        container.appendChild(overlay);
        activeOverlay = overlay;

        updateOverlay();
        overlay.focus({ preventScroll: true });
    }

    async function syncSelection() {
        const indices = [...selected];
        const wasConfirmed = node.properties?.[SELECTOR_SNAPSHOT]?.confirmed;
        const automatic = Boolean(node.widgets?.find(widget => widget.name === 'auto_select_and_confirm')?.value);
        _updateSelectorSnapshot(node, { selection: indices, confirmed: automatic }, true);
        if (!automatic && !wasConfirmed) return;
        try {
            const result = await _selectorRequest(node,
                automatic ? 'confirm' : 'reset_selection', automatic ? { indices } : {});
            if (!result.ok) throw new Error(result.error || 'Selection synchronization failed');
        } catch (err) {
            console.error('[Eclipse] Auto-sync selection failed:', err);
            showEclipseToast('Eclipse Image Selector', 'Automatic selection could not be synchronized. Check console for details.');
        }
    }

    // ── Grid area with vertical scroll ──────────────────────────────────────
    const grid = document.createElement('div');
    grid.className = 'eclipse-sel-grid';
    grid.style.cssText = 'flex:1;overflow-y:auto;overflow-x:hidden;padding:2px;gap:2px;display:grid;';
    container.appendChild(grid);

    const releaseWheelCaptureForCanvas = () => {
        if (!isVueMode() || container.dataset.captureWheel !== 'true') return;
        container.removeAttribute('data-capture-wheel');
        queueMicrotask(() => {
            if (!selectorInteractionDisposed && isVueMode() && container.isConnected) {
                container.setAttribute('data-capture-wheel', 'true');
            }
        });
    };

    // Stop propagation of wheel events on the grid when scrollbar is active, but bubble at boundaries
    grid.addEventListener('wheel', (e) => {
        if (grid.scrollHeight <= grid.clientHeight) {
            releaseWheelCaptureForCanvas();
            return;
        }

        const isScrollingDown = e.deltaY > 0;
        const isScrollingUp = e.deltaY < 0;
        const atBottom = grid.scrollTop + grid.clientHeight >= grid.scrollHeight - 1;
        const atTop = grid.scrollTop <= 0;

        if ((isScrollingDown && atBottom) || (isScrollingUp && atTop)) {
            // Let the event bubble to the Vue node's canvas-forwarding handler.
            // The capture marker must be absent when that handler re-checks the
            // event during bubbling, then restored before the next wheel event.
            releaseWheelCaptureForCanvas();
            return;
        }
        e.stopPropagation();
    });

    const cells = imageData.map((data, i) => {
        const url = api.apiURL(
            `/view?filename=${encodeURIComponent(data.filename)}` +
            `&type=${encodeURIComponent(data.type || 'temp')}` +
            `&subfolder=${encodeURIComponent(data.subfolder || '')}`
        );
        const cell = document.createElement('div');
        cell.className = 'eclipse-sel-cell';
        if (selected.has(i)) cell.classList.add('selected');

        const img = document.createElement('img');
        img.src = url;
        img.draggable = false;
        img.style.cssText = 'width:100%;height:100%;object-fit:contain;display:block;';

        const check = document.createElement('div');
        check.className = 'eclipse-sel-check';
        check.textContent = '✓';

        cell.appendChild(img);
        cell.appendChild(check);
        grid.appendChild(cell);

        cell.addEventListener('click', (e) => {
            if (e.shiftKey && lastClickedIdx !== null) {
                // Shift+click: range selection from lastClickedIdx to i
                const lo = Math.min(lastClickedIdx, i);
                const hi = Math.max(lastClickedIdx, i);
                const allSelected = Array.from({ length: hi - lo + 1 }, (_, k) => lo + k)
                    .every(k => selected.has(k));
                // If all in range are already selected, deselect the range; otherwise select all
                for (let k = lo; k <= hi; k++) {
                    if (allSelected) {
                        selected.delete(k);
                        cells[k].classList.remove('selected');
                    } else {
                        selected.add(k);
                        cells[k].classList.add('selected');
                    }
                }
            } else {
                // Normal click: toggle single image
                if (selected.has(i)) {
                    selected.delete(i);
                    cell.classList.remove('selected');
                } else {
                    selected.add(i);
                    cell.classList.add('selected');
                }
                lastClickedIdx = i;
            }
            updateToolbar();
            syncSelection();
        });

        cell.addEventListener('dblclick', (e) => {
            e.stopPropagation();
            openLargePreview(i);
        });
        return cell;
    });

    // ── Layout (same algorithm as eclipse-dom-preview.js) ───────────────────
    const aspects = { data: null };

    function applyLayout() {
        const n = imageData.length;
        if (n === 0) return;

        const gap = 2, pad = 4;
        const w = (container.clientWidth || 200) - pad;
        const h = (grid.clientHeight || 200) - pad;

        const displayMode = node.properties?.display_mode || 'auto';

        let cols = null;
        if (displayMode === 'one_image_per_row' || displayMode === '1_image_per_row') cols = 1;
        else if (displayMode.endsWith('_images_per_row') || displayMode.endsWith('_image_per_row')) {
            const m = displayMode.match(/^(\d+)_image(s)?_per_row$/);
            if (m) cols = parseInt(m[1], 10);
        }

        if (cols !== null) {
            grid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
            if (cols === 1) {
                grid.style.gridAutoRows = 'auto';
                cells.forEach((cell, idx) => {
                    const img = cell.querySelector('img');
                    const aspect = aspects.data?.[idx];
                    if (aspect) {
                        const ar = aspect.w / aspect.h;
                        const cellW = (container.clientWidth || 200) - pad;
                        const cellH = cellW / ar;
                        cell.style.height = `${cellH}px`;
                        if (img) {
                            img.style.height = '100%';
                            img.style.maxHeight = '100%';
                            img.style.objectFit = 'contain';
                        }
                    } else {
                        // Fallback before aspects load
                        cell.style.height = '200px';
                        if (img) {
                            img.style.height = '100%';
                            img.style.maxHeight = '';
                            img.style.objectFit = 'contain';
                        }
                    }
                });
            } else {
                let avgAR = null;
                if (aspects.data && aspects.data.length > 0) {
                    avgAR = aspects.data.reduce((s, a) => s + (a.w || 1) / (a.h || 1), 0) / aspects.data.length;
                }
                const MIN_CELL_H = 100;
                const totalGaps = (cols - 1) * gap;
                const cellW = (w - totalGaps) / cols;
                const rowH = Math.max(MIN_CELL_H, Math.floor(cellW / (avgAR || 1)));

                grid.style.gridAutoRows = `${rowH}px`;
                cells.forEach(cell => {
                    cell.style.height = '';
                    const img = cell.querySelector('img');
                    if (img) {
                        img.style.height = '100%';
                        img.style.maxHeight = '';
                        img.style.objectFit = 'contain';
                    }
                });
            }
            return;
        }

        // Auto mode: reset custom cell and image height styling
        cells.forEach(cell => {
            cell.style.height = '';
            const img = cell.querySelector('img');
            if (img) {
                img.style.height = '100%';
                img.style.maxHeight = '';
                img.style.objectFit = 'contain';
            }
        });

        let avgAR = null;
        if (aspects.data) {
            avgAR = aspects.data.reduce((s, a) => s + (a.w || 1) / (a.h || 1), 0) / aspects.data.length;
        }

        // Define minimum cell dimensions for scrolling triggers
        const MIN_CELL_W = 100;
        const MIN_CELL_H = 100;

        // Compute virtual height based on minimum dimensions and image aspects
        const avg = avgAR || 1;
        const cellMinW = Math.max(MIN_CELL_W, MIN_CELL_H * avg);
        const minArea = cellMinW * MIN_CELL_H;
        const h_virtual = Math.max(h, n * minArea / w);

        const idealFloat = avgAR ? Math.sqrt(n * w / h_virtual / avgAR) : Math.sqrt(n * w / h_virtual);
        const ideal = Math.max(1, Math.round(idealFloat));
        const maxCols = Math.min(n, Math.max(ideal + 2, 4));
        let bestCols = 1, bestScore = 0;
        for (let c = 1; c <= maxCols; c++) {
            const rows = Math.ceil(n / c);
            const cellW = (w - (c - 1) * gap) / c;
            const cellH = (h_virtual - (rows - 1) * gap) / rows;
            if (cellW <= 0 || cellH <= 0) continue;  // gaps exceed container — skip
            let score;
            if (avgAR !== null) {
                score = (avgAR <= cellW / cellH)
                    ? cellH * avgAR * cellH
                    : cellW * (cellW / avgAR);
            } else {
                score = Math.min(cellW, cellH);
            }
            if (score > bestScore) { bestScore = score; bestCols = c; }
        }
        const numRows = Math.ceil(n / bestCols);
        const rowH = Math.max(MIN_CELL_H, Math.floor((h_virtual - (numRows - 1) * gap) / numRows));
        grid.style.gridTemplateColumns = `repeat(${bestCols}, 1fr)`;
        grid.style.gridAutoRows = `${rowH}px`;
    }

    if (node._eclipseSelectorResizeObserver) {
        node._eclipseSelectorResizeObserver.disconnect();
    }
    const ro = new ResizeObserver(applyLayout);
    ro.observe(container);
    node._eclipseSelectorResizeObserver = ro;
    applyLayout();

    // Probe aspects for better layout after images load
    Promise.all(imageData.map(data => new Promise(resolve => {
        const p = new Image();
        p.onload = () => resolve({ w: p.naturalWidth, h: p.naturalHeight });
        p.onerror = () => resolve({ w: 1, h: 1 });
        p.src = api.apiURL(
            `/view?filename=${encodeURIComponent(data.filename)}` +
            `&type=${encodeURIComponent(data.type || 'temp')}` +
            `&subfolder=${encodeURIComponent(data.subfolder || '')}`
        );
    }))).then(probed => {
        if (selectorInteractionDisposed) return;
        aspects.data = probed;
        applyLayout();
    });

    // ── Keyboard shortcuts ───────────────────────────────────────────────────
    container.tabIndex = 0;
    container.style.outline = 'none';
    const onSelectorKeyDown = e => {
        if (activeOverlay) {
            if (e.key === 'Escape') {
                e.stopPropagation();
                e.preventDefault();
                activeOverlay.remove();
                activeOverlay = null;
                container.focus({ preventScroll: true });
                return;
            }
        }
        if (e.key === 'a' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault(); e.stopPropagation();
            imageData.forEach((_, i) => { selected.add(i); cells[i].classList.add('selected'); });
            lastClickedIdx = imageData.length - 1;
            updateToolbar();
            syncSelection();
        } else if (e.key === 'Escape') {
            selected.clear();
            cells.forEach(c => c.classList.remove('selected'));
            lastClickedIdx = null;
            updateToolbar();
            syncSelection();
        }
    };
    container.addEventListener('keydown', onSelectorKeyDown);
    const focusSelector = () => container.focus({ preventScroll: true });
    container.addEventListener('click', focusSelector);
    const focusSelectorOnPointerEnter = () => {
        if (isVueMode()) container.focus({ preventScroll: true });
    };
    container.addEventListener('pointerenter', focusSelectorOnPointerEnter);
    node._eclipseSelectorPointerEnterCleanup = () => {
        if (selectorInteractionDisposed) return;
        selectorInteractionDisposed = true;
        container.removeEventListener('keydown', onSelectorKeyDown);
        container.removeEventListener('click', focusSelector);
        container.removeEventListener('pointerenter', focusSelectorOnPointerEnter);
        container.removeAttribute('data-capture-wheel');
    };

    // ── Toolbar ──────────────────────────────────────────────────────────────
    const toolbar = document.createElement('div');
    toolbar.className = 'eclipse-sel-toolbar';

    const status = document.createElement('div');
    status.className = 'eclipse-sel-status';

    const btnDiscard = document.createElement('button');
    btnDiscard.className = 'eclipse-sel-btn eclipse-sel-btn-discard';
    btnDiscard.textContent = 'Discard ✕';
    btnDiscard.title = 'Clear selection and turn off automatic selection. Next queue shows selector again.';

    const btnConfirm = document.createElement('button');
    btnConfirm.className = 'eclipse-sel-btn eclipse-sel-btn-confirm';
    btnConfirm.disabled = true;
    btnConfirm.title = 'Confirm selection and re-queue the workflow to continue.';

    function updateVisualOrder() {
        const selectedArray = [...selected];
        cells.forEach((cell, idx) => {
            const check = cell.querySelector('.eclipse-sel-check');
            if (check) {
                const sIdx = selectedArray.indexOf(idx);
                if (sIdx !== -1) {
                    check.textContent = String(sIdx + 1);
                } else {
                    check.textContent = '';
                }
            }
        });
    }

    function updateToolbar() {
        const n = selected.size;
        if (n === 0) {
            status.innerHTML = `<span style="color:#ffb74d;">⚠ Select at least one image to proceed</span> (${totalCount} available)`;
        } else {
            status.textContent = `${n} of ${totalCount} selected`;
        }
        btnConfirm.textContent = n === 0 ? 'Confirm →' : `Confirm (${n}) →`;
        btnConfirm.disabled = n === 0;
        updateVisualOrder();
    }
    updateToolbar();

    btnDiscard.addEventListener('click', async () => {
        btnDiscard.disabled = true;
        autoCheckbox.checked = false;
        autoCheckbox.disabled = true;
        setAutoWidgetValue(false);
        try {
            const result = await _selectorRequest(node, 'reset_selection');
            if (!result.ok) throw new Error(result.error || 'Selection reset failed');
            if (selectorInteractionDisposed) return;
            // Clear visual selection
            selected.clear();
            lastClickedIdx = null;
            cells.forEach(c => c.classList.remove('selected'));
            updateToolbar();
            _updateSelectorSnapshot(node, { selection: [], confirmed: false }, true);

            // Update execution_trigger widget so fingerprint changes on next queue
            const triggerWidget = node.widgets?.find(w => w.name === 'execution_trigger');
            if (triggerWidget) {
                triggerWidget.value = Date.now() % 2147483647;
                node.graph?.setDirtyCanvas(true, true);
            }
        } catch (err) {
            console.error('[Eclipse] ImageSelector reset selection error', err);
            status.textContent = 'Error resetting selection — check console';
            showEclipseToast('Eclipse Image Selector', 'Could not reset the selection. Check console for details.');
        } finally {
            btnDiscard.disabled = false;
            autoCheckbox.disabled = false;
        }
    });

    btnConfirm.addEventListener('click', async () => {
        if (selected.size === 0) return;
        const indices = [...selected];
        btnConfirm.disabled = true;
        btnDiscard.disabled = true;
        try {
            const result = await _confirmSelectorSelection(node, indices);
            if (result.ok) {
                status.textContent = `✓ ${indices.length} image${indices.length !== 1 ? 's' : ''} confirmed — re-queuing…`;
                btnDiscard.disabled = false;
            } else {
                status.textContent = `Error: ${result.error}`;
                showEclipseToast('Eclipse Image Selector', result.error || 'Could not confirm the selection.');
                btnConfirm.disabled = false;
                btnDiscard.disabled = false;
            }
        } catch (err) {
            console.error('[Eclipse] ImageSelector confirm error', err);
            status.textContent = 'Network error — check console';
            showEclipseToast('Eclipse Image Selector', 'Could not confirm the selection. Check the connection and console.');
            btnConfirm.disabled = false;
            btnDiscard.disabled = false;
        }
    });

    const btnSelectAll = document.createElement('button');
    btnSelectAll.className = 'eclipse-sel-btn eclipse-sel-btn-all';
    btnSelectAll.textContent = 'All';
    btnSelectAll.title = 'Select all images (Ctrl+A).';
    btnSelectAll.addEventListener('click', () => {
        imageData.forEach((_, i) => { selected.add(i); cells[i].classList.add('selected'); });
        lastClickedIdx = imageData.length - 1;
        updateToolbar();
        syncSelection();
    });

    const autoWidget = node.widgets?.find(widget => widget.name === 'auto_select_and_confirm');
    const autoControl = document.createElement('label');
    autoControl.className = 'eclipse-sel-auto';
    autoControl.title = 'Select every incoming image on future queues in this session. Discard or loading a workflow turns this off.';

    const autoCheckbox = document.createElement('input');
    autoCheckbox.type = 'checkbox';
    autoCheckbox.checked = Boolean(autoWidget?.value);

    const autoLabel = document.createElement('span');
    autoLabel.textContent = 'Auto select and confirm';
    autoControl.appendChild(autoCheckbox);
    autoControl.appendChild(autoLabel);

    for (const eventName of ['click', 'mousedown', 'wheel', 'keydown']) {
        autoControl.addEventListener(eventName, event => event.stopPropagation());
    }

    const updateExecutionTrigger = () => {
        const triggerWidget = node.widgets?.find(widget => widget.name === 'execution_trigger');
        if (triggerWidget) triggerWidget.value = Date.now() % 2147483647;
    };

    const setAutoWidgetValue = (value) => {
        if (autoWidget) autoWidget.value = value;
        node.setDirtyCanvas?.(true, true);
        node.graph?.setDirtyCanvas?.(true, true);
        if (isVueMode()) notifyVue(node);
    };

    autoCheckbox.addEventListener('change', async () => {
        const enabled = autoCheckbox.checked;
        setAutoWidgetValue(enabled);
        if (enabled) {
            imageData.forEach((_, index) => {
                selected.add(index);
                cells[index].classList.add('selected');
            });
            lastClickedIdx = imageData.length - 1;
            updateToolbar();
            await syncSelection();
            return;
        }

        // Unchecking is intentionally passive for the current queue, but it
        // clears the automatic decision so the next queue waits for a manual
        // selection even when the input images are unchanged.
        selected.clear();
        cells.forEach(cell => cell.classList.remove('selected'));
        lastClickedIdx = null;
        updateToolbar();
        updateExecutionTrigger();
        _updateSelectorSnapshot(node, { selection: [], confirmed: false }, true);
        autoCheckbox.disabled = true;
        try {
            const result = await _selectorRequest(node, 'reset_selection');
            if (!result.ok) throw new Error(result.error || 'Selection reset failed');
        } catch (err) {
            console.error('[Eclipse] ImageSelector auto-selection reset error', err);
            autoCheckbox.checked = true;
            setAutoWidgetValue(true);
            imageData.forEach((_, index) => {
                selected.add(index);
                cells[index].classList.add('selected');
            });
            lastClickedIdx = imageData.length - 1;
            updateToolbar();
            _updateSelectorSnapshot(node, { selection: [...selected], confirmed: true }, true);
            status.textContent = 'Error disabling auto selection — check console';
            showEclipseToast('Eclipse Image Selector', 'Could not disable automatic selection. Check console for details.');
        } finally {
            autoCheckbox.disabled = false;
        }
    });

    const actions = document.createElement('div');
    actions.className = 'eclipse-sel-actions';

    const leftActions = document.createElement('div');
    leftActions.style.cssText = 'display:flex; align-items:center; gap:8px;';
    leftActions.appendChild(btnSelectAll);
    leftActions.appendChild(autoControl);

    const rightActions = document.createElement('div');
    rightActions.style.cssText = 'display:flex; gap:6px;';
    rightActions.appendChild(btnDiscard);
    rightActions.appendChild(btnConfirm);

    actions.appendChild(leftActions);
    actions.appendChild(rightActions);

    toolbar.appendChild(status);
    toolbar.appendChild(actions);
    container.appendChild(toolbar);

    node._eclipseSelectorRefreshLayout = applyLayout;
    const syncRendererLayout = () => {
        const vueMode = isVueMode();
        container.classList.toggle('eclipse-sel-vue-layout', vueMode);
        if (vueMode) {
            container.setAttribute('data-capture-wheel', 'true');
        } else {
            container.removeAttribute('data-capture-wheel');
        }
        applyLayout();
    };
    syncRendererLayout();
    node._eclipseSelectorModeUnsubscribe = onVueModeChange(syncRendererLayout);
}

// ─────────────────────────────────────────────────────────────────────────────
// Extension registration
// ─────────────────────────────────────────────────────────────────────────────

app.registerExtension({
    name: 'Eclipse.ImageSelector',

    async setup() {
        api.addEventListener('graphChanged', () => {
            if (isConfiguringGraph()) return;
            for (const { node } of _getLiveGraphNodeList(app.rootGraph || app.graph)) {
                const snapshot = node.properties?.[SELECTOR_SNAPSHOT];
                if (node.type === NODE_NAME && snapshot && !snapshot.expired
                    && !_selectorInputsMatch(node)) _expireSelectorSnapshot(node);
            }
        });
        const originalQueuePrompt = api.queuePrompt;
        api.queuePrompt = async function (number, promptData, options) {
            const snapshots = _snapshotSelectorContinuationDependencies(
                promptData?.output, app.graph,
            );
            const response = await originalQueuePrompt.apply(this, arguments);
            if (response?.prompt_id != null && snapshots.size > 0) {
                const promptId = String(response.prompt_id);
                _pendingContinuationDependencySnapshots.set(promptId, snapshots);
                for (const key of _earlyExecutedNodeKeys.get(promptId) || []) {
                    _attachExecutedDependencySnapshot({ node: key, prompt_id: promptId });
                }
                _earlyExecutedNodeKeys.delete(promptId);
                if (_earlyTerminalPromptIds.delete(promptId)) {
                    _pendingContinuationDependencySnapshots.delete(promptId);
                }
            }
            return response;
        };
        api.addEventListener('executed', ({ detail }) => {
            _attachExecutedDependencySnapshot(detail);
        });
        for (const eventName of ['execution_error', 'execution_interrupted', 'execution_success']) {
            api.addEventListener(eventName, ({ detail }) => {
                if (detail?.prompt_id != null) {
                    const promptId = String(detail.prompt_id);
                    if (_pendingContinuationDependencySnapshots.delete(promptId)) return;
                    _earlyTerminalPromptIds.add(promptId);
                    _capEarlyPromptEvents(_earlyTerminalPromptIds);
                }
            });
        }
    },

    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated?.apply(this, arguments);
            this._eclipseSelectorRemoved = false;
            // Initialize display_mode property
            if (!this.properties) this.properties = {};
            const VALID_MODES = ['auto', '1_image_per_row', '2_images_per_row', '3_images_per_row', '4_images_per_row', '5_images_per_row', '6_images_per_row'];
            if (!VALID_MODES.includes(this.properties.display_mode)) {
                this.properties.display_mode = 'auto';
            }
            // Both values are internal transport for the custom selector toolbar.
            const visibility = createWidgetVisibilityManager(this);
            visibility.hideInitially(['execution_trigger', 'auto_select_and_confirm']);
            // Create a standard DOM preview widget (used after second run)
            createDOMPreview(this, { minHeight: SELECTOR_MIN_HEIGHT });
            return ret;
        };


        const origOnSerialize = nodeType.prototype.onSerialize;
        nodeType.prototype.onSerialize = function (data) {
            const ret = origOnSerialize?.apply(this, arguments);
            // Keep the Boolean slot for workflow compatibility. API prompts still
            // read the live widget value, so auto remains active for this session.
            const autoIndex = this.widgets?.findIndex(widget => widget.name === 'auto_select_and_confirm');
            if (autoIndex >= 0 && data.widgets_values) data.widgets_values[autoIndex] = false;
            if (data.widgets_values_named) data.widgets_values_named.auto_select_and_confirm = false;
            return ret;
        };

        // Defer until all nodes and links have been configured, then validate read-only.
        const origOnConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (data) {
            origOnConfigure?.apply(this, arguments);
            // Also reset historical workflows that saved automatic selection on.
            const autoWidget = this.widgets?.find(widget => widget.name === 'auto_select_and_confirm');
            if (autoWidget) autoWidget.value = false;
            const revision = this._eclipseSelectorRevision = (this._eclipseSelectorRevision || 0) + 1;
            _disposeSelectorUI(this);
            delete this._eclipseSelectorContinuationDependencies;
            queueMicrotask(() => _restoreSelectorSnapshot(this, revision));
        };

        const origOnExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (output) {
            if (this._eclipseSelectorRemoved) return;
            this._eclipseSelectorRevision = (this._eclipseSelectorRevision || 0) + 1;
            const savedImages = output.images;
            delete output.images;
            origOnExecuted?.apply(this, arguments);
            if (savedImages) output.images = savedImages;
            this.imgs = null;

            const preview = this._eclipseDomPreview;
            if (!preview) return;

            const generation = output.selectorGeneration?.[0];
            const previous = this.properties?.[SELECTOR_SNAPSHOT];
            const images = output.selectorImages || savedImages || [];
            const selection = output.selectedIndices?.[0]
                ?? (generation && previous?.generation === generation ? previous.selection : [])
                ?? [];
            if (generation) {
                this.properties[SELECTOR_SNAPSHOT] = {
                    version: 1,
                    nodeId: _selectorNodeKey(this),
                    generation,
                    images,
                    totalCount: output.totalCount?.[0] ?? images.length,
                    selection,
                    confirmed: output.eclipseSelector?.[0] === false || output.autoSelectAndConfirm?.[0] === true,
                    dependencies: _savedContinuationDependencies(this),
                    inputState: _selectorInputState(this),
                };
                _updateSelectorSnapshot(this, {}, true);
            }
            if (output.eclipseSelector?.[0] === true || output.selectorImages) {
                _buildSelectorUI(
                    this,
                    preview.container,
                    images,
                    output.totalCount?.[0] || 0,
                    selection,
                );
            }
            // Older backends only return selected-output previews after confirmation;
            // keep their existing full grid instead of replacing it with that subset.

            // Suppress ComfyUI's native image display
            const nodeOutputs = app.nodeOutputs?.[this.id];
            if (nodeOutputs?.images) delete nodeOutputs.images;
        };

        // Clean up server state when node is removed from graph
        const origOnRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            const configuring = isConfiguringGraph();
            this._eclipseSelectorRemoved = true;
            this._eclipseSelectorRevision = (this._eclipseSelectorRevision || 0) + 1;
            if (!configuring) _selectorRequest(this, 'discard').catch(() => {});
            origOnRemoved?.apply(this, arguments);
            _disposeSelectorUI(this);
            delete this._eclipseSelectorContinuationDependencies;
        };
    },
});
