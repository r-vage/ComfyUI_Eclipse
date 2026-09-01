import {
    app
} from './comfy/index.js';
import {
    notifyVue,
    smartResize,
    createWidgetVisibilityManager,
    isConfiguringGraph,
    isVueMode
} from './eclipse-widget-performance-utils.js';
import {
    createComboChipWidget,
    injectComboChipCSS
} from './eclipse-combo-chip.js';
const NODE_NAME = 'Prompt Styler [Eclipse]';
const NODE_NAME_V2 = 'Prompt Styler v2 [Eclipse]';
const NODE_NAMES = new Set([NODE_NAME, NODE_NAME_V2]);
const V2_FEATURES = [
    {
        label: 'spaces_to_underscores',
        tooltip: 'Replace spaces in short comma-separated prompt segments with underscores.'
    },
    {
        label: 'apply_to_positive',
        tooltip: 'Apply the selected style to the positive prompt.'
    },
    {
        label: 'apply_to_negative',
        tooltip: 'Apply the selected style to the negative prompt.'
    },
    {
        label: 'log_prompt',
        tooltip: 'Log the styled positive and negative prompts.'
    }
];
const V2_BACKING_WIDGETS = V2_FEATURES.map((feature) => feature.label);
const V2_SERIALIZED_WIDGETS = [
    'style_mode',
    'style',
    'index',
    'features',
    'spaces_to_underscores',
    'max_words_to_combine',
    'apply_to_positive',
    'apply_to_negative',
    'log_prompt'
];
const MODE_RANDOM = -1;
const MODE_INCREMENT = -2;
const MODE_DECREMENT = -3;
const nodeStyleCounts = new Map();
injectComboChipCSS('psv2');

function selectedV2Features(node) {
    return V2_BACKING_WIDGETS.filter((name) =>
        Boolean(node.widgets?.find((widget) => widget.name === name)?.value)
    );
}

function syncV2FeaturesToBacking(node, selected) {
    for (const name of V2_BACKING_WIDGETS) {
        const widget = node.widgets?.find((candidate) => candidate.name === name);
        if (widget) widget.value = selected.has(name);
    }
}

function v2NativeWidgets(node) {
    return V2_SERIALIZED_WIDGETS.map((name) =>
        node.widgets?.find((widget) => widget.name === name)
    ).filter(Boolean);
}

function workflowValue(value) {
    if (value == null || typeof value !== 'object') return value ?? null;
    return JSON.parse(JSON.stringify(value));
}

function serializeV2Widgets(node, data) {
    const nativeWidgets = v2NativeWidgets(node);
    data.widgets_values = nativeWidgets.map((widget) => workflowValue(widget.value));
    data.widgets_values_named = Object.fromEntries(
        nativeWidgets.map((widget) => [widget.name, workflowValue(widget.value)])
    );
    return data;
}

function restoreV2Widgets(node, data) {
    const nativeWidgets = v2NativeWidgets(node);
    const named = data?.widgets_values_named;
    if (named && !Array.isArray(named) && Object.keys(named).length > 0) {
        for (const widget of nativeWidgets) {
            if (Object.prototype.hasOwnProperty.call(named, widget.name)) {
                widget.value = named[widget.name] ?? undefined;
            }
        }
        return;
    }
    if (!Array.isArray(data?.widgets_values)) return;
    for (let index = 0; index < nativeWidgets.length && index < data.widgets_values.length; index += 1) {
        nativeWidgets[index].value = data.widgets_values[index] ?? undefined;
    }
}

