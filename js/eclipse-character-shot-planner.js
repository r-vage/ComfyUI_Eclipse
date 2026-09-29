/**
 * Eclipse Character Shot Planner — reload and validate editable local examples.
 * SPDX-License-Identifier: Apache-2.0
 */
import { app, api } from './comfy/index.js';
import { isConfiguringGraph, notifyVue, smartResize } from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'Character Shot Planner [Eclipse]';
const LABEL = 'Reload planner files';

app.registerExtension({
    name: 'Eclipse.CharacterShotPlanner',
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = originalCreated?.apply(this, arguments);
            const node = this;
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
            button.label = LABEL;
            const originalRemoved = node.onRemoved;
            node.onRemoved = function () {
                controller?.abort();
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
