/**
 * ComfyUI Eclipse workflow ID normalization utilities.
 * SPDX-License-Identifier: Apache-2.0
 */

export class WorkflowIdNormalizationError extends Error {
    constructor(message, path = 'workflow') {
        super(`${path}: ${message}`);
        this.name = 'WorkflowIdNormalizationError';
        this.path = path;
    }
}

function fail(path, message) {
    throw new WorkflowIdNormalizationError(message, path);
}

function isObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function cloneWorkflow(workflow) {
    if (typeof structuredClone === 'function') return structuredClone(workflow);
    return JSON.parse(JSON.stringify(workflow));
}

function isNodeId(value) {
    return (typeof value === 'number' && Number.isInteger(value)) || typeof value === 'string';
}

function requireNodeId(value, path) {
    if (!isNodeId(value)) fail(path, 'expected an integer or string node ID');
    return value;
}

function requireLinkId(value, path) {
    if (!Number.isInteger(value)) fail(path, 'expected an integer link ID');
    return value;
}

function requireSlotIndex(value, path) {
    if (!Number.isInteger(value) || value < 0) fail(path, 'expected a non-negative integer slot index');
    return value;
}

function describeId(value) {
    return typeof value === 'string' ? JSON.stringify(value) : String(value);
}

function getCanvasPosition(node) {
    const pos = node?.pos;
    if (!Array.isArray(pos) || pos.length < 2 ||
        !Number.isFinite(pos[0]) || !Number.isFinite(pos[1])) {
        return null;
    }
    return { x: pos[0], y: pos[1] };
}

function getNodeCenter(node) {
    const position = getCanvasPosition(node);
    const size = node?.size;
    if (!position || !Array.isArray(size) || size.length < 2 ||
        !Number.isFinite(size[0]) || !Number.isFinite(size[1])) {
        return null;
    }
    return {
        x: position.x + size[0] / 2,
        y: position.y + size[1] / 2,
    };
}

function getGroupBounds(group) {
    const bounds = group?.bounding;
    if (!Array.isArray(bounds) || bounds.length < 4 ||
        !bounds.every(Number.isFinite) || bounds[2] < 0 || bounds[3] < 0) {
        return null;
    }
    return {
        left: bounds[0],
        top: bounds[1],
        right: bounds[0] + bounds[2],
        bottom: bounds[1] + bounds[3],
    };
}

function compareNodeCanvasOrder(left, right) {
    const leftPosition = getCanvasPosition(left.node);
    const rightPosition = getCanvasPosition(right.node);
    if (leftPosition && rightPosition) {
        return leftPosition.y - rightPosition.y ||
            leftPosition.x - rightPosition.x ||
            left.index - right.index;
    }
    if (leftPosition) return -1;
    if (rightPosition) return 1;
    return left.index - right.index;
}

function compareGroupCanvasOrder(left, right) {
    return left.bounds.top - right.bounds.top ||
        left.bounds.left - right.bounds.left ||
        left.index - right.index;
}

function getNodeCompactionOrder(record) {
    const entries = record.nodes.map((node, index) => ({ node, index }));
    const groups = Array.isArray(record.graph.groups)
        ? record.graph.groups
            .map((group, index) => ({ index, bounds: getGroupBounds(group) }))
            .filter((entry) => entry.bounds)
            .sort(compareGroupCanvasOrder)
        : [];
    if (!groups.length) return entries.sort(compareNodeCanvasOrder);

    const ordered = [];
    const assigned = new Set();
    for (const { bounds } of groups) {
        const members = entries.filter((entry) => {
            if (assigned.has(entry.index)) return false;
            const center = getNodeCenter(entry.node);
            return center &&
                center.x >= bounds.left && center.x <= bounds.right &&
                center.y >= bounds.top && center.y <= bounds.bottom;
        });
        members.sort(compareNodeCanvasOrder);
        for (const entry of members) {
            assigned.add(entry.index);
            ordered.push(entry);
        }
    }

    const ungrouped = entries
        .filter((entry) => !assigned.has(entry.index))
        .sort(compareNodeCanvasOrder);
    return [...ordered, ...ungrouped];
}

