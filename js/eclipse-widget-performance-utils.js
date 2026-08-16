if (!document.getElementById('eclipse-tooltip-fix')) {
    const _s = document.createElement('style');
    _s.id = 'eclipse-tooltip-fix';
    _s.textContent = '.p-tooltip { pointer-events: none !important; }';
    document.head.appendChild(_s);
}

// ---------------------------------------------------------------------------
// Performance logger (opt-in — OFF by default).
//
// Enable via localStorage (independent of Eclipse log_level):
//     localStorage.eclipse_perf_log = '1'        // enable counters
//     localStorage.eclipse_perf_log = 'verbose'  // enable + per-call console.log
// Then reload the page.  Remove the key (or set to '0') to disable.
//
// Usage once enabled:
//     window.eclipsePerfDump()      // console.table: fn | calls | duringLoad | firstSeenMs | topCallers
//     window.eclipsePerfReset()     // clear counters
// ---------------------------------------------------------------------------
let _perfFlag = '';
try {
    if (typeof localStorage !== 'undefined') {
        _perfFlag = localStorage.getItem('eclipse_perf_log') || '';
    }
} catch {}
// Legacy fallback: window.__eclipse_perf_log = 'verbose' still honored
if (!_perfFlag && typeof window !== 'undefined' && window.__eclipse_perf_log) {
    _perfFlag = String(window.__eclipse_perf_log);
}
let _perfEnabled = _perfFlag === '1' || _perfFlag === 'verbose' || _perfFlag === 'true';
let _perfVerbose = _perfFlag === 'verbose';
// callCounts: fnName -> count
const _perfCounts = new Map();
// callerCounts: fnName -> Map(callerLabel -> count)
const _perfCallers = new Map();
// firstSeenAt: fnName -> epochMs of first call (useful to correlate with load phase)
const _perfFirstSeen = new Map();
// phase counters: how many calls landed while configuringGraph was true
const _perfDuringLoad = new Map();
const _perfLoadStartMs = (typeof performance !== 'undefined') ? performance.now() : Date.now();

function _perfCaller() {
    // Skip 3 frames: Error, _perfCaller, _perfTrack.  Keep the next frame
    // (the function that called _perfTrack) stripped, and return the frame
    // ABOVE that — the user-land call site (e.g. node JS file).
    try {
        const stack = new Error().stack || '';
        const lines = stack.split('\n');
        // Frame 0 is "Error", 1 _perfCaller, 2 _perfTrack, 3 utility fn,
        // 4 = actual user-land caller.  Different engines include/omit the
        // "Error" header; handle both.
        const start = lines[0]?.startsWith('Error') ? 4 : 3;
        for (let i = start; i < lines.length; i++) {
            const line = lines[i].trim();
            if (!line) continue;
            // Strip vendor-vue / internal frames; keep first eclipse-*.js frame
            const m = line.match(/\(?(.*?eclipse-[^\/\s:]+\.js)[:\d]*\)?/);
            if (m) {
                // Normalize to just filename for grouping
                return m[1].split('/').pop();
            }
            // Non-eclipse frame — fall through, try next
        }
        // No eclipse frame found (called from vendor / anon); return first
        // non-util frame verbatim, truncated.
        for (let i = start; i < lines.length; i++) {
            const line = lines[i].trim();
            if (line && !line.includes('eclipse-widget-performance-utils')) {
                return line.slice(0, 120);
            }
        }
    } catch {}
    return '<unknown>';
}

function _perfTrack(fnName) {
    if (!_perfEnabled) return;
    _perfCounts.set(fnName, (_perfCounts.get(fnName) || 0) + 1);
    if (!_perfFirstSeen.has(fnName)) {
        _perfFirstSeen.set(fnName, ((typeof performance !== 'undefined') ? performance.now() : Date.now()) - _perfLoadStartMs);
    }
    if (typeof window !== 'undefined' && window.app?.configuringGraph) {
        _perfDuringLoad.set(fnName, (_perfDuringLoad.get(fnName) || 0) + 1);
    }
    const caller = _perfCaller();
    let m = _perfCallers.get(fnName);
    if (!m) { m = new Map(); _perfCallers.set(fnName, m); }
    m.set(caller, (m.get(caller) || 0) + 1);
    if (_perfVerbose) {
        // eslint-disable-next-line no-console
        console.log(`[eclipse-perf] ${fnName} ← ${caller}`);
    }
}

if (typeof window !== 'undefined' && _perfEnabled) {
    window.eclipsePerfDump = function () {
        const rows = [];
        for (const [fn, count] of _perfCounts) {
            const duringLoad = _perfDuringLoad.get(fn) || 0;
            const first = _perfFirstSeen.get(fn) || 0;
            const callers = _perfCallers.get(fn) || new Map();
            const top = [...callers.entries()]
                .sort((a, b) => b[1] - a[1])
                .slice(0, 5)
                .map(([c, n]) => `${c}×${n}`)
                .join(', ');
            rows.push({
                fn,
                calls: count,
                duringLoad,
                firstSeenMs: Math.round(first),
                topCallers: top,
            });
        }
        rows.sort((a, b) => b.calls - a.calls);
        // eslint-disable-next-line no-console
        console.table(rows);
        return rows;
    };
    window.eclipsePerfReset = function () {
        _perfCounts.clear();
        _perfCallers.clear();
        _perfFirstSeen.clear();
        _perfDuringLoad.clear();
        // eslint-disable-next-line no-console
        console.log('[eclipse-perf] counters reset');
    };
    // eslint-disable-next-line no-console
    console.log(`[eclipse-perf] logging ${_perfVerbose ? 'VERBOSE' : 'ON'} (opt-in via localStorage.eclipse_perf_log).  Call window.eclipsePerfDump() for summary.`);
}

