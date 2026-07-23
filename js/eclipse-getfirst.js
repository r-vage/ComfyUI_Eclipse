import {
    app
} from './comfy/index.js';
import {
    SETTER_TYPES,
    getVisibleSetNames,
    findSetterByName,
    isSetterPathToRootActive,
    isSetterActive,
    resolveBypassedLink,
    subgraphOpState,
    _pasteRenameMap,
    pasteRenameScheduler,
} from './eclipse-set-get-utils.js';
import { createRendererAwareSubmenuEntry } from './eclipse-context-menu-utils.js';
const LGraphNode = LiteGraph.LGraphNode;
const TYPE_FILTERS = ["*", "MODEL", "CLIP", "VAE", "CONDITIONING", "LATENT", "IMAGE", "MASK", "FLOAT", "INT", "STRING", "CONTROL_NET", "NOISE", "GUIDER", "SAMPLER", "SIGMAS"];

function showAlert(message) {
    app.extensionManager.toast.add({
        severity: 'warn',
        summary: "Eclipse GetFirst",
        detail: message,
        life: 5000,
    });
}

function getSetterVars(graph, typeFilter) {
    return getVisibleSetNames(graph, typeFilter);
}

function findSetter(graph, varName) {
    const result = findSetterByName(graph, varName);
    return result ? result.node : null;
}