function getDefinitions(graph, path) {
    const definitions = graph.definitions;
    if (definitions == null) return [];
    if (!isObject(definitions) || !Array.isArray(definitions.subgraphs)) {
        fail(`${path}.definitions`, 'expected a subgraphs array');
    }
    return definitions.subgraphs;
}

function collectGraphs(root) {
    const records = [];
    const definitionById = new Map();

    function visit(graph, path, isRoot = false) {
        if (!isObject(graph)) fail(path, 'expected a workflow graph object');
        if (graph.version !== 0.4 && graph.version !== 1) {
            fail(`${path}.version`, 'only workflow schema versions 0.4 and 1 are supported');
        }
        if (!Array.isArray(graph.nodes)) fail(`${path}.nodes`, 'expected an array');
        if (graph.links != null && !Array.isArray(graph.links)) fail(`${path}.links`, 'expected an array');
        if (graph.floatingLinks != null && !Array.isArray(graph.floatingLinks)) {
            fail(`${path}.floatingLinks`, 'expected an array');
        }

        const record = {
            graph,
            path,
            nodes: graph.nodes,
            nodeEntries: [],
            specialNodeIds: new Set(),
            regularLinks: [],
            floatingLinks: [],
        };
        records.push(record);

        if (!isRoot) {
            if (graph.id == null) fail(`${path}.id`, 'subgraph definitions require an ID');
            if (definitionById.has(graph.id)) {
                fail(`${path}.id`, `duplicate subgraph definition ID ${describeId(graph.id)}`);
            }
            definitionById.set(graph.id, record);

            for (const key of ['inputNode', 'outputNode']) {
                const node = graph[key];
                if (!isObject(node)) fail(`${path}.${key}`, 'expected a subgraph I/O node');
                const id = requireNodeId(node.id, `${path}.${key}.id`);
                if (record.specialNodeIds.has(id)) {
                    fail(`${path}.${key}.id`, `duplicate subgraph I/O node ID ${describeId(id)}`);
                }
                record.specialNodeIds.add(id);
            }
        }

        const definitions = getDefinitions(graph, path);
        for (let index = 0; index < definitions.length; index++) {
            visit(definitions[index], `${path}.definitions.subgraphs[${index}]`);
        }
    }

    visit(root, 'workflow', true);
    return { records, definitionById };
}

function linkFields(link, path, legacy) {
    if (legacy) {
        if (!Array.isArray(link) || link.length < 6) fail(path, 'expected a legacy six-item link tuple');
        return {
            get id() { return link[0]; },
            set id(value) { link[0] = value; },
            get originId() { return link[1]; },
            set originId(value) { link[1] = value; },
            get originSlot() { return link[2]; },
            get targetId() { return link[3]; },
            set targetId(value) { link[3] = value; },
            get targetSlot() { return link[4]; },
            get parentId() { return undefined; },
            clearParentId() {},
        };
    }

    if (!isObject(link)) fail(path, 'expected a link object');
    return {
        get id() { return link.id; },
        set id(value) { link.id = value; },
        get originId() { return link.origin_id; },
        set originId(value) { link.origin_id = value; },
        get originSlot() { return link.origin_slot; },
        get targetId() { return link.target_id; },
        set targetId(value) { link.target_id = value; },
        get targetSlot() { return link.target_slot; },
        get parentId() { return link.parentId; },
        clearParentId() { delete link.parentId; },
    };
}