export function debounce(fn, delay) {
    let timer;
    return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), delay);
    };
}
export const canvasDirtyBatcher = {
    markDirty(node, fg = true, bg = false) {
        _perfTrack('canvasDirtyBatcher.markDirty');
        if (node?.setDirtyCanvas) node.setDirtyCanvas(fg, bg);
    },
};
export function notifyVue(node) {
    _perfTrack('notifyVue');
    const widgets = node.widgets;
    if (widgets?.length) {
        const last = widgets.pop();
        widgets.push(last);
    }
}
const _pendingNotify = new Set();
let _notifyScheduled = false;
export function batchedNotifyVue(node) {
    _perfTrack('batchedNotifyVue');
    _pendingNotify.add(node);
    if (!_notifyScheduled) {
        _notifyScheduled = true;
        queueMicrotask(() => {
            _notifyScheduled = false;
            for (const n of _pendingNotify) notifyVue(n);
            _pendingNotify.clear();
        });
    }
}
// Native ComfyUI load-state flag.  Frontend auto-wraps LGraph.configure()
// with a counter (dialogService bundle): configuringGraphLevel++/--.
// Reading window.app.configuringGraph is truthy whenever ANY graph (root
// or subgraph) is being configured, and automatically clears at the
// correct moment (end of configure finally block).  Supersedes the
// rAF-based auto-clear which could not span async server fetches.
export function isConfiguringGraph() {
    try {
        return !!(typeof window !== 'undefined' && window.app?.configuringGraph);
    } catch {
        return false;
    }
}

