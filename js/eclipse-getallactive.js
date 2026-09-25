import {
    app
} from './comfy/index.js';
import {
    SETTER_TYPES,
    getVisibleSetNames,
    findSetterByName,
    isSetterPathToRootActive,
    isSetterActive,
    getLink,
    resolveBypassedLink,
    subgraphOpState,
    _pasteRenameMap,
    pasteRenameScheduler,
    editMultiGetterGraph as editGraph,
} from './eclipse-set-get-utils.js';
import { createRendererAwareSubmenuEntry } from './eclipse-context-menu-utils.js';
import { linkedChain, deriveChainList, excludeChainVariable, renameChainVariable } from './eclipse-getallactive-sync.js';
import { openChainEditor, syncChainMenu, installSyncChainMenu } from './eclipse-getallactive-sync-ui.js';
import { smartResize } from './eclipse-widget-performance-utils.js';
const LGraphNode = LiteGraph.LGraphNode;
const TYPE_FILTERS = ["*", "MODEL", "CLIP", "VAE", "CONDITIONING", "LATENT", "IMAGE", "MASK", "FLOAT", "INT", "STRING", "CONTROL_NET", "NOISE", "GUIDER", "SAMPLER", "SIGMAS", "PIPE"];

function showAlert(message) {
    app.extensionManager.toast.add({
        severity: 'warn',
        summary: "Eclipse GetAll",
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
    name: "Eclipse.GetAllActiveNode",
    setup() { installSyncChainMenu(); },
    registerCustomNodes() {
        class GetAllActiveNode extends LGraphNode {
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
                this.properties.showNav = true;
                this.properties.varCount = 2;
                this.properties.keepConnectionsInPosition = false;
                this._justAdded = false;
                const node = this;
                const VAR_WIDGET_START = 2;
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
                    const varWidgets = this.widgets.slice(VAR_WIDGET_START);
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
                        const chain = linkedChain(node);
                        if (chain) {
                            const values = deriveChainList(chain.variables, node.properties.eclipseSyncChain);
                            node.widgets.slice(VAR_WIDGET_START).forEach((w, i) => { w.value = values[i] || ''; });
                            openChainEditor(node);
                            return;
                        }
                        node.invalidateCache();
                        node.updateOutputTypes();
                    }, {
                        values: () => {
                            if (linkedChain(node)) return [node.widgets[VAR_WIDGET_START + index - 1]?.value || ''];
                            const vars = node.getFilteredVars();
                            const usedVars = new Set();
                            const varWidgets = node.widgets.slice(VAR_WIDGET_START);
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
                    const existingVarWidgets = this.widgets.slice(VAR_WIDGET_START);
                    const currentCount = existingVarWidgets.length;
                    if (currentCount < targetCount) {
                        for (let i = currentCount; i < targetCount; i++) {
                            this.createVarWidget(i + 1);
                        }
                    } else if (currentCount > targetCount) {
                        for (const widget of existingVarWidgets.slice(targetCount)) {
                            if (typeof this.removeWidget === 'function') this.removeWidget(widget);
                            else {
                                widget.onRemove?.();
                                const index = this.widgets.indexOf(widget);
                                if (index >= 0) this.widgets.splice(index, 1);
                            }
                        }
                    }
                    this.syncOutputs(targetCount);
                    const computed = this.computeSize();
                    this.setSize([Math.max(this.size[0], computed[0]), computed[1]]);
                };
                this.syncOutputs = function (targetCount) {
                    const currentOutputs = this.outputs?.length || 0;
                    if (currentOutputs < targetCount) {
                        for (let i = currentOutputs; i < targetCount; i++) {
                            this.addOutput(`out_${i + 1}`, '*');
                        }
                    } else if (currentOutputs > targetCount) {
                        while (this.outputs.length > targetCount) {
                            this.removeOutput(this.outputs.length - 1);
                        }
                    }
                    this.updateOutputTypes();
                };
                this.refreshVarWidgets = function () {
                    this.invalidateCache();
                    this.setDirtyCanvas(true, true);
                };
                this.refreshChainControls = function () {
                    const linked = !!linkedChain(this);
                    for (const widget of this.widgets.slice(1)) {
                        widget.options ||= {};
                        widget.options.read_only = linked;
                        widget.disabled = linked;
                    }
                    this.updateOutputTypes();
                    this.setDirtyCanvas(true, true);
                    if (this.id !== -1) smartResize(this);
                };
                this.updateOutputTypes = function () {
                    this.invalidateCache();
                    if (!this.outputs || this.outputs.length === 0) return;
                    const typeFilter = this.widgets?.[0]?.value || '*';
                    this.title = typeFilter !== '*' ? `Get All Active ${formatTypeName(typeFilter)}` : 'Get All Active';
                    const varWidgets = this.widgets.slice(VAR_WIDGET_START);
                    for (let i = 0; i < this.outputs.length; i++) {
                        const varName = varWidgets[i]?.value || '';
                        let type = '*';
                        if (typeFilter !== '*') {
                            type = typeFilter;
                        } else if (varName) {
                            const setter = findSetter(this.graph, varName);
                            if (setter?.inputs?.[0]?.type && setter.inputs[0].type !== '*') {
                                type = setter.inputs[0].type;
                            }
                        }
                        this.outputs[i].type = type;
                        this.outputs[i].name = varName || `out_${i + 1}`;
                    }
                };
                this.clone = function () {
                    const cloned = GetAllActiveNode.prototype.clone.apply(this);
                    cloned.setSize(cloned.computeSize());
                    return cloned;
                };
                this.renameVar = function (oldName, newName) {
                    if (!oldName || oldName === '') return;
                    if (renameChainVariable(this, oldName, newName)) return;
                    const varWidgets = this.widgets.slice(VAR_WIDGET_START);
                    let changed = false;
                    for (const w of varWidgets) {
                        if (w.value === oldName) {
                            w.value = newName;
                            changed = true;
                        }
                    }
                    if (changed) {
                        this.updateOutputTypes();
                        this.setDirtyCanvas(true, true);
                    }
                };
                this.swapOutputSlots = function (idxA, idxB) {
                    const outputs = this.outputs;
                    if (!outputs || idxA < 0 || idxB < 0 || idxA >= outputs.length || idxB >= outputs.length) return;
                    // Newer slot accessors derive connectivity from their current
                    // array index. Capture both sets before moving either slot or
                    // updating endpoints, so links keep following their variable.
                    const connectedLinks = (output) => [
                        ...(output?.links || []).map(id => getLink(this.graph, id)),
                        ...(output?._floatingLinks || []),
                    ];
                    const linksA = connectedLinks(outputs[idxA]);
                    const linksB = connectedLinks(outputs[idxB]);
                    [outputs[idxA], outputs[idxB]] = [outputs[idxB], outputs[idxA]];

                    const updateLinkOrigins = (links, slot) => {
                        for (const link of links) {
                            if (link?.origin_id === this.id) link.origin_slot = slot;
                        }
                    };
                    updateLinkOrigins(linksB, idxA);
                    updateLinkOrigins(linksA, idxB);
                };
                // Resolve from setters, not the type filter: a filter alone does
                // not make an empty, missing or wildcard variable concrete.
                this.positionalEditReason = function (first, last, remove, resolvedTypes) {
                    let commonType;
                    for (let i = first; i <= last; i++) {
                        const name = this.widgets[VAR_WIDGET_START + i]?.value;
                        const setter = name && findSetter(this.graph, name);
                        const type = setter ? setter.inputs?.[0]?.type : resolvedTypes?.get(name);
                        if (typeof type !== 'string' || !type.trim() || type === '*' ||
                            type.includes(',') || type.includes('COMFY_MATCHTYPE_V3')) {
                            return 'An affected variable has no resolved concrete type.';
                        }
                        if (commonType !== undefined && type !== commonType) {
                            return 'The affected variables have different types.';
                        }
                        commonType = type;
                    }
                    const filter = this.widgets[0].value;
                    if (filter !== '*' && filter !== commonType) {
                        return 'The type filter does not match the affected variables.';
                    }
                    for (let i = first; i <= last; i++) {
                        const output = this.outputs?.[i];
                        if (!output || output._floatingLinks?.size || output._floatingLinks?.length) {
                            return 'An affected connection is incomplete.';
                        }
                        for (const link of this.graph?.floatingLinks?.values?.() || []) {
                            if (link.origin_id === this.id && link.origin_slot === i) {
                                return 'An affected connection is incomplete.';
                            }
                        }
                        for (const id of output.links || []) {
                            const link = getLink(this.graph, id);
                            const input = this.graph?.getNodeById?.(link?.target_id)?.inputs?.[link?.target_slot];
                            if (!link || link.origin_id !== this.id || link.origin_slot !== i ||
                                !input || input.link !== id || input.type == null) {
                                return 'An affected connection is incomplete.';
                            }
                            // The final output is discarded by positional removal.
                            if (!(remove && i === last) && !LiteGraph.isValidConnection(commonType, input.type)) {
                                return 'An affected target input does not accept the variable type.';
                            }
                        }
                    }
                    return null;
                };
                this.editVarOrder = function (order, first, last, remove = false, options = {}) {
                    if (linkedChain(this) && !options.automatic) { openChainEditor(this); return; }
                    const positional = options.keepConnectionsInPosition ?? (this.properties.keepConnectionsInPosition === true);
                    const reason = positional ? this.positionalEditReason(first, last, remove, options.resolvedTypes) : null;
                    const keepPositions = positional && !reason;
                    const varWidgets = this.widgets.slice(VAR_WIDGET_START);
                    const values = varWidgets.map(w => w.value);
                    // Snapshot the entire permutation before touching slot indices.
                    const outputs = this.outputs.slice();
                    const floatingLinks = [...(this.graph?.floatingLinks?.values?.() || [])];
                    const links = keepPositions ? [] : outputs.map((output, i) => [
                        ...(output.links || []).map(id => getLink(this.graph, id)),
                        ...(output._floatingLinks || []),
                        ...floatingLinks.filter(link => link.origin_id === this.id && link.origin_slot === i),
                    ]);
                    const edit = () => {
                        if (remove && !keepPositions) {
                            // Discard this output directly. No successful positional
                            // move is required, even for broken or floating branches.
                            const discarded = order[order.length - 1];
                            if (varWidgets.length === 1) {
                                this.disconnectOutput(0);
                                varWidgets[0].value = '';
                            } else {
                                this.removeOutput(discarded);
                                const survivingValues = values.filter((_, i) => i !== discarded);
                                survivingValues.forEach((value, i) => { varWidgets[i].value = value; });
                                let slot = 0;
                                for (let i = 0; i < outputs.length; i++) {
                                    if (i === discarded) continue;
                                    for (const link of links[i]) if (link?.origin_id === this.id) link.origin_slot = slot;
                                    slot++;
                                }
                            }
                            this.properties.varCount = Math.max(1, varWidgets.length - 1);
                            this.widgets[1].value = String(this.properties.varCount);
                            this.syncVarWidgets();
                            this.setDirtyCanvas(true, true);
                            return;
                        }
                        for (let i = first; i <= last; i++) {
                            varWidgets[i].value = values[order[i]];
                            if (!keepPositions) this.outputs[i] = outputs[order[i]];
                        }
                        if (!keepPositions) {
                            for (let i = first; i <= last; i++) {
                                for (const link of links[order[i]]) {
                                    if (link?.origin_id === this.id) link.origin_slot = i;
                                }
                            }
                        }
                        if (remove) {
                            if (varWidgets.length === 1) {
                                varWidgets[0].value = '';
                                this.disconnectOutput(0);
                            }
                            this.properties.varCount = Math.max(1, varWidgets.length - 1);
                            this.widgets[1].value = String(this.properties.varCount);
                            this.syncVarWidgets();
                        } else {
                            this.updateOutputTypes();
                        }
                        this.setDirtyCanvas(true, true);
                    };
                    if (options.automatic) edit();
                    else editGraph(this, edit, remove);
                    if (reason) showAlert(`${remove ? 'Variable removed; surviving connections kept their destinations.' : 'Connections followed their variables.'} ${reason}`);
                };
                this.swapVars = function (idxA, idxB) {
                    const order = this.widgets.slice(VAR_WIDGET_START).map((_, i) => i);
                    if (!Number.isInteger(idxA) || !Number.isInteger(idxB) || idxA === idxB ||
                        idxA < 0 || idxB < 0 || idxA >= order.length || idxB >= order.length) return;
                    [order[idxA], order[idxB]] = [order[idxB], order[idxA]];
                    this.editVarOrder(order, Math.min(idxA, idxB), Math.max(idxA, idxB));
                };
                this.moveVarTo = function (idx, destination) {
                    const order = this.widgets.slice(VAR_WIDGET_START).map((_, i) => i);
                    if (!Number.isInteger(idx) || !Number.isInteger(destination) || idx < 0 || idx >= order.length) return;
                    destination = Math.max(0, Math.min(order.length - 1, destination));
                    if (idx === destination) return;
                    order.splice(destination, 0, order.splice(idx, 1)[0]);
                    this.editVarOrder(order, Math.min(idx, destination), Math.max(idx, destination));
                };
                this.moveVarUp = function (idx) {
                    this.moveVarTo(idx, idx - 1);
                };
                this.moveVarDown = function (idx) {
                    this.moveVarTo(idx, idx + 1);
                };
                this.moveVarUpBy = function (idx, count) {
                    if (count > 0) this.moveVarTo(idx, idx - count);
                };
                this.moveVarDownBy = function (idx, count) {
                    if (count > 0) this.moveVarTo(idx, idx + count);
                };
                this.moveVarToTop = function (idx) {
                    this.moveVarTo(idx, 0);
                };
                this.moveVarToBottom = function (idx) {
                    this.moveVarTo(idx, this.widgets.length - VAR_WIDGET_START - 1);
                };
                this.removeVar = function (idx, options = {}) {
                    if (linkedChain(this) && !options.automatic) { excludeChainVariable(this, idx); return; }
                    const order = this.widgets.slice(VAR_WIDGET_START).map((_, i) => i);
                    if (!Number.isInteger(idx) || idx < 0 || idx >= order.length ||
                        (order.length === 1 && !this.widgets[VAR_WIDGET_START].value)) return;
                    order.push(order.splice(idx, 1)[0]);
                    this.editVarOrder(order, idx, order.length - 1, true, options);
                };
                this.insertVarAt = function (idx) {
                    if (linkedChain(this)) { openChainEditor(this); return; }
                    const varWidgets = this.widgets.slice(VAR_WIDGET_START);
                    const maxCount = 20;
                    if (varWidgets.length >= maxCount) {
                        showAlert("Maximum 20 vars reached.");
                        return;
                    }
                    const newCount = varWidgets.length + 1;
                    this.properties.varCount = newCount;
                    this.widgets[1].value = String(newCount);
                    this.syncVarWidgets();
                    const updatedVarWidgets = this.widgets.slice(VAR_WIDGET_START);
                    for (let i = updatedVarWidgets.length - 1; i > idx; i--) {
                        updatedVarWidgets[i].value = updatedVarWidgets[i - 1].value;
                        this.swapOutputSlots(i, i - 1);
                    }
                    updatedVarWidgets[idx].value = "";
                    this.updateOutputTypes();
                    this.setDirtyCanvas(true, true);
                };
                this.addWidget("combo", "type_filter", "*", (value) => {
                    node.refreshVarWidgets();
                    node.updateOutputTypes();
                }, {
                    values: TYPE_FILTERS
                });
                const VAR_COUNT_OPTIONS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20"];
                this.addWidget("combo", "var_count", "2", (value) => {
                    if (linkedChain(node)) { node.widgets[1].value = String(node.properties.varCount); openChainEditor(node); return; }
                    const count = parseInt(value) || 2;
                    node.properties.varCount = count;
                    node.syncVarWidgets();
                    node.setDirtyCanvas(true, true);
                }, {
                    values: VAR_COUNT_OPTIONS
                });
                this.syncVarWidgets();
                this.isVirtualNode = true;
            }
            resolveVirtualOutput(slot) {
                if (!this.graph) return undefined;
                const VAR_WIDGET_START = 2;
                const varWidget = this.widgets?.[VAR_WIDGET_START + slot];
                if (!varWidget) return undefined;
                const varName = varWidget.value;
                if (!varName || varName === '') return undefined;
                const result = findSetterByName(this.graph, varName);
                if (!result) return undefined;
                // Same-graph → getInputLink handles this case.
                if (result.graph === this.graph) return undefined;
                const { node: setter, graph: setterGraph } = result;
                // Cross-graph resolution works for any relationship (ancestor, descendant,
                // sibling) as long as the setter's path to root is fully active — that's
                // the only condition the frontend uses to decide whether the source node's
                // DTO exists in nodesByExecutionId.
                if (!isSetterPathToRootActive(setterGraph)) return undefined;
                if (!isSetterActive(setterGraph, setter)) return undefined;
                const link = resolveBypassedLink(setterGraph, setter);
                if (!link) return undefined;
                const sourceNode = setterGraph.getNodeById(link.origin_id);
                if (!sourceNode) return undefined;
                return { node: sourceNode, slot: link.origin_slot };
            }
            getInputLink(slot) {
                if (!this.graph) return null;
                const VAR_WIDGET_START = 2;
                const varWidget = this.widgets?.[VAR_WIDGET_START + slot];
                if (!varWidget) return null;
                const varName = varWidget.value;
                if (!varName || varName === '') return null;
                // Only search same graph — cross-graph SetNodes are handled by resolveVirtualOutput.
                // findSetter searches all graphs (including descendants) which produces links that
                // LLink.resolve(this.graph) cannot resolve, causing InvalidLinkError on 1.42.11+.
                const setter = this.graph._nodes?.find(
                    n => SETTER_TYPES.has(n.type) && n.widgets?.[0]?.value === varName
                );
                if (!setter) return null;
                if (!isSetterActive(this.graph, setter)) return null;
                return resolveBypassedLink(this.graph, setter);
            }
            onAdded(graph) {
                this._justAdded = !subgraphOpState.active && !globalThis.comfyAPI?.changeTracker?.ChangeTracker?.isLoadingGraph &&
                    !app.extensionManager?.workflow?.activeWorkflow?.changeTracker?._restoringState;
                pasteRenameScheduler.schedule?.();
                this.updateOutputTypes();
            }
            onResize() {
                if (this.outputs?.length > 0) this.updateOutputTypes();
            }
            onAfterGraphConfigured() {
                // Root metadata is now available, including for members inside
                // subgraphs. Loading restores saved names without paste renames.
                if (!linkedChain(this)) delete this.properties.eclipseSyncChain;
                this.refreshChainControls();
            }
            _handlePasteRename() {
                // Called by the central paste-rename scan in eclipse-set-get.js schedulePasteRenamePass().
                // Phase 2: update all variable widgets using entries from _pasteRenameMap.
                const VAR_WIDGET_START = 2;
                // A same-workflow copy uses the existing definition. A member
                // pasted without its root metadata becomes an ordinary getter.
                if (linkedChain(this)) { this.refreshChainControls(); return; }
                delete this.properties.eclipseSyncChain;
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
                this.updateOutputTypes();
                this.refreshChainControls();
            }
            onConfigure(data) {
                this.properties.keepConnectionsInPosition = data.properties?.keepConnectionsInPosition === true;
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
                this.updateOutputTypes();
                this.refreshChainControls();
            }
            getExtraMenuOptions(canvas, options) {
                const node = this;
                options.unshift({
                    content: `${this.properties.keepConnectionsInPosition === true ? '✓ ' : ''}Keep connections in position when types match`,
                    checked: this.properties.keepConnectionsInPosition === true,
                    callback: () => {
                        editGraph(node, () => {
                            node.properties.keepConnectionsInPosition = !node.properties.keepConnectionsInPosition;
                            node.setDirtyCanvas(true, true);
                        });
                    },
                });
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
                            title: "Var List",
                            options: setterItems,
                        },
                    });
                }
                options.unshift(syncChainMenu(this, canvas));
                if (linkedChain(this)) return;
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
                        content: 'Remove Var',
                        disabled: varWidgets.length === 1 && !varWidgets[i].value,
                        callback: () => node.removeVar(i),
                    });
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
                    this._drawVirtualLinks(lGraphCanvas, ctx);
                }
            }
            drawWidgets(ctx, options) {
                // Shrink var widgets to make room for nav arrow
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
                // If nav arrows are enabled and click is in the arrow zone of a var widget,
                // return null so LiteGraph falls through to onMouseDown instead of opening combo
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
                // Check if click is in the nav arrow zone (28px lane, 6px right margin)
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
                        return true; // consume click
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
                    // Active dot
                    if (entry?.active) {
                        ctx.fillStyle = "#2E7D32";
                        ctx.beginPath();
                        ctx.arc(10, centerY, 4, 0, Math.PI * 2);
                        ctx.fill();
                    }
                    // Nav arrow — drawn in dedicated lane past narrowed widget
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
            _drawVirtualLinks(lGraphCanvas, ctx) {
                const cached = this.getCachedSetters();
                if (!cached) return;
                for (let i = 0; i < cached.length; i++) {
                    const entry = cached[i];
                    if (!entry || !entry.active) continue;
                    const setter = entry.setter;
                    const defaultLink = {
                        type: 'default',
                        color: this.slotColor
                    };
                    let start_node_slotpos = setter.getConnectionPos(false, 0);
                    start_node_slotpos = [start_node_slotpos[0] - this.pos[0], start_node_slotpos[1] - this.pos[1], ];
                    const outPos = this.getConnectionPos(false, i);
                    let end_node_slotpos = [outPos[0] - this.pos[0], outPos[1] - this.pos[1], ];
                    lGraphCanvas.renderLink(ctx, start_node_slotpos, end_node_slotpos, defaultLink, false, null, this.slotColor);
                }
            }
        }
        LiteGraph.registerNodeType("GetAllActiveNode", Object.assign(GetAllActiveNode, {
            title: "Get All Active",
        }));
        GetAllActiveNode.category = "🌒 Eclipse/ Set-Get";
    },
});
