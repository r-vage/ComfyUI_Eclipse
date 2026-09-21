/**
 * ComfyUI Eclipse - Classic collapsed-node layering
 * SPDX-License-Identifier: Apache-2.0
 */
import { app } from './comfy/index.js';
import { isVueMode, onVueModeChange } from './eclipse-widget-performance-utils.js';

let installation = null;
let unsubscribeModeChange = null;

function installCanvasLayering() {
    const canvas = app.canvas;
    if (installation?.canvas === canvas) return;
    installation?.dispose();
    installation = null;
    if (typeof canvas?.computeVisibleNodes !== 'function') return;

    const original = canvas.computeVisibleNodes;
    let active = true;
    function computeVisibleNodes(...args) {
        const visible = original.apply(this, args);
        if (!active || isVueMode() || !Array.isArray(visible)) return visible;

        // Native visibility resolves the current graph and render order. Only
        // partition its result: drawing and pointer targeting share this array.
        const behind = [];
        const expanded = [];
        const selected = [];
        for (const node of visible) {
            if (!node.flags?.collapsed) {
                expanded.push(node);
            } else {
                const isSelected = this.selectedItems?.has(node) ||
                    this.selected_nodes?.[node.id] === node;
                (isSelected ? selected : behind).push(node);
            }
        }
        let index = 0;
        for (const layer of [behind, expanded, selected]) {
            for (const node of layer) visible[index++] = node;
        }
        return visible;
    }
    canvas.computeVisibleNodes = computeVisibleNodes;
    installation = {
        canvas,
        dispose() {
            active = false;
            // A later extension may wrap us; leave its wrapper intact.
            if (canvas.computeVisibleNodes === computeVisibleNodes) {
                canvas.computeVisibleNodes = original;
            }
        },
    };
}

app.registerExtension({
    name: 'Eclipse.ClassicNodeLayering',
    setup() {
        installCanvasLayering();
        unsubscribeModeChange?.();
        unsubscribeModeChange = null;
        if (!installation) return;
        unsubscribeModeChange = onVueModeChange(() => {
            installCanvasLayering();
            app.canvas?.setDirty(true, true);
        });
        app.canvas?.setDirty(true, true);
    },
    afterConfigureGraph() {
        installCanvasLayering();
    },
});
