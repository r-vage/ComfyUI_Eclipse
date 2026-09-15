/**
 * Smart Folder widget-value migration helpers.
 * SPDX-License-Identifier: Apache-2.0
 */

const SMART_FOLDER_NODE_NAMES = Object.freeze(new Set([
    'Smart Folder [Eclipse]',
    'Smart Folder v2 [Eclipse]',
]));

const LEGACY = Object.freeze({
    generationMode: 0,
    dateTimeEnabled: 3,
    dateTimePosition: 5,
    batchEnabled: 6,
    batchControl: 9,
    imageSizeEnabled: 10,
    width: 12,
    height: 13,
    videoWidth: 16,
    videoHeight: 17,
    skipControl: 25,
    useSeed: 28,
});

const INSERTED_DEFAULTS = Object.freeze([
    8,      // divisible_by
    true,   // use_vhs
    true,   // use_loop
    true,   // use_context
    false,  // use_duration
    15.0,   // duration
]);
const ASPECT_DEFAULTS = Object.freeze(['Free', 'width']);
const ASPECT_RATIOS = Object.freeze(new Set([
    'Free', '1:1', '2:3', '3:2', '3:4', '4:3', '4:5', '5:4',
    '9:16', '16:9', '9:21', '21:9',
]));
const ASPECT_DRIVERS = Object.freeze(new Set(['width', 'height']));

function isBoolean(value) {
    return value === true || value === false;
}

function isFiniteNumber(value) {
    return typeof value === 'number' && Number.isFinite(value);
}

function isLegacyLayoutAt(values, offset) {
    return (
        ['Image Mode', 'Video Mode'].includes(values[offset + LEGACY.generationMode])
        && isBoolean(values[offset + LEGACY.dateTimeEnabled])
        && ['prefix', 'postfix'].includes(values[offset + LEGACY.dateTimePosition])
        && isBoolean(values[offset + LEGACY.batchEnabled])
        && ['fixed', 'increment'].includes(values[offset + LEGACY.batchControl])
        && isBoolean(values[offset + LEGACY.imageSizeEnabled])
        && isFiniteNumber(values[offset + LEGACY.width])
        && isFiniteNumber(values[offset + LEGACY.height])
        && isFiniteNumber(values[offset + LEGACY.videoWidth])
        && isFiniteNumber(values[offset + LEGACY.videoHeight])
        && ['fixed', 'increment'].includes(values[offset + LEGACY.skipControl])
        && isBoolean(values[offset + LEGACY.useSeed])
    );
}

function isCurrentLayoutAt(values, offset) {
    const insertion = offset + LEGACY.videoHeight + 1;
    const divisor = values[insertion];
    const duration = values[insertion + 5];
    return (
        Number.isInteger(divisor)
        && divisor >= 1
        && divisor <= 512
        && INSERTED_DEFAULTS.slice(1, 5).every(
            (_value, index) => isBoolean(values[insertion + index + 1])
        )
        && isFiniteNumber(duration)
        && duration >= 0.5
        && duration <= 180.0
    );
}

function isAspectLayoutAt(values, offset) {
    const divisor = values[offset + 22];
    const duration = values[offset + 27];
    return (
        ASPECT_RATIOS.has(values[offset + 12])
        && ASPECT_DRIVERS.has(values[offset + 13])
        && isFiniteNumber(values[offset + 14])
        && isFiniteNumber(values[offset + 15])
        && ASPECT_RATIOS.has(values[offset + 18])
        && ASPECT_DRIVERS.has(values[offset + 19])
        && isFiniteNumber(values[offset + 20])
        && isFiniteNumber(values[offset + 21])
        && Number.isInteger(divisor)
        && divisor >= 1
        && divisor <= 512
        && [23, 24, 25, 26].every((index) => isBoolean(values[offset + index]))
        && isFiniteNumber(duration)
        && duration >= 0.5
        && duration <= 180.0
    );
}

function findSchemaLayout(values) {
    for (let offset = 0; offset + LEGACY.skipControl < values.length; offset++) {
        if (isAspectLayoutAt(values, offset)) return { kind: 'aspect', offset };
        if (isCurrentLayoutAt(values, offset)) return { kind: 'current', offset };
        if (isLegacyLayoutAt(values, offset)) return { kind: 'legacy', offset };
    }
    return null;
}