function getEndpointSlot(record, nodeById, nodeId, slotIndex, direction, path) {
    const id = requireNodeId(nodeId, `${path}.${direction}_id`);
    const index = requireSlotIndex(slotIndex, `${path}.${direction}_slot`);
    const { graph } = record;

    if (record.specialNodeIds.has(id)) {
        const slots = id === graph.inputNode?.id ? graph.inputs : graph.outputs;
        if (!Array.isArray(slots) || !isObject(slots[index])) return null;
        return { slot: slots[index], key: 'linkIds', multiple: true };
    }

    const node = nodeById.get(id);
    const slots = direction === 'origin' ? node?.outputs : node?.inputs;
    if (!Array.isArray(slots) || !isObject(slots[index])) return null;
    return {
        slot: slots[index],
        key: direction === 'origin' ? 'links' : 'link',
        multiple: direction === 'origin',
    };
}

function slotReferencesId(endpoint, id) {
    const value = endpoint.slot[endpoint.key];
    return endpoint.multiple ? Array.isArray(value) && value.includes(id) : value === id;
}

function getLegacyLinkParents(record) {
    const parents = new Map();
    if (record.graph.version !== 0.4) return parents;
    const extensions = record.graph.extra?.linkExtensions;
    if (extensions == null) return parents;
    if (!Array.isArray(extensions)) {
        fail(`${record.path}.extra.linkExtensions`, 'expected an array');
    }
    for (let index = 0; index < extensions.length; index++) {
        const extension = extensions[index];
        const path = `${record.path}.extra.linkExtensions[${index}]`;
        if (!isObject(extension)) fail(path, 'expected a link extension object');
        const id = requireLinkId(extension.id, `${path}.id`);
        if (!Number.isInteger(extension.parentId)) fail(`${path}.parentId`, 'expected an integer reroute ID');
        parents.set(id, extension.parentId);
    }
    return parents;
}

function forEachSerializedSlot(record, callback) {
    function visit(owner, key, path) {
        const slots = owner[key];
        if (slots == null) return;
        if (!Array.isArray(slots)) fail(`${path}.${key}`, 'expected an array');
        for (let index = 0; index < slots.length; index++) {
            const slotPath = `${path}.${key}[${index}]`;
            if (!isObject(slots[index])) fail(slotPath, 'expected a slot object');
            callback(slots[index]);
        }
    }

    for (let index = 0; index < record.nodes.length; index++) {
        const node = record.nodes[index];
        const path = `${record.path}.nodes[${index}]`;
        visit(node, 'inputs', path);
        visit(node, 'outputs', path);
    }
    visit(record.graph, 'inputs', record.path);
    visit(record.graph, 'outputs', record.path);
}

function countStaleSlotReferences(record, links) {
    const expected = new Map();
    function add(endpoint, id) {
        const ids = expected.get(endpoint.slot) ?? [];
        ids.push(id);
        expected.set(endpoint.slot, ids);
    }
    for (const link of links) {
        add(link.origin, link.oldId);
        add(link.target, link.oldId);
    }

    let staleCount = 0;
    forEachSerializedSlot(record, (slot) => {
        const remaining = [...(expected.get(slot) ?? [])];
        const references = [];
        if (slot.link != null) references.push(slot.link);
        if (slot.links != null) {
            if (!Array.isArray(slot.links)) staleCount++;
            else references.push(...slot.links);
        }
        if (slot.linkIds != null) {
            if (!Array.isArray(slot.linkIds)) staleCount++;
            else references.push(...slot.linkIds);
        }
        for (const id of references) {
            const index = remaining.indexOf(id);
            if (index === -1) staleCount++;
            else remaining.splice(index, 1);
        }
    });
    return staleCount;
}

function resetSlotReferences(record) {
    forEachSerializedSlot(record, (slot) => {
        if ('link' in slot) slot.link = null;
        if ('links' in slot) slot.links = null;
        if ('linkIds' in slot) slot.linkIds = [];
    });
}

function addSlotReference(endpoint, id) {
    if (!endpoint.multiple) {
        endpoint.slot[endpoint.key] = id;
        return;
    }
    const ids = Array.isArray(endpoint.slot[endpoint.key]) ? endpoint.slot[endpoint.key] : [];
    ids.push(id);
    endpoint.slot[endpoint.key] = ids;
}

