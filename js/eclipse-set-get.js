import {
    app
} from './comfy/index.js';
import {
    batchedRefreshVueWidgetOptions,
    isVueMode,
} from './eclipse-widget-performance-utils.js';
import { addCommittedTextWidget } from './eclipse-committed-text-widget.js';
import {
    SETTER_TYPES,
    getLink,
    findRootGraph,
    getGraphDescendants,
    findSubgraphNodeFor,
    findSetterByName,
    findGettersByName,
    getVisibleSetNames,
    getSetNameSourceMap,
    resolveBypassedLink,
    isSetterPathToRootActive,
    subgraphOpState,
    _pasteRenameMap,
    clearPasteRenameMap,
    pasteRenameScheduler,
} from './eclipse-set-get-utils.js';
const SET_TYPE = 'SetNode [Eclipse]';
const GET_TYPE = 'GetNode [Eclipse]';
const GET_FIRST_TYPE = 'GetFirstNode';
const GET_ALL_ACTIVE_TYPE = 'GetAllActiveNode';
const CATEGORY = '🌒 Eclipse/ Set-Get';
const ALL_GETTER_TYPES = [GET_TYPE, 'GetNode'];
const MULTI_GETTER_TYPES = new Set([GET_FIRST_TYPE, GET_ALL_ACTIVE_TYPE]);
const LGraphNode = LiteGraph.LGraphNode;

// Filterable combo dropdown — used by GetNode's Constant widget. LiteGraph's
// built-in ContextMenu has no filter input; Vue's searchable combo is only
// available for Python-registered nodes. This helper builds a small HTML
// overlay with an input on top and a scrollable item list.
let _filterableComboCSSInjected = false;

function _injectFilterableComboCSS() {
    if (_filterableComboCSSInjected) return;
    _filterableComboCSSInjected = true;
    const style = document.createElement('style');
    style.textContent = `
        .eclipse-fcombo-root {
            position: fixed; z-index: 10000;
            background: #1a1a1a; color: #ddd;
            border: 1px solid #444; border-radius: 4px;
            box-shadow: 0 4px 18px rgba(0,0,0,0.6);
            font-family: Arial, sans-serif; font-size: 12px;
            min-width: 180px; max-width: 360px;
            display: flex; flex-direction: column;
            overflow: hidden;
        }
        .eclipse-fcombo-search {
            padding: 6px 8px;
            background: #222; border-bottom: 1px solid #333;
        }
        .eclipse-fcombo-search input {
            width: 100%; box-sizing: border-box;
            background: #111; color: #eee;
            border: 1px solid #444; border-radius: 3px;
            padding: 4px 6px; font-size: 12px;
            outline: none;
        }
        .eclipse-fcombo-search input:focus { border-color: #6a8; }
        .eclipse-fcombo-list {
            max-height: 320px; overflow-y: auto;
            padding: 2px 0;
        }
        .eclipse-fcombo-item {
            padding: 4px 10px; cursor: pointer;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .eclipse-fcombo-item:hover, .eclipse-fcombo-item.active {
            background: #2d4d2d; color: #fff;
        }
        .eclipse-fcombo-item.current { font-weight: bold; color: #9c9; }
        .eclipse-fcombo-empty { padding: 8px 10px; color: #888; font-style: italic; }
    `;
    document.head.appendChild(style);
}

function openFilterableCombo({ event, values, getLabel, current, scale, onSelect }) {
    _injectFilterableComboCSS();
    // Close any existing dropdown first
    document.querySelectorAll('.eclipse-fcombo-root').forEach(el => el.remove());
    const root = document.createElement('div');
    root.className = 'eclipse-fcombo-root';
    const searchWrap = document.createElement('div');
    searchWrap.className = 'eclipse-fcombo-search';
    const input = document.createElement('input');
    input.type = 'text';
    input.placeholder = 'Filter...';
    input.spellcheck = false;
    input.autocomplete = 'off';
    searchWrap.appendChild(input);
    root.appendChild(searchWrap);
    const list = document.createElement('div');
    list.className = 'eclipse-fcombo-list';
    root.appendChild(list);
    const labelOf = (v) => {
        try { return getLabel ? (getLabel(String(v)) ?? String(v)) : String(v); }
        catch { return String(v); }
    };
    let activeIdx = 0;
    let filtered = values.slice();

    const renderList = () => {
        list.innerHTML = '';
        if (!filtered.length) {
            const empty = document.createElement('div');
            empty.className = 'eclipse-fcombo-empty';
            empty.textContent = '(no matches)';
            list.appendChild(empty);
            return;
        }
        filtered.forEach((val, idx) => {
            const item = document.createElement('div');
            item.className = 'eclipse-fcombo-item';
            if (val === current) item.classList.add('current');
            if (idx === activeIdx) item.classList.add('active');
            item.textContent = labelOf(val);
            item.addEventListener('mouseenter', () => {
                list.querySelectorAll('.active').forEach(e => e.classList.remove('active'));
                item.classList.add('active');
                activeIdx = idx;
            });
            item.addEventListener('mousedown', (e) => {
                e.preventDefault(); e.stopPropagation();
                onSelect(val);
                close();
            });
            list.appendChild(item);
        });
    };

    const applyFilter = () => {
        const q = input.value.trim().toLowerCase();
        if (!q) filtered = values.slice();
        else filtered = values.filter(v => labelOf(v).toLowerCase().includes(q));
        activeIdx = 0;
        renderList();
    };

    const close = () => {
        controller.abort();
        root.remove();
    };
    const controller = new AbortController();
    const sig = { signal: controller.signal };

    input.addEventListener('input', applyFilter);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { close(); e.preventDefault(); return; }
        if (e.key === 'Enter') {
            if (filtered.length && activeIdx >= 0 && activeIdx < filtered.length) {
                onSelect(filtered[activeIdx]);
            }
            close(); e.preventDefault(); return;
        }
        if (e.key === 'ArrowDown') {
            if (filtered.length) { activeIdx = Math.min(activeIdx + 1, filtered.length - 1); renderList(); }
            e.preventDefault(); return;
        }
        if (e.key === 'ArrowUp') {
            if (filtered.length) { activeIdx = Math.max(activeIdx - 1, 0); renderList(); }
            e.preventDefault(); return;
        }
    }, sig);

    // Outside click → close
    document.addEventListener('pointerdown', (e) => {
        if (!root.contains(e.target)) close();
    }, { ...sig, capture: true });

    renderList();
    document.body.appendChild(root);
    // Position near click event, clamped to viewport
    const ev = event;
    let left = (ev?.clientX || 0);
    let top = (ev?.clientY || 0) + 4;
    const rect = root.getBoundingClientRect();
    if (left + rect.width > window.innerWidth - 10) left = window.innerWidth - rect.width - 10;
    if (top + rect.height > window.innerHeight - 10) top = window.innerHeight - rect.height - 10;
    if (left < 4) left = 4;
    if (top < 4) top = 4;
    root.style.left = `${left}px`;
    root.style.top = `${top}px`;
    if (scale && scale !== 1) {
        root.style.transformOrigin = 'top left';
        root.style.transform = `scale(${Math.round(scale * 4) * 0.25})`;
    }
    // Focus input so the user can type immediately
    setTimeout(() => input.focus(), 0);
}

