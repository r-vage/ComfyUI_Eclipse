/**
 * eclipse-load-batch-from-folder.js
 *
 * Adds a "↺ Refresh File List" button to Load Batch From Folder [Eclipse].
 * Clicking it calls /eclipse/load_image_folder/invalidate_cache for every
 * folder path entered in the folder_path widget — no toggle needed.
 */

import { app, api } from './comfy/index.js';

const NODE_NAME = 'Load Batch From Folder [Eclipse]';

const LABEL_IDLE        = '↺ Refresh File List';
const LABEL_WORKING     = '↺ Refreshing…';
const LABEL_DONE        = '✓ Refreshed';

app.registerExtension({
    name: 'Eclipse.LoadBatchFromFolder',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated?.apply(this, arguments);
            const node = this;

            const folderW = node.widgets?.find(w => w.name === 'folder_path');
            const folderIdx = folderW ? node.widgets.indexOf(folderW) : -1;

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

            return ret;
        };
    },
});
