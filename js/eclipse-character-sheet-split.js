/**
 * Character Sheet Split — mode-first controls with compatible saved crops.
 * SPDX-License-Identifier: Apache-2.0
 */
import { app } from './comfy/index.js';
import { createWidgetVisibilityManager, isConfiguringGraph, smartResize } from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'Character Sheet Split [Eclipse]';
const MANUAL_WIDGETS = ['front_end', 'rear_end'];
const SAVED_ORDER = ['front_end', 'rear_end', 'gutter', 'mode'];

app.registerExtension({
    name: 'Eclipse.CharacterSheetSplit',
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const originalConfigure = nodeType.prototype.configure;
        nodeType.prototype.configure = function (data) {
            const values = data?.widgets_values;
            // Keep the backend/serialized order stable; only the UI is reordered.
            // Pre-mode workflows have three numbers and implicitly use manual.
            if (Array.isArray(values) && typeof values[0] === 'number') {
                const saved = Object.fromEntries(SAVED_ORDER.map((name, index) => [name, values[index]]));
                saved.mode ??= 'manual';
                data = { ...data, widgets_values: this.widgets.map((widget, index) =>
                    Object.hasOwn(saved, widget.name) ? saved[widget.name] : values[index]) };
            }
            return originalConfigure.call(this, data);
        };

        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = originalCreated?.apply(this, arguments);
            const node = this;
            const modeIndex = node.widgets?.findIndex(widget => widget.name === 'mode') ?? -1;
            if (modeIndex > 0) node.widgets.unshift(...node.widgets.splice(modeIndex, 1));
            const mode = node.widgets?.find(widget => widget.name === 'mode');
            const visibility = createWidgetVisibilityManager(node);
            let removed = false;
            let pending = false;
            const refresh = () => {
                if (removed || node.id === -1 || !node.graph) return;
                // A connected mode is only known at execution time. Keep its
                // manual controls accessible; never disconnect saved links.
                const linkedMode = node.inputs?.some(input => input.name === 'mode' && input.link != null);
                const manual = linkedMode || mode?.value !== 'auto';
                for (const name of MANUAL_WIDGETS) visibility.setVisible(name, manual);
                smartResize(node);
            };
            const schedule = () => {
                if (pending || removed) return;
                pending = true;
                queueMicrotask(() => {
                    pending = false;
                    refresh();
                });
            };
            if (mode) {
                const callback = mode.callback;
                mode.callback = function () {
                    const value = callback?.apply(this, arguments);
                    refresh();
                    return value;
                };
            }
            const originalChanged = node.onWidgetChanged;
            node.onWidgetChanged = function (name) {
                const value = originalChanged?.apply(this, arguments);
                if (name === 'mode') refresh();
                return value;
            };
            for (const hook of ['onConfigure', 'onGraphConfigured']) {
                const original = node[hook];
                node[hook] = function () {
                    const value = original?.apply(this, arguments);
                    refresh();
                    return value;
                };
            }
            const originalSerialize = node.onSerialize;
            node.onSerialize = function (data) {
                const value = originalSerialize?.apply(this, arguments);
                if (Array.isArray(data.widgets_values)) {
                    const byName = new Map(node.widgets.map((widget, index) => [widget.name, data.widgets_values[index]]));
                    data.widgets_values = [
                        ...SAVED_ORDER.map(name => byName.get(name)),
                        ...node.widgets.flatMap((widget, index) => SAVED_ORDER.includes(widget.name) ? [] : [data.widgets_values[index]]),
                    ];
                }
                return value;
            };
            const originalConnections = node.onConnectionsChange;
            node.onConnectionsChange = function () {
                const value = originalConnections?.apply(this, arguments);
                schedule();
                return value;
            };
            const originalAdded = node.onAdded;
            node.onAdded = function () {
                removed = false;
                const value = originalAdded?.apply(this, arguments);
                if (!isConfiguringGraph()) {
                    refresh();
                    if (!node.flags?.collapsed && node.id !== -1) {
                        const height = node.size[1];
                        node.size[1] = 0;
                        const computed = node.computeSize();
                        if (computed[1] !== height) node.setSize?.([node.size[0], computed[1]]);
                        else node.size[1] = height;
                    }
                }
                return value;
            };
            const originalRemoved = node.onRemoved;
            node.onRemoved = function () {
                removed = true;
                return originalRemoved?.apply(this, arguments);
            };
            visibility.hideInitially(MANUAL_WIDGETS);
            if (!isConfiguringGraph()) refresh();
            return result;
        };
    },
});