function _notifyMultiGetters(graph, prevName, curName) {
    const graphs = [graph, ...getGraphDescendants(graph)];
    for (const g of graphs) {
        if (!g?._nodes) continue;
        for (const n of g._nodes) {
            if (!MULTI_GETTER_TYPES.has(n.type)) continue;
            if (prevName && curName && prevName !== curName && n.renameVar) {
                n.renameVar(prevName, curName);
            }
            if (n.refreshVarWidgets) n.refreshVarWidgets();
        }
    }
}

function _refreshMultiGetters(graph) {
    if (!graph?._nodes) return;
    for (const n of graph._nodes) {
        if (MULTI_GETTER_TYPES.has(n.type) && n.refreshVarWidgets) {
            n.refreshVarWidgets();
        }
    }
}

function showAlert(message) {
    app.extensionManager.toast.add({
        severity: 'warn',
        summary: 'Eclipse Set/Get',
        detail: `${message}. Most likely you're missing custom nodes`,
        life: 5000,
    });
}

function collectScopedSetNodes(graph) {
    const root = findRootGraph(graph);
    const allGraphs = [root, ...getGraphDescendants(root)];
    const results = [];
    for (const g of allGraphs) {
        if (!g?._nodes) continue;
        for (const node of g._nodes) {
            if (SETTER_TYPES.has(node.type)) results.push(node);
        }
    }
    return results;
}

function _automaticSetTitle(name) {
    return name ? 'Set_' + name : 'Set';
}

function _syncAutomaticSetTitle(node, name) {
    node._eclipseAutomaticSetTitle = _automaticSetTitle(name);
}

function _updateAutomaticSetTitle(node, name) {
    const previousTitle = node._eclipseAutomaticSetTitle || 'Set';
    const nextTitle = _automaticSetTitle(name);
    if (node.title === previousTitle) node.title = nextTitle;
    node._eclipseAutomaticSetTitle = nextTitle;
}

function collectOutputConnections(graph, output) {
    const conns = [];
    if (!output?.links) return conns;
    for (const linkId of output.links) {
        const link = getLink(graph, linkId);
        if (link) conns.push({
            targetId: link.target_id,
            targetSlot: link.target_slot
        });
    }
    return conns;
}

function convertSetGetToLinks(graph, setNode) {
    if (!graph || !setNode) return;
    const name = setNode.widgets[0].value;
    let sameGraphGetters = [];
    if (name) {
        for (const gt of ALL_GETTER_TYPES) {
            for (const e of findGettersByName(graph, name, gt)) {
                if (e.graph === graph) sameGraphGetters.push(e.node);
            }
        }
    }
    const setInput = setNode.inputs[0];
    if (setInput.link == null) return;
    const sourceLink = getLink(graph, setInput.link);
    if (!sourceLink) return;
    const sourceNode = graph.getNodeById(sourceLink.origin_id);
    if (!sourceNode) return;
    const sourceSlot = sourceLink.origin_slot;
    const connections = [];
    for (const getter of sameGraphGetters) {
        connections.push(...collectOutputConnections(graph, getter.outputs[0]));
    }
    connections.push(...collectOutputConnections(graph, setNode.outputs[0]));
    for (const getter of sameGraphGetters) graph.remove(getter);
    graph.remove(setNode);
    for (const conn of connections) {
        const targetNode = graph.getNodeById(conn.targetId);
        if (targetNode) sourceNode.connect(sourceSlot, targetNode, conn.targetSlot);
    }
    app.canvas?.setDirty(true, true);
}

