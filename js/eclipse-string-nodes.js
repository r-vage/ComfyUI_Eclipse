import { app } from './comfy/index.js';
import { onVueModeChange } from './eclipse-widget-performance-utils.js';

const ECLIPSE_TEXT_NODES = new Set([
    'String Multiline [Eclipse]',
    'String Multiline List [Eclipse]',
    'String Dual [Eclipse]',
    'Wildcard Processor List [Eclipse]',
]);
const WRAPPABLE_TEXT_NODES = new Set([
    'String Multiline [Eclipse]',
    'String Multiline List [Eclipse]',
    'Wildcard Processor List [Eclipse]',
]);
const WRAP_PROPERTY = 'eclipse_wrap_long_lines';
const trackedNodes = new Set();
let textareaObserver = null;
let syncPending = false;

(function injectCSS() {
    if (document.getElementById('eclipse-textarea-styles')) return;
    const s = document.createElement('style');
    s.id = 'eclipse-textarea-styles';
    s.textContent = `
textarea.eclipse-textarea {
    font-family: monospace;
    font-size: 12px;
    padding: 6px;
    border-radius: 4px;
    overflow-y: auto;
}
textarea.eclipse-textarea.eclipse-textarea-nowrap {
    white-space: pre;
    overflow-x: auto;
    overflow-y: auto;
}`;
    document.head.appendChild(s);
})();

function addTextareasFromElement(element, textareas) {
    if (!element) return;
    if (element.tagName === 'TEXTAREA') textareas.add(element);
    for (const textarea of element.querySelectorAll?.('textarea') || []) {
        textareas.add(textarea);
    }
}

function getNodeTextareas(node) {
    const textareas = new Set();
    for (const widget of node.widgets || []) {
        addTextareasFromElement(widget.element, textareas);
    }

    const nodeId = String(node.id);
    for (const element of document.querySelectorAll?.('.lg-node[data-node-id]') || []) {
        if (element.getAttribute?.('data-node-id') === nodeId) {
            addTextareasFromElement(element, textareas);
        }
    }
    return textareas;
}

function wrapLongLines(node) {
    return node.properties?.[WRAP_PROPERTY] !== false;
}

function applyTextareaAppearance(node, allowWrapToggle) {
    const shouldWrap = !allowWrapToggle || wrapLongLines(node);
    for (const textarea of getNodeTextareas(node)) {
        textarea.classList.add('eclipse-textarea');
        if (allowWrapToggle) {
            const wrapMode = shouldWrap ? 'soft' : 'off';
            textarea.wrap = wrapMode;
            textarea.setAttribute?.('wrap', wrapMode);
            textarea.classList.toggle('eclipse-textarea-nowrap', !shouldWrap);
        }
    }
}

function syncTrackedNodes() {
    syncPending = false;
    for (const node of trackedNodes) {
        applyTextareaAppearance(node, true);
    }
}

function scheduleTrackedNodeSync() {
    if (syncPending) return;
    syncPending = true;
    queueMicrotask(syncTrackedNodes);
}

function mutationContainsTextarea(records) {
    for (const record of records) {
        for (const element of record.addedNodes || []) {
            if (element.tagName === 'TEXTAREA' || element.querySelector?.('textarea')) {
                return true;
            }
        }
    }
    return false;
}

function startTextareaObserver() {
    if (textareaObserver || typeof MutationObserver !== 'function') return;
    const observerTarget = document.documentElement;
    if (!observerTarget) return;
    textareaObserver = new MutationObserver((records) => {
        if (mutationContainsTextarea(records)) scheduleTrackedNodeSync();
    });
    textareaObserver.observe(observerTarget, { childList: true, subtree: true });
}

app.registerExtension({
    name: 'Eclipse.StringNodes',
    setup() {
        startTextareaObserver();
        onVueModeChange(scheduleTrackedNodeSync);
    },
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (!ECLIPSE_TEXT_NODES.has(nodeData.name)) return;
        const allowWrapToggle = WRAPPABLE_TEXT_NODES.has(nodeData.name);
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated?.apply(this, arguments);
            if (allowWrapToggle) {
                if (!this.properties) this.properties = {};
                if (typeof this.properties[WRAP_PROPERTY] !== 'boolean') {
                    this.properties[WRAP_PROPERTY] = true;
                }
                trackedNodes.add(this);
            }
            applyTextareaAppearance(this, allowWrapToggle);
            return ret;
        };

        if (!allowWrapToggle) return;

        const origOnConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const ret = origOnConfigure?.apply(this, arguments);
            if (!this.properties) this.properties = {};
            if (typeof this.properties[WRAP_PROPERTY] !== 'boolean') {
                this.properties[WRAP_PROPERTY] = true;
            }
            trackedNodes.add(this);
            applyTextareaAppearance(this, true);
            scheduleTrackedNodeSync();
            return ret;
        };

        const origGetExtraMenuOptions = nodeType.prototype.getExtraMenuOptions;
        nodeType.prototype.getExtraMenuOptions = function (_canvas, options) {
            origGetExtraMenuOptions?.apply(this, arguments);
            const node = this;
            const enabled = wrapLongLines(node);
            options.unshift(
                {
                    content: `${enabled ? '✓ ' : '\u2003'}Wrap long lines`,
                    callback: () => {
                        if (!node.properties) node.properties = {};
                        const nextValue = !wrapLongLines(node);
                        node.properties[WRAP_PROPERTY] = nextValue;
                        applyTextareaAppearance(node, true);
                        node.setDirtyCanvas?.(true, true);
                    },
                },
                null
            );
            return options;
        };

        const origOnRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            trackedNodes.delete(this);
            return origOnRemoved?.apply(this, arguments);
        };
    },
    nodeCreated(node) {
        if (!WRAPPABLE_TEXT_NODES.has(node.type)) return;
        trackedNodes.add(node);
        applyTextareaAppearance(node, true);
    },
    loadedGraphNode(node) {
        if (!WRAPPABLE_TEXT_NODES.has(node.type)) return;
        trackedNodes.add(node);
        applyTextareaAppearance(node, true);
        scheduleTrackedNodeSync();
    },
});
