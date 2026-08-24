import { isVueMode } from './eclipse-widget-performance-utils.js';

const _inlineInputControllers = new WeakMap();
let _inlineCommitListenersInstalled = false;

function _installInlineCommitListeners() {
    if (_inlineCommitListenersInstalled || typeof document === 'undefined') return;
    _inlineCommitListenersInstalled = true;

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' || event.isComposing) return;
        _inlineInputControllers.get(event.target)?.commit('enter');
    }, true);

    document.addEventListener('focusout', (event) => {
        const controller = _inlineInputControllers.get(event.target);
        if (!controller) return;
        controller.commit('focusout');
        _inlineInputControllers.delete(event.target);
    }, true);
}

function _bindActiveInlineInput(controller) {
    if (typeof document === 'undefined') return false;
    const activeElement = document.activeElement;
    if (!activeElement?.getAttribute) return false;
    if (activeElement.getAttribute('aria-label') !== controller.widget.name) return false;
    const nodeElement = activeElement.closest?.('[data-node-id]');
    const renderedNodeId = nodeElement?.getAttribute?.('data-node-id')
        ?? nodeElement?.dataset?.nodeId;
    if (renderedNodeId != null && String(renderedNodeId) !== String(controller.node.id)) {
        return false;
    }
    _inlineInputControllers.set(activeElement, controller);
    return true;
}

function _installNodeLifecycle(node) {
    if (node._eclipseCommittedTextControllers) return;
    node._eclipseCommittedTextControllers = new Set();

    const origSerialize = node.serialize;
    if (typeof origSerialize === 'function') {
        node.serialize = function (...args) {
            for (const controller of this._eclipseCommittedTextControllers || []) {
                controller.commit('serialize');
            }
            return origSerialize.apply(this, args);
        };
    }

    const origOnConfigure = node.onConfigure;
    node.onConfigure = function (...args) {
        const result = origOnConfigure?.apply(this, args);
        for (const controller of this._eclipseCommittedTextControllers || []) {
            controller.syncCommittedValue(controller.widget.value, 'configure');
        }
        return result;
    };
}

/**
 * Add a text widget whose visible value can change freely while Nodes 2.0 is
 * editing it, but whose side effects run only on Enter, focus leave, or save.
 * Classic canvas already exposes text through a modal, so its callback is the
 * commit boundary and retains the native Enter/OK behavior.
 */
export function addCommittedTextWidget(node, name, initialValue, onCommit, options = {}) {
    _installInlineCommitListeners();
    _installNodeLifecycle(node);

    let committing = false;
    let committedValue = String(initialValue ?? '');
    let widget;

    const controller = {
        get committedValue() {
            return committedValue;
        },
        get node() {
            return node;
        },
        get widget() {
            return widget;
        },
        commit(reason = 'explicit') {
            if (committing || !widget) return false;
            const draftValue = String(widget.value ?? '');
            if (draftValue === committedValue) return false;

            committing = true;
            try {
                const result = onCommit?.(draftValue, {
                    previousValue: committedValue,
                    reason,
                    widget,
                });
                const finalValue = String((result === undefined ? widget.value : result) ?? '');
                widget.value = finalValue;
                committedValue = finalValue;
                return true;
            } finally {
                committing = false;
            }
        },
        syncCommittedValue(value = widget?.value, reason = 'external') {
            const finalValue = String(value ?? '');
            if (widget) widget.value = finalValue;
            committedValue = finalValue;
            options.onSync?.(finalValue, { reason, widget });
        },
    };

    widget = node.addWidget('text', name, committedValue, (value) => {
        if (!widget) return;
        widget.value = String(value ?? '');
        if (!isVueMode() || !_bindActiveInlineInput(controller)) {
            controller.commit(isVueMode() ? 'programmatic' : 'classic');
        }
    }, options.widgetOptions || {});
    widget._eclipseCommittedText = controller;
    widget.serializeValue = () => {
        controller.commit('serialize');
        return widget.value;
    };
    node._eclipseCommittedTextControllers.add(controller);
    options.onSync?.(committedValue, { reason: 'initial', widget });
    return widget;
}