export function createWidgetVisibilityManager(node) {
    _perfTrack('createWidgetVisibilityManager');
    const stateCache = new Map();
    let widgetMap = null;
    let notifyPending = false;
    // loadMode: manual override for callers that need an explicit "don't
    // schedule a Vue notify" window, e.g. around async fetch resolutions
    // that land after the native configuringGraph flag has cleared.
    //
    // Primary gate is isConfiguringGraph() — true whenever the frontend's
    // LGraph.configure counter > 0.  loadMode OR'd on top as an explicit
    // manual override.  No rAF auto-clear: the native flag clears itself.
    let loadMode = false;

    // Install slot-targeting guards regardless of the renderer active when
    // the manager is created. Nodes survive Vue/classic switches without
    // running onNodeCreated again, so a Vue-created manager must already be
    // safe when the same node later returns to classic canvas.
    if (!node._eclipse_inputPosPatch) {
        node._eclipse_inputPosPatch = true;

        // Recovery: fix slot types corrupted by prior serialization with type-faking.
        // Old code set slot.type = '__eclipse_hidden__' which got serialized;
        // _eclipse_origType was runtime-only and lost on reload.
        for (const slot of node.inputs || []) {
            if (slot.type === '__eclipse_hidden__') {
                const nd = node.constructor?.nodeData?.input;
                const def = nd?.required?.[slot.name] || nd?.optional?.[slot.name];
                slot.type = def ? (Array.isArray(def[0]) ? 'COMBO' : def[0]) : '*';
            }
            delete slot._eclipse_origType;
        }

        // Patch node methods ONCE so hidden widget slots are untargetable
        // during classic connection dragging. Four layers:
        //   1. getInputPos(i)        — off-screen coords for hidden slots
        //   2. getInputOnPos(pos)    — returns null for hidden slots
        //   3. getSlotInPosition()   — returns null for hidden slots
        //   4. findFreeSlotOfType()  — skips hidden slots during auto-connect
        const _origGetInputPos = node.getInputPos;
        node.getInputPos = function (i) {
            const slot = this.inputs?.[i];
            if (slot?._eclipse_hidden) return [-1e9, -1e9];
            return _origGetInputPos.call(this, i);
        };
        const _origGetInputOnPos = node.getInputOnPos;
        const _origGetSlotInPosition = node.getSlotInPosition;
        const isHiddenInputResult = (result) => !!(
            result?._eclipse_hidden ||
            result?.input?._eclipse_hidden ||
            result?.slot?._eclipse_hidden
        );
        node.getInputOnPos = function (e) {
            const result = _origGetInputOnPos.call(this, e);
            return isHiddenInputResult(result) ? null : result;
        };
        node.getSlotInPosition = function (e, t) {
            const result = _origGetSlotInPosition.call(this, e, t);
            return isHiddenInputResult(result) ? null : result;
        };
        const _origFindFreeSlot = node.constructor.prototype.findFreeSlotOfType;
        if (_origFindFreeSlot) {
            node.findFreeSlotOfType = function (type, isOutput, opts) {
                if (isOutput) return _origFindFreeSlot.call(this, type, isOutput, opts);
                const marked = [];
                for (const slot of this.inputs || []) {
                    if (slot._eclipse_hidden && slot.link == null) {
                        marked.push([slot, slot.link]);
                        slot.link = -1;
                    }
                }
                try {
                    return _origFindFreeSlot.call(this, type, isOutput, opts);
                } finally {
                    for (const [slot, link] of marked) slot.link = link;
                }
            };
        }
    }

    function findWidget(name) {
        if (!widgetMap || widgetMap.size !== (node.widgets?.length || 0)) {
            widgetMap = new Map();
            for (const w of node.widgets || []) widgetMap.set(w.name, w);
        }
        return widgetMap.get(name);
    }
    let userDriven = false;
    let userDrivenBatch = 0;

    function syncSlotVisibility(name, visible) {
        const slot = node.inputs?.find((input) => input.widget?.name === name);
        if (!slot) return;
        if (!visible) {
            // Only disconnect on user-driven changes (widget callback), not
            // during onNodeCreated / onConfigure / workflow restore.
            if (userDriven && slot.link != null) {
                const slotIdx = node.inputs.indexOf(slot);
                if (slotIdx !== -1) node.disconnectInput(slotIdx);
            }
            slot._eclipse_hidden = true;
            if (!slot._eclipse_hiddenDrawInstalled) {
                slot._eclipse_hiddenDrawInstalled = true;
                slot._eclipse_hadOwnDraw = Object.prototype.hasOwnProperty.call(slot, 'draw');
                slot._eclipse_originalDraw = slot.draw;
                slot.draw = () => {};
            }
            return;
        }
        delete slot._eclipse_hidden;
        if (slot._eclipse_hiddenDrawInstalled) {
            if (slot._eclipse_hadOwnDraw) slot.draw = slot._eclipse_originalDraw;
            else delete slot.draw;
            delete slot._eclipse_hiddenDrawInstalled;
            delete slot._eclipse_hadOwnDraw;
            delete slot._eclipse_originalDraw;
        }
    }

    return {
        // Mark one synchronous visibility batch as user-driven. The microtask
        // expiry prevents the flag from leaking into later unrelated updates,
        // while every setVisible() in the current stack can disconnect a slot.
        markUserDriven() {
            userDriven = true;
            const batch = ++userDrivenBatch;
            queueMicrotask(() => {
                if (userDrivenBatch === batch) userDriven = false;
            });
        },
        // Hide the named widgets synchronously without scheduling a Vue
        // notify.  Call AFTER all addWidget/addDOMWidget calls complete
        // (typically last line of onNodeCreated, or after the dynamic-widget
        // loop) but BEFORE refreshVisibility().  The subsequent
        // refreshVisibility() will unhide only the correct subset, so Vue's
        // first render sees the final layout — no show-then-hide flash on
        // cold workflow loads.
        //
        // Skips names not found on the node (safe for files that share
        // a CONDITIONAL_WIDGETS set across related node types).
        hideInitially(names) {
            _perfTrack('vis.hideInitially');
            for (const name of names) {
                const widget = findWidget(name);
                if (!widget) continue;
                widget.hidden = true;
                if (widget.options) widget.options.hidden = true;
                stateCache.set(name, false);
                syncSlotVisibility(name, false);
            }
        },
        // Toggle load-mode.  When true, setVisible mutates widget state
        // synchronously (so Vue's first render sees correct visibility) but
        // does NOT schedule a reactivity notify.  Use inside onConfigure():
        //     vis.setLoadMode(true); refreshVisibility(); vis.setLoadMode(false);
        // Eliminates the "show all widgets then hide" flash on cold workflow
        // loads with many Eclipse nodes.
        setLoadMode(v) { loadMode = !!v; },
        setVisible(name, visible) {
            const widget = findWidget(name);
            if (!widget) return;
            // Keep slot state synchronized even on a widget-state cache hit.
            // This covers links restored or renderer switches after the prior
            // visibility mutation.
            syncSlotVisibility(name, visible);
            // Seed cache from current widget state on first encounter so
            // default-matching no-op calls during onConfigure skip the write.
            // hideInitially() pre-populates the cache, so pre-hid widgets
            // hit the fast path below directly.
            let cached = stateCache.get(name);
            if (cached === undefined) cached = !widget.hidden;
            if (cached === visible) {
                stateCache.set(name, visible);
                _perfTrack('vis.setVisible.skip');
                return;
            }
            // Only count real writes — fast-path exits are ~free.
            _perfTrack('vis.setVisible');
            stateCache.set(name, visible);
            widget.hidden = !visible;
            if (widget.options) widget.options.hidden = !visible;
            if (loadMode || isConfiguringGraph()) {
                // No notify — Vue's first render will pick up options.hidden.
                // Covers both manual callers (loadMode) and native workflow
                // load window (app.configuringGraph, set by frontend's
                // LGraph.configure wrapper).
                return;
            }
            // P1: Classic mode doesn't need Vue reactivity.  LiteGraph reads
            // widget.hidden directly on every draw and redraws via the dirty
            // canvas flag.  The pop/push reactivity nudge is pure overhead.
            if (!isVueMode()) {
                node.setDirtyCanvas?.(true, false);
                return;
            }
            if (!notifyPending) {
                notifyPending = true;
                // Per-manager microtask: dedup multiple setVisible() on the
                // same node within one tick.  Uses the module-level
                // batchedNotifyVue so N managers notifying in the same tick
                // share ONE flush instead of N independent flushes — the
                // cold-load win when 100+ Eclipse nodes refresh visibility
                // at the same moment.
                queueMicrotask(() => {
                    notifyPending = false;
                    batchedNotifyVue(node);
                });
            }
        },
        getValue(name) {
            const widget = findWidget(name);
            return widget ? widget.value : null;
        },
        clearCache() {
            stateCache.clear();
            widgetMap = null;
        },
    };
}

