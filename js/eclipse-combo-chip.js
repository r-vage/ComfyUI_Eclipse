import {
    isVueMode,
    onVueModeChange
} from './eclipse-widget-performance-utils.js';
const WIDGET_MARGIN = 2;
const WIDGET_BOTTOM_PAD = 4;
const TRIGGER_HEIGHT = 24;
const WIDGET_TOTAL_HEIGHT = TRIGGER_HEIGHT + 2 * WIDGET_MARGIN + WIDGET_BOTTOM_PAD;
const VUE_TRIGGER_HEIGHT = TRIGGER_HEIGHT + 5;
const VUE_TRIGGER_BOTTOM_MARGIN = WIDGET_TOTAL_HEIGHT - VUE_TRIGGER_HEIGHT;
export function injectComboChipCSS(prefix) {
    const styleId = `eclipse-combo-chip-css-${prefix || 'default'}`;
    if (document.getElementById(styleId)) return;
    const p = prefix ? `${prefix}-` : '';
    const style = document.createElement('style');
    style.id = styleId;
    style.textContent = `
.eclipse-${p}chip {
    cursor: pointer;
    padding: 2px 7px;
    border-radius: 4px;
    font-size: 0.95rem;
    font-family: sans-serif;
    user-select: none;
    white-space: nowrap;
    transition: background 0.15s, color 0.15s, border-color 0.15s;
    background: #2a2a2a;
    color: #888;
    border: 1px solid #444;
}
.eclipse-${p}chip:hover {
    background: #363636;
    border-color: #666;
}
.eclipse-${p}chip.selected {
    background: #2a5a3a;
    color: #ddd;
    border-color: #4a8a5a;
}
.eclipse-${p}chip.selected:hover {
    background: #356b46;
}
.eclipse-${p}chip.disabled {
    opacity: 0.35;
    cursor: not-allowed;
    border-style: dashed;
}
.eclipse-${p}combo-trigger {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: calc(100% - 26px);
    height: 24px;
    margin: 0 auto 4px auto;
    padding: 0 10px;
    background: #1a3324;
    border: 1px solid #2a5a3a;
    border-radius: 4px;
    color: #aaa;
    font-size: 0.75rem;
    font-family: sans-serif;
    cursor: pointer;
    box-sizing: border-box;
    user-select: none;
}
.eclipse-${p}combo-trigger:hover {
    border-color: #3a6a4a;
    background: #213d2c;
}
.eclipse-${p}combo-trigger .arrow {
    font-size: 0.65rem;
    margin-left: 6px;
    color: #888;
}
.eclipse-${p}chip-panel {
    position: fixed;
    z-index: 100000;
    background: #1e1e1e;
    border: 1px solid #555;
    border-radius: 6px;
    padding: 6px;
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.5);
}
@keyframes eclipse-${p}chip-pulse {
    0%   { background: #2a5a3a; color: #ddd; border-color: #4a8a5a; }
    100% { background: #2a2a2a; color: #888; border-color: #444; }
}
.eclipse-${p}chip.momentary-pulse {
    animation: eclipse-${p}chip-pulse 0.35s ease-out;
}
`;
    document.head.appendChild(style);
}

