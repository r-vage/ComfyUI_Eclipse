/**
 * Eclipse Save Video — resizable DOM video preview (uses shared helper).
 */
import { app } from './comfy/index.js';
import { attachVideoPreview, setVideoPreviewSource, stopVideoPreview } from './eclipse-video-preview-common.js';
import { createWidgetVisibilityManager, isConfiguringGraph } from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'Save Video [Eclipse]';

// Loop widgets that are only relevant when a loop_match mode is active.
const LOOP_ALWAYS = ['loop_search_pct', 'loop_metric', 'loop_trim_start'];
const LOOP_BLEND_ONLY = ['loop_blend_frames'];
const ALL_LOOP_WIDGETS = [...LOOP_ALWAYS, ...LOOP_BLEND_ONLY];

app.registerExtension({
    name: 'Eclipse.SaveVideo',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;

        const origCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origCreated?.apply(this, arguments);
            const node = this;

            // Hide single-option combos (kept in schema for future formats/codecs).
            for (const wname of ['format', 'codec']) {
                const w = node.widgets?.find(x => x.name === wname);
                if (w) {
                    w.hidden = true;
                    if (w.options) w.options.hidden = true;
                    w.computeSize = () => [0, -4];
                }
            }

            const vis = createWidgetVisibilityManager(node);

            // Pre-hide all loop widgets so fresh-add doesn't flash them.
            vis.hideInitially(ALL_LOOP_WIDGETS);

            const trimModeW = node.widgets?.find(w => w.name === 'trim_mode');

            const updateVisibility = () => {
                if (node.id === -1) return;
                const mode = trimModeW?.value ?? 'none';
                const isLoop = mode === 'loop_match' || mode === 'loop_match_blend';
                const isBlend = mode === 'loop_match_blend';
                for (const n of LOOP_ALWAYS) vis.setVisible(n, isLoop);
                vis.setVisible('loop_blend_frames', isBlend);
            };

            if (trimModeW) {
                const origCb = trimModeW.callback;
                trimModeW.callback = function () {
                    origCb?.apply(this, arguments);
                    updateVisibility();
                };
            }

            if (!node._Eclipse_initialized && !isConfiguringGraph()) {
                node._Eclipse_initialized = true;
                updateVisibility();
            }

            const origConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                origConfigure?.apply(this, arguments);
                updateVisibility();
            };

            attachVideoPreview(node, { sourceType: 'output' });
        };

        const origExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            origExecuted?.apply(this, arguments);
            const list = message?.eclipse_video;
            if (Array.isArray(list) && list.length > 0) {
                setVideoPreviewSource(this, list[0]);
            }
        };

        const origRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            stopVideoPreview(this);
            origRemoved?.apply(this, arguments);
        };
    },
});