const _SMART_RESIZE_NODE_SELECTOR = '.lg-node[data-node-id]';
const _SMART_RESIZE_MAX_FRAMES = 60;
const _SMART_RESIZE_STABLE_FRAMES = 4;
const _smartResizeOptions = new WeakMap();
const _smartResizeDemand = new WeakSet();
const _smartResizeElements = new WeakMap();
const _smartResizeAppliedGeometry = new WeakMap();
const _smartResizeRuns = new Map();
const _smartResizeVerifications = new Map();
const _smartResizeModeTransitions = new Map();
let _smartResizeMountObserver = null;
let _smartResizeMountObserverTarget = null;
let _smartResizeModeWatcherInstalled = false;
let _smartResizeCanvasElement = null;
let _smartResizeGraphFrame = null;
let _smartResizeRestartAll = false;
let _smartResizeVerificationFrame = null;
let _smartResizeModeTransitionFrame = null;

function _getActiveGraph() {
    return window.app?.canvas?.graph || null;
}

function _isNodeInGraph(node, graph = _getActiveGraph()) {
    if (!graph || node?.graph !== graph) return false;
    const nodes = graph._nodes || graph.nodes;
    return !nodes || nodes.includes(node);
}

function _getGraphNodes(graph = _getActiveGraph()) {
    return graph?._nodes || graph?.nodes || [];
}

function _finishSmartResize(node, run, clearDemand = false) {
    if (_smartResizeRuns.get(node) !== run) return;
    if (run.probeFrame !== null) {
        cancelAnimationFrame(run.probeFrame);
        run.probeFrame = null;
    }
    _smartResizeRuns.delete(node);
    _smartResizeVerifications.delete(node);
    if (!_smartResizeVerifications.size && _smartResizeVerificationFrame !== null) {
        cancelAnimationFrame(_smartResizeVerificationFrame);
        _smartResizeVerificationFrame = null;
    }
    if (clearDemand) {
        _smartResizeDemand.delete(node);
        if (run.applied) {
            _smartResizeAppliedGeometry.set(node, {
                width: run.applied.width,
                height: run.applied.height,
            });
        }
    }
    node._smartResizePending = false;
}

function _cancelInactiveSmartResizes(graph = _getActiveGraph()) {
    for (const [node, run] of _smartResizeRuns) {
        if (!_isNodeInGraph(node, graph)) _finishSmartResize(node, run);
    }
}

function _cancelAllSmartResizes() {
    for (const [node, run] of [..._smartResizeRuns]) {
        _finishSmartResize(node, run);
    }
}

function _invalidateActiveSmartResizeElements(graph = _getActiveGraph()) {
    for (const node of _getGraphNodes(graph)) {
        if (_smartResizeOptions.has(node)) {
            delete node._eclipse_el;
            _smartResizeElements.delete(node);
        }
    }
}

function _scheduleSmartResizeProbe(run, callback) {
    run.probeFrame = requestAnimationFrame(() => {
        run.probeFrame = null;
        callback();
    });
}

function _clearSmartResizeModeTransitions() {
    _smartResizeModeTransitions.clear();
    if (_smartResizeModeTransitionFrame !== null) {
        cancelAnimationFrame(_smartResizeModeTransitionFrame);
        _smartResizeModeTransitionFrame = null;
    }
}

function _verifySmartResizeModeTransitions() {
    _smartResizeModeTransitionFrame = null;
    const activeGraph = _getActiveGraph();
    const vueMode = isVueMode();
    for (const [node, transition] of _smartResizeModeTransitions) {
        if (
            !_isNodeInGraph(node, activeGraph) ||
            _smartResizeDemand.has(node) ||
            _smartResizeRuns.has(node) ||
            node.flags?.collapsed
        ) {
            _smartResizeModeTransitions.delete(node);
            continue;
        }

        let matches = node.size[0] === transition.width &&
            node.size[1] === transition.height;
        if (!matches) {
            node.setSize?.([transition.width, transition.height]);
        }
        if (vueMode) {
            const element = _getNodeElement(node);
            if (!element) {
                matches = false;
            } else if (_syncNodeCSSSize(element, transition.width, transition.height)) {
                matches = false;
            }
        }

        if (matches) transition.stableFrames++;
        else transition.stableFrames = 0;
        if (
            transition.stableFrames >= _SMART_RESIZE_STABLE_FRAMES ||
            ++transition.frames >= _SMART_RESIZE_MAX_FRAMES
        ) {
            _smartResizeModeTransitions.delete(node);
        }
    }
    if (_smartResizeModeTransitions.size) {
        _smartResizeModeTransitionFrame = requestAnimationFrame(
            _verifySmartResizeModeTransitions
        );
    }
}

