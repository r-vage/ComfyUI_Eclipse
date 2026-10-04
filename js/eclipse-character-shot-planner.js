/**
 * Eclipse Character Shot Planner — reload and validate editable local examples.
 * SPDX-License-Identifier: Apache-2.0
 */
import { app, api } from './comfy/index.js';
import { stopAutomaticQueue } from './eclipse-queue-control-utils.js';
import { createWidgetVisibilityManager, isConfiguringGraph, notifyVue, smartResize } from './eclipse-widget-performance-utils.js';

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
const PLANNER_ORDER = [
    ...EXPRESSION_ORDER.slice(0, 10), 'stop_when', 'operation',
    ...EXPRESSION_ORDER.slice(10, -1),
];
const WIDGET_ORDER = [...PLANNER_ORDER.slice(0, 2), 'mode', 'manual_start', ...PLANNER_ORDER.slice(2)];
const LAYOUT_PROPERTY = 'eclipseShotPlannerWidgetVersion';
const LAYOUTS = [LEGACY_ORDER, EXPRESSION_ORDER, COVERAGE_ORDER, PLANNER_ORDER, WIDGET_ORDER];
const PLANNER_CONTROLS = ['body_mode', 'expression', 'pose_category', 'selection', 'stop_when',
    'operation', 'choices', 'pose_cooldown', 'expression_cooldown', 'camera_cooldown', LABEL, 'Reset reservations'];

// Keep recognition in sync with tools/migrate_shot_planner_workflows.py. Both
// implementations run against the same fixtures; never reinterpret a v3 array
// using the v4 order. Converted widgets retain their positional placeholders.
function fitsLayout(positional, order) {
    const minimum = order === LEGACY_ORDER ? 14 : order.length;
    return positional.length >= minimum && positional.length <= order.length + 2
        && positional.slice(order.length).every(value => value == null);
}

function recognizesLayout(positional, order) {
    const numbers = ['count', 'seed', 'manual_start', 'pose_cooldown', 'expression_cooldown', 'camera_cooldown'];
    const options = {body_mode: ['any', 'seated', 'standing', 'lying'],
        selection: ['balanced', 'close', 'mid', 'wide'], operation: ['reserve', 'preview'],
        stop_when: ['poses', 'expressions', 'poses_and_expressions', 'cameras', 'never'], mode: ['Planner', 'Manual']};
    return fitsLayout(positional, order) && order.every((name, index) => {
        const value = positional[index];
        if (value == null) return true;
        if (options[name]) return options[name].includes(value);
        return numbers.includes(name) ? Number.isInteger(value) : typeof value === 'string';
    });
}

function migrateWidgetValues(data, report = message => console.warn(message)) {
    if (!data) return data;
    const positional = Array.isArray(data.widgets_values) ? data.widgets_values : [];
    const version = data.properties?.[LAYOUT_PROPERTY];
    if (version === 4) return data;
    const named = data.widgets_values_named
        ?? (!Array.isArray(data.widgets_values) ? data.widgets_values : {}) ?? {};
    const review = () => {
        report(`Shot Planner node ${data.id ?? '?'}: ambiguous or unsupported widget layout; left unchanged.`);
        return data;
    };
    if (typeof named !== 'object' || Array.isArray(named)
        || (version != null && (!Number.isInteger(version) || version < 0 || version > 4))) return review();
    let order;
    if (positional.length) {
        const candidates = version == null ? LAYOUTS.filter(layout => recognizesLayout(positional, layout))
            : (fitsLayout(positional, LAYOUTS[version]) ? [LAYOUTS[version]] : []);
        if (candidates.length === 1) [order] = candidates;
        else if (PLANNER_ORDER.every(name => Object.hasOwn(named, name))) order = [];
        else return review();
    } else {
        // Named-only exports need every original field; optional controls have
        // historical defaults. Partial named overrides still work with arrays.
        if (!LEGACY_ORDER.slice(0, 14).every(name => Object.hasOwn(named, name))) return review();
        order = [];
    }
    const saved = Object.fromEntries(order.flatMap((name, index) =>
        positional[index] === undefined ? [] : [[name, positional[index]]]));
    // Named values are authoritative, including connected widgets' saved values.
    Object.assign(saved, named);
    saved.expression ??= 'random';
    saved.pose_category ??= 'all';
    saved.stop_when ??= 'cameras';
    saved.mode ??= 'Planner';
    saved.manual_start ??= 1;
    return {
        ...data,
        properties: { ...data.properties, [LAYOUT_PROPERTY]: 4 },
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
            const configured = { ...migrateWidgetValues(data) };
            if (Array.isArray(configured.inputs) && this.inputs) {
                // ComfyUI merges serialized inputs in the current definition's
                // order. Preserve existing slot indices, including old exports
                // with only converted widgets, and append newly defined slots.
                const byName = new Map(this.inputs.map(input => [input.name, input]));
                const savedNames = new Set(configured.inputs.map(input => input.name));
                this.inputs = [
                    ...configured.inputs.flatMap(input => byName.has(input.name) ? [byName.get(input.name)] : []),
                    ...this.inputs.filter(input => !savedNames.has(input.name)),
                ];
            }
            return originalConfigure.call(this, configured);
        };
        const originalSerialize = nodeType.prototype.onSerialize;
        nodeType.prototype.onSerialize = function (data) {
            const result = originalSerialize?.apply(this, arguments);
            data.properties ??= {};
            data.properties[LAYOUT_PROPERTY] = 4;
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
            // The frontend creates forceInput sockets first. Move the new
            // socket behind the existing schema before any links are attached.
            if (node.inputs) node.inputs = [
                ...node.inputs.filter(input => input.name !== 'manual_shots'),
                ...node.inputs.filter(input => input.name === 'manual_shots'),
            ];
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
            const visibility = createWidgetVisibilityManager(node);
            const updateVisibility = () => {
                if (node.id === -1) return;
                const manual = node.widgets.find(widget => widget.name === 'mode')?.value === 'Manual';
                // Mode switching must retain linked planner inputs. Explicitly
                // opt out of the manager's user-driven disconnect behavior.
                visibility.setVisibleBatch([
                    ['manual_start', manual], ...PLANNER_CONTROLS.map(name => [name, !manual]),
                ], { userDriven: false });
                smartResize(node);
            };
            visibility.hideInitially(['manual_start', ...PLANNER_CONTROLS]);
            const mode = node.widgets.find(widget => widget.name === 'mode');
            if (mode) {
                const callback = mode.callback;
                mode.callback = function () {
                    const result = callback?.apply(this, arguments);
                    updateVisibility();
                    return result;
                };
            }
            const originalOnConfigure = node.onConfigure;
            node.onConfigure = function () {
                const configured = originalOnConfigure?.apply(this, arguments);
                updateVisibility();
                return configured;
            };
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
                if (this.id !== -1 && !isConfiguringGraph()) {
                    updateVisibility();
                    // Shrink fresh nodes synchronously after the initial hide.
                    const height = this.size[1];
                    this.size[1] = 0;
                    const size = this.computeSize();
                    this.size[1] = height;
                    this.setSize?.([this.size[0], size[1]]);
                }
                return added;
            };
            if (node.id !== -1 && !isConfiguringGraph()) updateVisibility();
            return result;
        };
    },
});