function getReroutes(record) {
    const reroutes = record.graph.version === 0.4
        ? record.graph.extra?.reroutes
        : record.graph.reroutes;
    if (reroutes == null) return [];
    if (!Array.isArray(reroutes)) fail(`${record.path}.reroutes`, 'expected an array');
    return reroutes;
}

function repairReroutes(record, regularLinks, floatingLinks) {
    const reroutes = getReroutes(record);
    const byId = new Map();
    for (let index = 0; index < reroutes.length; index++) {
        const reroute = reroutes[index];
        const path = `${record.path}.reroutes[${index}]`;
        if (!isObject(reroute)) fail(path, 'expected a reroute object');
        const id = requireLinkId(reroute.id, `${path}.id`);
        if (byId.has(id)) fail(`${path}.id`, `duplicate reroute ID ${id}`);
        byId.set(id, reroute);
        reroute.linkIds = [];
    }

    const live = new Set();
    const liveFloating = new Set();
    function attach(link, floating) {
        let parentId = link.parentId;
        const visited = new Set();
        while (parentId != null) {
            if (!Number.isInteger(parentId) || visited.has(parentId)) {
                fail(link.path, 'invalid or cyclic reroute parent chain');
            }
            visited.add(parentId);
            const reroute = byId.get(parentId);
            if (!reroute) {
                if (parentId === link.parentId) {
                    link.parentId = undefined;
                    link.fields.clearParentId();
                }
                break;
            }
            live.add(parentId);
            if (floating) liveFloating.add(parentId);
            else reroute.linkIds.push(link.fields.id);
            const nextParentId = reroute.parentId;
            if (nextParentId != null && !byId.has(nextParentId)) delete reroute.parentId;
            parentId = reroute.parentId;
        }
    }

    for (const link of regularLinks) attach(link, false);
    for (const link of floatingLinks) attach(link, true);

    const kept = reroutes.filter((reroute) => live.has(reroute.id));
    for (const reroute of kept) {
        if (!liveFloating.has(reroute.id)) delete reroute.floating;
    }
    if (record.graph.version === 0.4) {
        if (kept.length) {
            record.graph.extra ??= {};
            record.graph.extra.reroutes = kept;
        } else if (record.graph.extra) delete record.graph.extra.reroutes;
    } else if (kept.length) {
        record.graph.reroutes = kept;
    } else {
        delete record.graph.reroutes;
    }

    if (record.graph.version === 0.4) {
        const extensions = regularLinks
            .filter((link) => link.parentId != null && byId.has(link.parentId))
            .map((link) => ({ id: link.fields.id, parentId: link.parentId }));
        if (extensions.length) {
            record.graph.extra ??= {};
            record.graph.extra.linkExtensions = extensions;
        } else if (record.graph.extra) delete record.graph.extra.linkExtensions;
    } else if (record.graph.extra) {
        delete record.graph.extra.linkExtensions;
    }

    return reroutes.length - kept.length;
}