function _captureSmartResizeModeTransitions() {
    for (const node of _getGraphNodes()) {
        if (
            !_smartResizeModeTransitions.has(node) &&
            _smartResizeOptions.has(node) &&
            !_smartResizeDemand.has(node) &&
            !_smartResizeRuns.has(node) &&
            !node.flags?.collapsed
        ) {
            const applied = _smartResizeAppliedGeometry.get(node);
            _smartResizeModeTransitions.set(node, {
                width: node.size[0],
                height: applied?.height ?? node.size[1],
                frames: 0,
                stableFrames: 0,
            });
        }
    }
    if (
        _smartResizeModeTransitions.size &&
        _smartResizeModeTransitionFrame === null
    ) {
        _smartResizeModeTransitionFrame = requestAnimationFrame(
            _verifySmartResizeModeTransitions
        );
    }
}

function _findActiveSmartResizeNode(nodeId) {
    const graph = _getActiveGraph();
    if (!graph) return null;
    return _getGraphNodes(graph).find((node) =>
        _isNodeInGraph(node, graph) &&
        _smartResizeOptions.has(node) &&
        String(node.id) === nodeId
    ) || null;
}

function _restartSmartResize(node) {
    const options = _smartResizeOptions.get(node);
    if (
        !options ||
        !_smartResizeDemand.has(node) ||
        !_isNodeInGraph(node) ||
        _smartResizeRuns.has(node)
    ) return;
    _startSmartResize(node, options);
}

function _reapplySmartResizeOnMount(element) {
    if (!isVueMode() || !element?.isConnected) return;
    const nodeId = element.getAttribute?.('data-node-id');
    if (nodeId == null || nodeId.startsWith('preview-')) return;
    const node = _findActiveSmartResizeNode(nodeId);
    if (!node || !_isSmartResizeNodeElement(element, node)) return;

    // A replacement can be added before its predecessor disconnects. Always
    // bind the newest mounted element so pending work targets it. A genuine
    // replacement needs its native CSS geometry verified again; an initial
    // mount caused by a stable renderer switch does not.
    const previousElement = _smartResizeElements.get(node);
    const replacedElement = !!previousElement && previousElement !== element;
    node._eclipse_el = element;
    _smartResizeElements.set(node, element);
    if (replacedElement) _smartResizeDemand.add(node);
    if (replacedElement || _smartResizeDemand.has(node)) _restartSmartResize(node);
}

function _handleSmartResizeMounts(records) {
    for (const record of records) {
        for (const addedNode of record.addedNodes || []) {
            if (addedNode.matches?.(_SMART_RESIZE_NODE_SELECTOR)) {
                _reapplySmartResizeOnMount(addedNode);
            }
            for (const element of addedNode.querySelectorAll?.(_SMART_RESIZE_NODE_SELECTOR) || []) {
                _reapplySmartResizeOnMount(element);
            }
        }
    }
}

function _scheduleActiveSmartResizeScan(restartAll = false) {
    if (restartAll) _smartResizeRestartAll = true;
    if (_smartResizeGraphFrame !== null) return;
    _smartResizeGraphFrame = requestAnimationFrame(() => {
        _smartResizeGraphFrame = null;
        const activeGraph = _getActiveGraph();
        const shouldRestartAll = _smartResizeRestartAll;
        _smartResizeRestartAll = false;
        _cancelInactiveSmartResizes(activeGraph);
        for (const node of _getGraphNodes(activeGraph)) {
            if (shouldRestartAll && _smartResizeOptions.has(node)) {
                _smartResizeDemand.add(node);
            }
            _restartSmartResize(node);
        }
    });
}

function _handleSmartResizeGraphChange() {
    const graph = _getActiveGraph();
    _clearSmartResizeModeTransitions();
    _cancelInactiveSmartResizes(graph);
    // Frontend 1.47 can reuse the existing root node elements while rebuilding
    // the Nodes 2.0 layout store, so no mount mutation restarts smartResize.
    // Coalesce all graph events before the next paint into one active-graph scan.
    _scheduleActiveSmartResizeScan(true);
}

function _bindSmartResizeGraphLifecycle() {
    const canvasElement = window.app?.canvas?.canvas;
    if (canvasElement === _smartResizeCanvasElement) return;
    _smartResizeCanvasElement?.removeEventListener?.(
        'litegraph:set-graph',
        _handleSmartResizeGraphChange
    );
    canvasElement?.addEventListener?.('litegraph:set-graph', _handleSmartResizeGraphChange);
    _smartResizeCanvasElement = canvasElement || null;
}

function _startSmartResizeMountObserver() {
    _bindSmartResizeGraphLifecycle();
    if (!isVueMode() || typeof MutationObserver !== 'function') {
        _stopSmartResizeMountObserver();
        return;
    }
    const observerTarget = document.documentElement;
    if (!observerTarget || observerTarget === _smartResizeMountObserverTarget) return;
    if (!_smartResizeMountObserver) {
        _smartResizeMountObserver = new MutationObserver(_handleSmartResizeMounts);
    } else {
        _smartResizeMountObserver.disconnect();
    }
    _smartResizeMountObserver.observe(observerTarget, { childList: true, subtree: true });
    _smartResizeMountObserverTarget = observerTarget;
}

function _stopSmartResizeMountObserver() {
    _smartResizeMountObserver?.disconnect();
    _smartResizeMountObserverTarget = null;
}

