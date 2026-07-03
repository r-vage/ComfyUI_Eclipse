import {
    app
} from './comfy/index.js';
import {
    debounce,
    canvasDirtyBatcher,
    smartResize,
    createWidgetVisibilityManager,
    isConfiguringGraph,
} from './eclipse-widget-performance-utils.js';
const NODE_NAME = 'Smart Folder [Eclipse]';
app.registerExtension({
    name: 'Eclipse.SmartFolder',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            const vis = createWidgetVisibilityManager(node);
            node._Eclipse_lastBatchNumber = null;
            node._Eclipse_lastSkipFirstFramesCalc = null;
            const setVis = (name, visible) => vis.setVisible(name, visible);
            const getVal = (name) => vis.getValue(name);
            const refreshVisibility = () => {
                const genMode = getVal('generation_mode');
                const hasDateFolder = getVal('create_date_time_folder');
                const hasBatchFolder = getVal('create_batch_folder');
                const useImgSize = getVal('use_image_size');
                const isImage = genMode === 'Image Mode';
                const isVideo = genMode === 'Video Mode';
                const isCustomImage = getVal('image_size') === 'Custom';
                const isCustomVideo = getVal('video_size') === 'Custom';
                setVis('date_time_format', hasDateFolder);
                setVis('date_time_position', hasDateFolder);
                setVis('root_folder_image', isImage);
                setVis('use_image_size', isImage);
                setVis('image_size', isImage && useImgSize);
                setVis('width', isImage && useImgSize && isCustomImage);
                setVis('height', isImage && useImgSize && isCustomImage);
                setVis('root_folder_video', isVideo);
                setVis('video_size', isVideo);
                setVis('video_width', isVideo && isCustomVideo);
                setVis('video_height', isVideo && isCustomVideo);
                setVis('frame_rate', isVideo);
                setVis('frame_load_cap', isVideo);
                setVis('context_length', isVideo);
                setVis('loop_count', isVideo);
                setVis('overlap', isVideo);
                setVis('skip_first_frames', isVideo);
                setVis('skip_calculation', isVideo);
                setVis('skip_calculation_control', isVideo);
                setVis('select_every_nth', isVideo);
                setVis('batch_folder_name', hasBatchFolder);
                setVis('batch_number', hasBatchFolder);
                setVis('batch_number_control', hasBatchFolder);
                setVis('batch_size', isImage);
                smartResize(node);
            };
            const debouncedRefresh = debounce(refreshVisibility, 100);
            const triggerWidgets = ['generation_mode', 'create_date_time_folder', 'create_batch_folder', 'use_image_size', 'image_size', 'video_size', 'root_folder_image', 'root_folder_video', ];
            for (const wName of triggerWidgets) {
                const w = node.widgets?.find((w) => w.name === wName);
                if (w) {
                    const origCb = w.callback;
                    w.callback = function (val) {
                        vis.markUserDriven();
                        debouncedRefresh();
                        if (origCb) origCb(val);
                    };
                }
            }
            if (!isConfiguringGraph()) {
                refreshVisibility();
            }
            const origOnConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                if (origOnConfigure) origOnConfigure.apply(this, arguments);
                refreshVisibility();
            };
            return ret;
        };
    },
    async setup() {
        const origGraphToPrompt = app.graphToPrompt;
        app.graphToPrompt = async function () {
            const promptData = await origGraphToPrompt.apply(this, arguments);
            const nodes = app.graph._nodes;
            for (const node of nodes) {
                if (node.type !== NODE_NAME) continue;
                if (node.mode === 2 || node.mode === 4) continue;
                const nodeId = String(node.id);
                if (!promptData.output || !promptData.output[nodeId]) continue;
                const inputs = promptData.output[nodeId].inputs;
                const batchWidget = node.widgets?.find((w) => w.name === 'batch_number');
                const batchControl = node.widgets?.find((w) => w.name === 'batch_number_control');
                if (batchWidget && batchControl && inputs) {
                    if (batchControl.value === 'increment') {
                        if (node._Eclipse_lastBatchNumber != null) {
                            const nextBatch = node._Eclipse_lastBatchNumber + 1;
                            try {
                                const cur = inputs.batch_number;
                                if (!inputs.batch_number || Number(cur) !== nextBatch) inputs.batch_number = nextBatch;
                            } catch (_) {
                                inputs.batch_number = nextBatch;
                            }
                            node._Eclipse_lastBatchNumber = nextBatch;
                            try {
                                if (Number(batchWidget.value) !== nextBatch) batchWidget.value = nextBatch;
                            } catch (_) {}
                            if (promptData.workflow && promptData.workflow.nodes) {
                                const wfNode = promptData.workflow.nodes.find((n) => n.id === node.id);
                                if (wfNode && wfNode.widgets_values) {
                                    const idx = node.widgets.indexOf(batchWidget);
                                    if (idx >= 0) {
                                        try {
                                            if (wfNode.widgets_values[idx] !== nextBatch) wfNode.widgets_values[idx] = nextBatch;
                                        } catch (_) {}
                                    }
                                }
                            }
                        } else {
                            node._Eclipse_lastBatchNumber = batchWidget.value;
                        }
                    } else {
                        node._Eclipse_lastBatchNumber = batchWidget.value;
                    }
                }
                const skipWidget = node.widgets?.find((w) => w.name === 'skip_calculation');
                const skipControl = node.widgets?.find((w) => w.name === 'skip_calculation_control');
                if (skipWidget && skipControl && inputs) {
                    if (skipControl.value === 'increment') {
                        if (node._Eclipse_lastSkipFirstFramesCalc != null) {
                            const nextSkip = node._Eclipse_lastSkipFirstFramesCalc + 1;
                            try {
                                const cur = inputs.skip_calculation;
                                if (!inputs.skip_calculation || Number(cur) !== nextSkip) inputs.skip_calculation = nextSkip;
                            } catch (_) {
                                inputs.skip_calculation = nextSkip;
                            }
                            node._Eclipse_lastSkipFirstFramesCalc = nextSkip;
                            if (Number(skipWidget.value) !== nextSkip) skipWidget.value = nextSkip;
                            if (promptData.workflow && promptData.workflow.nodes) {
                                const wfNode = promptData.workflow.nodes.find((n) => n.id === node.id);
                                if (wfNode && wfNode.widgets_values) {
                                    const idx = node.widgets.indexOf(skipWidget);
                                    if (idx >= 0) {
                                        try {
                                            if (wfNode.widgets_values[idx] !== nextSkip) wfNode.widgets_values[idx] = nextSkip;
                                        } catch (_) {}
                                    }
                                }
                            }
                        } else {
                            node._Eclipse_lastSkipFirstFramesCalc = skipWidget.value;
                        }
                    } else {
                        node._Eclipse_lastSkipFirstFramesCalc = skipWidget.value;
                    }
                }
            }
            return promptData;
        };
    },
});