function repairGraphLinks(record) {
    const nodeById = new Map();
    for (let index = 0; index < record.nodes.length; index++) {
        const node = record.nodes[index];
        const path = `${record.path}.nodes[${index}]`;
        if (!isObject(node)) fail(path, 'expected a node object');
        nodeById.set(requireNodeId(node.id, `${path}.id`), node);
    }

    const legacyParents = getLegacyLinkParents(record);
    const regularEntries = [];
    const seenRegularIds = new Set();
    let duplicateLinkCount = 0;
    for (let index = 0; index < (record.graph.links ?? []).length; index++) {
        const raw = record.graph.links[index];
        const path = `${record.path}.links[${index}]`;
        const fields = linkFields(raw, path, record.graph.version === 0.4);
        const oldId = requireLinkId(fields.id, `${path}.id`);
        if (seenRegularIds.has(oldId)) duplicateLinkCount++;
        else seenRegularIds.add(oldId);
        const origin = getEndpointSlot(record, nodeById, fields.originId, fields.originSlot, 'origin', path);
        const target = getEndpointSlot(record, nodeById, fields.targetId, fields.targetSlot, 'target', path);
        regularEntries.push({
            raw,
            fields,
            oldId,
            path,
            origin,
            target,
            parentId: record.graph.version === 0.4 ? legacyParents.get(oldId) : fields.parentId,
        });
    }

    const claimedTargets = new Set();
    const keptRegular = [];
    for (let index = regularEntries.length - 1; index >= 0; index--) {
        const link = regularEntries[index];
        if (!link.origin || !link.target || !slotReferencesId(link.target, link.oldId)) continue;
        if (claimedTargets.has(link.target.slot)) continue;
        claimedTargets.add(link.target.slot);
        keptRegular.push(link);
    }
    keptRegular.reverse();

    const floatingEntries = [];
    const seenFloatingIds = new Set();
    for (let index = 0; index < (record.graph.floatingLinks ?? []).length; index++) {
        const raw = record.graph.floatingLinks[index];
        const path = `${record.path}.floatingLinks[${index}]`;
        const fields = linkFields(raw, path, false);
        const oldId = requireLinkId(fields.id, `${path}.id`);
        if (seenFloatingIds.has(oldId)) duplicateLinkCount++;
        else seenFloatingIds.add(oldId);
        const originDisconnected = fields.originId === -1;
        const targetDisconnected = fields.targetId === -1;
        let endpoint = null;
        if (originDisconnected !== targetDisconnected) {
            endpoint = originDisconnected
                ? getEndpointSlot(record, nodeById, fields.targetId, fields.targetSlot, 'target', path)
                : getEndpointSlot(record, nodeById, fields.originId, fields.originSlot, 'origin', path);
        }
        floatingEntries.push({
            raw,
            fields,
            oldId,
            path,
            endpoint,
            parentId: fields.parentId,
        });
    }

    const keptFloating = [];
    const claimedFloatingIds = new Set();
    for (let index = floatingEntries.length - 1; index >= 0; index--) {
        const link = floatingEntries[index];
        if (!link.endpoint || claimedFloatingIds.has(link.oldId)) continue;
        claimedFloatingIds.add(link.oldId);
        keptFloating.push(link);
    }
    keptFloating.reverse();

    const removedLinkReferenceCount = countStaleSlotReferences(record, keptRegular);
    resetSlotReferences(record);
    let temporaryId = 0;
    for (const link of keptRegular) {
        link.fields.id = ++temporaryId;
        addSlotReference(link.origin, temporaryId);
        addSlotReference(link.target, temporaryId);
    }
    for (const link of keptFloating) link.fields.id = ++temporaryId;

    if (record.graph.links != null) record.graph.links = keptRegular.map((link) => link.raw);
    if (record.graph.floatingLinks != null) {
        record.graph.floatingLinks = keptFloating.map((link) => link.raw);
    }
    const removedRerouteCount = repairReroutes(record, keptRegular, keptFloating);

    return {
        duplicateLinkCount,
        removedLinkCount: regularEntries.length + floatingEntries.length - keptRegular.length - keptFloating.length,
        removedLinkReferenceCount,
        removedRerouteCount,
    };
}