function _handleSmartResizeModeChange(vueModeEnabled) {
    // A run captures its renderer strategy at start. Cancel it before changing
    // observer/cache ownership. Cancellation preserves resize demand, so the
    // coalesced scan resumes only interrupted or otherwise pending nodes.
    // Stable nodes keep a lightweight snapshot so renderer-owned layout-store
    // writes can be reversed without another computeSize() or explicit dirty.
    _captureSmartResizeModeTransitions();
    _cancelAllSmartResizes();
    _invalidateActiveSmartResizeElements();
    _bindSmartResizeGraphLifecycle();
    if (vueModeEnabled) _startSmartResizeMountObserver();
    else _stopSmartResizeMountObserver();
    _scheduleActiveSmartResizeScan();
}

function _ensureSmartResizeMountLifecycle() {
    if (!_smartResizeModeWatcherInstalled) {
        _smartResizeModeWatcherInstalled = true;
        onVueModeChange(_handleSmartResizeModeChange);
    }
    _startSmartResizeMountObserver();
}

function _isSmartResizeNodeElement(element, node) {
    return !!(
        element?.isConnected &&
        element.matches?.(_SMART_RESIZE_NODE_SELECTOR) &&
        element.getAttribute?.('data-node-id') === String(node?.id)
    );
}

function _getSmartResizeNodeSelector(node) {
    const escape = globalThis.CSS?.escape;
    if (typeof escape !== 'function' || null == node?.id) return null;
    return `.lg-node[data-node-id="${escape(String(node.id))}"]`;
}

function _getNodeElement(node) {
    if (!isVueMode()) {
        delete node._eclipse_el;
        return null;
    }
    if (_isSmartResizeNodeElement(node._eclipse_el, node)) {
        _smartResizeElements.set(node, node._eclipse_el);
        return node._eclipse_el;
    }
    delete node._eclipse_el;
    const selector = _getSmartResizeNodeSelector(node);
    if (!selector) return null;
    const candidates = document.querySelectorAll?.(selector) || [];
    // Prefer the last connected match. During remounts the replacement can be
    // added before its predecessor disconnects and appears later in DOM order.
    for (let index = candidates.length - 1; index >= 0; index--) {
        const candidate = candidates[index];
        if (_isSmartResizeNodeElement(candidate, node)) {
            node._eclipse_el = candidate;
            _smartResizeElements.set(node, candidate);
            return candidate;
        }
    }
    return null;
}

function _setStylePropertyIfChanged(style, name, value) {
    if (style.getPropertyValue(name) === value) return false;
    style.setProperty(name, value);
    return true;
}

function _syncNodeCSSSize(el, width, height) {
    const heightChanged = _setStylePropertyIfChanged(
        el.style,
        '--node-height',
        `${height}px`
    );
    const widthChanged = _setStylePropertyIfChanged(
        el.style,
        '--node-width',
        `${width}px`
    );
    return heightChanged || widthChanged;
}

function _applyResize(node, minW, minH, padding, computedHeight = null) {
    if (node.flags?.collapsed) return null;
    const curW = node.size[0];
    const curH = node.size[1];
    let measuredHeight = computedHeight;
    if (measuredHeight === null) {
        node.size[1] = 0;
        measuredHeight = node.computeSize()[1];
        node.size[1] = curH;
    }
    const newH = Math.max(measuredHeight, minH) + padding;
    let logicalChanged = false;
    if (newH !== curH) {
        node.setSize?.([curW, newH]);
        logicalChanged = node.size[0] !== curW || node.size[1] !== curH;
    }
    // CSS var override only applies once the DOM element is mounted.
    // During cold Vue workflow loads the element may not exist yet;
    // the trailing rAF pass in smartResize() retries until it does.
    const el = _getNodeElement(node);
    const renderedChanged = el
        ? _syncNodeCSSSize(el, curW, node.size[1])
        : false;
    if (logicalChanged || renderedChanged) {
        node.graph?.setDirtyCanvas?.(true, false);
    }
    return { width: curW, height: node.size[1] };
}
export function patchNodeCSSSize(node) {
    _perfTrack('patchNodeCSSSize');
    if (node.flags?.collapsed) return;
    const el = _getNodeElement(node);
    if (el) {
        _syncNodeCSSSize(el, node.size[0], node.size[1]);
    }
}
export function smartResize(node, {
    minWidth = 259,
    minHeight = 100,
    padding = 0
} = {}) {
    _perfTrack('smartResize');
    _smartResizeOptions.set(node, { minWidth, minHeight, padding });
    _smartResizeDemand.add(node);
    _smartResizeModeTransitions.delete(node);
    const activeRun = _smartResizeRuns.get(node);
    if (activeRun) {
        activeRun.minWidth = minWidth;
        activeRun.minHeight = minHeight;
        activeRun.padding = padding;
        // A probing run has not measured anything yet and naturally observes
        // the newest visibility state. Once verification begins, a new public
        // request needs a fresh measurement rather than clearing that demand
        // against the previously applied geometry.
        if (activeRun.applied) _finishSmartResize(node, activeRun);
    }
    _ensureSmartResizeMountLifecycle();
    _restartSmartResize(node);
}

function _queueSmartResizeVerification(node, run, applied) {
    run.applied = applied;
    run.verifiedFrames = 0;
    _smartResizeVerifications.set(node, run);
    if (_smartResizeVerificationFrame === null) {
        _smartResizeVerificationFrame = requestAnimationFrame(_verifySmartResizes);
    }
}

