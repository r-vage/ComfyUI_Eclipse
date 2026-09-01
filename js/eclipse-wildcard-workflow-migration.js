/**
 * Wildcard Processor input migration helpers.
 * SPDX-License-Identifier: Apache-2.0
 */

const NODE_NAME = 'Wildcard Processor [Eclipse]';

function inputName(input) {
    return input?.name ?? input?.widget?.name ?? '';
}

function linkFields(link) {
    if (Array.isArray(link)) {
        return {
            id: link[0],
            targetId: link[3],
            targetSlot: link[4],
            setTargetSlot(value) { link[4] = value; },
        };
    }
    return {
        id: link?.id,
        targetId: link?.target_id,
        targetSlot: link?.target_slot,
        setTargetSlot(value) { link.target_slot = value; },
    };
}

function visitSlots(graph, callback) {
    for (const node of graph.nodes ?? []) {
        for (const input of node.inputs ?? []) callback(input);
        for (const output of node.outputs ?? []) callback(output);
    }
    for (const input of graph.inputs ?? []) callback(input);
    for (const output of graph.outputs ?? []) callback(output);
}

function removeLinkReferences(graph, removedIds) {
    if (!removedIds.size) return;
    visitSlots(graph, (slot) => {
        if (removedIds.has(slot.link)) slot.link = null;
        for (const key of ['links', 'linkIds']) {
            if (Array.isArray(slot[key])) {
                slot[key] = slot[key].filter((id) => !removedIds.has(id));
            }
        }
    });
    const reroutes = graph.version === 0.4 ? graph.extra?.reroutes : graph.reroutes;
    for (const reroute of reroutes ?? []) {
        if (Array.isArray(reroute.linkIds)) {
            reroute.linkIds = reroute.linkIds.filter((id) => !removedIds.has(id));
        }
    }
    if (Array.isArray(graph.extra?.linkExtensions)) {
        graph.extra.linkExtensions = graph.extra.linkExtensions.filter(
            (extension) => !removedIds.has(extension.id)
        );
    }
}

function migrateGraph(graph) {
    if (!graph || !Array.isArray(graph.nodes)) return { nodes: 0, links: 0 };
    const linkLists = [graph.links, graph.floatingLinks].filter(Array.isArray);
    const removedIds = new Set();
    let migratedNodes = 0;

    for (const node of graph.nodes) {
        if (node?.type !== NODE_NAME || !Array.isArray(node.inputs)) continue;
        const oldInputs = node.inputs;
        const negativeIndexes = [];
        const retained = [];
        let canonicalSeed = null;
        let existingSeedIndex = -1;
        let localSeedIndex = -1;

        for (let index = 0; index < oldInputs.length; index++) {
            const input = oldInputs[index];
            const name = inputName(input);
            if (name === 'seed_input') {
                existingSeedIndex = index;
                canonicalSeed ??= input;
            } else if (name === 'seed') {
                localSeedIndex = index;
                retained.push({ input, oldIndex: index });
            } else if (name === 'negative_prompt') {
                negativeIndexes.push(index);
            } else {
                retained.push({ input, oldIndex: index });
            }
        }

        const targetLinks = [];
        for (const list of linkLists) {
            for (const link of list) {
                const fields = linkFields(link);
                if (String(fields.targetId) === String(node.id)) {
                    targetLinks.push(fields);
                }
            }
        }
        const linksAt = (index) => targetLinks.filter((link) => link.targetSlot === index);
        const existingLinks = existingSeedIndex >= 0 ? linksAt(existingSeedIndex) : [];
        const legacyLinks = localSeedIndex >= 0 ? linksAt(localSeedIndex) : [];
        const hasLegacySeedLink = localSeedIndex >= 0
            && (oldInputs[localSeedIndex]?.link != null || legacyLinks.length > 0);
        if (!negativeIndexes.length && !hasLegacySeedLink) continue;

        const preferredId = canonicalSeed?.link ?? existingLinks[0]?.id
            ?? oldInputs[localSeedIndex]?.link ?? legacyLinks[0]?.id ?? null;

        if (!canonicalSeed) {
            canonicalSeed = { name: 'seed_input', type: 'INT', link: null };
        }
        canonicalSeed.name = 'seed_input';
        canonicalSeed.type = canonicalSeed.type || 'INT';
        canonicalSeed.link = preferredId;
        delete canonicalSeed.widget;
        if (localSeedIndex >= 0) oldInputs[localSeedIndex].link = null;

        const indexMap = new Map();
        retained.forEach(({ oldIndex }, index) => indexMap.set(oldIndex, index + 1));
        const negativeSet = new Set(negativeIndexes);
        const externalSeedSet = new Set(
            [existingSeedIndex, hasLegacySeedLink ? localSeedIndex : -1].filter((index) => index >= 0)
        );

        for (const link of targetLinks) {
            if (negativeSet.has(link.targetSlot)) {
                removedIds.add(link.id);
                continue;
            }
            if (externalSeedSet.has(link.targetSlot)) {
                if (link.id === preferredId) link.setTargetSlot(0);
                else removedIds.add(link.id);
                continue;
            }
            const newIndex = indexMap.get(link.targetSlot);
            if (newIndex === undefined) {
                removedIds.add(link.id);
            } else {
                link.setTargetSlot(newIndex);
            }
        }

        node.inputs = [canonicalSeed, ...retained.map(({ input }) => input)];
        migratedNodes++;
    }

    for (const key of ['links', 'floatingLinks']) {
        if (Array.isArray(graph[key])) {
            graph[key] = graph[key].filter((link) => !removedIds.has(linkFields(link).id));
        }
    }
    removeLinkReferences(graph, removedIds);
    return { nodes: migratedNodes, links: removedIds.size };
}

export function migrateWildcardProcessorWorkflow(workflow) {
    const result = { nodes: 0, links: 0 };
    const visited = new Set();
    const visit = (graph) => {
        if (!graph || visited.has(graph)) return;
        visited.add(graph);
        const migrated = migrateGraph(graph);
        result.nodes += migrated.nodes;
        result.links += migrated.links;
        for (const subgraph of graph.definitions?.subgraphs ?? []) visit(subgraph);
    };
    visit(workflow);
    return result;
}
