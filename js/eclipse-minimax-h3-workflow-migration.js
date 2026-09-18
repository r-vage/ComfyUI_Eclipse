/**
 * MiniMax H3 V2 planner widget-to-input migration helpers.
 * SPDX-License-Identifier: Apache-2.0
 */

const PLANNER_NAME = 'MiniMax H3 Audio Timeline Planner V2 [Eclipse]';
const STRING_NODE = 'String Multiline [Eclipse]';
const STRING_LIST_NODE = 'String Multiline List [Eclipse]';
const DEFAULT_CUT_INSTRUCTION = (
    'During the hidden lead-in, rapidly orbit to a strong left-side three-quarter '
    + 'camera angle and dolly far backward. At the cut, hold a waist-up medium-wide '
    + 'shot, visibly different from the prior frontal close-up. Preserve identity, '
    + 'wardrobe, scene, lighting, and ongoing action; do not show a frontal close-up.'
);

const LEGACY = Object.freeze({
    conditioningFamily: 0,
    segmentStrategy: 1,
    warmupSeconds: 2,
    resetAnchor: 3,
    technicalSplitSource: 4,
    refImageSize: 5,
    manualTimes: 6,
    maxRenderFrames: 7,
    alignToActivityGap: 8,
    transitionEdge: 9,
    searchWindowSeconds: 10,
    minGapDuration: 11,
    resumeHoldDuration: 12,
    technicalSeamStyle: 13,
    technicalCutInstruction: 14,
});

const SOCKET_ORDER = Object.freeze([
    'audio',
    'image_batch',
    'manual_transition_times',
    'audio_encoder_output',
    'conditioning_audio',
    'technical_cut_instruction',
]);
const WIDGET_ORDER = Object.freeze([
    'conditioning_family',
    'segment_strategy',
    'warmup_seconds',
    'reset_anchor',
    'technical_split_source',
    'ref_image_size',
    'max_render_frames',
    'align_to_activity_gap',
    'transition_edge',
    'search_window_seconds',
    'min_gap_duration',
    'resume_hold_duration',
    'technical_seam_style',
]);

function inputName(input) {
    return input?.name ?? input?.widget?.name ?? '';
}

function isFiniteNumber(value) {
    return typeof value === 'number' && Number.isFinite(value);
}