function collectIds(records, definitionById) {
    const allNodeIds = new Set();
    const visitedRecords = new Set();
    let nextNodeId = 0;
    let nextLinkId = 0;

    function collectRecordNodeIds(record) {
        if (visitedRecords.has(record)) return;
        visitedRecords.add(record);

        for (const { node, index } of getNodeCompactionOrder(record)) {
            const path = `${record.path}.nodes[${index}]`;
            if (!isObject(node)) fail(path, 'expected a node object');
            const id = requireNodeId(node.id, `${path}.id`);
            if (record.specialNodeIds.has(id)) {
                fail(`${path}.id`, `node ID ${describeId(id)} conflicts with a subgraph I/O node`);
            }
            if (allNodeIds.has(id)) fail(`${path}.id`, `duplicate node ID ${describeId(id)}`);
            allNodeIds.add(id);
            record.nodeEntries.push({ node, oldId: id });
            if (typeof id === 'number') {
                nextNodeId++;
                node.id = nextNodeId;
            }

            const definition = definitionById.get(node.type);
            if (definition) collectRecordNodeIds(definition);
        }
    }

    collectRecordNodeIds(records[0]);
    for (const record of records) collectRecordNodeIds(record);

    for (const record of records) {
        const regular = record.graph.links ?? [];
        const floating = record.graph.floatingLinks ?? [];
        const graphLinkIds = new Set();
        for (const [collection, label, target] of [
            [regular, 'links', record.regularLinks],
            [floating, 'floatingLinks', record.floatingLinks],
        ]) {
            for (let index = 0; index < collection.length; index++) {
                const path = `${record.path}.${label}[${index}]`;
                const fields = linkFields(collection[index], path, record.graph.version === 0.4 && label === 'links');
                const id = requireLinkId(fields.id, `${path}.id`);
                if (graphLinkIds.has(id)) fail(`${path}.id`, `duplicate link ID ${id} within this graph`);
                graphLinkIds.add(id);
                nextLinkId++;
                target.push({ fields, oldId: id, newId: nextLinkId, path });
            }
        }
    }

    return { nodeCount: nextNodeId, linkCount: nextLinkId };
}

function buildRemaps(records) {
    for (const record of records) {
        record.nodeRemap = new Map();
        for (const { node, oldId } of record.nodeEntries) {
            record.nodeRemap.set(oldId, node.id);
        }
        record.linkRemap = new Map();
        for (const link of [...record.regularLinks, ...record.floatingLinks]) {
            record.linkRemap.set(link.oldId, link.newId);
        }
    }
}

function remapGraphNodeRef(value, record, path, allowDisconnected = false) {
    const id = requireNodeId(value, path);
    if (allowDisconnected && id === -1) return id;
    if (record.specialNodeIds.has(id)) return id;
    if (!record.nodeRemap.has(id)) fail(path, `dangling node reference ${describeId(id)}`);
    return record.nodeRemap.get(id);
}

function remapSerializedNodeRef(value, record, path, allowLegacyMinusOne = false) {
    if (allowLegacyMinusOne && (value === -1 || value === '-1')) return value;
    if (record.nodeRemap.has(value)) return remapGraphNodeRef(value, record, path);
    if (typeof value === 'string' && /^-?\d+$/.test(value)) {
        const numericId = Number(value);
        if (record.nodeRemap.has(numericId)) {
            return String(remapGraphNodeRef(numericId, record, path));
        }
    }
    return remapGraphNodeRef(value, record, path);
}

function remapGraphLinkRef(value, record, path) {
    const id = requireLinkId(value, path);
    if (!record.linkRemap.has(id)) fail(path, `dangling link reference ${id}`);
    return record.linkRemap.get(id);
}

function remapUniqueLinkList(values, record, path) {
    if (values == null) return values;
    if (!Array.isArray(values)) fail(path, 'expected an array of link IDs');
    const seen = new Set();
    return values.map((value, index) => {
        const id = requireLinkId(value, `${path}[${index}]`);
        if (seen.has(id)) fail(`${path}[${index}]`, `duplicate link reference ${id}`);
        seen.add(id);
        return remapGraphLinkRef(id, record, `${path}[${index}]`);
    });
}

function patchSlot(slot, record, path) {
    if (!isObject(slot)) fail(path, 'expected a slot object');
    if (slot.link != null) slot.link = remapGraphLinkRef(slot.link, record, `${path}.link`);
    if (slot.links != null) slot.links = remapUniqueLinkList(slot.links, record, `${path}.links`);
    if (slot.linkIds != null) slot.linkIds = remapUniqueLinkList(slot.linkIds, record, `${path}.linkIds`);
}

