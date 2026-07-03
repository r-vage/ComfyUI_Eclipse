/**
 * eclipse-folder-path.js
 * Widget visibility for Folder Path [Eclipse] node.
 * Hides date/time and batch sub-widgets when their toggle is off.
 */
import { app } from './comfy/index.js';
import {
    createWidgetVisibilityManager,
    smartResize,
    isConfiguringGraph,
} from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'Folder Path [Eclipse]';

const CONDITIONAL_WIDGETS = [
    'date_time_format', 'date_time_position',
    'batch_folder_name', 'batch_number', 'batch_number_control',
];

function updateVisibility(node, vis) {
    if (node.id === -1) return;
    const hasDateTime = vis.getValue('create_date_time_folder') === true;
    const hasBatch    = vis.getValue('create_batch_folder')    === true;

    vis.setVisible('date_time_format',   hasDateTime);
    vis.setVisible('date_time_position', hasDateTime);
    vis.setVisible('batch_folder_name',  hasBatch);
    vis.setVisible('batch_number',       hasBatch);
    vis.setVisible('batch_number_control', hasBatch);

    smartResize(node);
}

app.registerExtension({
    name: 'Eclipse.FolderPath',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;

        const origCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origCreated?.apply(this, arguments);
            const node = this;
            const vis  = createWidgetVisibilityManager(node);

            vis.hideInitially(CONDITIONAL_WIDGETS);

            const refresh = () => updateVisibility(node, vis);

            const dtW    = node.widgets?.find((w) => w.name === 'create_date_time_folder');
            const batchW = node.widgets?.find((w) => w.name === 'create_batch_folder');

            if (dtW) {
                const origCb = dtW.callback;
                dtW.callback = function () { origCb?.apply(this, arguments); refresh(); };
            }
            if (batchW) {
                const origCb = batchW.callback;
                batchW.callback = function () { origCb?.apply(this, arguments); refresh(); };
            }

            if (!node._Eclipse_initialized && !isConfiguringGraph()) {
                node._Eclipse_initialized = true;
                // node.id may still be -1 during onNodeCreated on a fresh add,
                // which makes updateVisibility() early-exit and leave the
                // default-visible widgets hidden.  Defer to the next frame so
                // the id is assigned before the first visibility pass.
                const runInit = () => {
                    refresh();
                    // Sync size-shrink for fresh-add
                    const _oldH = node.size[1];
                    node.size[1] = 0;
                    const _c = node.computeSize();
                    if (_c[1] !== _oldH) node.setSize?.([node.size[0], _c[1]]);
                    else node.size[1] = _oldH;
                };
                if (node.id === -1) requestAnimationFrame(runInit);
                else runInit();
            }

            const origConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                origConfigure?.apply(this, arguments);
                refresh();
            };
        };
    },
});