function _verifySmartResizes() {
    _smartResizeVerificationFrame = null;
    const activeGraph = _getActiveGraph();
    for (const [node, run] of _smartResizeVerifications) {
        if (_smartResizeRuns.get(node) !== run) {
            _smartResizeVerifications.delete(node);
            continue;
        }
        if (!_isNodeInGraph(node, activeGraph)) {
            _finishSmartResize(node, run);
            continue;
        }

        const element = _getNodeElement(node);
        const applied = run.applied;
        const matches = element &&
            node.size[0] === applied.width &&
            node.size[1] === applied.height &&
            element.style.getPropertyValue('--node-width') === `${applied.width}px` &&
            element.style.getPropertyValue('--node-height') === `${applied.height}px`;

        if (!matches) {
            run.verifiedFrames = 0;
            if (element) {
                run.applied = _applyResize(
                    node,
                    run.minWidth,
                    run.minHeight,
                    run.padding
                );
                if (!run.applied) {
                    _finishSmartResize(node, run);
                    continue;
                }
            }
        } else if (++run.verifiedFrames >= _SMART_RESIZE_STABLE_FRAMES) {
            _finishSmartResize(node, run, true);
            continue;
        }

        if (++run.frames >= _SMART_RESIZE_MAX_FRAMES) {
            _finishSmartResize(node, run);
        }
    }
    if (_smartResizeVerifications.size) {
        _smartResizeVerificationFrame = requestAnimationFrame(_verifySmartResizes);
    }
}

function _startSmartResize(node, { minWidth, minHeight, padding }) {
    // P3 reverted (2026-04-22): Vue's DOM-driven layout store does NOT
    // auto-shrink node height when widgets hide via options.hidden — the
    // node stays at its creation-time tall size with a gap where the
    // hidden widgets used to be (confirmed on Lora Stack).  We still
    // need to call setSize() in Vue mode so the node recomputes to its
    // visible-widgets height.  node.computeSize() internally routes
    // through computeLayoutSize() on 1.42.11 Vue, which correctly skips
    // hidden widgets.
    //
    // Classic mode fast-path (2026-04-22): there is no per-node DOM
    // element in classic mode (LiteGraph renders on a canvas), so the
    // rAF loop waiting for _getNodeElement never completes — every call
    // spun up to 60 frames then returned without applying resize.  Just
    // apply once next frame.
    const run = {
        minWidth,
        minHeight,
        padding,
        frames: 0,
        lastComputedH: -1,
        stableCount: 0,
        applied: null,
        verifiedFrames: 0,
        probeFrame: null,
    };
    _smartResizeRuns.set(node, run);
    node._smartResizePending = true;
    if (!isVueMode()) {
        const runClassic = () => {
            if (_smartResizeRuns.get(node) !== run) return;
            if (!_isNodeInGraph(node)) {
                _finishSmartResize(node, run);
                return;
            }
            // Same load-window gate as vue path: don't override the
            // workflow's serialized node.size while it's still being
            // restored.
            if (isConfiguringGraph()) {
                if (++run.frames >= _SMART_RESIZE_MAX_FRAMES) {
                    _finishSmartResize(node, run);
                    return;
                }
                _scheduleSmartResizeProbe(run, runClassic);
                return;
            }
            const applied = _applyResize(
                node,
                run.minWidth,
                run.minHeight,
                run.padding
            );
            run.applied = applied;
            _finishSmartResize(node, run, !!applied);
        };
        _scheduleSmartResizeProbe(run, runClassic);
        return;
    }
    // Vue mode: defer everything to rAF. Running setSize/setDirtyCanvas
    // synchronously while Vue is still flushing its initial mount can be
    // clobbered by Vue's reactivity pass. Instead, wait for the DOM
    // element to exist AND for the computed size to stabilize (two
    // consecutive identical readings), then apply once.
    const tryResize = () => {
        if (_smartResizeRuns.get(node) !== run) return;
        if (!_isNodeInGraph(node)) {
            _finishSmartResize(node, run);
            return;
        }
        // Wait out the workflow-load window — the frontend restores
        // serialized node.size AFTER onConfigure fires; applying a
        // computed resize here would override the user's saved size.
        // Resume probing once configuringGraph clears.
        if (isConfiguringGraph()) {
            if (++run.frames >= _SMART_RESIZE_MAX_FRAMES) {
                _finishSmartResize(node, run);
                return;
            }
            _scheduleSmartResizeProbe(run, tryResize);
            return;
        }
        if (!_getNodeElement(node)) {
            if (++run.frames >= _SMART_RESIZE_MAX_FRAMES) {
                _finishSmartResize(node, run);
                return;
            }
            _scheduleSmartResizeProbe(run, tryResize);
            return;
        }
        // Element exists — probe computed height without mutating node.size
        // across multiple frames until stable.
        const prevSizeH = node.size[1];
        node.size[1] = 0;
        const computed = node.computeSize()[1];
        node.size[1] = prevSizeH;
        if (computed === run.lastComputedH) {
            run.stableCount++;
        } else {
            run.stableCount = 0;
            run.lastComputedH = computed;
        }
        if (run.stableCount >= 1 || ++run.frames >= _SMART_RESIZE_MAX_FRAMES) {
            const applied = _applyResize(
                node,
                run.minWidth,
                run.minHeight,
                run.padding,
                computed
            );
            if (!applied) {
                _finishSmartResize(node, run);
                return;
            }

            // Nodes 2.0 rebuilds its layout store asynchronously when the
            // active graph changes. A remounted node can therefore receive an
            // older stored size after smartResize already completed. Keep the
            // resize pending until the logical size and CSS variables survive
            // several paint boundaries; reapply only when that owner writes a
            // different value during the settling window.
            _queueSmartResizeVerification(node, run, applied);
            return;
        }
        _scheduleSmartResizeProbe(run, tryResize);
    };
    _scheduleSmartResizeProbe(run, tryResize);
}
// Shared global vue-mode watcher — first repo to load installs the
// defineProperty on LiteGraph.vueNodesMode, subsequent repos piggyback
// on the shared callback set.  Prevents repos from overwriting each
// other's watcher regardless of load order.
const _VMC_KEY = '__comfy_vueModeCallbacks';
const _VMC_LOCK = '__comfy_vueModeWatcherInstalled';