function convertNodeToLinks(graph, node) {
    if (!graph || !node) return;
    const setNodesToConvert = new Set();
    if (node.outputs) {
        for (const output of node.outputs) {
            if (!output?.links?.length) continue;
            for (const linkId of [...output.links]) {
                const link = getLink(graph, linkId);
                if (!link) continue;
                const target = graph.getNodeById(link.target_id);
                if (target && SETTER_TYPES.has(target.type)) setNodesToConvert.add(target);
            }
        }
    }
    if (node.inputs) {
        for (const input of node.inputs) {
            if (!input?.link) continue;
            const link = getLink(graph, input.link);
            if (!link) continue;
            const src = graph.getNodeById(link.origin_id);
            if (!src || !ALL_GETTER_TYPES.includes(src.type)) continue;
            const setterResult = findSetterByName(graph, src.widgets?.[0]?.value);
            if (setterResult?.node) setNodesToConvert.add(setterResult.node);
        }
    }
    for (const setNode of setNodesToConvert) convertSetGetToLinks(graph, setNode);
}
app.registerExtension({
    name: 'Eclipse.SetNode',
    registerCustomNodes() {
        class SetNode extends LGraphNode {
            defaultVisibility = true;
            serialize_widgets = true;
            drawConnection = false;
            currentGetters = null;
            slotColor = '#FFF';
            canvas = app.canvas;
            constructor(title) {
                super(title);
                this.color = '#000000';
                this.bgcolor = '#000000';
                if (!this.properties) {
                    this.properties = {
                        previousName: ''
                    };
                }
                this.properties['Node name for S&R'] = 'SetNode [Eclipse]';
                this.properties.showOutputText = SetNode.defaultVisibility;
                this.isVirtualNode = true;
                this._eclipseAutomaticSetTitle = 'Set';
                addCommittedTextWidget(this, 'Constant', '', (value) => {
                    if (!this.graph || app.configuringGraph) return value;
                    this.widgets[0].value = value;
                    this.validateName(this.graph);
                    this.update();
                    this.properties.previousName = this.widgets[0].value;
                    return this.widgets[0].value;
                }, {
                    onSync: (value, { reason }) => {
                        if (reason === 'configure') _syncAutomaticSetTitle(this, value);
                    },
                });
                this.addInput('*', '*');
                this.addOutput('*', '*');
            }
            onConfigure() {
                _syncAutomaticSetTitle(this, this.widgets?.[0]?.value || '');
            }
            onConnectionsChange(slotType, slot, isChangeConnect, link_info) {
                if (app.configuringGraph) return;
                if (slotType === LiteGraph.INPUT && !isChangeConnect) {
                    const outputConnected = this.outputs[0]?.links?.length > 0;
                    if (outputConnected) {
                        this.inputs[slot].type = this.outputs[0].type;
                        this.inputs[slot].name = this.outputs[0].name;
                    } else {
                        this.inputs[slot].type = '*';
                        this.inputs[slot].name = '*';
                        this.outputs[0].type = '*';
                        this.outputs[0].name = '*';
                        _updateAutomaticSetTitle(this, this.widgets[0]?.value || '');
                    }
                    this.update();
                }
                if (slotType === LiteGraph.OUTPUT && !isChangeConnect) {
                    if (this.outputs && this.outputs[slot]) {
                        const inputConnected = this.inputs[0]?.link != null;
                        if (inputConnected) {
                            this.outputs[slot].type = this.inputs[0].type;
                            this.outputs[slot].name = this.inputs[0].name;
                        } else {
                            this.inputs[0].type = '*';
                            this.inputs[0].name = '*';
                            this.outputs[slot].type = '*';
                            this.outputs[slot].name = '*';
                        }
                    }
                }
                if (link_info && this.graph && slotType === LiteGraph.INPUT && isChangeConnect) {
                    const resolve = link_info.resolve(this.graph);
                    const resolvedSlot = resolve?.subgraphInput ?? resolve?.output;
                    const type = resolvedSlot?.type;
                    if (type) {
                        if (this.widgets[0].value === '' || this.widgets[0].value === '*') {
                            this.widgets[0].value = type;
                        }
                        this.validateName(this.graph);
                        this.properties.previousName = this.widgets[0].value;
                        this.widgets[0]._eclipseCommittedText?.syncCommittedValue(
                            this.widgets[0].value,
                            'connection'
                        );
                        this.inputs[0].type = type;
                        this.inputs[0].name = type;
                        this.outputs[0].type = type;
                        this.outputs[0].name = type;
                    } else {
                        showAlert(`node ${this.title} input undefined.`);
                    }
                }
                if (link_info && this.graph && slotType === LiteGraph.OUTPUT && isChangeConnect) {
                    const inputType = this.inputs[0]?.type;
                    if (inputType && inputType !== '*') {
                        this.outputs[0].type = inputType;
                        this.outputs[0].name = inputType;
                    } else {
                        const resolve = link_info.resolve(this.graph);
                        const type = resolve?.input?.type;
                        if (type && type !== '*') {
                            this.inputs[0].type = type;
                            this.inputs[0].name = type;
                            this.outputs[0].type = type;
                            this.outputs[0].name = type;
                        }
                    }
                }
                this.update();
            }
            validateName(graph) {
                let widgetValue = this.widgets[0].value;
                if (widgetValue !== '') {
                    let tries = 1;
                    const existingValues = new Set();
                    const scopedSetNodes = collectScopedSetNodes(graph);
                    scopedSetNodes.forEach(node => {
                        if (node !== this) existingValues.add(node.widgets[0].value);
                    });
                    const originalValue = widgetValue;
                    const baseName = this._justAdded ? widgetValue.replace(/_\d+$/, '') : widgetValue;
                    while (existingValues.has(widgetValue)) {
                        widgetValue = baseName + '_' + tries;
                        tries++;
                    }
                    this.widgets[0].value = widgetValue;
                    _updateAutomaticSetTitle(this, widgetValue);
                    return widgetValue !== originalValue;
                }
                _updateAutomaticSetTitle(this, '');
                return false;
            }
            clone() {
                const cloned = super.clone();
                cloned.inputs[0].name = '*';
                cloned.inputs[0].type = '*';
                cloned.properties.previousName = '';
                cloned.size = cloned.computeSize();
                return cloned;
            }
            onAdded() {
                this._justAdded = true;
                schedulePasteRenamePass();
            }
            _handlePasteValidation() {
                // Called by the central paste-rename scan in schedulePasteRenamePass().
                // Phase 1: detect name conflict, rename self, write oldName→newName to _pasteRenameMap.
                const oldName = this.widgets[0].value;
                this.validateName(this.graph);
                const newName = this.widgets[0].value;
                if (newName !== oldName) {
                    _pasteRenameMap.set(oldName, newName);
                }
                this.widgets[0]._eclipseCommittedText?.syncCommittedValue(newName, 'paste');
                if (this.inputs[0]?.link == null) {
                    this.inputs[0].type = '*';
                    this.inputs[0].name = '*';
                    this.outputs[0].type = '*';
                    this.outputs[0].name = '*';
                }
            }
            update() {
                if (!this.graph) return;
                const name = this.widgets[0].value;
                const prevName = this.properties.previousName;
                const inputType = this.inputs[0].type;
                for (const gt of ALL_GETTER_TYPES) {
                    for (const entry of findGettersByName(this.graph, name, gt)) {
                        entry.node.setType(inputType);
                    }
                }
                if (name && prevName) {
                    for (const gt of ALL_GETTER_TYPES) {
                        for (const entry of findGettersByName(this.graph, prevName, gt)) {
                            entry.node.setName(name);
                        }
                    }
                }
                _notifyMultiGetters(this.graph, prevName, name);
                app.canvas?.setDirty(true, true);
            }
            findGetters(graph, checkForPreviousName) {
                const name = checkForPreviousName ? this.properties.previousName : this.widgets[0].value;
                if (!name || name === '') return [];
                return findGettersByName(graph, name, GET_TYPE).map(entry => entry.node);
            }
            onRemoved() {
                const allGetters = this.graph._nodes.filter((otherNode) => otherNode.type === GET_TYPE);
                allGetters.forEach((otherNode) => {
                    if (otherNode.setComboValues) otherNode.setComboValues([this]);
                });
                _refreshMultiGetters(this.graph);
            }
            getExtraMenuOptions(_, options) {
                const name = this.widgets?.[0]?.value;
                const entries = name ? findGettersByName(this.graph, name, GET_TYPE) : [];
                const drawTargets = [];
                const seenSubgraphs = new Set();
                for (const entry of entries) {
                    if (entry.graph === this.graph) {
                        drawTargets.push(entry.node);
                    } else {
                        const sgNode = findSubgraphNodeFor(this.graph, entry.node);
                        if (sgNode && !seenSubgraphs.has(sgNode)) {
                            seenSubgraphs.add(sgNode);
                            drawTargets.push(sgNode);
                        }
                    }
                }
                this.currentGetters = drawTargets;
            }
            onDrawForeground(ctx, lGraphCanvas) {
                if (this.drawConnection) this._drawVirtualLinks(lGraphCanvas, ctx);
            }
            _drawVirtualLinks(lGraphCanvas, ctx) {
                if (!this.currentGetters?.length) return;
                const title = this.getTitle ? this.getTitle() : this.title;
                const title_width = ctx.measureText(title).width;
                let start_node_slotpos;
                if (!this.flags.collapsed) {
                    start_node_slotpos = [this.size[0], LiteGraph.NODE_TITLE_HEIGHT * 0.5];
                } else {
                    start_node_slotpos = [title_width + 55, -15];
                }
                const defaultLink = {
                    type: 'default',
                    color: this.slotColor
                };
                for (const getter of this.currentGetters) {
                    let end_node_slotpos = this.getConnectionPos(false, 0);
                    if (!this.flags.collapsed) {
                        end_node_slotpos = [getter.pos[0] - end_node_slotpos[0] + this.size[0], getter.pos[1] - end_node_slotpos[1], ];
                    } else {
                        end_node_slotpos = [getter.pos[0] - end_node_slotpos[0] + title_width + 50, getter.pos[1] - end_node_slotpos[1] - 30, ];
                    }
                    lGraphCanvas.renderLink(ctx, start_node_slotpos, end_node_slotpos, defaultLink, false, null, this.slotColor, LiteGraph.RIGHT, LiteGraph.LEFT);
                }
            }
        }
        LiteGraph.registerNodeType(SET_TYPE, Object.assign(SetNode, {
            title: 'Set'
        }));
        SetNode.category = CATEGORY;
    },
    setup() {
        const KJSetNodeType = LiteGraph.registered_node_types?.['SetNode'];
        if (!KJSetNodeType?.prototype?.update) return;
        const origUpdate = KJSetNodeType.prototype.update;
        KJSetNodeType.prototype.update = function () {
            const prevName = this.properties?.previousName || '';
            const curName = this.widgets?.[0]?.value || '';
            origUpdate.call(this);
            if (!this.graph) return;
            if (curName) {
                for (const entry of findGettersByName(this.graph, curName, GET_TYPE)) {
                    entry.node.setType(this.inputs?.[0]?.type);
                }
            }
            if (prevName && curName && prevName !== curName) {
                for (const entry of findGettersByName(this.graph, prevName, GET_TYPE)) {
                    entry.node.setName(curName);
                }
            }
            _notifyMultiGetters(this.graph, prevName, curName);
        };
        const origOnRemoved = KJSetNodeType.prototype.onRemoved;
        KJSetNodeType.prototype.onRemoved = function (...args) {
            origOnRemoved?.apply(this, args);
            if (!this.graph) return;
            _refreshMultiGetters(this.graph);
        };
    },
});
app.registerExtension({
    name: 'Eclipse.GetNode',
    registerCustomNodes() {
        class GetNode extends LGraphNode {
            defaultVisibility = true;
            serialize_widgets = true;
            drawConnection = false;
            slotColor = '#FFF';
            currentSetter = null;
            canvas = app.canvas;
            constructor(title) {
                super(title);
                this.color = '#000000';
                this.bgcolor = '#000000';
                if (!this.properties) {
                    this.properties = {};
                }
                this.properties['Node name for S&R'] = 'GetNode [Eclipse]';
                this.properties.showOutputText = GetNode.defaultVisibility;
                this.isVirtualNode = true;
                const comboOptions = {
                    getOptionLabel: (value) => {
                        if (!value) return '';
                        const source = getSetNameSourceMap().get(value);
                        if (!source || source === 'local') return value;
                        return `${value} (${source})`;
                    },
                };
                Object.defineProperty(comboOptions, 'values', {
                    get: () => {
                        if (!this.graph) return [];
                        let filterType = null;
                        if (this.outputs[0]?.links?.length) {
                            const linkId = this.outputs[0].links[0];
                            const link = getLink(this.graph, linkId);
                            if (link) {
                                const targetNode = this.graph.getNodeById(link.target_id);
                                filterType = targetNode?.inputs?.[link.target_slot]?.type || null;
                            }
                        }
                        return getVisibleSetNames(this.graph, filterType);
                    },
                    enumerable: true,
                    configurable: true,
                });
                const constantW = this.addWidget('combo', 'Constant', '', () => {
                    if (!app.configuringGraph) this.onRename();
                }, comboOptions);
                // Override default combo onClick with a filterable dropdown
                // (LiteGraph.ContextMenu has no filter; Vue's searchable combo
                // is only available to Python-registered nodes).
                constantW.onClick = function ({ e, node, canvas }) {
                    const rawValues = comboOptions.values;
                    const values = Array.isArray(rawValues) ? rawValues : (typeof rawValues === 'function' ? rawValues(this, node) : []);
                    openFilterableCombo({
                        event: e,
                        values: values.map(v => String(v)),
                        getLabel: comboOptions.getOptionLabel,
                        current: this.value,
                        scale: Math.max(1, canvas.ds.scale),
                        onSelect: (val) => {
                            this.value = val;
                            if (this.callback) this.callback(val, canvas, node, [e.canvasX, e.canvasY], e);
                            canvas.setDirty(true, true);
                        },
                    });
                };
                this.addOutput('*', '*');
            }
            onConnectionsChange() {
                if (app.configuringGraph) return;
                this.validateLinks();
            }
            setName(name) {
                this.widgets[0].value = name;
                this.onRename();
                this.serialize();
            }
            onRename() {
                const setter = this.findSetter(this.graph);
                if (setter) {
                    let linkType = setter.inputs[0].type;
                    this.setType(linkType);
                    this.title = 'Get_' + setter.widgets[0].value;
                } else {
                    this.setType('*');
                    const name = this.widgets[0].value;
                    this.title = name ? 'Get_' + name : 'Get';
                }
                app.canvas?.setDirty(true, true);
            }
            clone() {
                const cloned = super.clone();
                cloned.size = cloned.computeSize();
                return cloned;
            }
            onAdded() {
                this._justAdded = true;
                schedulePasteRenamePass();
            }
            onDblClick() {
                const setter = this.findSetter(this.graph);
                if (!setter) return;
                const setterGraph = setter.graph;
                if (setterGraph && setterGraph !== this.graph) {
                    this.canvas.setGraph?.(setterGraph);
                    setTimeout(() => {
                        this.canvas.centerOnNode(setter);
                        this.canvas.selectNode(setter, false);
                        this.canvas.setDirty(true, true);
                    }, 0);
                } else {
                    this.canvas.centerOnNode(setter);
                    this.canvas.selectNode(setter, false);
                    this.canvas.setDirty(true, true);
                }
            }
            _handlePasteRename() {
                // Called by the central paste-rename scan in schedulePasteRenamePass().
                // Phase 2: look up widget value in _pasteRenameMap, update if a rename exists.
                const name = this.widgets[0].value;
                if (name) {
                    const newName = _pasteRenameMap.get(name);
                    if (newName) {
                        this.widgets[0].value = newName;
                    }
                    setTimeout(() => this.onRename(), 0);
                }
            }
            validateLinks() {
                const output = this.outputs?.[0];
                if (!output?.links || !this.graph) return;
                const outputType = output.type;
                const linkColor = LGraphCanvas.link_type_colors?.[outputType];
                for (const linkId of [...output.links]) {
                    const link = getLink(this.graph, linkId);
                    if (!link) continue;
                    let targetSlot = null;
                    try {
                        const resolved = link.resolve?.(this.graph);
                        targetSlot = resolved?.subgraphOutput ?? resolved?.input ?? null;
                    } catch (_) {}
                    if (!targetSlot) {
                        const targetNode = this.graph.getNodeById(link.target_id);
                        targetSlot = targetNode?.inputs?.[link.target_slot] ?? null;
                    }
                    const targetType = targetSlot?.type;
                    if (outputType && targetType
                        && !LiteGraph.isValidConnection(outputType, targetType)) {
                        this.graph.removeLink(linkId);
                        continue;
                    }
                    link.type = outputType;
                    if (linkColor) {
                        link.color = linkColor;
                    } else {
                        delete link.color;
                    }
                }
            }
            setType(type) {
                this.outputs[0].name = type;
                this.outputs[0].type = type;
                this.validateLinks();
            }
            findSetter(graph) {
                const name = this.widgets[0].value;
                const result = findSetterByName(graph, name);
                return result ? result.node : undefined;
            }
            goToSetter() {
                if (!this.currentSetter) return;
                const setterGraph = this.currentSetter.graph;
                if (setterGraph && setterGraph !== this.graph) {
                    this.canvas.setGraph?.(setterGraph);
                    setTimeout(() => {
                        this.canvas.centerOnNode(this.currentSetter);
                        this.canvas.selectNode(this.currentSetter, false);
                        this.canvas.setDirty(true, true);
                    }, 0);
                } else {
                    this.canvas.centerOnNode(this.currentSetter);
                    this.canvas.selectNode(this.currentSetter, false);
                }
            }
            getInputLink(slot) {
                const name = this.widgets[0].value;
                if (!name || name === '') return null;
                const setter = this.graph?._nodes?.find(n => SETTER_TYPES.has(n.type) && n.widgets?.[0]?.value === name);
                if (setter) {
                    const slotInfo = setter.inputs[slot];
                    if (!slotInfo || slotInfo.link == null) return null;
                    return getLink(this.graph, slotInfo.link);
                }
                if (name && !findSetterByName(this.graph, name)) {
                    showAlert('No SetNode found for ' + name + ' (' + this.type + ')');
                }
                return null;
            }
            resolveVirtualOutput(slot) {
                const name = this.widgets[0].value;
                const result = findSetterByName(this.graph, name);
                if (!result) return undefined;
                if (result.graph === this.graph) {
                    const dupes = result.graph._nodes.filter(n => SETTER_TYPES.has(n.type) && n.widgets?.[0]?.value === name);
                    if (dupes.length > 1) {
                        console.warn(`[Eclipse] Multiple SetNodes named "${name}" in same graph — using first match (id ${dupes[0].id})`);
                    }
                }
                // Same-graph → getInputLink handles this case.
                if (result.graph === this.graph) return undefined;
                const {
                    node: setter,
                    graph: setterGraph
                } = result;
                // Cross-graph resolution works for any relationship (ancestor,
                // descendant, sibling) as long as the setter's path to root is
                // fully active — see isSetterPathToRootActive.
                if (!isSetterPathToRootActive(setterGraph)) return undefined;
                const link = resolveBypassedLink(setterGraph, setter);
                if (!link) return undefined;
                const sourceNode = setterGraph.getNodeById(link.origin_id);
                if (!sourceNode) return undefined;
                return {
                    node: sourceNode,
                    slot: link.origin_slot
                };
            }
            getExtraMenuOptions(_, options) {
                this.currentSetter = this.findSetter(this.graph);
            }
            onDrawForeground(ctx, lGraphCanvas) {
                if (this.drawConnection) this._drawVirtualLink(lGraphCanvas, ctx);
            }
            _drawVirtualLink(lGraphCanvas, ctx) {
                if (!this.currentSetter) return;
                const defaultLink = {
                    type: 'default',
                    color: this.slotColor
                };
                let start_node_slotpos = this.currentSetter.getConnectionPos(false, 0);
                start_node_slotpos = [start_node_slotpos[0] - this.pos[0], start_node_slotpos[1] - this.pos[1], ];
                const end_node_slotpos = [0, -LiteGraph.NODE_TITLE_HEIGHT * 0.5];
                lGraphCanvas.renderLink(ctx, start_node_slotpos, end_node_slotpos, defaultLink, false, null, this.slotColor);
            }
        }
        LiteGraph.registerNodeType(GET_TYPE, Object.assign(GetNode, {
            title: 'Get'
        }));
        GetNode.category = CATEGORY;
    },
});
app.registerExtension({
    name: 'Eclipse.CrossGraphSetGet',
    setup() {
        let patched = false;
        const originalGraphToPrompt = app.graphToPrompt.bind(app);
        app.graphToPrompt = async function (...args) {
            if (!patched) {
                try {
                    const subgraphNode = app.graph._nodes.find(n => typeof n.getInnerNodes === 'function');
                    if (subgraphNode) {
                        const tempMap = new Map();
                        const dtos = subgraphNode.getInnerNodes(tempMap, []);
                        if (dtos.length > 0) {
                            const proto = Object.getPrototypeOf(dtos[0]);
                            const nativeSource = proto.resolveOutput.toString();
                            const hasNativeSupport = nativeSource.includes('resolveVirtualOutput');
                            if (!hasNativeSupport) {
                                const DtoClass = proto.constructor;
                                const origResolveOutput = proto.resolveOutput;
                                proto.resolveOutput = function (slot, type, visited) {
                                    if (typeof this.node?.resolveVirtualOutput === 'function') {
                                        const virtualSource = this.node.resolveVirtualOutput(slot);
                                        if (virtualSource) {
                                            const inputNodeDto = [...this.nodesByExecutionId.values()].find(dto => dto instanceof DtoClass && dto.node === virtualSource.node);
                                            if (inputNodeDto) {
                                                return inputNodeDto.resolveOutput(virtualSource.slot, type, visited);
                                            }
                                            throw new Error(`Eclipse: No DTO found for cross-graph source node [${virtualSource.node.id}]`);
                                        }
                                    }
                                    return origResolveOutput.call(this, slot, type, visited);
                                };
                            }
                            patched = true;
                        }
                    }
                } catch (e) {
                    console.warn('[Eclipse] Failed to probe ExecutableNodeDTO for cross-graph patch:', e);
                }
            }
            return originalGraphToPrompt(...args);
        };
    },
});
app.registerExtension({
    name: 'Eclipse.LinkToSetGet',
    setup() {
        const origShowLinkMenu = LGraphCanvas.prototype.showLinkMenu;
        if (!origShowLinkMenu) return;
        LGraphCanvas.prototype.showLinkMenu = function (segment, e) {
            const menusBefore = document.querySelectorAll('.litecontextmenu').length;
            const result = origShowLinkMenu.call(this, segment, e);
            const graph = this.graph;
            if (!graph) return result;
            const link = getLink(graph, segment.id);
            if (!link || link.origin_id == null || link.target_id == null) return result;
            const menus = document.querySelectorAll('.litecontextmenu');
            if (menus.length <= menusBefore) return result;
            const lastMenu = menus[menus.length - 1];
            if (!lastMenu) return result;
            const entries = lastMenu.querySelector('.litemenu-entry')?.parentElement;
            if (!entries) return result;
            const separator = document.createElement('div');
            separator.className = 'litemenu-entry separator';
            entries.appendChild(separator);
            const menuItem = document.createElement('div');
            menuItem.className = 'litemenu-entry submenu';
            menuItem.textContent = 'Eclipse: Convert to Set/Get';
            entries.appendChild(menuItem);
            const canvas = this;
            menuItem.addEventListener('click', () => {
                lastMenu.remove();
                const originNode = graph.getNodeById(link.origin_id);
                const targetNode = graph.getNodeById(link.target_id);
                if (!originNode || !targetNode) return;
                const outputSlot = originNode.outputs[link.origin_slot];
                const linkType = outputSlot?.type || '*';
                const linkName = outputSlot?.name || linkType;
                const setNode = LiteGraph.createNode(SET_TYPE);
                if (!setNode) return;
                setNode.pos = [originNode.pos[0] + originNode.size[0] + 30, originNode.pos[1], ];
                graph.add(setNode);
                const getNode = LiteGraph.createNode(GET_TYPE);
                if (!getNode) return;
                getNode.pos = [targetNode.pos[0] - (getNode.size?.[0] || 200) - 30, targetNode.pos[1], ];
                graph.add(getNode);
                graph.removeLink(link.id);
                originNode.connect(link.origin_slot, setNode, 0);
                setNode.widgets[0].value = linkName;
                setNode.title = 'Set_' + linkName;
                setNode.validateName(graph);
                setNode.properties.previousName = setNode.widgets[0].value;
                const finalName = setNode.widgets[0].value;
                getNode.widgets[0].value = finalName;
                getNode.onRename();
                getNode.connect(0, targetNode, link.target_slot);
                canvas.setDirty(true, true);
            });
            return result;
        };
    },
});