function patchSlots(owner, key, record, path) {
    const slots = owner[key];
    if (slots == null) return;
    if (!Array.isArray(slots)) fail(`${path}.${key}`, 'expected an array');
    for (let index = 0; index < slots.length; index++) {
        patchSlot(slots[index], record, `${path}.${key}[${index}]`);
    }
}

function patchProxyWidgets(node, definitionById, path) {
    const rawProxyWidgets = node.properties?.proxyWidgets;
    if (rawProxyWidgets == null) return;
    let proxyWidgets = rawProxyWidgets;
    const serializedAsString = typeof rawProxyWidgets === 'string';
    if (serializedAsString) {
        try {
            proxyWidgets = JSON.parse(rawProxyWidgets);
        } catch {
            fail(`${path}.properties.proxyWidgets`, 'expected valid JSON');
        }
    }
    if (!Array.isArray(proxyWidgets)) fail(`${path}.properties.proxyWidgets`, 'expected an array');

    const definition = definitionById.get(node.type);
    if (!definition && proxyWidgets.length) {
        fail(`${path}.properties.proxyWidgets`, `node type ${describeId(node.type)} has no subgraph definition`);
    }
    if (!definition) return;

    for (let index = 0; index < proxyWidgets.length; index++) {
        const entry = proxyWidgets[index];
        const entryPath = `${path}.properties.proxyWidgets[${index}]`;
        if (!Array.isArray(entry) || (entry.length !== 2 && entry.length !== 3)) {
            fail(entryPath, 'expected [nodeId, widgetName] or [nodeId, widgetName, disambiguatingNodeId]');
        }
        for (const position of entry.length === 3 ? [0, 2] : [0]) {
            const rawId = entry[position];
            entry[position] = remapSerializedNodeRef(
                rawId,
                definition,
                `${entryPath}[${position}]`,
                true,
            );
        }
    }
    if (serializedAsString) node.properties.proxyWidgets = JSON.stringify(proxyWidgets);
}

function patchLinearData(graph, record) {
    const linearData = graph.extra?.linearData;
    if (linearData == null) return;
    if (!isObject(linearData)) fail(`${record.path}.extra.linearData`, 'expected an object');

    if (linearData.inputs != null) {
        if (!Array.isArray(linearData.inputs)) fail(`${record.path}.extra.linearData.inputs`, 'expected an array');
        for (let index = 0; index < linearData.inputs.length; index++) {
            const input = linearData.inputs[index];
            const path = `${record.path}.extra.linearData.inputs[${index}]`;
            if (!Array.isArray(input) || input.length < 2) fail(path, 'expected a linear input tuple');
            input[0] = remapGraphNodeRef(input[0], record, `${path}[0]`);
        }
    }

    if (linearData.outputs != null) {
        if (!Array.isArray(linearData.outputs)) fail(`${record.path}.extra.linearData.outputs`, 'expected an array');
        linearData.outputs = linearData.outputs.map((id, index) =>
            remapGraphNodeRef(id, record, `${record.path}.extra.linearData.outputs[${index}]`));
    }
}