function findRadioGroup(feature, radioGroups) {
    if (!radioGroups) return null;
    for (const group of radioGroups) {
        if (group.includes(feature)) return group;
    }
    return null;
}
export function createComboChipWidget(config) {
    const {
        node,
        options: rawOptions,
        savedValue,
        origIdx,
        widgetName = 'features',
        cssPrefix = '',
        radioGroups = null,
        radioToggle = false,
        serialize = true,
        onSelectionChange = null,
        disabledChips = null,
        momentaryChips = null,
    } = config;
    const tooltipMap = new Map();
    const featureOptions = rawOptions.map((raw) => {
        if (typeof raw === 'string') return raw;
        if (raw.tooltip) tooltipMap.set(raw.label, raw.tooltip);
        return raw.label;
    });
    const p = cssPrefix ? `${cssPrefix}-` : '';
    const momentarySet = new Set(momentaryChips);
    let selectedSet = new Set(savedValue);
    for (const m of momentarySet) selectedSet.delete(m);
    let disabledSet = new Set(disabledChips);
    let panel = null;
    for (const d of disabledSet) selectedSet.delete(d);
    const trigger = document.createElement('div');
    trigger.className = `eclipse-${p}combo-trigger`;
    Object.assign(trigger.style, {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        height: '24px',
        padding: '0 10px',
        background: '#1a3324',
        border: '1px solid #2a5a3a',
        borderRadius: '4px',
        color: '#aaa',
        fontSize: '0.75rem',
        fontFamily: 'sans-serif',
        cursor: 'pointer',
        boxSizing: 'border-box',
        userSelect: 'none',
    });

    function applyTriggerLayout() {
        if (isVueMode()) {
            trigger.style.width = '100%';
            trigger.style.margin = `0 0 ${VUE_TRIGGER_BOTTOM_MARGIN}px 0`;
            trigger.style.height = `${VUE_TRIGGER_HEIGHT}px`;
            trigger.style.minHeight = `${VUE_TRIGGER_HEIGHT}px`;
            // Nodes 2.0's WidgetDOM wrapper applies `flex: 1 1 0%` to its
            // child, which otherwise overrides this element's fixed height.
            trigger.style.flex = '0 0 auto';
        } else {
            trigger.style.width = 'calc(100% - 26px)';
            trigger.style.margin = '0 auto 4px auto';
            trigger.style.height = `${TRIGGER_HEIGHT}px`;
            trigger.style.minHeight = '';
            trigger.style.flex = '';
        }
    }
    applyTriggerLayout();

    function updateLabel() {
        let n = 0;
        for (const s of selectedSet) { if (!momentarySet.has(s)) n++; }
        trigger.innerHTML = `<span>${n} item${n !== 1 ? 's' : ''} selected</span><span class="arrow">▼</span>`;
    }
    updateLabel();
    let featWidget;

    function closePanel() {
        if (panel) {
            panel.remove();
            panel = null;
        }
    }

    function openPanel() {
        if (panel) {
            closePanel();
            return;
        }
        panel = document.createElement('div');
        panel.className = `eclipse-${p}chip-panel`;
        for (const opt of featureOptions) {
            const chip = document.createElement('span');
            const isDisabled = disabledSet.has(opt);
            chip.className = `eclipse-${p}chip` +
                (selectedSet.has(opt) ? ' selected' : '') +
                (isDisabled ? ' disabled' : '');
            chip.textContent = opt;
            if (tooltipMap.has(opt)) chip.title = tooltipMap.get(opt);
            chip.addEventListener('pointerdown', (e) => {
                e.stopPropagation();
                e.preventDefault();
                if (disabledSet.has(opt)) return;
                if (momentarySet.has(opt)) {
                    chip.classList.add('momentary-pulse');
                    chip.addEventListener('animationend', () => chip.classList.remove('momentary-pulse'), { once: true });
                    featWidget.callback?.({ momentary: opt });
                    return;
                }
                const radioGroup = findRadioGroup(opt, radioGroups);
                if (radioGroup) {
                    if (selectedSet.has(opt)) {
                        if (radioToggle) selectedSet.delete(opt);
                        else return;
                    } else {
                        for (const sibling of radioGroup) selectedSet.delete(sibling);
                        selectedSet.add(opt);
                    }
                } else {
                    if (selectedSet.has(opt)) selectedSet.delete(opt);
                    else selectedSet.add(opt);
                }
                for (const c of panel.children) {
                    c.classList.toggle('selected', selectedSet.has(c.textContent));
                    c.classList.toggle('disabled', disabledSet.has(c.textContent));
                }
                updateLabel();
                featWidget.callback?.(featWidget.value);
            });
            panel.appendChild(chip);
        }
        const rect = trigger.getBoundingClientRect();
        // Use a fixed panel width so chip layout (row count, wrapping) stays
        // identical regardless of canvas zoom. `rect.width` reflects the
        // zoomed trigger width and would otherwise make the panel narrow at
        // low zoom (many rows) and wide at high zoom (single row).
        const PANEL_WIDTH = 320;
        panel.style.left = `${rect.left}px`;
        panel.style.top = `${rect.bottom + 2}px`;
        panel.style.width = `${PANEL_WIDTH}px`;
        document.body.appendChild(panel);
        const pr = panel.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        if (pr.right > vw) {
            panel.style.left = `${Math.max(0, vw - pr.width - 4)}px`;
        }
        if (pr.bottom > vh) {
            const above = rect.top - 2 - pr.height;
            panel.style.top = `${above >= 0 ? above : Math.max(0, vh - pr.height - 4)}px`;
        }
        const onOutside = (e) => {
            if (panel && !panel.contains(e.target) && !trigger.contains(e.target)) {
                closePanel();
                document.removeEventListener('pointerdown', onOutside, true);
            }
        };
        requestAnimationFrame(() => {
            document.addEventListener('pointerdown', onOutside, true);
        });
    }
    trigger.addEventListener('pointerdown', (e) => {
        e.stopPropagation();
        e.preventDefault();
        openPanel();
    });
    const widgetOpts = {
        getValue: () => [...selectedSet],
        setValue: (v) => {
            let arr;
            if (typeof v === 'string') {
                arr = v.split(',').map((s) => s.trim()).filter(Boolean);
            } else {
                arr = Array.isArray(v) ? v : [];
            }
            selectedSet = new Set(arr.filter((x) => featureOptions.includes(x) && !disabledSet.has(x) && !momentarySet.has(x)));
            updateLabel();
            if (panel) {
                for (const c of panel.children) {
                    c.classList.toggle('selected', selectedSet.has(c.textContent));
                    c.classList.toggle('disabled', disabledSet.has(c.textContent));
                }
            }
        },
        getMinHeight: () => WIDGET_TOTAL_HEIGHT,
        getMaxHeight: () => WIDGET_TOTAL_HEIGHT,
        margin: WIDGET_MARGIN,
    };
    if (!serialize) widgetOpts.serialize = false;
    featWidget = node.addDOMWidget(widgetName, 'custom', trigger, widgetOpts);
    featWidget.computedHeight = WIDGET_TOTAL_HEIGHT;
    // In Vue Nodes 2.0, DOMWidgetImpl.computeLayoutSize makes the widget claim
    // layout space in `_arrangeWidgets` / `distributeSpace`. Even with min==max,
    // some frontend versions (e.g. 1.42.11) allocate extra vertical space to
    // this widget instead of the preview/textarea, visibly stretching the chip
    // bar. Overriding to undefined forces the chip into the fixed-height else
    // branch so it stays at WIDGET_TOTAL_HEIGHT. Classic mode keeps
    // computeLayoutSize so the canvas renderer sizes the DOM container correctly.
    if (isVueMode()) {
        featWidget.computeLayoutSize = undefined;
    }
    const unsubMode = onVueModeChange(applyTriggerLayout);
    const origOnRemove = featWidget.onRemove;
    featWidget.onRemove = function () {
        closePanel();
        unsubMode();
        origOnRemove?.call(this);
    };
    const newIdx = node.widgets.indexOf(featWidget);
    if (newIdx >= 0 && newIdx !== origIdx) {
        node.widgets.splice(newIdx, 1);
        node.widgets.splice(origIdx, 0, featWidget);
    }
    if (serialize) {
        featWidget.serializeValue = () => [...selectedSet].join(',');
    }
    featWidget.setDisabledChips = (chips) => {
        disabledSet = new Set(chips);
        let changed = false;
        for (const d of disabledSet) {
            if (selectedSet.has(d)) {
                selectedSet.delete(d);
                changed = true;
            }
        }
        if (changed) {
            updateLabel();
            featWidget.callback?.(featWidget.value);
        }
        if (panel) {
            for (const c of panel.children) {
                c.classList.toggle('disabled', disabledSet.has(c.textContent));
                c.classList.toggle('selected', selectedSet.has(c.textContent));
            }
        }
    };
    return featWidget;
}
