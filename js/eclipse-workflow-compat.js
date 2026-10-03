/**
 * Restore published Eclipse node IDs and recognized historical widget layouts.
 * SPDX-License-Identifier: Apache-2.0
 */
import { app } from './comfy/index.js';

const LEGACY_NODE_IDS = new Map([
    ['Smart Folder v2 [Eclipse]', 'Smart Folder [Eclipse]'],
    ['Save Images v2 [Eclipse]', 'Save Images [Eclipse]'],
    ['Seed 32-bit [Eclipse]', 'Seed [Eclipse]'],
]);

function migrateNode(node) {
    const originalType = node.type;
    const values = node.widgets_values;
    const old32BitSeed = originalType === 'Seed 32-bit [Eclipse]';
    const legacySeed = Array.isArray(values) && values.length >= 1 && values.length <= 4
        && Number.isSafeInteger(values[0]) && values[0] >= -3
        && values.slice(1).every(value => value === '' || value == null);
    if (originalType === 'Seed [Eclipse]' || old32BitSeed) {
        // Old seed nodes saved the number followed by optional button placeholders.
        // The merged node inserts bit_depth before the number; names alone cannot
        // preserve a 32-bit seed or prevent the number shifting into that combo.
        if (legacySeed) {
            const bitDepth = old32BitSeed ? '32-bit' : '64-bit';
            node.widgets_values = [bitDepth, ...values];
            if (node.widgets_values_named && typeof node.widgets_values_named === 'object') {
                node.widgets_values_named = { bit_depth: bitDepth, ...node.widgets_values_named };
            }
        } else if (old32BitSeed) {
            // Do not reinterpret an unrecognized historical seed layout.
            return;
        }
    }
    if (originalType === 'Image Selector [Eclipse]' && Array.isArray(values)
        && values.length === 2 && Number.isFinite(values[0]) && values[1] === '') {
        // The former DOM placeholder now occupies the automation Boolean's slot.
        node.widgets_values = [values[0], false, values[1]];
    }
    // The pre-4.0 sampler has two extra upscale fields before upscale_value/seed.
    // Keep its historical schema and pipe channels instead of dropping values.
    const oldSampler = originalType === 'Smart Sampler Settings [Eclipse]'
        && Array.isArray(values) && [14, 17].includes(values.length)
        && (Array.isArray(values[0]) || typeof values[0] === 'string')
        && typeof values[1] === 'boolean'
        && Number.isInteger(values[10]) && values[10] >= 1
        && Number.isFinite(values[11]) && values[11] >= 0 && values[11] <= 1
        && Number.isFinite(values[12]) && values[12] > 0
        && Number.isSafeInteger(values[13])
        && values.slice(14).every(value => value === '' || value == null);
    const replacement = oldSampler
        ? 'Smart Sampler Settings (Legacy) [Eclipse]'
        : LEGACY_NODE_IDS.get(originalType);
    if (!replacement) return;
    node.type = replacement;
    if (node.properties?.['Node name for S&R'] === originalType) {
        node.properties['Node name for S&R'] = replacement;
    }
}

export function migrateLegacyEclipseWorkflow(workflow) {
    const visited = new Set();
    const visit = graph => {
        if (!graph || typeof graph !== 'object' || visited.has(graph)) return;
        visited.add(graph);
        for (const node of graph.nodes ?? []) {
            migrateNode(node);
            if (node.subgraph) visit(node.subgraph);
        }
        for (const subgraph of graph.definitions?.subgraphs ?? []) visit(subgraph);
    };
    visit(workflow);
}

app.registerExtension({
    name: 'Eclipse.LegacyWorkflowCompatibility',
    beforeConfigureGraph(graphData) {
        migrateLegacyEclipseWorkflow(graphData);
    },
});