function patchGraph(record, definitionById, finalNodeId, finalLinkId) {
    const { graph, path } = record;

    for (const link of record.regularLinks) {
        link.fields.originId = remapGraphNodeRef(link.fields.originId, record, `${link.path}.origin_id`);
        link.fields.targetId = remapGraphNodeRef(link.fields.targetId, record, `${link.path}.target_id`);
        link.fields.id = link.newId;
    }
    for (const link of record.floatingLinks) {
        link.fields.originId = remapGraphNodeRef(link.fields.originId, record, `${link.path}.origin_id`, true);
        link.fields.targetId = remapGraphNodeRef(link.fields.targetId, record, `${link.path}.target_id`, true);
        link.fields.id = link.newId;
    }

    for (let nodeIndex = 0; nodeIndex < graph.nodes.length; nodeIndex++) {
        const node = graph.nodes[nodeIndex];
        const nodePath = `${path}.nodes[${nodeIndex}]`;
        patchSlots(node, 'inputs', record, nodePath);
        patchSlots(node, 'outputs', record, nodePath);
        patchProxyWidgets(node, definitionById, nodePath);
    }

    for (const key of ['inputs', 'outputs']) patchSlots(graph, key, record, path);

    if (graph.widgets != null) {
        if (!Array.isArray(graph.widgets)) fail(`${path}.widgets`, 'expected an array');
        for (let index = 0; index < graph.widgets.length; index++) {
            const widget = graph.widgets[index];
            const widgetPath = `${path}.widgets[${index}]`;
            if (!isObject(widget)) fail(widgetPath, 'expected an exposed widget object');
            widget.id = remapSerializedNodeRef(widget.id, record, `${widgetPath}.id`);
        }
    }

    const reroutes = graph.version === 0.4 ? graph.extra?.reroutes : graph.reroutes;
    if (reroutes != null) {
        if (!Array.isArray(reroutes)) fail(`${path}.reroutes`, 'expected an array');
        for (let index = 0; index < reroutes.length; index++) {
            const reroute = reroutes[index];
            const reroutePath = `${path}.reroutes[${index}]`;
            if (!isObject(reroute)) fail(reroutePath, 'expected a reroute object');
            if (reroute.linkIds != null) {
                reroute.linkIds = remapUniqueLinkList(reroute.linkIds, record, `${reroutePath}.linkIds`);
            }
        }
    }

    const linkExtensions = graph.extra?.linkExtensions;
    if (linkExtensions != null) {
        if (!Array.isArray(linkExtensions)) fail(`${path}.extra.linkExtensions`, 'expected an array');
        const regularIds = new Set(record.regularLinks.map((link) => link.oldId));
        const seen = new Set();
        for (let index = 0; index < linkExtensions.length; index++) {
            const extension = linkExtensions[index];
            const extensionPath = `${path}.extra.linkExtensions[${index}]`;
            if (!isObject(extension)) fail(extensionPath, 'expected a link extension object');
            const id = requireLinkId(extension.id, `${extensionPath}.id`);
            if (!regularIds.has(id)) fail(`${extensionPath}.id`, `dangling regular link reference ${id}`);
            if (seen.has(id)) fail(`${extensionPath}.id`, `duplicate link extension for ${id}`);
            seen.add(id);
            extension.id = record.linkRemap.get(id);
        }
    }

    patchLinearData(graph, record);

    if (graph.version === 1) {
        if (!isObject(graph.state)) fail(`${path}.state`, 'expected a graph state object');
        graph.state.lastNodeId = finalNodeId;
        graph.state.lastLinkId = finalLinkId;
    } else {
        graph.last_node_id = finalNodeId;
        graph.last_link_id = finalLinkId;
    }
}

export function normalizeWorkflowIds(workflow) {
    if (!isObject(workflow)) fail('workflow', 'expected an object');
    const originalJson = JSON.stringify(workflow);
    const normalized = cloneWorkflow(workflow);
    const { records, definitionById } = collectGraphs(normalized);
    const repairs = {
        duplicateLinkCount: 0,
        removedLinkCount: 0,
        removedLinkReferenceCount: 0,
        removedRerouteCount: 0,
    };
    for (const record of records) {
        const graphRepairs = repairGraphLinks(record);
        for (const key of Object.keys(repairs)) repairs[key] += graphRepairs[key];
    }
    const { nodeCount, linkCount } = collectIds(records, definitionById);
    buildRemaps(records);

    for (const record of records) {
        patchGraph(record, definitionById, nodeCount, linkCount);
    }

    return {
        workflow: normalized,
        changed: JSON.stringify(normalized) !== originalJson,
        nodeCount,
        stringNodeCount: records.reduce(
            (count, record) => count + record.nodes.filter((node) => typeof node.id === 'string').length,
            0,
        ),
        linkCount,
        graphCount: records.length,
        ...repairs,
    };
}
