import { app } from './comfy/index.js';

const ECLIPSE_TEXT_NODES = new Set([
    'String Multiline [Eclipse]',
    'String Multiline List [Eclipse]',
    'String Dual [Eclipse]',
]);

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
}`;
    document.head.appendChild(s);
})();

app.registerExtension({
    name: 'Eclipse.StringNodes',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (!ECLIPSE_TEXT_NODES.has(nodeData.name)) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated?.apply(this, arguments);
            for (const w of this.widgets || []) {
                const el = w.element;
                if (el?.tagName === 'TEXTAREA') {
                    el.classList.add('eclipse-textarea');
                }
            }
            return ret;
        };
    },
});