function convertOutputsToSetGet(graph, node) {
    if (!graph || !node?.outputs) return;
    for (let slot = 0; slot < node.outputs.length; slot++) {
        const output = node.outputs[slot];
        if (!output?.links?.length) continue;
        const linkName = output.name || output.type || '*';
        const targets = [];
        for (const linkId of [...output.links]) {
            const link = getLink(graph, linkId);
            if (!link) continue;
            const targetNode = graph.getNodeById(link.target_id);
            if (!targetNode) continue;
            if (SETTER_TYPES.has(targetNode.type) || ALL_GETTER_TYPES.includes(targetNode.type)) continue;
            targets.push({
                targetNode,
                targetSlot: link.target_slot,
                linkId: link.id
            });
        }
        if (!targets.length) continue;
        for (const t of targets) graph.removeLink(t.linkId);
        let existingSetter = null;
        if (output.links) {
            for (const lid of output.links) {
                const existingLink = getLink(graph, lid);
                if (!existingLink) continue;
                const targetNode = graph.getNodeById(existingLink.target_id);
                if (targetNode && SETTER_TYPES.has(targetNode.type)) {
                    existingSetter = targetNode;
                    break;
                }
            }
        }
        let finalName;
        if (existingSetter) {
            finalName = existingSetter.widgets[0].value;
        } else {
            const setNode = LiteGraph.createNode(SET_TYPE);
            if (!setNode) continue;
            setNode.pos = [node.pos[0] + node.size[0] + 30, node.pos[1] + slot * 80];
            graph.add(setNode);
            node.connect(slot, setNode, 0);
            setNode.widgets[0].value = linkName;
            setNode.title = 'Set_' + linkName;
            setNode.validateName(graph);
            setNode.properties.previousName = setNode.widgets[0].value;
            finalName = setNode.widgets[0].value;
        }
        for (const t of targets) {
            const getNode = LiteGraph.createNode(GET_TYPE);
            if (!getNode) continue;
            getNode.pos = [t.targetNode.pos[0] - (getNode.size?.[0] || 200) - 30, t.targetNode.pos[1]];
            graph.add(getNode);
            getNode.widgets[0].value = finalName;
            getNode.onRename();
            getNode.connect(0, t.targetNode, t.targetSlot);
        }
    }
    app.canvas?.setDirty(true, true);
}

