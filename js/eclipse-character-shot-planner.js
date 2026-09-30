/**
 * Eclipse Character Shot Planner — reload and validate editable local examples.
 * SPDX-License-Identifier: Apache-2.0
 */
import { app, api } from './comfy/index.js';
import { stopAutomaticQueue } from './eclipse-queue-control-utils.js';
import { isConfiguringGraph, notifyVue, smartResize } from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'Character Shot Planner [Eclipse]';
const LABEL = 'Reload planner files';
const LEGACY_ORDER = [
    'project', 'batch_id', 'count', 'character_lock', 'outfit_lock', 'scene',
    'body_mode', 'selection', 'choices', 'seed', 'pose_cooldown',
    'expression_cooldown', 'camera_cooldown', 'operation', 'pose_category',
];
const EXPRESSION_ORDER = [
    ...LEGACY_ORDER.slice(0, 7), 'expression', 'pose_category',
    ...LEGACY_ORDER.slice(7, 14),
];
const COVERAGE_ORDER = [...EXPRESSION_ORDER.slice(0, -1), 'stop_when', 'operation'];
const WIDGET_ORDER = [
    ...EXPRESSION_ORDER.slice(0, 10), 'stop_when', 'operation',
    ...EXPRESSION_ORDER.slice(10, -1),
];
const LAYOUT_PROPERTY = 'eclipseShotPlannerWidgetVersion';

function migrateWidgetValues(data) {
    if (!data || (!data.widgets_values && !data.widgets_values_named)) return data;
    const positional = Array.isArray(data.widgets_values) ? data.widgets_values : [];
    const version = data.properties?.[LAYOUT_PROPERTY];
    const modern = version >= 1
        || ['balanced', 'close', 'mid', 'wide'].includes(positional[9]);
    const reordered = version >= 3 || (version == null
        && ['poses', 'expressions', 'poses_and_expressions', 'cameras', 'never'].includes(positional[10])
        && ['reserve', 'preview'].includes(positional[11]));
    const order = reordered ? WIDGET_ORDER : modern
        ? (version === 2 || ['poses', 'expressions', 'poses_and_expressions', 'cameras', 'never'].includes(positional[15])
            ? COVERAGE_ORDER : EXPRESSION_ORDER)
        : LEGACY_ORDER;
    const saved = Object.fromEntries(order.flatMap((name, index) =>
        positional[index] === undefined ? [] : [[name, positional[index]]]));
    // Named values are authoritative, including connected widgets' saved values.
    Object.assign(saved, data.widgets_values_named
        ?? (!Array.isArray(data.widgets_values) ? data.widgets_values : {}));
    saved.expression ??= 'random';
    saved.pose_category ??= 'all';
    saved.stop_when ??= 'cameras';
    return {
        ...data,
        properties: { ...data.properties, [LAYOUT_PROPERTY]: 3 },
        widgets_values: WIDGET_ORDER.map(name => saved[name]),
        widgets_values_named: saved,
    };
}

function migrateGraph(graph) {
    for (const node of graph?.nodes ?? []) {
        if (node.type === NODE_NAME) Object.assign(node, migrateWidgetValues(node));
        if (node.subgraph) migrateGraph(node.subgraph);
    }
    for (const subgraph of graph?.definitions?.subgraphs ?? []) migrateGraph(subgraph);
}

