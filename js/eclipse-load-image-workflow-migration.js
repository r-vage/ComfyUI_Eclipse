/**
 * Load Image widget-value migration helpers.
 * SPDX-License-Identifier: Apache-2.0
 */

const LOAD_IMAGE_NODE_NAMES = Object.freeze(new Set([
    'Load Image (Metadata Pipe) [Eclipse]',
    'Load Image (Pipe) [Eclipse]',
]));
const MODES = Object.freeze(new Set(['input', 'output', 'url']));
const SOURCES = Object.freeze(new Set(['input', 'output']));
const MODE_WIDGET_NAMES = Object.freeze(['_li_source', '_lip_source']);

function isString(value) {
    return typeof value === 'string';
}

function isRemoteURL(value) {
    return isString(value) && /^[a-z][a-z\d+.-]*:\/\//i.test(value.trim());
}

function hasNamedURLWidget(named) {
    return named && typeof named === 'object' && !Array.isArray(named)
        && Object.hasOwn(named, '_url_input');
}

function layoutFor(values, named) {
    if (
        values.length >= 6
        && MODES.has(values[0])
        && isString(values[1])
        && isString(values[2])
        && (!SOURCES.has(values[2]) || hasNamedURLWidget(named))
        && SOURCES.has(values[3])
        && isString(values[4])
        && isString(values[5])
    ) {
        return {
            kind: 'browser',
            mode: 0,
            browser: 1,
            url: 2,
            source: 3,
            image: 4,
        };
    }
    if (
        values.length >= 5
        && MODES.has(values[0])
        && isString(values[1])
        && SOURCES.has(values[2])
        && isString(values[3])
        && isString(values[4])
    ) {
        return {
            kind: 'pre-browser',
            mode: 0,
            browser: 1,
            source: 2,
            image: 3,
        };
    }
    return null;
}

function migrateNode(node) {
    if (!LOAD_IMAGE_NODE_NAMES.has(node?.type) || !Array.isArray(node.widgets_values)) {
        return false;
    }
    const layout = layoutFor(node.widgets_values, node.widgets_values_named);
    if (!layout) return false;

    const values = [...node.widgets_values];
    const selectedInput = values[layout.image];
    const wasURLMode = values[layout.mode] === 'url';
    let changed = false;

    if (wasURLMode) {
        values[layout.mode] = 'input';
        values[layout.source] = 'input';
        changed = true;
    }

    if (layout.kind === 'pre-browser') {
        // This URL value occupied the slot now owned by the browser. Reuse the
        // slot so all schema and trailing values keep their positional meaning.
        const hasObsoleteValue = wasURLMode
            || values[layout.browser] === ''
            || isRemoteURL(values[layout.browser])
            || hasNamedURLWidget(node.widgets_values_named);
        if (hasObsoleteValue && values[layout.browser] !== selectedInput) {
            values[layout.browser] = selectedInput;
            changed = true;
        }
    } else {
        if ((wasURLMode || isRemoteURL(values[layout.browser]))
            && values[layout.browser] !== selectedInput) {
            values[layout.browser] = selectedInput;
            changed = true;
        }
        values.splice(layout.url, 1);
        changed = true;
    }

    const originalNamed = node.widgets_values_named;
    if (originalNamed && typeof originalNamed === 'object' && !Array.isArray(originalNamed)) {
        const named = { ...originalNamed };
        let namedChanged = false;
        const namedURLMode = MODE_WIDGET_NAMES.some((name) => named[name] === 'url');
        for (const name of MODE_WIDGET_NAMES) {
            if (named[name] === 'url') {
                named[name] = 'input';
                namedChanged = true;
            }
        }
        if ((wasURLMode || namedURLMode) && named.folder_source !== 'input') {
            named.folder_source = 'input';
            namedChanged = true;
        }
        if ((wasURLMode || namedURLMode || isRemoteURL(named._image_browser))
            && named._image_browser !== selectedInput) {
            named._image_browser = selectedInput;
            namedChanged = true;
        }
        if (Object.hasOwn(named, '_url_input')) {
            delete named._url_input;
            namedChanged = true;
        }
        if (namedChanged) {
            node.widgets_values_named = named;
            changed = true;
        }
    }

    if (changed) node.widgets_values = values;
    return changed;
}

export function migrateLoadImageWorkflow(workflow) {
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
