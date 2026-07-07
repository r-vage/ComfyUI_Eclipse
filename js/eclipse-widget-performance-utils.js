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

    // Slot-targeting patches are CLASSIC-ONLY.  In Vue mode inputs are not
    // rendered as draggable dots, so getInputPos/getInputOnPos/
    // getSlotInPosition/findFreeSlotOfType interception is pure overhead.
    // We still run the slot-type recovery pass in both modes because old
    // workflows may have serialized the buggy '__eclipse_hidden__' marker.
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

        if (!isVueMode()) {
            // Patch node methods ONCE so hidden widget slots are untargetable
            // in classic renderer during connection dragging.  Four layers:
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
            node.getInputOnPos = function (e) {
                const result = _origGetInputOnPos.call(this, e);
                if (result?._eclipse_hidden) return null;
                return result;
            };
            node.getSlotInPosition = function (e, t) {
                const result = _origGetSlotInPosition.call(this, e, t);
                if (result?.input?._eclipse_hidden) return null;
                return result;
            };
            const _origFindFreeSlot = node.constructor.prototype.findFreeSlotOfType;
            if (_origFindFreeSlot) {
                node.findFreeSlotOfType = function (type, isOutput, opts) {
                    if (isOutput) return _origFindFreeSlot.call(this, type, isOutput, opts);
                    const faked = [];
                    for (const s of this.inputs || []) {
                        if (s._eclipse_hidden && s.link == null) { s.link = -1; faked.push(s); }
                    }
                    const idx = _origFindFreeSlot.call(this, type, isOutput, opts);
                    for (const s of faked) s.link = null;
                    return idx;
                };
            }
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
    return {
        // Mark next visibility batch as user-driven (disconnects hidden linked slots).
        // Call synchronously before updateVisibility() — no debounce needed.
        markUserDriven() { userDriven = true; },
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
            // Slot-level bookkeeping is classic-only.  Vue renders widgets as
            // DOM, not canvas, so the _eclipse_hidden / slot.draw stub are
            // never consulted in Vue mode.
            if (!isVueMode()) {
                const slot = node.inputs?.find((s) => s.widget?.name === name);
                if (slot) {
                    if (!visible) {
                        // Only disconnect on user-driven changes (widget callback),
                        // not during onNodeCreated / onConfigure / workflow restore.
                        if (userDriven && slot.link != null) {
                            const slotIdx = node.inputs.indexOf(slot);
                            if (slotIdx !== -1) node.disconnectInput(slotIdx);
                        }
                        slot._eclipse_hidden = true;
                        slot.draw = () => {};
                    } else {
                        delete slot._eclipse_hidden;
                        delete slot.draw;
                    }
                }
            }
            if (loadMode || isConfiguringGraph()) {
                // No notify — Vue's first render will pick up options.hidden.
                // Covers both manual callers (loadMode) and native workflow
                // load window (app.configuringGraph, set by frontend's
                // LGraph.configure wrapper).
                userDriven = false;
                return;
            }
            // P1: Classic mode doesn't need Vue reactivity.  LiteGraph reads
            // widget.hidden directly on every draw and redraws via the dirty
            // canvas flag.  The pop/push reactivity nudge is pure overhead.
            if (!isVueMode()) {
                userDriven = false;
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
                    userDriven = false;
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

function _getNodeElement(node) {
    if (node._eclipse_el?.isConnected) return node._eclipse_el;
    if (null == node.id) return null;
    node._eclipse_el = document.querySelector(`[data-node-id="${node.id}"]`);
    return node._eclipse_el;
}

function _applyResize(node, minW, minH, padding) {
    if (node.flags?.collapsed) return;
    const curW = node.size[0];
    const curH = node.size[1];
    node.size[1] = 0;
    const computed = node.computeSize();
    const newH = Math.max(computed[1], minH) + padding;
    if (newH !== curH) {
        node.setSize?.([curW, newH]);
    } else {
        node.size[1] = curH;
    }
    // CSS var override only applies once the DOM element is mounted.
    // During cold Vue workflow loads the element may not exist yet;
    // the trailing rAF pass in smartResize() retries until it does.
    const el = _getNodeElement(node);
    if (el) {
        el.style.setProperty('--node-height', `${node.size[1]}px`);
        el.style.setProperty('--node-width', `${curW}px`);
    }
    node.graph?.setDirtyCanvas?.(true, false);
}
export function patchNodeCSSSize(node) {
    _perfTrack('patchNodeCSSSize');
    if (node.flags?.collapsed) return;
    const el = _getNodeElement(node);
    if (el) {
        el.style.setProperty('--node-height', `${node.size[1]}px`);
        el.style.setProperty('--node-width', `${node.size[0]}px`);
    }
}
export function smartResize(node, {
    minWidth = 259,
    minHeight = 100,
    padding = 0
} = {}) {
    _perfTrack('smartResize');
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
    if (node._smartResizePending) return;
    node._smartResizePending = true;
    if (!isVueMode()) {
        const runClassic = () => {
            // Same load-window gate as vue path: don't override the
            // workflow's serialized node.size while it's still being
            // restored.
            if (isConfiguringGraph()) {
                requestAnimationFrame(runClassic);
                return;
            }
            node._smartResizePending = false;
            _applyResize(node, minWidth, minHeight, padding);
        };
        requestAnimationFrame(runClassic);
        return;
    }
    // Vue mode: defer everything to rAF. Running setSize/setDirtyCanvas
    // synchronously while Vue is still flushing its initial mount can be
    // clobbered by Vue's reactivity pass. Instead, wait for the DOM
    // element to exist AND for the computed size to stabilize (two
    // consecutive identical readings), then apply once.
    const MAX_FRAMES = 60;  // ~1s @ 60fps
    let frames = 0;
    let lastComputedH = -1;
    let stableCount = 0;
    const tryResize = () => {
        // Wait out the workflow-load window — the frontend restores
        // serialized node.size AFTER onConfigure fires; applying a
        // computed resize here would override the user's saved size.
        // Resume probing once configuringGraph clears.
        if (isConfiguringGraph()) {
            if (++frames >= MAX_FRAMES) {
                node._smartResizePending = false;
                return;
            }
            requestAnimationFrame(tryResize);
            return;
        }
        if (!_getNodeElement(node)) {
            if (++frames >= MAX_FRAMES) {
                node._smartResizePending = false;
                return;
            }
            requestAnimationFrame(tryResize);
            return;
        }
        // Element exists — probe computed height without mutating node.size
        // across multiple frames until stable.
        const prevSizeH = node.size[1];
        node.size[1] = 0;
        const computed = node.computeSize()[1];
        node.size[1] = prevSizeH;
        if (computed === lastComputedH) {
            stableCount++;
        } else {
            stableCount = 0;
            lastComputedH = computed;
        }
        if (stableCount >= 1 || ++frames >= MAX_FRAMES) {
            node._smartResizePending = false;
            _applyResize(node, minWidth, minHeight, padding);
            return;
        }
        requestAnimationFrame(tryResize);
    };
    requestAnimationFrame(tryResize);
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
    removeSocketlessInputs: removeSocketlessInputs,
};
