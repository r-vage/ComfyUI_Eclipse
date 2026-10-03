/**
 * Pass-local widget/slot lookups for Eclipse-owned backend nodes.
 * Copyright (c) 2026 r-vage. MIT License.
 */
import { app } from './comfy/index.js';

const activePasses = new WeakMap();
const installed = new WeakSet();

function hasCustomAccessor(widget, key) {
    const own = Object.getOwnPropertyDescriptor(widget, key);
    if (!own?.get && !own?.set) return false;
    // ComfyUI mirrors native BaseWidget accessors onto each instance. Their
    // identity is retained; a custom disabled predicate has a different getter.
    for (let proto = Object.getPrototypeOf(widget); proto; proto = Object.getPrototypeOf(proto)) {
        const inherited = Object.getOwnPropertyDescriptor(proto, key);
        if (inherited) return own.get !== inherited.get || own.set !== inherited.set;
    }
    return true;
}

function createIndex(node) {
    const widgets = node.widgets;
    const inputs = node.inputs;
    if (!Array.isArray(widgets) || !Array.isArray(inputs)) return null;
    const widgetMap = new Map();
    const slotMap = new Map();
    for (let i = 0; i < widgets.length; i++) {
        const widget = widgets[i];
        if (!widget || typeof widget.name !== 'string') return null;
        if (!widgetMap.has(widget.name)) widgetMap.set(widget.name, { value: widget, index: i });
    }
    for (let i = 0; i < inputs.length; i++) {
        const slot = inputs[i];
        if (!slot) return null;
        if (slot.widget && !slotMap.has(slot.widget.name)) {
            slotMap.set(slot.widget.name, { value: slot, index: i });
        }
    }
    return { widgets, inputs, widgetCount: widgets.length, inputCount: inputs.length, widgetMap, slotMap };
}

function currentIndex(node) {
    const pass = activePasses.get(node);
    if (!pass) return null;
    if (node.widgets !== pass.widgets || node.inputs !== pass.inputs ||
        node.widgets.length !== pass.widgetCount || node.inputs.length !== pass.inputCount) {
        activePasses.delete(node);
        return null;
    }
    return pass;
}

export function installNodeLookupPerformance(nodeType, nodeData, native = globalThis.LiteGraph?.LGraphNode?.prototype) {
    if (!/(?:^|\.)comfyui_eclipse(?:\.|$)/i.test(nodeData?.python_module ?? '') ||
        !native || installed.has(nodeType)) return;
    const proto = nodeType.prototype;
    const getWidget = native.getWidgetFromSlot;
    const getSlot = native.getSlotFromWidget;
    // A pack/extension override owns its semantics. Never bypass it.
    if (typeof getWidget !== 'function' || typeof getSlot !== 'function' ||
        proto.getWidgetFromSlot !== getWidget || proto.getSlotFromWidget !== getSlot) return;
    installed.add(nodeType);

    proto.getWidgetFromSlot = function (slot) {
        const pass = currentIndex(this);
        const entry = slot?.widget && pass?.widgetMap.get(slot.widget.name);
        if (entry && pass.widgets[entry.index] === entry.value && entry.value.name === slot.widget.name) {
            // Native sizing completes all input lookups before invoking widget
            // size callbacks. Never serve their later/reentrant lookups from
            // the earlier snapshot.
            if (pass.kind === 'computeSize') {
                const slotEntry = pass.slotMap.get(slot.widget.name);
                if (!slotEntry || slotEntry.value !== slot || slotEntry.index <= pass.lastInput) {
                    activePasses.delete(this);
                    return getWidget.apply(this, arguments);
                }
                pass.lastInput = slotEntry.index;
                if (slotEntry.index === pass.inputCount - 1) activePasses.delete(this);
            }
            return entry.value;
        }
        return getWidget.apply(this, arguments);
    };
    proto.getSlotFromWidget = function (widget) {
        const pass = currentIndex(this);
        const entry = widget && pass?.slotMap.get(widget.name);
        if (entry && pass.inputs[entry.index] === entry.value && entry.value.widget?.name === widget.name) {
            return entry.value;
        }
        return getSlot.apply(this, arguments);
    };

    for (const kind of ['computeSize', 'updateComputedDisabled', 'drawSlots']) {
        const original = proto[kind];
        if (typeof original !== 'function' || original !== native[kind]) continue;
        proto[kind] = function () {
            // Nested calls may follow an extension callback that changed names
            // without changing array lengths. Give each native pass a fresh
            // snapshot and discard the outer snapshot on return.
            activePasses.delete(this);
            // Small nodes do not amortize the compatibility checks and maps.
            if ((this.widgets?.length ?? 0) < 16 || (this.inputs?.length ?? 0) < 16) {
                return original.apply(this, arguments);
            }
            let index = null;
            const nativeLookups = this.getWidgetFromSlot === proto.getWidgetFromSlot &&
                this.getSlotFromWidget === proto.getSlotFromWidget;
            const nativeQueries = this.isInputConnected === native.isInputConnected &&
                this._isMouseOverWidget === native._isMouseOverWidget &&
                this._isMouseOverSlot === native._isMouseOverSlot;
            // Custom slot drawing can mutate the next slot/widget in place.
            // Hidden guards installed by our visibility helper are inert.
            const customDraw = kind === 'drawSlots' && [...(this.inputs || []), ...(this.outputs || [])].some(
                (slot) => Object.hasOwn(slot, 'draw') && slot.draw !== slot._eclipse_hiddenDraw
            );
            const customDisabled = kind === 'updateComputedDisabled' && this.widgets?.some(
                (widget) => hasCustomAccessor(widget, 'computedDisabled') || hasCustomAccessor(widget, 'disabled')
            );
            const customSize = kind === 'computeSize' && (this.onComputeSize || this.widgets?.some(
                (widget) => Object.hasOwn(widget, 'computeSize') || Object.hasOwn(widget, 'computeLayoutSize')
            ));
            if (nativeLookups && nativeQueries && !customDraw && !customDisabled && !customSize) index = createIndex(this);
            if (index) activePasses.set(this, { ...index, kind, lastInput: -1 });
            try { return original.apply(this, arguments); }
            finally {
                activePasses.delete(this);
            }
        };
    }
}

app.registerExtension({
    name: 'Eclipse.NodeLookupPerformance',
    beforeRegisterNodeDef(nodeType, nodeData) {
        installNodeLookupPerformance(nodeType, nodeData);
    },
});