function legacyLayout(values) {
    if (!Array.isArray(values)) return null;
    for (let offset = 0; offset + LEGACY.technicalCutInstruction < values.length; offset++) {
        if (
            ['fl2va_keyframes', 'ref2va_active_reference'].includes(
                values[offset + LEGACY.conditioningFamily]
            )
            && ['hard_cut', 'first_last_bridge', 'warmup_reset'].includes(
                values[offset + LEGACY.segmentStrategy]
            )
            && isFiniteNumber(values[offset + LEGACY.warmupSeconds])
            && ['first_only', 'same_first_last_experimental'].includes(
                values[offset + LEGACY.resetAnchor]
            )
            && ['original_image_reset', 'generated_continuation'].includes(
                values[offset + LEGACY.technicalSplitSource]
            )
            && ['match', 'max'].includes(values[offset + LEGACY.refImageSize])
            && typeof values[offset + LEGACY.manualTimes] === 'string'
            && Number.isInteger(values[offset + LEGACY.maxRenderFrames])
            && typeof values[offset + LEGACY.alignToActivityGap] === 'boolean'
            && ['activity_resume', 'silence_start'].includes(
                values[offset + LEGACY.transitionEdge]
            )
            && isFiniteNumber(values[offset + LEGACY.searchWindowSeconds])
            && isFiniteNumber(values[offset + LEGACY.minGapDuration])
            && isFiniteNumber(values[offset + LEGACY.resumeHoldDuration])
            && ['plain_reset', 'intentional_camera_cut'].includes(
                values[offset + LEGACY.technicalSeamStyle]
            )
            && typeof values[offset + LEGACY.technicalCutInstruction] === 'string'
        ) {
            return { offset };
        }
    }
    return null;
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

function collectGraphs(workflow) {
    const graphs = [];
    const visited = new Set();
    const visit = (graph) => {
        if (!graph || visited.has(graph)) return;
        visited.add(graph);
        graphs.push(graph);
        for (const subgraph of graph.definitions?.subgraphs ?? []) visit(subgraph);
    };
    visit(workflow);
    return graphs;
}

function idAllocator(workflow) {
    const graphs = collectGraphs(workflow);
    const nodeIds = new Set();
    const linkIds = new Set();
    let numericNode = 0;
    let numericLink = 0;
    let fallback = 0;
    for (const graph of graphs) {
        for (const node of graph.nodes ?? []) {
            nodeIds.add(String(node.id));
            if (typeof node.id === 'number') numericNode = Math.max(numericNode, node.id);
        }
        for (const list of [graph.links, graph.floatingLinks].filter(Array.isArray)) {
            for (const link of list) {
                const id = linkFields(link).id;
                linkIds.add(String(id));
                if (typeof id === 'number') numericLink = Math.max(numericLink, id);
            }
        }
        if (typeof graph.last_node_id === 'number') {
            numericNode = Math.max(numericNode, graph.last_node_id);
        }
        if (typeof graph.last_link_id === 'number') {
            numericLink = Math.max(numericLink, graph.last_link_id);
        }
    }
    const stringId = (kind, used) => {
        let id;
        do {
            id = globalThis.crypto?.randomUUID?.()
                ?? `eclipse-minimax-h3-${kind}-${++fallback}`;
        } while (used.has(String(id)));
        used.add(String(id));
        return id;
    };
    return {
        node(graph, numeric) {
            const id = numeric ? ++numericNode : stringId('node', nodeIds);
            nodeIds.add(String(id));
            if (numeric) {
                graph.last_node_id = Math.max(graph.last_node_id ?? 0, id);
                if (graph.state && typeof graph.state === 'object') {
                    graph.state.lastNodeId = Math.max(graph.state.lastNodeId ?? 0, id);
                }
            }
            return id;
        },
        link(graph, numeric) {
            const id = numeric ? ++numericLink : stringId('link', linkIds);
            linkIds.add(String(id));
            if (numeric) {
                graph.last_link_id = Math.max(graph.last_link_id ?? 0, id);
                if (graph.state && typeof graph.state === 'object') {
                    graph.state.lastLinkId = Math.max(graph.state.lastLinkId ?? 0, id);
                }
            }
            return id;
        },
    };
}

function graphUsesNumericLinks(graph, fallback) {
    for (const list of [graph.links, graph.floatingLinks].filter(Array.isArray)) {
        if (list.length) return typeof linkFields(list[0]).id === 'number';
    }
    return fallback;
}

function createTextNode(id, type, value, pos, order) {
    const isList = type === STRING_LIST_NODE;
    return {
        id,
        type,
        pos,
        size: isList ? [420, 220] : [360, 140],
        flags: {},
        order,
        mode: 0,
        inputs: [
            {
                localized_name: 'input_string',
                name: 'input_string',
                shape: 7,
                type: 'STRING',
                link: null,
            },
            {
                localized_name: 'string',
                name: 'string',
                type: 'STRING',
                widget: { name: 'string' },
                link: null,
            },
        ],
        outputs: isList
            ? [
                { localized_name: 'string', name: 'string', type: 'STRING', links: [] },
                {
                    localized_name: 'string_list',
                    name: 'string_list',
                    shape: 6,
                    type: 'STRING',
                    links: [],
                },
            ]
            : [{ localized_name: 'string', name: 'string', type: 'STRING', links: [] }],
        properties: { 'Node name for S&R': type },
        widgets_values: [value],
        widgets_values_named: { string: value },
    };
}

function appendLink(graph, allocator, origin, originSlot, target, targetSlot) {
    graph.links ??= [];
    const numeric = graphUsesNumericLinks(graph, typeof target.id === 'number');
    const id = allocator.link(graph, numeric);
    const objectLinks = graph.links.some((link) => !Array.isArray(link))
        || (!graph.links.length && graph.version !== 0.4);
    const link = objectLinks
        ? {
            id,
            origin_id: origin.id,
            origin_slot: originSlot,
            target_id: target.id,
            target_slot: targetSlot,
            type: 'STRING',
        }
        : [id, origin.id, originSlot, target.id, targetSlot, 'STRING'];
    graph.links.push(link);
    origin.outputs[originSlot].links.push(id);
    target.inputs[targetSlot].link = id;
    return id;
}

function reorderPlannerInputs(graph, node) {
    if (!Array.isArray(node.inputs)) return;
    const oldInputs = node.inputs;
    const byName = new Map(oldInputs.map((input) => [inputName(input), input]));
    for (const name of ['manual_transition_times', 'technical_cut_instruction']) {
        const input = byName.get(name);
        if (!input) continue;
        delete input.widget;
        input.shape ??= 7;
        if (name === 'technical_cut_instruction') {
            input.localized_name = 'Technical Cut Instructions';
        }
    }
    const orderedNames = [...SOCKET_ORDER, ...WIDGET_ORDER];
    const retained = orderedNames.map((name) => byName.get(name)).filter(Boolean);
    const retainedSet = new Set(retained);
    retained.push(...oldInputs.filter((input) => !retainedSet.has(input)));
    const newIndexes = new Map(retained.map((input, index) => [input, index]));
    const oldToNew = new Map(
        oldInputs.map((input, index) => [index, newIndexes.get(input)])
    );
    for (const list of [graph.links, graph.floatingLinks].filter(Array.isArray)) {
        for (const link of list) {
            const fields = linkFields(link);
            if (String(fields.targetId) !== String(node.id)) continue;
            const newIndex = oldToNew.get(fields.targetSlot);
            if (newIndex !== undefined) fields.setTargetSlot(newIndex);
        }
    }
    node.inputs = retained;
}

function migrateNode(graph, node, allocator) {
    if (node?.type !== PLANNER_NAME || !Array.isArray(node.inputs)) return null;
    const layout = legacyLayout(node.widgets_values);
    const named = node.widgets_values_named;
    const hasNamed = named && typeof named === 'object' && !Array.isArray(named);
    const namedManual = hasNamed && Object.hasOwn(named, 'manual_transition_times');
    const namedCut = hasNamed && Object.hasOwn(named, 'technical_cut_instruction');
    if (!layout && !namedManual && !namedCut) return null;

    const manualValue = namedManual
        ? named.manual_transition_times
        : (layout ? node.widgets_values[layout.offset + LEGACY.manualTimes] : '');
    const cutValue = namedCut
        ? named.technical_cut_instruction
        : (layout
            ? node.widgets_values[layout.offset + LEGACY.technicalCutInstruction]
            : DEFAULT_CUT_INSTRUCTION);
    if (typeof manualValue !== 'string' || typeof cutValue !== 'string') return null;

    if (layout) {
        const values = [...node.widgets_values];
        values.splice(layout.offset + LEGACY.technicalCutInstruction, 1);
        values.splice(layout.offset + LEGACY.manualTimes, 1);
        node.widgets_values = values;
    }
    if (hasNamed) {
        const values = { ...named };
        delete values.manual_transition_times;
        delete values.technical_cut_instruction;
        node.widgets_values_named = values;
    }

    reorderPlannerInputs(graph, node);
    const manualSlot = node.inputs.findIndex(
        (input) => inputName(input) === 'manual_transition_times'
    );
    const cutSlot = node.inputs.findIndex(
        (input) => inputName(input) === 'technical_cut_instruction'
    );
    if (manualSlot < 0 || cutSlot < 0) return null;

    const [x, y] = Array.isArray(node.pos) ? node.pos : [0, 0];
    const numericNodes = typeof node.id === 'number';
    const maxOrder = Math.max(0, ...(graph.nodes ?? []).map((item) => item.order ?? 0));
    const created = [];
    let links = 0;
    if (node.inputs[manualSlot].link == null) {
        const textNode = createTextNode(
            allocator.node(graph, numericNodes),
            STRING_NODE,
            manualValue,
            [x - 400, y + 40],
            maxOrder + created.length + 1
        );
        graph.nodes.push(textNode);
        appendLink(graph, allocator, textNode, 0, node, manualSlot);
        created.push(textNode);
        links++;
    }
    if (node.inputs[cutSlot].link == null) {
        const listNode = createTextNode(
            allocator.node(graph, numericNodes),
            STRING_LIST_NODE,
            cutValue,
            [x - 460, y + 240],
            maxOrder + created.length + 1
        );
        graph.nodes.push(listNode);
        appendLink(graph, allocator, listNode, 1, node, cutSlot);
        created.push(listNode);
        links++;
    }
    return { nodes: created.length, links };
}

export function migrateMiniMaxH3Workflow(workflow) {
    const result = { planners: 0, nodes: 0, links: 0 };
    const allocator = idAllocator(workflow);
    for (const graph of collectGraphs(workflow)) {
        for (const node of [...(graph.nodes ?? [])]) {
            const migrated = migrateNode(graph, node, allocator);
            if (!migrated) continue;
            result.planners++;
            result.nodes += migrated.nodes;
            result.links += migrated.links;
        }
    }
    return result;
}