function _installVueModeWatcher() {
    if (!window[_VMC_KEY]) window[_VMC_KEY] = new Set();
    if (window[_VMC_LOCK]) return;
    window[_VMC_LOCK] = true;
    try {
        let _value = !!LiteGraph.vueNodesMode;
        Object.defineProperty(LiteGraph, 'vueNodesMode', {
            get() { return _value; },
            set(v) {
                const prev = _value;
                _value = !!v;
                if (prev !== _value) {
                    for (const cb of (window[_VMC_KEY] || [])) {
                        try { cb(_value, prev); }
                        catch (e) { console.error('vueModeChange callback error', e); }
                    }
                }
            },
            configurable: true,
            enumerable: true,
        });
    } catch {}
}
export function isVueMode() {
    // Not instrumented — called extremely frequently; would skew numbers.
    try {
        return !!LiteGraph.vueNodesMode;
    } catch {
        return false;
    }
}
export function onVueModeChange(callback) {
    _installVueModeWatcher();
    window[_VMC_KEY].add(callback);
    return () => window[_VMC_KEY].delete(callback);
}
export function captureScrollableWheelInVue(element) {
    let disposed = false;

    const releaseWheelCaptureForCanvas = () => {
        if (!isVueMode() || element.dataset.captureWheel !== 'true') return;
        element.removeAttribute('data-capture-wheel');
        queueMicrotask(() => {
            if (!disposed && isVueMode() && element.isConnected) {
                element.setAttribute('data-capture-wheel', 'true');
            }
        });
    };

    const handleWheel = (event) => {
        if (!isVueMode()) return;
        if (element.scrollHeight <= element.clientHeight) {
            releaseWheelCaptureForCanvas();
            return;
        }

        const atTop = element.scrollTop <= 0;
        const atBottom = element.scrollTop + element.clientHeight >= element.scrollHeight - 1;
        if ((event.deltaY < 0 && atTop) || (event.deltaY > 0 && atBottom)) {
            releaseWheelCaptureForCanvas();
            return;
        }
        event.stopPropagation();
    };

    const focusOnPointerEnter = () => {
        if (isVueMode()) element.focus({ preventScroll: true });
    };
    const syncRendererMode = () => {
        if (isVueMode()) {
            element.setAttribute('data-capture-wheel', 'true');
        } else {
            element.removeAttribute('data-capture-wheel');
        }
    };

    element.addEventListener('wheel', handleWheel);
    element.addEventListener('pointerenter', focusOnPointerEnter);
    syncRendererMode();
    const unsubscribe = onVueModeChange(syncRendererMode);

    return () => {
        if (disposed) return;
        disposed = true;
        unsubscribe();
        element.removeEventListener('wheel', handleWheel);
        element.removeEventListener('pointerenter', focusOnPointerEnter);
        element.removeAttribute('data-capture-wheel');
    };
}
export function removeSocketlessInputs(node) {
    _perfTrack('removeSocketlessInputs');
    if (isVueMode()) return;
    const nodeData = node.constructor?.nodeData;
    if (!nodeData?.input) return;
    const allInputs = {
        ...nodeData.input.required,
        ...nodeData.input.optional
    };
    const toRemove = [];
    for (const [name, spec] of Object.entries(allInputs)) {
        if (spec?.[1]?.socketless) toRemove.push(name);
    }
    if (!toRemove.length) return;
    for (const name of toRemove) {
        const idx = node.inputs?.findIndex(inp => inp.name === name);
        if (idx != null && idx !== -1) node.removeInput(idx);
    }
}
export default {
    debounce: debounce,
    canvasDirtyBatcher: canvasDirtyBatcher,
    notifyVue: notifyVue,
    batchedNotifyVue: batchedNotifyVue,
    createWidgetVisibilityManager: createWidgetVisibilityManager,
    patchNodeCSSSize: patchNodeCSSSize,
    smartResize: smartResize,
    isVueMode: isVueMode,
    isConfiguringGraph: isConfiguringGraph,
    onVueModeChange: onVueModeChange,
    captureScrollableWheelInVue: captureScrollableWheelInVue,
    removeSocketlessInputs: removeSocketlessInputs,
};