app.registerExtension({
    name: 'Eclipse.PromptStyler',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (!NODE_NAMES.has(nodeData.name)) return;
        const isV2 = nodeData.name === NODE_NAME_V2;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            const nodeId = node.id;
            const vis = createWidgetVisibilityManager(node);
            // Pre-hide conditional and backing widgets before the first render.
            vis.hideInitially(isV2
                ? ['features', ...V2_BACKING_WIDGETS, 'max_words_to_combine']
                : ['max_words_to_combine']);
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
            let featureWidget = null;
            if (isV2) {
                const origSerialize = node.serialize;
                node.serialize = function () {
                    const data = origSerialize.apply(this, arguments);
                    return serializeV2Widgets(this, data);
                };
                featureWidget = createComboChipWidget({
                    node,
                    options: V2_FEATURES,
                    savedValue: selectedV2Features(node),
                    // Feature selection is the primary v2 control, so keep the
                    // cosmetic bar above the ordinary style widgets.
                    origIdx: 0,
                    widgetName: '_psv2_features',
                    cssPrefix: 'psv2',
                    serialize: false
                });
                node._Eclipse_promptStylerFeatureWidget = featureWidget;
            }
            const refreshSpacesVisibility = () => {
                vis.setVisible('max_words_to_combine', spacesWidget?.value ?? false);
                if (isV2) {
                    vis.setVisible('features', false);
                    for (const name of V2_BACKING_WIDGETS) vis.setVisible(name, false);
                    if (node.id === -1) return;
                }
                smartResize(node, {
                    minWidth: 0,
                    minHeight: 0,
                    padding: 0
                });
            };
            if (featureWidget) {
                featureWidget.callback = () => {
                    syncV2FeaturesToBacking(node, new Set(featureWidget.value));
                    vis.markUserDriven();
                    refreshSpacesVisibility();
                    _app.graph.setDirtyCanvas(true);
                };
                syncV2FeaturesToBacking(node, new Set(featureWidget.value));
            }
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
                    if (origModeCb) await origModeCb.apply(this, arguments);
                    node._Eclipse_lastResolvedIndex = null;
                    node._Eclipse_manualIndex = null;
                    const styles = await fetchStyles(val);
                    if (styles) applyStyles(styles, true);
                };
            }
            const origIndexCb = indexWidget.callback;
            const setLastIndexButtonLabel = (label) => {
                if (!node._Eclipse_lastIndexButton) return;
                if (isV2) node._Eclipse_lastIndexButton.label = label;
                else node._Eclipse_lastIndexButton.name = label;
            };
            indexWidget.callback = function (val) {
                if (origIndexCb) origIndexCb.apply(this, arguments);
                if (node._Eclipse_updatingIndex) return;
                if (node._Eclipse_lastIndexButton) {
                    if (val >= 0) {
                        setLastIndexButtonLabel('🌘 (Use Last Queued Index)');
                        node._Eclipse_lastIndexButton.disabled = true;
                        node._Eclipse_lastResolvedIndex = null;
                        node._Eclipse_manualIndex = null;
                    } else if (node._Eclipse_lastResolvedIndex != null) {
                        setLastIndexButtonLabel(`🌘 ${node._Eclipse_lastResolvedIndex}`);
                        node._Eclipse_lastIndexButton.disabled = false;
                    } else {
                        setLastIndexButtonLabel('🌘 (Use Last Queued Index)');
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
                        setLastIndexButtonLabel(`🌘 ${foundIdx}`);
                        node._Eclipse_lastIndexButton.disabled = false;
                        if (isVueMode()) notifyVue(node);
                    }
                }
            };
            const addButton = (internalName, label, tooltip, onClick) => {
                const btn = isV2
                    ? node.addWidget('button', internalName, '', onClick, { serialize: false })
                    : node.addWidget('button', label, null, onClick);
                if (isV2) btn.label = label;
                btn.tooltip = tooltip;
                btn.serialize = false;
                return btn;
            };
            addButton('_psv2_randomize', '🌑 Randomize Each Time', 'Set index to -1 (random style on each queue)', () => {
                node._Eclipse_updatingIndex = true;
                indexWidget.value = -1;
                node._Eclipse_updatingIndex = false;
                node._Eclipse_lastResolvedIndex = null;
                node._Eclipse_manualIndex = null;
                _app.graph.setDirtyCanvas(true);
            }, );
            const lastIndexBtn = addButton('_psv2_last_index', '🌘 (Use Last Queued Index)', 'Lock to the index from last queue (disables increment/decrement/random)', () => {
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
                node._Eclipse_promptStylerFeatureWidget = null;
                if (origOnRemoved) origOnRemoved.apply(this, arguments);
            };
            const origOnConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                if (origOnConfigure) origOnConfigure.apply(this, arguments);
                if (featureWidget) {
                    restoreV2Widgets(node, data);
                    vis.clearCache?.();
                    featureWidget.value = selectedV2Features(node);
                }
                refreshSpacesVisibility();
                syncStyleCounts();
            };
            if (isV2) {
                if (!isConfiguringGraph()) {
                    requestAnimationFrame(() => {
                        syncStyleToIndex(indexWidget.value);
                        refreshSpacesVisibility();
                        syncStyleCounts();
                        const oldHeight = node.size[1];
                        node.size[1] = 0;
                        const computed = node.computeSize();
                        if (computed[1] !== oldHeight) node.setSize?.([node.size[0], computed[1]]);
                        else node.size[1] = oldHeight;
                    });
                }
            } else {
                setTimeout(() => {
                    syncStyleToIndex(indexWidget.value);
                    refreshSpacesVisibility();
                    syncStyleCounts();
                }, 100);
            }
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
                if (!NODE_NAMES.has(node.type) || !node._Eclipse_indexWidget) continue;
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
                        if (node.type === NODE_NAME_V2) {
                            node._Eclipse_lastIndexButton.label = `🌘 ${node._Eclipse_lastResolvedIndex}`;
                        } else {
                            node._Eclipse_lastIndexButton.name = `🌘 ${node._Eclipse_lastResolvedIndex}`;
                        }
                        node._Eclipse_lastIndexButton.disabled = false;
                    } else {
                        if (node.type === NODE_NAME_V2) {
                            node._Eclipse_lastIndexButton.label = '🌘 (Use Last Queued Index)';
                        } else {
                            node._Eclipse_lastIndexButton.name = '🌘 (Use Last Queued Index)';
                        }
                        node._Eclipse_lastIndexButton.disabled = true;
                    }
                    if (isVueMode()) notifyVue(node);
                }
                if (promptData.workflow && promptData.workflow.nodes) {
                    const wfNode = promptData.workflow.nodes.find((n) => n.id === node.id);
                    if (wfNode && wfNode.widgets_values) {
                        const widgetIndex = node.widgets.indexOf(indexWidget);
                        const serializedIndex = node.widgets
                            .slice(0, widgetIndex)
                            .filter((widget) => widget.serialize !== false)
                            .length;
                        if (widgetIndex >= 0) wfNode.widgets_values[serializedIndex] = resolvedIndex;
                        if (wfNode.widgets_values_named) {
                            wfNode.widgets_values_named.index = resolvedIndex;
                        }
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
            if (!NODE_NAMES.has(node.type)) continue;
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
