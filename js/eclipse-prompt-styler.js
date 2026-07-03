import {
    app
} from './comfy/index.js';
import {
    notifyVue,
    smartResize,
    createWidgetVisibilityManager,
    isVueMode
} from './eclipse-widget-performance-utils.js';
const NODE_NAME = 'Prompt Styler [Eclipse]';
const MODE_RANDOM = -1;
const MODE_INCREMENT = -2;
const MODE_DECREMENT = -3;
const nodeStyleCounts = new Map();
app.registerExtension({
    name: 'Eclipse.PromptStyler',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            const nodeId = node.id;
            const vis = createWidgetVisibilityManager(node);
            // Pre-hide max_words_to_combine — default spaces_to_underscores=False.
            vis.hideInitially(['max_words_to_combine']);
            const findWidget = (name) => node.widgets?.find((w) => w.name === name);
            const modeWidget = findWidget('style_mode');
            const styleWidget = findWidget('style');
            const indexWidget = findWidget('index');
            const spacesWidget = findWidget('spaces_to_underscores');
            if (!styleWidget || !indexWidget) {
                console.warn('[PromptStyler] Required widgets not found');
                return ret;
            }
            node._Eclipse_indexWidget = indexWidget;
            node._Eclipse_styleWidget = styleWidget;
            node._Eclipse_lastResolvedIndex = null;
            node._Eclipse_manualIndex = null;
            node._Eclipse_updatingIndex = false;
            node._Eclipse_updatingStyle = false;
            const refreshSpacesVisibility = () => {
                vis.setVisible('max_words_to_combine', spacesWidget?.value ?? false);
                smartResize(node, {
                    minWidth: 0,
                    minHeight: 0,
                    padding: 0
                });
            };
            if (spacesWidget) {
                const origSpacesCb = spacesWidget.callback;
                spacesWidget.callback = function (val) {
                    if (origSpacesCb) origSpacesCb.apply(this, arguments);
                    vis.markUserDriven();
                    refreshSpacesVisibility();
                };
            }
            const getStyleValues = () => styleWidget.options?.values || [];
            const syncStyleCounts = () => {
                const styles = getStyleValues();
                nodeStyleCounts.set(nodeId, styles.length);
                if (indexWidget.options) indexWidget.options.max = Math.max(0, styles.length - 1);
            };
            const syncStyleToIndex = (idx) => {
                const styles = getStyleValues();
                if (styles.length === 0 || idx < 0) return;
                const styleName = styles[idx % styles.length];
                if (styleName && styleWidget.value !== styleName) {
                    node._Eclipse_updatingStyle = true;
                    styleWidget.value = styleName;
                    if (styleWidget.callback) styleWidget.callback(styleName);
                    node._Eclipse_updatingStyle = false;
                    _app.graph.setDirtyCanvas(true);
                }
            };
            const fetchStyles = async (mode) => {
                try {
                    const resp = await fetch(`/eclipse/prompt_styler/styles/${mode}`);
                    if (!resp.ok) {
                        console.error(`[PromptStyler] Failed to fetch styles for mode ${mode}`);
                        return null;
                    }
                    return (await resp.json()).styles || [];
                } catch (err) {
                    console.error(`[PromptStyler] Error fetching styles: ${err}`);
                    return null;
                }
            };
            const applyStyles = (newStyles, preserveSelection = true) => {
                if (!newStyles || newStyles.length === 0) return;
                const prevStyle = styleWidget.value;
                const prevIndex = indexWidget.value;
                styleWidget.options.values = newStyles;
                syncStyleCounts();
                node._Eclipse_lastResolvedIndex = null;
                node._Eclipse_manualIndex = null;
                if (preserveSelection && newStyles.includes(prevStyle)) {
                    node._Eclipse_updatingStyle = true;
                    styleWidget.value = prevStyle;
                    const foundIdx = newStyles.indexOf(prevStyle);
                    if (foundIdx >= 0 && indexWidget.value !== foundIdx) {
                        node._Eclipse_updatingIndex = true;
                        indexWidget.value = foundIdx;
                        node._Eclipse_updatingIndex = false;
                    }
                    node._Eclipse_updatingStyle = false;
                } else {
                    let newIndex = prevIndex;
                    if (prevIndex >= 0 && prevIndex >= newStyles.length) {
                        newIndex = prevIndex % newStyles.length;
                    } else if (prevIndex >= newStyles.length || (prevIndex < 0 && prevIndex >= -3)) {
                        newIndex = prevIndex;
                    } else if (prevIndex < -3) {
                        newIndex = 0;
                    }
                    if (newIndex >= 0) {
                        const safeIdx = newIndex % newStyles.length;
                        node._Eclipse_updatingStyle = true;
                        styleWidget.value = newStyles[safeIdx];
                        node._Eclipse_updatingStyle = false;
                        if (indexWidget.value !== safeIdx) {
                            node._Eclipse_updatingIndex = true;
                            indexWidget.value = safeIdx;
                            node._Eclipse_updatingIndex = false;
                        }
                    }
                }
                _app.graph.setDirtyCanvas(true);
            };
            if (modeWidget) {
                const origModeCb = modeWidget.callback;
                modeWidget.callback = async function (val) {
                    if (origModeCb) origModeCb.apply(this, arguments);
                    node._Eclipse_lastResolvedIndex = null;
                    node._Eclipse_manualIndex = null;
                    const styles = await fetchStyles(val);
                    if (styles) applyStyles(styles, true);
                };
            }
            const origIndexCb = indexWidget.callback;
            indexWidget.callback = function (val) {
                if (origIndexCb) origIndexCb.apply(this, arguments);
                if (node._Eclipse_updatingIndex) return;
                if (node._Eclipse_lastIndexButton) {
                    if (val >= 0) {
                        node._Eclipse_lastIndexButton.name = '♻️ (Use Last Queued Index)';
                        node._Eclipse_lastIndexButton.disabled = true;
                        node._Eclipse_lastResolvedIndex = null;
                        node._Eclipse_manualIndex = null;
                    } else if (node._Eclipse_lastResolvedIndex != null) {
                        node._Eclipse_lastIndexButton.name = `♻️ ${node._Eclipse_lastResolvedIndex}`;
                        node._Eclipse_lastIndexButton.disabled = false;
                    } else {
                        node._Eclipse_lastIndexButton.name = '♻️ (Use Last Queued Index)';
                        node._Eclipse_lastIndexButton.disabled = true;
                    }
                    if (isVueMode()) notifyVue(node);
                }
                syncStyleToIndex(val);
            };
            const origStyleCb = styleWidget.callback;
            styleWidget.callback = function (val) {
                if (origStyleCb) origStyleCb.apply(this, arguments);
                if (node._Eclipse_updatingStyle) return;
                node._Eclipse_lastResolvedIndex = null;
                node._Eclipse_manualIndex = null;
                if (indexWidget.value >= 0) {
                    const foundIdx = getStyleValues().indexOf(val);
                    if (foundIdx >= 0 && indexWidget.value !== foundIdx) {
                        node._Eclipse_updatingIndex = true;
                        indexWidget.value = foundIdx;
                        node._Eclipse_updatingIndex = false;
                        _app.graph.setDirtyCanvas(true);
                    }
                } else {
                    const foundIdx = getStyleValues().indexOf(val);
                    if (foundIdx >= 0 && node._Eclipse_lastIndexButton) {
                        node._Eclipse_lastIndexButton.name = `♻️ ${foundIdx}`;
                        node._Eclipse_lastIndexButton.disabled = false;
                        if (isVueMode()) notifyVue(node);
                    }
                }
            };
            const addButton = (label, tooltip, onClick) => {
                const btn = node.addWidget('button', label, null, onClick);
                btn.tooltip = tooltip;
                btn.serialize = false;
                return btn;
            };
            addButton('🎲 Randomize Each Time', 'Set index to -1 (random style on each queue)', () => {
                node._Eclipse_updatingIndex = true;
                indexWidget.value = -1;
                node._Eclipse_updatingIndex = false;
                node._Eclipse_lastResolvedIndex = null;
                node._Eclipse_manualIndex = null;
                _app.graph.setDirtyCanvas(true);
            }, );
            const lastIndexBtn = addButton('♻️ (Use Last Queued Index)', 'Lock to the index from last queue (disables increment/decrement/random)', () => {
                if (node._Eclipse_lastResolvedIndex != null) {
                    node._Eclipse_updatingIndex = true;
                    indexWidget.value = node._Eclipse_lastResolvedIndex;
                    node._Eclipse_updatingIndex = false;
                    syncStyleToIndex(node._Eclipse_lastResolvedIndex);
                    node._Eclipse_lastResolvedIndex = null;
                    node._Eclipse_manualIndex = null;
                }
                _app.graph.setDirtyCanvas(true);
            }, );
            lastIndexBtn.disabled = true;
            node._Eclipse_lastIndexButton = lastIndexBtn;
            const origOnRemoved = node.onRemoved;
            node.onRemoved = function () {
                nodeStyleCounts.delete(nodeId);
                if (origOnRemoved) origOnRemoved.apply(this, arguments);
            };
            const origOnConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                if (origOnConfigure) origOnConfigure.apply(this, arguments);
                refreshSpacesVisibility();
                syncStyleCounts();
            };
            setTimeout(() => {
                syncStyleToIndex(indexWidget.value);
                refreshSpacesVisibility();
                syncStyleCounts();
            }, 100);
            return ret;
        };
        nodeType.prototype.getIndexToUse = function () {
            const indexWidget = this._Eclipse_indexWidget;
            if (!indexWidget) return 0;
            const indexVal = indexWidget.value;
            const lastResolved = this._Eclipse_lastResolvedIndex;
            const maxIdx = indexWidget.options?.max ?? 999999;
            const totalStyles = nodeStyleCounts.get(this.id) || maxIdx + 1;
            let result = indexVal;
            if (indexVal === -1) {
                if (totalStyles > 1) {
                    let attempts = 0;
                    do {
                        result = Math.floor(Math.random() * totalStyles);
                        attempts++;
                    } while (result === lastResolved && attempts < 10);
                } else {
                    result = 0;
                }
            } else if (indexVal === -2) {
                if (lastResolved !== null) {
                    result = lastResolved + 1;
                    if (result >= totalStyles) result = 0;
                } else {
                    const styles = this._Eclipse_styleWidget?.options?.values || [];
                    const currentStyle = this._Eclipse_styleWidget?.value;
                    const foundIdx = styles.indexOf(currentStyle);
                    result = foundIdx >= 0 ? foundIdx : 0;
                }
            } else if (indexVal === -3) {
                if (lastResolved !== null) {
                    result = lastResolved - 1;
                    if (result < 0) result = Math.max(0, totalStyles - 1);
                } else {
                    const styles = this._Eclipse_styleWidget?.options?.values || [];
                    const currentStyle = this._Eclipse_styleWidget?.value;
                    const foundIdx = styles.indexOf(currentStyle);
                    result = foundIdx >= 0 ? foundIdx : 0;
                }
            } else {
                result = indexVal >= 0 ? indexVal % totalStyles : 0;
            }
            return result;
        };
    },
    async setup() {
        const origGraphToPrompt = app.graphToPrompt;
        app.graphToPrompt = async function () {
            const promptData = await origGraphToPrompt.apply(this, arguments);
            if (!promptData || !promptData.output) return promptData;
            const nodes = app.graph._nodes;
            for (const node of nodes) {
                if (node.type !== NODE_NAME || !node._Eclipse_indexWidget) continue;
                if (node.mode === 2 || node.mode === 4) continue;
                const nodeId = String(node.id);
                if (!promptData.output[nodeId]) continue;
                const resolvedIndex = node.getIndexToUse();
                const indexWidget = node._Eclipse_indexWidget;
                const styleWidget = node._Eclipse_styleWidget;
                const indexVal = indexWidget.value;
                if (promptData.output[nodeId].inputs && promptData.output[nodeId].inputs.index !== undefined) {
                    promptData.output[nodeId].inputs.index = resolvedIndex;
                }
                const isSpecial = indexVal < 0;
                if (!isSpecial && indexWidget.value !== resolvedIndex) {
                    node._Eclipse_updatingIndex = true;
                    indexWidget.value = resolvedIndex;
                    if (indexWidget.callback) indexWidget.callback(resolvedIndex);
                    node._Eclipse_updatingIndex = false;
                }
                const styles = styleWidget.options?.values || [];
                if (styles.length > 0) {
                    const styleName = styles[resolvedIndex % styles.length];
                    if (styleName && styleWidget.value !== styleName) {
                        node._Eclipse_updatingStyle = true;
                        styleWidget.value = styleName;
                        node._Eclipse_updatingStyle = false;
                    }
                }
                node.setDirtyCanvas(true, true);
                node._Eclipse_lastResolvedIndex = resolvedIndex;
                if (node._Eclipse_lastIndexButton) {
                    if (isSpecial && node._Eclipse_lastResolvedIndex != null) {
                        node._Eclipse_lastIndexButton.name = `♻️ ${node._Eclipse_lastResolvedIndex}`;
                        node._Eclipse_lastIndexButton.disabled = false;
                    } else {
                        node._Eclipse_lastIndexButton.name = '♻️ (Use Last Queued Index)';
                        node._Eclipse_lastIndexButton.disabled = true;
                    }
                    if (isVueMode()) notifyVue(node);
                }
                if (promptData.workflow && promptData.workflow.nodes) {
                    const wfNode = promptData.workflow.nodes.find((n) => n.id === node.id);
                    if (wfNode && wfNode.widgets_values) {
                        const idx = node.widgets.indexOf(indexWidget);
                        if (idx >= 0) wfNode.widgets_values[idx] = resolvedIndex;
                    }
                }
            }
            return promptData;
        };
    },
    async refreshComboInNodes() {
        // Invalidate server-side caches first so /eclipse/prompt_styler/styles returns fresh data.
        try { await fetch('/eclipse/reload_all'); } catch (_) {}
        for (const node of app.graph?._nodes || []) {
            if (node.type !== NODE_NAME) continue;
            const modeW = node.widgets?.find((w) => w.name === 'style_mode');
            const styleW = node._Eclipse_styleWidget;
            const indexW = node._Eclipse_indexWidget;
            if (!modeW || !styleW) continue;
            try {
                const resp = await fetch(`/eclipse/prompt_styler/styles/${modeW.value}`);
                if (resp.ok) {
                    const styles = (await resp.json()).styles || [];
                    if (styles.length > 0) {
                        const prevStyle = styleW.value;
                        styleW.options.values = styles;
                        nodeStyleCounts.set(node.id, styles.length);
                        if (indexW?.options) indexW.options.max = Math.max(0, styles.length - 1);
                        if (!styles.includes(prevStyle) && styles.length > 0) {
                            styleW.value = styles[0];
                        }
                        node.setDirtyCanvas(true, true);
                    }
                }
            } catch (_) {}
        }
    },
});