function formatTypeName(type) {
    if (!type || type === '*') return '';
    if (type === 'CONTROL_NET') return 'ControlNet';
    if (type === 'VAE') return 'VAE';
    return type.charAt(0) + type.slice(1).toLowerCase();
}
app.registerExtension({
    name: "Eclipse.GetFirstNode",
    registerCustomNodes() {
        class GetFirstNode extends LGraphNode {
            serialize_widgets = true;
            drawConnection = false;
            slotColor = "#FFF";
            canvas = app.canvas;
            constructor(title) {
                super(title);
                this.color = '#000000';
                this.bgcolor = '#000000';
                if (!this.properties) {
                    this.properties = {};
                }
                this.properties.showOutputText = true;
                this.properties.varCount = 2;
                this.properties.showNav = true;
                this._justAdded = false;
                const node = this;
                this._resolvedSetters = null;
                this._cacheTimestamp = 0;
                this.invalidateCache = function () {
                    this._resolvedSetters = null;
                    this._cacheTimestamp = 0;
                };
                this.rebuildCache = function () {
                    if (!this.graph) {
                        this._resolvedSetters = [];
                        return;
                    }
                    const varWidgets = this.widgets.slice(2);
                    this._resolvedSetters = varWidgets.map(w => {
                        const name = w.value;
                        if (!name || name === '') return null;
                        const setter = findSetter(this.graph, name);
                        if (!setter) return null;
                        return {
                            setter,
                            active: isSetterActive(this.graph, setter)
                        };
                    });
                    this._cacheTimestamp = performance.now();
                };
                this.getCachedSetters = function () {
                    const now = performance.now();
                    if (!this._resolvedSetters || (now - this._cacheTimestamp) > 500) {
                        this.rebuildCache();
                    }
                    return this._resolvedSetters;
                };
                this.getFilteredVars = function () {
                    const typeFilter = this.widgets?.[0]?.value || '*';
                    return getSetterVars(this.graph, typeFilter);
                };
                this.createVarWidget = function (index) {
                    this.addWidget("combo", `var_${index}`, "", (value) => {
                        node.invalidateCache();
                        node.updateOutputType();
                    }, {
                        values: () => {
                            const vars = node.getFilteredVars();
                            const usedVars = new Set();
                            const varWidgets = node.widgets.slice(2);
                            for (let i = 0; i < varWidgets.length; i++) {
                                if (i !== (index - 1) && varWidgets[i].value) {
                                    usedVars.add(varWidgets[i].value);
                                }
                            }
                            return ["", ...vars.filter(v => !usedVars.has(v))];
                        }
                    });
                };
                this.syncVarWidgets = function () {
                    const targetCount = Math.max(1, Math.min(20, this.properties.varCount || 2));
                    const varWidgetStartIdx = 2;
                    const existingVarWidgets = this.widgets.slice(varWidgetStartIdx);
                    const currentCount = existingVarWidgets.length;
                    if (currentCount < targetCount) {
                        for (let i = currentCount; i < targetCount; i++) {
                            this.createVarWidget(i + 1);
                        }
                    } else if (currentCount > targetCount) {
                        const keepWidgets = this.widgets.slice(0, varWidgetStartIdx + targetCount);
                        this.widgets.length = 0;
                        for (const w of keepWidgets) {
                            this.widgets.push(w);
                        }
                    }
                    const computed = this.computeSize();
                    this.setSize([Math.max(this.size[0], computed[0]), computed[1]]);
                };
                this.refreshVarWidgets = function () {
                    this.invalidateCache();
                    this.setDirtyCanvas(true, true);
                };
                this.updateOutputType = function () {
                    this.invalidateCache();
                    const typeFilter = this.widgets[0].value;
                    this.title = typeFilter !== '*' ? `Get First ${formatTypeName(typeFilter)}` : 'Get First';
                    if (typeFilter !== '*') {
                        this.outputs[0].type = typeFilter;
                        this.outputs[0].name = typeFilter;
                    } else {
                        const resolvedType = this.resolveOutputType();
                        this.outputs[0].type = resolvedType || '*';
                        this.outputs[0].name = resolvedType || '*';
                    }
                };
                this.resolveOutputType = function () {
                    if (!this.graph) return null;
                    const varWidgets = this.widgets.slice(2);
                    for (const w of varWidgets) {
                        const varName = w.value;
                        if (!varName || varName === '') continue;
                        const setter = findSetter(this.graph, varName);
                        if (setter && setter.inputs?.[0]?.type && setter.inputs[0].type !== '*') {
                            return setter.inputs[0].type;
                        }
                    }
                    return null;
                };
                this.clone = function () {
                    const cloned = GetFirstNode.prototype.clone.apply(this);
                    cloned.setSize(cloned.computeSize());
                    return cloned;
                };
                this.renameVar = function (oldName, newName) {
                    if (!oldName || oldName === '') return;
                    const varWidgets = this.widgets.slice(2);
                    let changed = false;
                    for (const w of varWidgets) {
                        if (w.value === oldName) {
                            w.value = newName;
                            changed = true;
                        }
                    }
                    if (changed) {
                        this.updateOutputType();
                        this.setDirtyCanvas(true, true);
                    }
                };
                this.swapVars = function (idxA, idxB) {
                    const varWidgets = this.widgets.slice(2);
                    if (idxA < 0 || idxB < 0 || idxA >= varWidgets.length || idxB >= varWidgets.length) return;
                    const tmp = varWidgets[idxA].value;
                    varWidgets[idxA].value = varWidgets[idxB].value;
                    varWidgets[idxB].value = tmp;
                    varWidgets[idxA].name = `var_${idxA + 1}`;
                    varWidgets[idxB].name = `var_${idxB + 1}`;
                    this.updateOutputType();
                    this.setDirtyCanvas(true, true);
                };
                this.moveVarUp = function (idx) {
                    if (idx <= 0) return;
                    this.swapVars(idx, idx - 1);
                };
                this.moveVarDown = function (idx) {
                    const varWidgets = this.widgets.slice(2);
                    if (idx >= varWidgets.length - 1) return;
                    this.swapVars(idx, idx + 1);
                };
                this.moveVarUpBy = function (idx, count) {
                    let currentIdx = idx;
                    for (let c = 0; c < count; c++) {
                        if (currentIdx <= 0) break;
                        this.swapVars(currentIdx, currentIdx - 1);
                        currentIdx--;
                    }
                };
                this.moveVarDownBy = function (idx, count) {
                    let currentIdx = idx;
                    const varWidgets = this.widgets.slice(2);
                    for (let c = 0; c < count; c++) {
                        if (currentIdx >= varWidgets.length - 1) break;
                        this.swapVars(currentIdx, currentIdx + 1);
                        currentIdx++;
                    }
                };
                this.moveVarToTop = function (idx) {
                    for (let i = idx; i > 0; i--) {
                        this.swapVars(i, i - 1);
                    }
                };
                this.moveVarToBottom = function (idx) {
                    const varWidgets = this.widgets.slice(2);
                    for (let i = idx; i < varWidgets.length - 1; i++) {
                        this.swapVars(i, i + 1);
                    }
                };
                this.insertVarAt = function (idx) {
                    const varWidgets = this.widgets.slice(2);
                    const maxCount = 20;
                    if (varWidgets.length >= maxCount) {
                        showAlert("Maximum 20 vars reached.");
                        return;
                    }
                    const newCount = varWidgets.length + 1;
                    this.properties.varCount = newCount;
                    this.widgets[1].value = String(newCount);
                    this.syncVarWidgets();
                    const updatedVarWidgets = this.widgets.slice(2);
                    for (let i = updatedVarWidgets.length - 1; i > idx; i--) {
                        updatedVarWidgets[i].value = updatedVarWidgets[i - 1].value;
                    }
                    updatedVarWidgets[idx].value = "";
                    this.updateOutputType();
                    this.setDirtyCanvas(true, true);
                };
                this.addWidget("combo", "type_filter", "*", (value) => {
                    node.refreshVarWidgets();
                    node.updateOutputType();
                }, {
                    values: TYPE_FILTERS
                });
                const VAR_COUNT_OPTIONS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20"];
                this.addWidget("combo", "var_count", "2", (value) => {
                    const count = parseInt(value) || 2;
                    node.properties.varCount = count;
                    node.syncVarWidgets();
                    node.setDirtyCanvas(true, true);
                }, {
                    values: VAR_COUNT_OPTIONS
                });
                this.addOutput("*", "*");
                this.syncVarWidgets();
                this.isVirtualNode = true;
            }
            resolveVirtualOutput(slot) {
                if (!this.graph) return undefined;
                const varWidgets = this.widgets.slice(2);
                for (const w of varWidgets) {
                    const varName = w.value;
                    if (!varName || varName === '') continue;
                    const result = findSetterByName(this.graph, varName);
                    if (!result) continue;
                    // Same-graph → getInputLink handles this case.
                    if (result.graph === this.graph) {
                        const setter = result.node;
                        if (!isSetterActive(this.graph, setter)) continue;
                        return undefined;
                    }
                    const { node: setter, graph: setterGraph } = result;
                    // Cross-graph resolution works for any relationship (ancestor,
                    // descendant, sibling) as long as the setter's path to root is
                    // fully active — see isSetterPathToRootActive.
                    if (!isSetterPathToRootActive(setterGraph)) continue;
                    if (!isSetterActive(setterGraph, setter)) continue;
                    const link = resolveBypassedLink(setterGraph, setter);
                    if (!link) continue;
                    const sourceNode = setterGraph.getNodeById(link.origin_id);
                    if (!sourceNode) continue;
                    return { node: sourceNode, slot: link.origin_slot };
                }
                return undefined;
            }
            getInputLink(slot) {
                if (!this.graph) return null;
                const varWidgets = this.widgets.slice(2);
                for (const w of varWidgets) {
                    const varName = w.value;
                    if (!varName || varName === '') continue;
                    // Only search same graph — cross-graph SetNodes are handled by resolveVirtualOutput.
                    // findSetter searches all graphs (including descendants) which produces links that
                    // LLink.resolve(this.graph) cannot resolve, causing InvalidLinkError on 1.42.11+.
                    const setter = this.graph._nodes?.find(
                        n => SETTER_TYPES.has(n.type) && n.widgets?.[0]?.value === varName
                    );
                    if (!setter) continue;
                    if (!isSetterActive(this.graph, setter)) continue;
                    const link = resolveBypassedLink(this.graph, setter);
                    if (!link) continue;
                    return link;
                }
                return null;
            }
            onAdded(graph) {
                this._justAdded = true;
                pasteRenameScheduler.schedule?.();
                this.updateOutputType();
            }
            onResize() {
                if (this.outputs?.[0]) this.updateOutputType();
            }
            _handlePasteRename() {
                // Called by the central paste-rename scan in eclipse-set-get.js schedulePasteRenamePass().
                // Phase 2: update all variable widgets using entries from _pasteRenameMap.
                const VAR_WIDGET_START = 2;
                const varWidgets = this.widgets.slice(VAR_WIDGET_START);
                for (const w of varWidgets) {
                    const oldName = w.value;
                    if (!oldName) continue;
                    const newName = _pasteRenameMap.get(oldName);
                    if (newName) {
                        w.value = newName;
                    }
                }
                this.invalidateCache();
                this.updateOutputType();
            }
            onConfigure(data) {
                if (data.properties?.varCount) {
                    this.properties.varCount = data.properties.varCount;
                    this.widgets[1].value = String(data.properties.varCount);
                }
                const savedWidgets = data.widgets_values;
                if (savedWidgets && savedWidgets.length > 2) {
                    const varCount = savedWidgets.length - 2;
                    this.properties.varCount = varCount;
                    this.widgets[1].value = String(varCount);
                    this.syncVarWidgets();
                    for (let i = 0; i < varCount; i++) {
                        if (this.widgets[i + 2]) {
                            this.widgets[i + 2].value = savedWidgets[i + 2] || "";
                        }
                    }
                }
                // Paste rename is handled by the central schedulePasteRenamePass() scan.
                this.updateOutputType();
            }
            getExtraMenuOptions(_, options) {
                const node = this;
                options.unshift({
                    content: this.properties?.showNav ? 'Hide Nav Arrows' : 'Show Nav Arrows',
                    callback: () => {
                        node.properties.showNav = !node.properties.showNav;
                        node.setDirtyCanvas(true, false);
                    },
                });
                const menuEntry = this.drawConnection ? "Hide connections" : "Show connections";
                options.unshift({
                    content: menuEntry,
                    callback: () => {
                        node.drawConnection = !node.drawConnection;
                        const varWidgets = node.widgets.slice(2);
                        for (const w of varWidgets) {
                            const setter = findSetter(node.graph, w.value);
                            if (setter && isSetterActive(node.graph, setter)) {
                                const linkType = setter.inputs[0].type;
                                node.slotColor = node.canvas.default_connection_color_byType?.[linkType] || "#FFF";
                                break;
                            }
                        }
                        node.canvas.setDirty(true, true);
                    },
                }, {
                    content: "Go to active setter",
                    callback: () => {
                        const varWidgets = node.widgets.slice(2);
                        for (const w of varWidgets) {
                            const setter = findSetter(node.graph, w.value);
                            if (setter && isSetterActive(node.graph, setter)) {
                                node.canvas.centerOnNode(setter);
                                node.canvas.selectNode(setter, false);
                                node.canvas.setDirty(true, true);
                                return;
                            }
                        }
                        showAlert("No active setter found.");
                    },
                }, );
                const varWidgets = this.widgets.slice(2);
                const setterItems = [];
                for (let i = 0; i < varWidgets.length; i++) {
                    const varName = varWidgets[i].value;
                    if (!varName) continue;
                    const setter = findSetter(this.graph, varName);
                    const active = setter && isSetterActive(this.graph, setter);
                    setterItems.push({
                        content: `${i + 1}. ${varName} ${active ? '✓' : '✗'}`,
                        callback: () => {
                            if (setter) {
                                node.canvas.centerOnNode(setter);
                                node.canvas.selectNode(setter, false);
                                node.canvas.setDirty(true, true);
                            }
                        },
                    });
                }
                if (setterItems.length > 0) {
                    options.unshift({
                        content: "Setters",
                        has_submenu: true,
                        submenu: {
                            title: "Priority List",
                            options: setterItems,
                        },
                    });
                }
                const reorderItems = [];
                for (let i = 0; i < varWidgets.length; i++) {
                    const label = varWidgets[i].value || `(empty)`;
                    const subOpts = [];
                    if (i > 0) {
                        subOpts.push({
                            content: "↑ Move to Top",
                            callback: () => {
                                node.moveVarToTop(i);
                            },
                        });
                        subOpts.push({
                            content: "↑ Move Up",
                            callback: () => {
                                node.moveVarUp(i);
                            },
                        });
                        const upOptions = [];
                        const maxUp = Math.min(10, i);
                        for (let k = 1; k <= maxUp; k++) {
                            upOptions.push({
                                content: `${k} slot${k > 1 ? 's' : ''}`,
                                callback: () => {
                                    node.moveVarUpBy(i, k);
                                }
                            });
                        }
                        subOpts.push({
                            content: "↑ Move Up By...",
                            has_submenu: true,
                            submenu: {
                                title: "Move Up",
                                options: upOptions
                            }
                        });
                    }
                    if (i < varWidgets.length - 1) {
                        const downOptions = [];
                        const maxDown = Math.min(10, varWidgets.length - 1 - i);
                        for (let k = 1; k <= maxDown; k++) {
                            downOptions.push({
                                content: `${k} slot${k > 1 ? 's' : ''}`,
                                callback: () => {
                                    node.moveVarDownBy(i, k);
                                }
                            });
                        }
                        subOpts.push({
                            content: "↓ Move Down By...",
                            has_submenu: true,
                            submenu: {
                                title: "Move Down",
                                options: downOptions
                            }
                        });
                        subOpts.push({
                            content: "↓ Move Down",
                            callback: () => {
                                node.moveVarDown(i);
                            },
                        });
                        subOpts.push({
                            content: "↓ Move to Bottom",
                            callback: () => {
                                node.moveVarToBottom(i);
                            },
                        });
                    }
                    subOpts.push(null);
                    subOpts.push({
                        content: "＋ Insert Above",
                        callback: () => {
                            node.insertVarAt(i);
                        },
                    });
                    reorderItems.push({
                        content: `${i + 1}. ${label}`,
                        has_submenu: true,
                        submenu: {
                            title: label,
                            options: subOpts
                        },
                    });
                }
                options.unshift(createRendererAwareSubmenuEntry({
                    content: "Reorder Vars",
                    has_submenu: true,
                    submenu: {
                        title: "Reorder Vars",
                        options: reorderItems
                    },
                }));
            }
            onDrawForeground(ctx, lGraphCanvas) {
                if (this.flags?.collapsed) return;
                const canvas = lGraphCanvas || this.canvas;
                if (canvas?.visible_area) {
                    const [vx, vy, vw, vh] = canvas.visible_area;
                    const [nx, ny] = this.pos;
                    const [nw, nh] = this.size;
                    if (nx + nw < vx || nx > vx + vw || ny + nh < vy || ny > vy + vh) return;
                }
                if (this.drawConnection) {
                    this._drawVirtualLink(lGraphCanvas, ctx);
                }
            }
            drawWidgets(ctx, options) {
                const showNav = false !== this.properties?.showNav;
                const NAV_LANE = 18;
                const VAR_WIDGET_START = 2;
                if (showNav && this.widgets) {
                    const varW = this.size[0] - NAV_LANE;
                    for (let i = VAR_WIDGET_START; i < this.widgets.length; i++) {
                        this.widgets[i].width = varW;
                    }
                } else if (this.widgets) {
                    for (let i = VAR_WIDGET_START; i < this.widgets.length; i++) {
                        delete this.widgets[i].width;
                    }
                }
                const result = super.drawWidgets?.(ctx, options);
                if (!this.flags?.collapsed) {
                    this._drawActiveIndicators(ctx);
                }
                return result;
            }
            getWidgetOnPos(canvasX, canvasY) {
                if (false !== this.properties?.showNav && !this.flags?.collapsed) {
                    const localX = canvasX - this.pos[0];
                    if (localX >= this.size[0] - 28 && localX <= this.size[0] - 6) {
                        const localY = canvasY - this.pos[1];
                        const VAR_WIDGET_START = 2;
                        for (let i = VAR_WIDGET_START; i < this.widgets.length; i++) {
                            const w = this.widgets[i];
                            if (!w || w.last_y === undefined || !w.value) continue;
                            if (localY >= w.last_y && localY <= w.last_y + LiteGraph.NODE_WIDGET_HEIGHT) {
                                return null;
                            }
                        }
                    }
                }
                return super.getWidgetOnPos(canvasX, canvasY);
            }
            onMouseDown(e, localPos, graphCanvas) {
                if (this.flags?.collapsed) return false;
                if (false === this.properties?.showNav) return false;
                const x = localPos[0];
                const y = localPos[1];
                if (x < this.size[0] - 28 || x > this.size[0] - 6) return false;
                const VAR_WIDGET_START = 2;
                const varWidgets = this.widgets.slice(VAR_WIDGET_START);
                for (let i = 0; i < varWidgets.length; i++) {
                    const w = varWidgets[i];
                    if (!w || w.last_y === undefined || !w.value) continue;
                    if (y >= w.last_y && y <= w.last_y + LiteGraph.NODE_WIDGET_HEIGHT) {
                        const setter = findSetter(this.graph, w.value);
                        if (setter && graphCanvas) {
                            graphCanvas.centerOnNode(setter);
                            graphCanvas.selectNode(setter, false);
                            graphCanvas.setDirty(true, true);
                        }
                        return true;
                    }
                }
                return false;
            }
            _drawActiveIndicators(ctx) {
                const cached = this.getCachedSetters();
                if (!cached) return;
                const showNav = false !== this.properties?.showNav;
                const nodeW = this.size[0];
                for (let i = 0; i < cached.length; i++) {
                    const w = this.widgets[i + 2];
                    if (!w || w.last_y === undefined) continue;
                    const entry = cached[i];
                    const centerY = w.last_y + LiteGraph.NODE_WIDGET_HEIGHT * 0.5;
                    if (entry?.active) {
                        ctx.fillStyle = "#2E7D32";
                        ctx.beginPath();
                        ctx.arc(10, centerY, 4, 0, Math.PI * 2);
                        ctx.fill();
                    }
                    if (showNav && w.value) {
                        const ax = nodeW - 15;
                        ctx.fillStyle = ctx.strokeStyle = '#89A';
                        ctx.lineJoin = 'round';
                        ctx.lineCap = 'round';
                        ctx.beginPath();
                        ctx.moveTo(ax, centerY);
                        ctx.lineTo(ax - 5, centerY + 5);
                        ctx.lineTo(ax - 5, centerY + 2);
                        ctx.lineTo(ax - 11, centerY + 2);
                        ctx.lineTo(ax - 11, centerY - 2);
                        ctx.lineTo(ax - 5, centerY - 2);
                        ctx.lineTo(ax - 5, centerY - 5);
                        ctx.closePath();
                        ctx.fill();
                        ctx.stroke();
                    }
                }
            }
            _drawVirtualLink(lGraphCanvas, ctx) {
                const cached = this.getCachedSetters();
                if (!cached) return;
                for (const entry of cached) {
                    if (!entry || !entry.active) continue;
                    const setter = entry.setter;
                    const defaultLink = {
                        type: 'default',
                        color: this.slotColor
                    };
                    let start_node_slotpos = setter.getConnectionPos(false, 0);
                    start_node_slotpos = [start_node_slotpos[0] - this.pos[0], start_node_slotpos[1] - this.pos[1], ];
                    let end_node_slotpos = [0, -LiteGraph.NODE_TITLE_HEIGHT * 0.5];
                    lGraphCanvas.renderLink(ctx, start_node_slotpos, end_node_slotpos, defaultLink, false, null, this.slotColor);
                    break;
                }
            }
        }
        LiteGraph.registerNodeType("GetFirstNode", Object.assign(GetFirstNode, {
            title: "Get First",
        }));
        GetFirstNode.category = "🌒 Eclipse/ Set-Get";
    },
});
