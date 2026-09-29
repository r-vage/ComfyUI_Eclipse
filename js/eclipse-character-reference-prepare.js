/**
 * Show only the prompt controls used by Character Reference Prepare.
 * SPDX-License-Identifier: Apache-2.0
 */
import { app } from './comfy/index.js';
import { createWidgetVisibilityManager, isConfiguringGraph, smartResize } from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'Character Reference Prepare [Eclipse]';

app.registerExtension({
    name: 'Eclipse.CharacterReferencePrepare',
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = originalCreated?.apply(this, arguments);
            const node = this;
            const visibility = createWidgetVisibilityManager(node);
            let removed = false;
            let pending = false;
            const linked = name => node.inputs?.some(input => input.name === name && input.link != null);
            const refresh = () => {
                if (removed || node.id === -1 || !node.graph) return;
                const positive = linked('positive_override');
                // Preserve saved values and converted-widget links while hidden.
                // Do not mark this as a destructive user-driven visibility batch.
                visibility.setVisible('description', !positive);
                visibility.setVisible('instructions', !positive);
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
                // Link assignment may finish after this callback returns.
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
            if (!isConfiguringGraph()) refresh();
            return result;
        };
    },
});