function migrateCosmeticFeatures(value, videoMode) {
    const wasString = typeof value === 'string';
    const features = Array.isArray(value)
        ? [...value]
        : (wasString ? value.split(',').map((item) => item.trim()).filter(Boolean) : null);
    if (!features || !features.some((item) => item === 'image' || item === 'video')) {
        return value;
    }
    if (videoMode && !features.includes('image_size')) features.push('image_size');
    for (const feature of ['vhs', 'loop', 'context']) {
        if (!features.includes(feature)) features.push(feature);
    }
    return wasString ? features.join(',') : features;
}

function featuresFromBacking(values, offset) {
    const features = [];
    features.push(values[offset + LEGACY.generationMode] === 'Video Mode' ? 'video' : 'image');
    if (values[offset + LEGACY.dateTimeEnabled]) features.push('date_time');
    if (values[offset + LEGACY.batchEnabled]) features.push('batch');
    if (values[offset + LEGACY.imageSizeEnabled]) features.push('image_size');
    features.push('vhs', 'loop', 'context');
    if (values[offset + LEGACY.useSeed]) features.push('seed');
    return features;
}

function migrateNode(node) {
    if (!SMART_FOLDER_NODE_NAMES.has(node?.type) || !Array.isArray(node.widgets_values)) {
        return false;
    }
    const values = node.widgets_values;
    const layout = findSchemaLayout(values);
    if (!layout || layout.kind === 'aspect') return false;

    let migrated = [...values];
    let offset = layout.offset;
    const videoMode = values[offset + LEGACY.generationMode] === 'Video Mode';
    if (layout.kind === 'legacy') {
        const insertion = offset + LEGACY.videoHeight + 1;
        migrated = [
            ...migrated.slice(0, insertion),
            ...INSERTED_DEFAULTS,
            ...migrated.slice(insertion),
        ];

        // Video resolution used to be unconditional. Turn on the newly shared
        // Image Size group so a migrated Video workflow retains width and height.
        if (videoMode) migrated[offset + LEGACY.imageSizeEnabled] = true;
        for (let index = 0; index < offset; index++) {
            migrated[index] = migrateCosmeticFeatures(migrated[index], videoMode);
        }

        // Workflows from before the cosmetic combo row need one leading value so
        // the current row does not consume generation_mode and shift every widget.
        if (offset === 0) {
            migrated.unshift(featuresFromBacking(migrated, offset));
            offset = 1;
        }
    }

    const imageInsertion = offset + LEGACY.width;
    const videoInsertion = offset + LEGACY.videoWidth;
    migrated = [
        ...migrated.slice(0, imageInsertion),
        ...ASPECT_DEFAULTS,
        ...migrated.slice(imageInsertion, videoInsertion),
        ...ASPECT_DEFAULTS,
        ...migrated.slice(videoInsertion),
    ];

    if (node.widgets_values_named && typeof node.widgets_values_named === 'object') {
        const named = { ...node.widgets_values_named };
        named.divisible_by ??= 8;
        named.use_vhs ??= true;
        named.use_loop ??= true;
        named.use_context ??= true;
        named.use_duration ??= false;
        named.duration ??= 15.0;
        named.image_aspect_ratio ??= 'Free';
        named.image_aspect_driver ??= 'width';
        named.video_aspect_ratio ??= 'Free';
        named.video_aspect_driver ??= 'width';
        if (layout.kind === 'legacy') {
            if (videoMode) named.use_image_size = true;
            for (const name of ['_sf_features', 'features']) {
                if (name in named) named[name] = migrateCosmeticFeatures(named[name], videoMode);
            }
        }
        node.widgets_values_named = named;
    }

    // Assign a new array: callers holding the serialized legacy array never see
    // it mutated, and unknown cosmetic/trailing values retain their order.
    node.widgets_values = migrated;
    return true;
}

export function migrateSmartFolderWorkflow(workflow) {
    let nodes = 0;
    const visited = new Set();
    const visit = (graph) => {
        if (!graph || visited.has(graph)) return;
        visited.add(graph);
        for (const node of graph.nodes ?? []) {
            if (migrateNode(node)) nodes++;
        }
        for (const subgraph of graph.definitions?.subgraphs ?? []) visit(subgraph);
    };
    visit(workflow);
    return { nodes };
}
