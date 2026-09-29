/**
 * Character Sheet Pack — mannequin mode and independent physique controls.
 * SPDX-License-Identifier: Apache-2.0
 */
import { app } from './comfy/index.js';
import { createWidgetVisibilityManager, isConfiguringGraph, smartResize } from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'Character Sheet Pack [Eclipse]';
const BODY_WIDGETS = ['build', 'muscularity', 'breast_size', 'chest_breadth', 'hip_width', 'buttock_size'];
const CONDITIONAL_WIDGETS = ['generation_mode', 'gender', ...BODY_WIDGETS,
    'use_front_body', 'use_rear_body', 'use_wardrobe', 'use_rear'];

app.registerExtension({
    name: 'Eclipse.CharacterSheetPack',
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = originalCreated?.apply(this, arguments);
            const node = this;
            const visibility = createWidgetVisibilityManager(node);
            let removed = false;
            let pending = false;
            const refresh = () => {
                if (removed || node.id === -1 || !node.graph) return;
                const value = name => node.widgets?.find(widget => widget.name === name)?.value;
                const mannequin = value('stage') === 'mannequin';
                const preset = mannequin && value('generation_mode') === 'Preset';
                visibility.setVisible('generation_mode', mannequin);
                visibility.setVisible('gender', mannequin);
                for (const name of BODY_WIDGETS) visibility.setVisible(name, preset);
                visibility.setVisible('use_front_body', mannequin && !preset);
                visibility.setVisible('use_rear_body', mannequin && !preset);
                visibility.setVisible('use_wardrobe', value('stage') === 'sheet');
                visibility.setVisible('use_rear', value('stage') === 'dataset');
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
            for (const widget of node.widgets ?? []) {
                if (!['stage', 'generation_mode'].includes(widget.name)) continue;
                const callback = widget.callback;
                widget.callback = function () {
                    const value = callback?.apply(this, arguments);
                    refresh();
                    return value;
                };
            }
            const originalChanged = node.onWidgetChanged;
            node.onWidgetChanged = function (name) {
                const value = originalChanged?.apply(this, arguments);
                if (['stage', 'generation_mode'].includes(name)) refresh();
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
            visibility.hideInitially(CONDITIONAL_WIDGETS);
            if (!isConfiguringGraph()) refresh();
            return result;
        };
    },
});