function convertInputsToSetGet(graph, node) {
    if (!graph || !node?.inputs) return;
    const setBySource = new Map();
    for (let slot = 0; slot < node.inputs.length; slot++) {
        const input = node.inputs[slot];
        if (!input?.link) continue;
        const link = getLink(graph, input.link);
        if (!link) continue;
        const srcNode = graph.getNodeById(link.origin_id);
        if (!srcNode) continue;
        if (SETTER_TYPES.has(srcNode.type) || ALL_GETTER_TYPES.includes(srcNode.type)) continue;
        const srcSlot = link.origin_slot;
        const sourceKey = srcNode.id + ':' + srcSlot;
        const linkId = link.id;
        const linkName = input.name || input.type || '*';
        graph.removeLink(linkId);
        let finalName;
        if (setBySource.has(sourceKey)) {
            finalName = setBySource.get(sourceKey);
        } else {
            const srcOutput = srcNode.outputs?.[srcSlot];
            let existingSetter = null;
            if (srcOutput?.links) {
                for (const lid of srcOutput.links) {
                    const existingLink = getLink(graph, lid);
                    if (!existingLink) continue;
                    const targetNode = graph.getNodeById(existingLink.target_id);
                    if (targetNode && SETTER_TYPES.has(targetNode.type)) {
                        existingSetter = targetNode;
                        break;
                    }
                }
            }
            if (existingSetter) {
                finalName = existingSetter.widgets[0].value;
            } else {
                const setNode = LiteGraph.createNode(SET_TYPE);
                if (!setNode) continue;
                const setName = srcOutput?.name || srcOutput?.type || linkName;
                setNode.pos = [srcNode.pos[0] + srcNode.size[0] + 30, srcNode.pos[1] + srcSlot * 80];
                graph.add(setNode);
                srcNode.connect(srcSlot, setNode, 0);
                setNode.widgets[0].value = setName;
                setNode.title = 'Set_' + setName;
                setNode.validateName(graph);
                setNode.properties.previousName = setNode.widgets[0].value;
                finalName = setNode.widgets[0].value;
            }
            setBySource.set(sourceKey, finalName);
        }
        const getNode = LiteGraph.createNode(GET_TYPE);
        if (!getNode) continue;
        getNode.pos = [node.pos[0] - (getNode.size?.[0] || 200) - 30, node.pos[1] + slot * 80];
        graph.add(getNode);
        getNode.widgets[0].value = finalName;
        getNode.onRename();
        getNode.connect(0, node, slot);
    }
    app.canvas?.setDirty(true, true);
}
app.registerExtension({
    name: 'Eclipse.SetGetUI',
});
// Provide Set/Get canvas-level menu items (bulk operations on selected nodes)
(window._eclipseCanvasMenuProviders ??= []).push(() => {
    return [
        {
            content: 'Add SetNode',
            callback: () => {
                const pos = app.canvas.graph_mouse;
                const n = LiteGraph.createNode(SET_TYPE);
                if (!n) return;
                n.pos = [pos[0], pos[1]];
                (app.canvas.graph || app.graph).add(n);
                app.canvas?.selectNode(n, false);
                app.canvas?.setDirty(true, true);
            },
        },
        {
            content: 'Add GetNode',
            callback: () => {
                const pos = app.canvas.graph_mouse;
                const n = LiteGraph.createNode(GET_TYPE);
                if (!n) return;
                n.pos = [pos[0], pos[1]];
                (app.canvas.graph || app.graph).add(n);
                app.canvas?.selectNode(n, false);
                app.canvas?.setDirty(true, true);
            },
        },
        {
            content: 'Add Get First',
            callback: () => {
                const pos = app.canvas.graph_mouse;
                const n = LiteGraph.createNode(GET_FIRST_TYPE);
                if (!n) return;
                n.pos = [pos[0], pos[1]];
                (app.canvas.graph || app.graph).add(n);
                app.canvas?.selectNode(n, false);
                app.canvas?.setDirty(true, true);
            },
        },
        {
            content: 'Add Get All Active',
            callback: () => {
                const pos = app.canvas.graph_mouse;
                const n = LiteGraph.createNode(GET_ALL_ACTIVE_TYPE);
                if (!n) return;
                n.pos = [pos[0], pos[1]];
                (app.canvas.graph || app.graph).add(n);
                app.canvas?.selectNode(n, false);
                app.canvas?.setDirty(true, true);
            },
        },
        null,
        {
            content: 'Convert selected outputs to Set/Get',
            callback: () => {
                const selected = Object.values(app.canvas.selected_nodes || {});
                if (!selected.length) return;
                for (const n of selected) convertOutputsToSetGet(n.graph, n);
            },
        },
        {
            content: 'Convert selected inputs to Set/Get',
            callback: () => {
                const selected = Object.values(app.canvas.selected_nodes || {});
                if (!selected.length) return;
                for (const n of selected) convertInputsToSetGet(n.graph, n);
            },
        },
        {
            content: 'Convert selected Set/Get to links',
            callback: () => {
                const graph = app.canvas.graph || app.graph;
                const selected = Object.values(app.canvas.selected_nodes || {});
                const setNodes = selected.filter(n => n.type === SET_TYPE);
                const getNodes = selected.filter(n => n.type === GET_TYPE);
                for (const n of setNodes) convertSetGetToLinks(graph, n);
                const setters = new Set(getNodes.map(n => findSetterByName(graph, n.widgets?.[0]?.value)?.node).filter(Boolean));
                for (const s of setters) convertSetGetToLinks(graph, s);
            },
        },
    ];
});
// Provide Set/Get menu items via shared Eclipse submenu collector
(window._eclipseMenuProviders ??= []).push((node) => {
        const isSet = node.type === SET_TYPE;
        const isGet = node.type === GET_TYPE;
        const items = [];
        if (!isSet && !isGet) {
            items.push(null);
            items.push({
                content: 'Add SetNode',
                callback: () => {
                    const setNode = LiteGraph.createNode(SET_TYPE);
                    if (!setNode) return;
                    setNode.pos = [node.pos[0] + node.size[0] + 30, node.pos[1]];
                    node.graph.add(setNode);
                    app.canvas?.selectNode(setNode, false);
                    app.canvas?.setDirty(true, true);
                },
            });
            items.push({
                content: 'Add GetNode',
                callback: () => {
                    const getNode = LiteGraph.createNode(GET_TYPE);
                    if (!getNode) return;
                    getNode.pos = [node.pos[0] - (getNode.size?.[0] || 200) - 30, node.pos[1]];
                    node.graph.add(getNode);
                    app.canvas?.selectNode(getNode, false);
                    app.canvas?.setDirty(true, true);
                },
            });
            items.push({
                content: 'Add Get First',
                callback: () => {
                    const getFirstNode = LiteGraph.createNode(GET_FIRST_TYPE);
                    if (!getFirstNode) return;
                    getFirstNode.pos = [node.pos[0] - (getFirstNode.size?.[0] || 200) - 30, node.pos[1]];
                    node.graph.add(getFirstNode);
                    app.canvas?.selectNode(getFirstNode, false);
                    app.canvas?.setDirty(true, true);
                },
            });
            items.push({
                content: 'Add Get All Active',
                callback: () => {
                    const getAllActiveNode = LiteGraph.createNode(GET_ALL_ACTIVE_TYPE);
                    if (!getAllActiveNode) return;
                    getAllActiveNode.pos = [node.pos[0] - (getAllActiveNode.size?.[0] || 200) - 30, node.pos[1]];
                    node.graph.add(getAllActiveNode);
                    app.canvas?.selectNode(getAllActiveNode, false);
                    app.canvas?.setDirty(true, true);
                },
            });
            items.push({
                content: 'Convert all outputs to Set/Get',
                callback: () => {
                    convertOutputsToSetGet(node.graph, node);
                },
            });
            items.push({
                content: 'Convert all inputs to Set/Get',
                callback: () => {
                    convertInputsToSetGet(node.graph, node);
                },
            });
            items.push({
                content: 'Convert all to Set/Get',
                callback: () => {
                    convertOutputsToSetGet(node.graph, node);
                    convertInputsToSetGet(node.graph, node);
                },
            });
            items.push({
                content: 'Convert all to links',
                callback: () => {
                    convertNodeToLinks(node.graph, node);
                },
            });
        }
        if (isSet) {
            items.push(null);
            items.push({
                content: 'Convert to links',
                callback: () => {
                    convertSetGetToLinks(node.graph, node);
                },
            });
            items.push({
                content: node.drawConnection ? 'Hide connections' : 'Show connections',
                callback: () => {
                    node.drawConnection = !node.drawConnection;
                    const linkType = node.inputs[0].type;
                    node.slotColor = app.canvas.default_connection_color_byType[linkType];
                    app.canvas.setDirty(true, true);
                },
            });
            items.push({
                content: 'Hide all connections',
                callback: () => {
                    for (const n of node.graph._nodes) {
                        if (n.type === GET_TYPE || n.type === SET_TYPE) n.drawConnection = false;
                    }
                    app.canvas.setDirty(true, true);
                },
            });
            const getterEntries = findGettersByName(node.graph, node.widgets?.[0]?.value, GET_TYPE);
            if (getterEntries.length > 0) {
                const gettersSubmenu = getterEntries.map((entry) => {
                    const getter = entry.node;
                    const sameGraph = entry.graph === node.graph;
                    const sgNode = !sameGraph ? findSubgraphNodeFor(node.graph, getter) : null;
                    const label = sameGraph ? `${getter.title} id: ${getter.id}` : `${getter.title} (in subgraph${sgNode ? ': ' + (sgNode.title || sgNode.type) : ''})`;
                    return {
                        content: label,
                        callback: () => {
                            if (sameGraph) {
                                app.canvas.centerOnNode(getter);
                                app.canvas.selectNode(getter, false);
                            } else if (sgNode?.subgraph) {
                                app.canvas.openSubgraph?.(sgNode.subgraph, sgNode);
                                setTimeout(() => {
                                    app.canvas.centerOnNode(getter);
                                    app.canvas.selectNode(getter, false);
                                    app.canvas.setDirty(true, true);
                                }, 0);
                            } else {
                                app.canvas.setGraph?.(entry.graph);
                                setTimeout(() => {
                                    app.canvas.centerOnNode(getter);
                                    app.canvas.selectNode(getter, false);
                                    app.canvas.setDirty(true, true);
                                }, 0);
                            }
                            app.canvas.setDirty(true, true);
                        },
                    };
                });
                items.push({
                    content: 'Getters',
                    has_submenu: true,
                    submenu: {
                        title: 'GetNodes',
                        options: gettersSubmenu
                    },
                });
            }
        }
        if (isGet) {
            items.push(null);
            const setterResult = findSetterByName(node.graph, node.widgets?.[0]?.value);
            const setter = setterResult?.node;
            if (setter) {
                const crossGraph = setterResult.graph !== node.graph;
                const isRoot = crossGraph && setterResult.graph === findRootGraph(node.graph);
                const goLabel = crossGraph ? `Go to setter (in ${isRoot ? 'parent graph' : 'subgraph'})` : 'Go to setter';
                items.push({
                    content: 'Convert to links',
                    callback: () => {
                        convertSetGetToLinks(node.graph, setter);
                    },
                });
                items.push({
                    content: goLabel,
                    callback: () => {
                        node.goToSetter?.();
                    },
                });
                items.push({
                    content: node.drawConnection ? 'Hide connections' : 'Show connections',
                    callback: () => {
                        const linkType = setter.inputs[0].type;
                        setter.drawConnection = !setter.drawConnection;
                        setter.slotColor = app.canvas.default_connection_color_byType[linkType];
                        node.drawConnection = setter.drawConnection;
                        app.canvas.setDirty(true, true);
                    },
                });
            }
        }
        return items;
});

