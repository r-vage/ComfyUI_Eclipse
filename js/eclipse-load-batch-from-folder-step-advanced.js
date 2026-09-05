/**
 * eclipse-load-batch-from-folder-step-advanced.js — Load Batch From Folder (Step Advanced) dynamic widget visibility
 *
 * Visibility rules:
 *   resize_mode !== "custom"  → hide all resize options
 *   resize_mode === "custom"  → show resize options
 *   nested:
 *     scale_to = "custom"  → show custom_width, custom_height; hide size, aspect_ratio
 *     scale_to ≠ "custom"  → show size, aspect_ratio; hide custom_width, custom_height
 *     fit = "resize" | "stretch"                        → hide crop_position, pad_color
 *     fit = "pad"                                       → show crop_position, pad_color
 *     fit = "crop"|"pad_edge"|"pad_edge_pixel"|"pillarbox_blur" → show crop_position; hide pad_color
 *
 * Copyright (c) 2026 r-vage. MIT License.
 */

import { app, api } from './comfy/index.js';
import { canvasDirtyBatcher, createWidgetVisibilityManager, isVueMode, notifyVue, smartResize } from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'Load Batch From Folder (Step Advanced) [Eclipse]';
const NOTICE_SUMMARY = 'Load Files From Folder (Step)';

function showEmptySelectionNotice(output) {
    const notices = output?.eclipse_empty_selection;
    const notice = Array.isArray(notices) ? notices[notices.length - 1] : notices;
    if (!notice?.message) return;
    app.extensionManager.toast.add({
        severity: 'warn',
        summary: NOTICE_SUMMARY,
        detail: notice.message,
        life: 8000,
    });
}

app.registerExtension({
    name: 'Eclipse.LoadBatchFromFolderStepAdvanced',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;

        const origOnExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (output) {
            const ret = origOnExecuted ? origOnExecuted.apply(this, arguments) : undefined;
            showEmptySelectionNotice(output);
            return ret;
        };

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            const vis = createWidgetVisibilityManager(node);

            // 1. Refresh button setup
            const folderW = node.widgets?.find(w => w.name === 'folder_path');
            const folderIdx = folderW ? node.widgets.indexOf(folderW) : -1;

            const LABEL_IDLE        = '↺ Refresh File List';
            const LABEL_WORKING     = '↺ Refreshing…';
            const LABEL_DONE        = '✓ Refreshed';

            const btn = node.addWidget('button', LABEL_IDLE, null, async () => {
                const raw = folderW?.value || '';
                const lines = raw.split('\n').map(l => l.trim()).filter(Boolean);
                if (!lines.length) return;

                btn.label = LABEL_WORKING;
                btn.disabled = true;
                node.graph?.setDirtyCanvas(true, false);

                try {
                    await Promise.all(lines.map(line =>
                        api.fetchApi('/eclipse/load_image_folder/invalidate_cache', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ folder_path: line }),
                        }).catch(() => {})
                    ));
                    btn.label = LABEL_DONE;
                    setTimeout(() => {
                        btn.label = LABEL_IDLE;
                        btn.disabled = false;
                        node.graph?.setDirtyCanvas(true, false);
                    }, 2000);
                } catch (_e) {
                    btn.label = LABEL_IDLE;
                    btn.disabled = false;
                }
                node.graph?.setDirtyCanvas(true, false);
            }, { serialize: false });

            btn.label = LABEL_IDLE;

            // Place button directly after folder_path
            const btnIdx = node.widgets.indexOf(btn);
            if (folderIdx >= 0 && btnIdx !== folderIdx + 1) {
                node.widgets.splice(btnIdx, 1);
                node.widgets.splice(folderIdx + 1, 0, btn);
            }

            // 2. Pre-hide conditional widgets hidden at defaults
            // (resize_mode='first', scale_to='longest', fit='resize').
            vis.hideInitially([
                'scale_to', 'size', 'custom_width', 'custom_height', 'aspect_ratio',
                'fit', 'crop_position', 'pad_color', 'method', 'divisible_by', 'device'
            ]);

            const updateVisibility = () => {
                const d = (name, show) => vis.setVisible(name, show);

                const resizeMode = vis.getValue('resize_mode') || 'first';
                const isCustomResize = resizeMode === 'custom';

                if (!isCustomResize) {
                    // Hide all resize options
                    const resizeOptions = [
                        'scale_to', 'size', 'custom_width', 'custom_height', 'aspect_ratio',
                        'fit', 'crop_position', 'pad_color', 'method', 'divisible_by', 'device'
                    ];
                    for (const name of resizeOptions) {
                        d(name, false);
                    }
                } else {
                    // Show custom resize options
                    d('scale_to', true);
                    d('fit', true);
                    d('method', true);
                    d('divisible_by', true);
                    d('device', true);

                    const scaleTo = vis.getValue('scale_to') || 'longest';
                    const isCustomScale = scaleTo === 'custom';

                    // scale_to group
                    d('size', !isCustomScale);
                    d('custom_width', isCustomScale);
                    d('custom_height', isCustomScale);
                    d('aspect_ratio', !isCustomScale);

                    // fit group
                    const fit = vis.getValue('fit') || 'resize';
                    const needsCropPos = ['crop', 'pad', 'pad_edge', 'pad_edge_pixel', 'pillarbox_blur'].includes(fit);
                    const needsPadColor = fit === 'pad';
                    d('crop_position', needsCropPos);
                    d('pad_color', needsPadColor);
                }

                smartResize(node);
                if (isVueMode()) {
                    notifyVue(node);
                    canvasDirtyBatcher.markDirty(node, true, true);
                }
            };

            const debouncedUpdate = () => updateVisibility();

            // Hook widget callbacks for user-driven changes
            const triggerWidgets = ['resize_mode', 'scale_to', 'fit'];
            for (const wName of triggerWidgets) {
                const w = node.widgets?.find((ww) => ww.name === wName);
                if (!w) continue;
                const orig = w.callback;
                w.callback = function () {
                    if (orig) orig.apply(this, arguments);
                    vis.clearCache();
                    vis.markUserDriven();
                    debouncedUpdate();
                };
            }

            // Workflow restore
            const origConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                if (origConfigure) origConfigure.apply(this, arguments);
                vis.clearCache();
                updateVisibility();
                requestAnimationFrame(() => {
                    vis.clearCache();
                    updateVisibility();
                });
            };

            // Initial visibility
            updateVisibility();
            requestAnimationFrame(() => {
                vis.clearCache();
                updateVisibility();
            });

            return ret;
        };
    },
});