app.registerExtension({
    name: 'Eclipse.CharacterShotPlanner',
    beforeConfigureGraph(graphData) {
        migrateGraph(graphData);
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const originalConfigure = nodeType.prototype.configure;
        nodeType.prototype.configure = function (data) {
            // Also covers clipboard/clone and subgraph configuration without a
            // root workflow load. Normalize before LiteGraph restores any value.
            return originalConfigure.call(this, migrateWidgetValues(data));
        };
        const originalSerialize = nodeType.prototype.onSerialize;
        nodeType.prototype.onSerialize = function (data) {
            const result = originalSerialize?.apply(this, arguments);
            data.properties ??= {};
            data.properties[LAYOUT_PROPERTY] = 3;
            return result;
        };
        const originalExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const result = originalExecuted?.apply(this, arguments);
            if (message?.eclipse_shot_planner_stopped?.some(Boolean)) stopAutomaticQueue();
            for (const detail of message?.eclipse_shot_planner_notice ?? []) {
                app.extensionManager.toast.add({
                    severity: 'warn', summary: 'Shot Planner', detail, life: 12000,
                });
            }
            for (const detail of message?.eclipse_shot_planner_info ?? []) {
                app.extensionManager.toast.add({
                    severity: 'info', summary: 'Shot Planner preview', detail, life: 12000,
                });
            }
            return result;
        };
        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = originalCreated?.apply(this, arguments);
            const node = this;
            const widgets = new Map(node.widgets.map(widget => [widget.name, widget]));
            node.widgets = [
                ...WIDGET_ORDER.flatMap(name => widgets.has(name) ? [widgets.get(name)] : []),
                ...node.widgets.filter(widget => !WIDGET_ORDER.includes(widget.name)),
            ];
            let controller = null;
            const refresh = () => {
                if (!node.graph || node.id === -1) return;
                notifyVue(node);
                node.graph.setDirtyCanvas(true, false);
            };
            const button = node.addWidget('button', LABEL, null, async () => {
                if (controller) return;
                const request = new AbortController();
                controller = request;
                button.disabled = true;
                button.label = 'Reloading planner files…';
                refresh();
                try {
                    const response = await api.fetchApi('/eclipse/shot_planner/pools', {
                        cache: 'no-store', signal: request.signal,
                    });
                    const data = await response.json();
                    if (!response.ok || !data.success) throw new Error(data.error || 'Could not load planner files.');
                    if (request.signal.aborted || !node.graph) return;
                    const expression = node.widgets.find(widget => widget.name === 'expression');
                    if (expression && Array.isArray(data.expression_ids)) {
                        // Do not coerce a removed selection; allocation reports the
                        // missing ID while a saved batch can still replay it.
                        expression.options.values = ['random', 'off', ...data.expression_ids];
                    }
                    button.label = `Reloaded: ${data.poses} poses, ${data.expressions} expressions`;
                    app.extensionManager.toast.add({
                        severity: 'success', summary: 'Planner files reloaded',
                        detail: `${data.directory}: ${data.files.join(', ')}. New batches use these edits; reserved batches replay unchanged.`,
                        life: 8000,
                    });
                } catch (error) {
                    if (request.signal.aborted || !node.graph) return;
                    button.label = 'Reload failed — fix files and retry';
                    app.extensionManager.toast.add({
                        severity: 'error', summary: 'Planner files could not be loaded',
                        detail: error.message, life: 10000,
                    });
                } finally {
                    if (controller === request) controller = null;
                    button.disabled = false;
                    refresh();
                }
            }, { serialize: false });
            button.serialize = false;
            button.label = LABEL;
            let resetting = false;
            let resetController = null;
            const resetButton = node.addWidget('button', 'Reset reservations', null, async () => {
                if (resetting) return;
                resetting = true;
                resetButton.disabled = true;
                refresh();
                try {
                    const connected = node.inputs?.some(input => input.name === 'project' && input.link != null);
                    const project = await app.extensionManager.dialog.prompt({
                        title: 'Reset Shot Planner reservations',
                        message: 'Enter the project name to clear ALL its reserved batches. Stop queueing first. Saved images and prompt files stay intact; saved plans will no longer replay. If project is connected, enter the connected name, not the widget value.',
                        defaultValue: connected ? '' : node.widgets.find(widget => widget.name === 'project')?.value ?? '',
                    });
                    if (!project?.trim() || !node.graph) return;
                    resetController = new AbortController();
                    const response = await api.fetchApi('/eclipse/shot_planner/reset', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ project: project.trim(), confirmation: 'reset' }),
                        signal: resetController.signal,
                    });
                    const data = await response.json();
                    if (resetController.signal.aborted || !node.graph) return;
                    if (!response.ok || !data.success) throw new Error(data.error || 'Could not reset reservations.');
                    app.extensionManager.toast.add({
                        severity: 'success', summary: 'Reservations reset',
                        detail: `${data.project}: cleared ${data.cleared_batches} reserved batches. You can reuse the same project and batch names.`,
                        life: 10000,
                    });
                } catch (error) {
                    if (resetController?.signal.aborted || !node.graph) return;
                    app.extensionManager.toast.add({
                        severity: 'error', summary: 'Reservations could not be reset', detail: error.message, life: 10000,
                    });
                } finally {
                    resetController = null;
                    resetting = false;
                    resetButton.disabled = false;
                    refresh();
                }
            }, { serialize: false });
            resetButton.serialize = false;
            const originalRemoved = node.onRemoved;
            node.onRemoved = function () {
                controller?.abort();
                resetController?.abort();
                controller = null;
                button.disabled = false;
                button.label = LABEL;
                return originalRemoved?.apply(this, arguments);
            };
            const originalAdded = node.onAdded;
            node.onAdded = function () {
                const added = originalAdded?.apply(this, arguments);
                if (this.id !== -1 && !isConfiguringGraph()) smartResize(this);
                return added;
            };
            return result;
        };
    },
});