// Clear paste rename map after a stable delay.
let _pasteRenameMapClearTimer = null;
function schedulePasteRenameMapClear() {
    clearTimeout(_pasteRenameMapClearTimer);
    _pasteRenameMapClearTimer = setTimeout(() => {
        clearPasteRenameMap();
        _pasteRenameMapClearTimer = null;
    }, 500);
}

// Central two-phase paste rename coordinator.
//
// Triggered via setTimeout(0) from graph-changed. By the time the callback
// fires, app.configuringGraph is false and all pasted nodes are fully in the
// graph — eliminating the race condition where a GetNode's onAfterGraphConfigured
// fired before SetNode's, reading an empty map.
//
// Phase 1: iterate every node with _justAdded=true across the entire graph
//   hierarchy. For SetNodes: call _handlePasteValidation() to detect conflicts,
//   rename, and populate _pasteRenameMap. All SetNodes complete before Phase 2.
//
// Phase 2: for every other node with _justAdded=true call _handlePasteRename()
//   (a no-op for nodes that don't define it). The map is now fully populated
//   so all GetNode types see every rename regardless of their position in _nodes.
function runPasteRenamePass() {
    if (app.configuringGraph || subgraphOpState.active) return;
    const root = findRootGraph(app.graph);
    if (!root) return;
    // Clear stale map if we've switched to a different workflow (root graph changed).
    const rootId = root.id;
    if (rootId && _pasteRenameMap._lastRootGraphId !== rootId) {
        clearPasteRenameMap();
        _pasteRenameMap._lastRootGraphId = rootId;
    }
    // Collect freshly-added nodes across the entire graph hierarchy.
    const allGraphs = [root, ...getGraphDescendants(root)];
    const setterNodes = [];
    const otherNodes = [];
    for (const g of allGraphs) {
        if (!g?._nodes) continue;
        for (const n of g._nodes) {
            if (!n._justAdded) continue;
            if (SETTER_TYPES.has(n.type)) setterNodes.push(n);
            else otherNodes.push(n);
        }
    }
    if (setterNodes.length === 0 && otherNodes.length === 0) return;
    // Phase 1 — SetNodes validate and populate the map.
    for (const n of setterNodes) {
        n._handlePasteValidation?.();
        n._justAdded = false;
    }
    // Phase 2 — getter nodes read the now-fully-populated map.
    for (const n of otherNodes) {
        n._handlePasteRename?.();
        n._justAdded = false;
    }
    // Nodes 2.0 can snapshot a getter's dynamic combo options before a fresh
    // subgraph attachment is complete. Refresh only the newly attached
    // Eclipse getters after the coordinated add/paste pass has settled.
    if (isVueMode()) {
        for (const n of otherNodes) {
            if (n.type === GET_TYPE || MULTI_GETTER_TYPES.has(n.type)) {
                batchedRefreshVueWidgetOptions(n);
            }
        }
    }
    schedulePasteRenameMapClear();
}

let _pasteRenamePassTimer = null;
function schedulePasteRenamePass() {
    clearTimeout(_pasteRenamePassTimer);
    _pasteRenamePassTimer = setTimeout(runPasteRenamePass, 0);
}
// Register with the shared ref so getallactive/getfirst can trigger the scan
// from their onAdded hooks without a circular import.
pasteRenameScheduler.schedule = schedulePasteRenamePass;

// Clear stale map before queuing a prompt (no paste in flight at that point).
if (app.ui) {
    const origQueuePrompt = app.ui.queuePrompt?.bind(app.ui);
    if (origQueuePrompt) {
        app.ui.queuePrompt = function(...args) {
            schedulePasteRenameMapClear();
            return origQueuePrompt.apply(this, args);
        };
    }
}

app.canvas.addEventListener('graph-changed', () => {
    schedulePasteRenamePass();
});
