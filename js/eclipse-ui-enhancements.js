import {
    app
} from './comfy/index.js';
import {
    isVueMode,
    onVueModeChange,
    patchNodeCSSSize,
    removeSocketlessInputs
} from './eclipse-widget-performance-utils.js';
import { adaptNestedMenuItems } from './eclipse-context-menu-utils.js';

const HIDE_NODE_STATE_BADGES_CLASS = 'eclipse-hide-node-state-badges';
const VUE_LOW_ZOOM_LOD_CLASS = 'eclipse-vue-low-zoom-lod';
const VUE_LOW_ZOOM_LOD_DS_KEY = Symbol.for('Eclipse.VueLowZoomLOD.DragAndScale');

let vueLowZoomLODEnabled = true;
let vueFullDetailZoom = 50;
let vueLowZoomLODActive;

function injectNodeStateBadgeStyles() {
    if (document.getElementById('eclipse-node-state-badge-styles')) return;
    const style = document.createElement('style');
    style.id = 'eclipse-node-state-badge-styles';
    const nodeBadge = [
        '.lg-node-header[data-testid^="node-header-"]',
        '> div[class~="flex"][class~="min-w-0"][class~="items-center"][class~="justify-between"]',
        '> div[class~="flex"][class~="min-w-max"][class~="items-center"][class~="rounded-sm"]' +
        '[class~="bg-node-component-surface"][class~="text-xs"]',
    ].join(' ');
    style.textContent = [
        `html.${HIDE_NODE_STATE_BADGES_CLASS} ${nodeBadge}:has(> i[class~="icon-[lucide--ban]"]) {`,
        '  display: none !important;',
        '}',
        `html.${HIDE_NODE_STATE_BADGES_CLASS} ${nodeBadge}:has(> i[class~="icon-[lucide--redo-dot]"]) {`,
        '  display: none !important;',
        '}',
    ].join('\n');
    document.head.appendChild(style);
}

function setNodeStateBadgesHidden(hidden) {
    document.documentElement.classList.toggle(HIDE_NODE_STATE_BADGES_CLASS, hidden);
}

function injectVueLowZoomLODStyles() {
    if (document.getElementById('eclipse-vue-low-zoom-lod-styles')) return;
    const root = `html.${VUE_LOW_ZOOM_LOD_CLASS}`;
    const node = `${root} .lg-node[data-node-id]:not([data-node-id^="preview-"])`;
    const style = document.createElement('style');
    style.id = 'eclipse-vue-low-zoom-lod-styles';
    style.textContent = [
        `${node} .lg-node-widget {`,
        '  visibility: hidden !important;',
        '  pointer-events: none !important;',
        '}',
        `${node} .lg-node-widget > [class~="opacity-100"] .lg-slot:not(.invisible) {`,
        '  visibility: visible !important;',
        '  pointer-events: auto !important;',
        '}',
        `${node} .lg-node-content,`,
        `${node} .image-preview,`,
        `${node} .video-preview,`,
        `${node} [data-testid^="node-body-"] > img,`,
        `${node} [data-testid^="node-body-"] > .text-center.text-xs,`,
        `${node} [data-testid^="node-body-"] > .text-pure-white.text-center,`,
        `${node} [data-testid^="node-body-"] > .mt-auto.h-5,`,
        `${node} .lg-node-header > div > :not(:first-child),`,
        `${root} .dom-widget,`,
        `${node} > :is(.cursor-se-resize, .cursor-ne-resize, .cursor-sw-resize, .cursor-nw-resize) {`,
        '  visibility: hidden !important;',
        '  pointer-events: none !important;',
        '}',
    ].join('\n');
    document.head.appendChild(style);
}

function normalizeVueFullDetailZoom(value) {
    const zoom = Number(value);
    return Number.isFinite(zoom) ? Math.min(100, Math.max(10, zoom)) : 50;
}

async function saveUIEnhancementConfig(values) {
    try {
        const resp = await fetch('/eclipse/config/update', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(values),
        });
        const result = await resp.json();
        if (!resp.ok || !result.success) {
            console.error('[Eclipse] Failed to update UI enhancement config:', result.error || resp.status);
        }
    } catch (err) {
        console.error('[Eclipse] Failed to update UI enhancement config:', err);
    }
}

function updateVueLowZoomLOD(appRef, scale = appRef.canvas?.ds?.scale) {
    const canvasScale = Number(scale);
    const lowDetail = vueLowZoomLODEnabled &&
        isVueMode() &&
        Number.isFinite(canvasScale) &&
        canvasScale < vueFullDetailZoom / 100;
    if (lowDetail === vueLowZoomLODActive) return;
    vueLowZoomLODActive = lowDetail;
    const root = document.documentElement;
    if (root.classList.contains(VUE_LOW_ZOOM_LOD_CLASS) !== lowDetail) {
        root.classList.toggle(VUE_LOW_ZOOM_LOD_CLASS, lowDetail);
    }
}

function installVueLowZoomLODWatcher(appRef) {
    const dragAndScale = appRef.canvas?.ds;
    if (!dragAndScale) {
        updateVueLowZoomLOD(appRef);
        return;
    }
    let controller = dragAndScale[VUE_LOW_ZOOM_LOD_DS_KEY];
    if (!controller) {
        controller = {
            evaluate: null,
            unsubscribeModeChange: null,
        };
        const nativeOnChanged = dragAndScale.onChanged;
        dragAndScale.onChanged = function (scale) {
            let result;
            try {
                result = nativeOnChanged?.apply(this, arguments);
            } finally {
                controller.evaluate?.(scale);
            }
            return result;
        };
        Object.defineProperty(dragAndScale, VUE_LOW_ZOOM_LOD_DS_KEY, {
            value: controller,
            configurable: true,
        });
    }
    controller.evaluate = (scale) => updateVueLowZoomLOD(appRef, scale);
    controller.unsubscribeModeChange?.();
    controller.unsubscribeModeChange = onVueModeChange(() => {
        controller.evaluate?.(appRef.canvas?.ds?.scale);
    });
    controller.evaluate(dragAndScale.scale);
}

function getElFunction() {
    return 'function' == typeof $el ? $el : function (tag, props, children) {
        let options = props;
        Array.isArray(props) && ((children = props), (options = {}));
        const [tagName, ...classes] = tag.split('.'), el = document.createElement(tagName);
        for (const cls of classes) el.classList.add(cls);
        if (options) {
            options.style && Object.assign(el.style, options.style);
            for (const [key, val] of Object.entries(options)) 'style' !== key && (key.startsWith('on') && 'function' == typeof val ? el.addEventListener(key.slice(2).toLowerCase(), val) : 'children' !== key && (el[key] = val));
        }
        if (children)
            for (const child of children) 'string' == typeof child ? el.appendChild(document.createTextNode(child)) : child instanceof Node && el.appendChild(child);
        return el;
    };
}

function rgbToHex(r, g, b) {
    return '#' + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
}
window.eclipse_rgb = (r, g, b) => {
    const hex = rgbToHex(Math.round(r), Math.round(g), Math.round(b));
    console.log(`rgb(${r}, ${g}, ${b}) → ${hex}`);
    return hex;
};

function shadeHexColor(hex, amount = -0.2) {
    hex.startsWith('#') && (hex = hex.slice(1));
    let r = parseInt(hex.slice(0, 2), 16),
        g = parseInt(hex.slice(2, 4), 16),
        b = parseInt(hex.slice(4, 6), 16);
    return ((r = Math.max(0, Math.min(255, r + 100 * amount))), (g = Math.max(0, Math.min(255, g + 100 * amount))), (b = Math.max(0, Math.min(255, b + 100 * amount))), rgbToHex(r, g, b));
}

function applyCustomColor(node, applyColor) {
    const canvas = LGraphCanvas.active_canvas,
        selectedNodes = canvas?.selected_nodes,
        targets = selectedNodes && Object.keys(selectedNodes).length > 1 ? Object.values(selectedNodes) : [node];
    targets.forEach(applyColor);
    if (canvas?.setDirty) canvas.setDirty(true, true);
    else node.setDirtyCanvas?.(true, true);
}

let customColorInput = null;
let customColorSession = null;

function getCustomColorTargets(node) {
    const selectedNodes = LGraphCanvas.active_canvas?.selected_nodes;
    return selectedNodes && Object.keys(selectedNodes).length > 1 ? Object.values(selectedNodes) : [node];
}

function updateCustomColorSession(value) {
    const session = customColorSession;
    if (!session) return;
    if (!session.started) {
        session.graph?.beforeChange?.();
        session.started = true;
    }
    for (const target of session.targets) session.applyColor(target, value);
    const canvas = LGraphCanvas.active_canvas;
    if (canvas?.setDirty) canvas.setDirty(true, true);
    else session.node.setDirtyCanvas?.(true, true);
}

function finishCustomColorSession() {
    const session = customColorSession;
    if (!session) return;
    customColorSession = null;
    if (session.started) session.graph?.afterChange?.();
}

function getCustomColorInput() {
    if (customColorInput) return customColorInput;
    customColorInput = document.createElement('input');
    customColorInput.type = 'color';
    customColorInput.style.cssText = 'position:fixed;left:50%;top:50%;width:1px;height:1px;opacity:0.01;pointer-events:none;';
    customColorInput.addEventListener('input', (event) => {
        updateCustomColorSession(event.target.value);
    });
    customColorInput.addEventListener('change', (event) => {
        updateCustomColorSession(event.target.value);
        finishCustomColorSession();
    });
    customColorInput.addEventListener('cancel', finishCustomColorSession);
    customColorInput.addEventListener('blur', () => {
        setTimeout(() => {
            if (customColorSession?.started) finishCustomColorSession();
        }, 0);
    });
    document.body.appendChild(customColorInput);
    return customColorInput;
}

function openCustomColorInput(node, applyColor) {
    finishCustomColorSession();
    const input = getCustomColorInput();
    customColorSession = {
        node,
        graph: node.graph,
        targets: getCustomColorTargets(node),
        applyColor,
        started: false,
    };
    input.value = /^#[0-9a-f]{6}$/i.test(node.bgcolor || '') ? node.bgcolor : '#000000';
    try {
        if (typeof input.showPicker === 'function') input.showPicker();
        else input.click();
    } catch {
        input.click();
    }
}
let afterChange;

function invokeAfterChange() {
    return afterChange?.apply(this, arguments);
}

function setColorMode(mode, appRef) {
    appRef.graph._nodes.forEach((node) => {
        node.bgcolor = node._bgcolor ?? node.bgcolor;
        node.color = node._color ?? node.color;
        node.setDirtyCanvas(true, true);
    });
}
let loading = false;
if ((app.registerExtension({
        name: 'Eclipse.ForceBoxNodes',
        async init(appRef) {
            appRef.ui.settings.addSetting({
                id: 'Eclipse.ForceBoxNodes',
                name: '📦 Eclipse Force Box Nodes',
                type: 'boolean',
                tooltip: 'Remove rounded corners - nodes will always be boxes.',
                defaultValue: false,
                onChange(val) {
                    appRef.canvas.round_radius = val ? 0 : 8;
                    appRef.graph.setDirtyCanvas(true, true);
                },
            });
        },
    }), app.registerExtension({
        name: 'Eclipse.LogLevel',
        async init(appRef) {
            let currentLevel = 'warning';
            try {
                const resp = await fetch('/eclipse/config/log_level');
                if (resp.ok) {
                    currentLevel = (await resp.json()).log_level || 'warning';
                }
            } catch (err) {
                console.error('[Eclipse] Failed to fetch log level:', err);
            }
            appRef.ui.settings.addSetting({
                id: 'Eclipse.LogLevel',
                name: '📝 Eclipse Log Level',
                type: 'combo',
                tooltip: 'Set the logging verbosity level. Changes are saved to config.json and applied immediately.\n\nerror: Only critical errors\nwarning: Errors + warnings\ninfo: Errors + warnings + general messages\ndebug: All messages including detailed debug info',
                defaultValue: currentLevel,
                options: ['error', 'warning', 'info', 'debug'],
                async onChange(val) {
                    try {
                        const resp = await fetch('/eclipse/config/log_level', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({
                                log_level: val
                            }),
                        });
                        if (resp.ok) {
                            const result = await resp.json();
                            result.success ? console.log(`[Eclipse] Log level changed to: ${val}`) : console.error('[Eclipse] Failed to update log level:', result.error);
                        } else console.error('[Eclipse] Server error updating log level:', resp.status);
                    } catch (err) {
                        console.error('[Eclipse] Failed to update log level:', err);
                    }
                },
            });
        },
    }), app.registerExtension({
        name: 'Eclipse.VueSizeFix',
        async init(appRef) {
            let currentVal = true;
            try {
                const resp = await fetch('/eclipse/config/all');
                if (resp.ok) {
                    currentVal = false !== (await resp.json()).vue_size_fix;
                }
            } catch (err) {
                console.error('[Eclipse] Failed to fetch vue_size_fix:', err);
            }
            let initialized = false;
            appRef.ui.settings.addSetting({
                id: 'Eclipse.VueSizeFix',
                name: '📐 Eclipse Vue Size Fix',
                type: 'boolean',
                tooltip: 'Keep compact and collapsed nodes at their intended width in the Vue renderer. Applies to fresh nodes, loaded workflows, and live renderer switching. Requires page reload after changing.',
                defaultValue: currentVal,
                async onChange(val) {
                    if (initialized)
                        try {
                            const resp = await fetch('/eclipse/config/update', {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/json'
                                },
                                body: JSON.stringify({
                                    vue_size_fix: val
                                }),
                            });
                            if (resp.ok) {
                                (await resp.json()).success && console.log(`[Eclipse] Vue size fix ${val ? 'enabled' : 'disabled'} (reload required)`, );
                            }
                        } catch (err) {
                            console.error('[Eclipse] Failed to update vue_size_fix:', err);
                        }
                    else initialized = true;
                },
            });
        },
    }), app.registerExtension({
        name: 'Eclipse.HideNodeStateBadges',
        async init(appRef) {
            injectNodeStateBadgeStyles();
            let configuredHidden = true;
            try {
                const resp = await fetch('/eclipse/config/all');
                if (resp.ok) {
                    const config = await resp.json();
                    if (typeof config.hide_node_state_badges === 'boolean') {
                        configuredHidden = config.hide_node_state_badges;
                    }
                }
            } catch (err) {
                console.error('[Eclipse] Failed to fetch node state badge config:', err);
            }
            let initialized = false;
            appRef.ui.settings.addSetting({
                id: 'Eclipse.HideNodeStateBadges',
                name: '🏷️ Eclipse Hide Node State Badges',
                type: 'boolean',
                tooltip: 'Hide the Muted and Bypassed badges from Nodes 2.0 headers while retaining the node state opacity and overlay. Applies immediately.',
                defaultValue: configuredHidden,
                async onChange(val) {
                    const hidden = val !== false;
                    setNodeStateBadgesHidden(hidden);
                    if (initialized) {
                        await saveUIEnhancementConfig({
                            hide_node_state_badges: hidden
                        });
                    }
                },
            });
            await appRef.ui.settings.setSettingValue?.(
                'Eclipse.HideNodeStateBadges',
                configuredHidden
            );
            initialized = true;
            setNodeStateBadgesHidden(configuredHidden);
        },
    }), app.registerExtension({
        name: 'Eclipse.VueLowZoomLOD',
        async init(appRef) {
            injectVueLowZoomLODStyles();
            let configuredEnabled = true;
            let configuredZoom = 50;
            try {
                const resp = await fetch('/eclipse/config/all');
                if (resp.ok) {
                    const config = await resp.json();
                    if (typeof config.vue_low_zoom_lod === 'boolean') {
                        configuredEnabled = config.vue_low_zoom_lod;
                    }
                    configuredZoom = normalizeVueFullDetailZoom(config.vue_full_detail_zoom);
                }
            } catch (err) {
                console.error('[Eclipse] Failed to fetch low-zoom LOD config:', err);
            }
            vueLowZoomLODEnabled = configuredEnabled;
            vueFullDetailZoom = configuredZoom;
            let initialized = false;
            appRef.ui.settings.addSetting({
                id: 'Eclipse.VueLowZoomLOD',
                name: '🔎 Eclipse Nodes 2.0 Low-Zoom LOD',
                type: 'boolean',
                tooltip: 'Reduce Nodes 2.0 painting below the full-detail zoom cutoff while keeping node shells, titles, sockets, links, and execution indicators visible. Applies immediately.',
                defaultValue: configuredEnabled,
                async onChange(val) {
                    vueLowZoomLODEnabled = val !== false;
                    updateVueLowZoomLOD(appRef);
                    if (initialized) {
                        await saveUIEnhancementConfig({
                            vue_low_zoom_lod: vueLowZoomLODEnabled
                        });
                    }
                },
            });
            appRef.ui.settings.addSetting({
                id: 'Eclipse.VueFullDetailZoom',
                name: '🔍 Eclipse Nodes 2.0 Full Detail Zoom',
                type: 'number',
                tooltip: 'Canvas zoom percentage at which Nodes 2.0 returns to full detail. Low detail is used only below this value. Applies immediately.',
                attrs: {
                    min: 10,
                    max: 100,
                    step: 5,
                },
                defaultValue: configuredZoom,
                async onChange(val) {
                    vueFullDetailZoom = normalizeVueFullDetailZoom(val);
                    updateVueLowZoomLOD(appRef);
                    if (initialized) {
                        await saveUIEnhancementConfig({
                            vue_full_detail_zoom: vueFullDetailZoom
                        });
                    }
                },
            });
            await appRef.ui.settings.setSettingValue?.(
                'Eclipse.VueLowZoomLOD',
                configuredEnabled
            );
            await appRef.ui.settings.setSettingValue?.(
                'Eclipse.VueFullDetailZoom',
                configuredZoom
            );
            vueLowZoomLODEnabled = configuredEnabled;
            vueFullDetailZoom = configuredZoom;
            initialized = true;
            updateVueLowZoomLOD(appRef);
        },
        async setup(appRef) {
            installVueLowZoomLODWatcher(appRef);
        },
    }), app.registerExtension({
        name: 'Eclipse.UseSliders',
        async init(appRef) {
            let currentVal = true;
            try {
                const resp = await fetch('/eclipse/config/all');
                if (resp.ok) {
                    currentVal = false !== (await resp.json()).use_sliders;
                }
            } catch (err) {
                console.error('[Eclipse] Failed to fetch use_sliders:', err);
            }
            let initialized = false;
            appRef.ui.settings.addSetting({
                id: 'Eclipse.UseSliders',
                name: '🎚️ Eclipse Use Sliders',
                type: 'boolean',
                tooltip: 'Show numeric inputs as sliders instead of plain number fields in Eclipse nodes (steps, cfg, guidance, denoise, etc.). Requires restart after changing.',
                defaultValue: currentVal,
                async onChange(val) {
                    if (initialized)
                        try {
                            const resp = await fetch('/eclipse/config/update', {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/json'
                                },
                                body: JSON.stringify({
                                    use_sliders: val
                                }),
                            });
                            if (resp.ok) {
                                (await resp.json()).success && console.log(`[Eclipse] Use sliders ${val ? 'enabled' : 'disabled'} (restart required)`, );
                            }
                        } catch (err) {
                            console.error('[Eclipse] Failed to update use_sliders:', err);
                        }
                    else initialized = true;
                },
            });
        },
    }), app.registerExtension({
        name: 'Eclipse.PreviewCullingSetting',
        async init(appRef) {
            let currentVal = true;
            try {
                const resp = await fetch('/eclipse/config/all');
                if (resp.ok) {
                    currentVal = false !== (await resp.json()).preview_culling;
                }
            } catch (err) {
                console.error('[Eclipse] Failed to fetch preview_culling:', err);
            }
            let initialized = false;
            appRef.ui.settings.addSetting({
                id: 'Eclipse.PreviewCulling',
                name: '👁️ Eclipse Preview Culling',
                type: 'boolean',
                tooltip: 'Skip rendering nodes that are fully hidden behind other nodes. Reduces draw calls for large workflows. Requires page reload after changing.',
                defaultValue: currentVal,
                async onChange(val) {
                    if (initialized)
                        try {
                            const resp = await fetch('/eclipse/config/update', {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/json'
                                },
                                body: JSON.stringify({
                                    preview_culling: val
                                }),
                            });
                            if (resp.ok) {
                                (await resp.json()).success && console.log(`[Eclipse] Preview culling ${val ? 'enabled' : 'disabled'} (reload required)`);
                            }
                        } catch (err) {
                            console.error('[Eclipse] Failed to update preview_culling:', err);
                        }
                    else initialized = true;
                },
            });
        },
    }), app.registerExtension({
        name: 'Eclipse.colors',
        async setup(appRef) {
            const colorMode = +(window.localStorage.getItem('Comfy.Settings.Eclipse.colors') ?? '0');
            appRef.graph._nodes.forEach((node) => {
                node._bgcolor = node._bgcolor ?? node.bgcolor;
                node._color = node._color ?? node.color;
            });
            setColorMode(colorMode, appRef);
        },
        loadedGraphNode(node, appRef) {
            node._bgcolor = node._bgcolor ?? node.bgcolor;
            node._color = node._color ?? node.color;
            if (!loading) {
                loading = true;
                setTimeout(function () {
                    loading = false;
                    setColorMode(+(window.localStorage.getItem('Comfy.Settings.Eclipse.colors') ?? '0'), appRef);
                }, 500);
            }
        },
        async init(appRef) {
            afterChange = appRef.graph.afterChange;
            appRef.graph.afterChange = invokeAfterChange;
            const origOnMenuNodeColors = LGraphCanvas.onMenuNodeColors;
            'function' == typeof origOnMenuNodeColors ? (LGraphCanvas.onMenuNodeColors = function (values, options, event, parentMenu, node) {
                const result = origOnMenuNodeColors.apply(this, arguments),
                    $el = getElFunction(),
                    menuRoot = parentMenu?.current_submenu?.root;
                if (!menuRoot) return (console.debug('[Eclipse.colors] Could not access menu submenu root'), result);
                const isGroup = node instanceof LGraphGroup;
                try {
                    if (!isGroup) {
                        menuRoot.append($el('div.litemenu-entry.submenu', [$el('label', {
                            style: {
                                position: 'relative',
                                overflow: 'hidden',
                                display: 'block',
                                paddingLeft: '4px',
                                borderLeft: '8px solid #222',
                            },
                        }, ['Custom Title', $el('input', {
                            type: 'color',
                            value: node.bgcolor,
                            style: {
                                position: 'absolute',
                                right: '200%'
                            },
                            oninput(ev) {
                                const titleColor = shadeHexColor(ev.target.value);
                                applyCustomColor(node, (target) => {
                                    target.color = titleColor;
                                });
                            },
                            onchange(ev) {
                                console.log(`[Eclipse] Title: ${node.color}`);
                            },
                        }), ], ), ]), );
                        menuRoot.append($el('div.litemenu-entry.submenu', [$el('label', {
                            style: {
                                position: 'relative',
                                overflow: 'hidden',
                                display: 'block',
                                paddingLeft: '4px',
                                borderLeft: '8px solid #222',
                            },
                        }, ['Custom BG', $el('input', {
                            type: 'color',
                            value: node.bgcolor,
                            style: {
                                position: 'absolute',
                                right: '200%'
                            },
                            oninput(ev) {
                                const backgroundColor = ev.target.value;
                                applyCustomColor(node, (target) => {
                                    target.bgcolor = backgroundColor;
                                });
                            },
                            onchange(ev) {
                                console.log(`[Eclipse] BG: ${ev.target.value}`);
                            },
                        }), ], ), ]), );
                        menuRoot.append($el('div.litemenu-entry.submenu', [$el('label', {
                            style: {
                                position: 'relative',
                                overflow: 'hidden',
                                display: 'block',
                                paddingLeft: '4px',
                                borderLeft: '8px solid #222',
                            },
                        }, ['Custom All', $el('input', {
                            type: 'color',
                            value: node.bgcolor,
                            style: {
                                position: 'absolute',
                                right: '200%'
                            },
                            oninput(ev) {
                                const backgroundColor = ev.target.value,
                                    titleColor = shadeHexColor(backgroundColor);
                                applyCustomColor(node, (target) => {
                                    target.bgcolor = backgroundColor;
                                    target.color = titleColor;
                                });
                            },
                            onchange(ev) {
                                console.log(`[Eclipse] All → BG: ${node.bgcolor}, Title: ${node.color}`);
                            },
                        }), ], ), ]), );
                    }
                    if (isGroup) {
                        menuRoot.append($el('div.litemenu-entry.submenu', [$el('label', {
                            style: {
                                position: 'relative',
                                overflow: 'hidden',
                                display: 'block',
                                paddingLeft: '4px',
                                borderLeft: '8px solid #222',
                            },
                        }, ['Color Group', $el('input', {
                            type: 'color',
                            value: node.bgcolor,
                            style: {
                                position: 'absolute',
                                right: '200%'
                            },
                            oninput(ev) {
                                node.bgcolor = ev.target.value;
                                node.color = shadeHexColor(node.bgcolor);
                                node.setDirtyCanvas(true, true);
                            },
                        }), ], ), ]), );
                        menuRoot.append($el('div.litemenu-entry.submenu', [$el('label', {
                            style: {
                                position: 'relative',
                                overflow: 'hidden',
                                display: 'block',
                                paddingLeft: '4px',
                                borderLeft: '8px solid #222',
                            },
                        }, ['Color All Title', $el('input', {
                            type: 'color',
                            value: node.bgcolor,
                            style: {
                                position: 'absolute',
                                right: '200%'
                            },
                            oninput(ev) {
                                node.recomputeInsideNodes();
                                node.color = shadeHexColor(ev.target.value);
                                node._nodes.forEach((child) => {
                                    child.color = shadeHexColor(ev.target.value);
                                    child.setDirtyCanvas(true, true);
                                });
                                node.setDirtyCanvas(true, true);
                            },
                        }), ], ), ]), );
                        menuRoot.append($el('div.litemenu-entry.submenu', [$el('label', {
                            style: {
                                position: 'relative',
                                overflow: 'hidden',
                                display: 'block',
                                paddingLeft: '4px',
                                borderLeft: '8px solid #222',
                            },
                        }, ['Color All BG', $el('input', {
                            type: 'color',
                            value: node.bgcolor,
                            style: {
                                position: 'absolute',
                                right: '200%'
                            },
                            oninput(ev) {
                                node.recomputeInsideNodes();
                                node.bgcolor = ev.target.value;
                                node._nodes.forEach((child) => {
                                    child.bgcolor = ev.target.value;
                                    child.setDirtyCanvas(true, true);
                                });
                                node.setDirtyCanvas(true, true);
                            },
                        }), ], ), ]), );
                        menuRoot.append($el('div.litemenu-entry.submenu', [$el('label', {
                            style: {
                                position: 'relative',
                                overflow: 'hidden',
                                display: 'block',
                                paddingLeft: '4px',
                                borderLeft: '8px solid #222',
                            },
                        }, ['Color All', $el('input', {
                            type: 'color',
                            value: node.bgcolor,
                            style: {
                                position: 'absolute',
                                right: '200%'
                            },
                            oninput(ev) {
                                node.recomputeInsideNodes();
                                node.bgcolor = ev.target.value;
                                node.color = shadeHexColor(node.bgcolor);
                                node._nodes.forEach((child) => {
                                    child.bgcolor = ev.target.value;
                                    child.color = shadeHexColor(node.bgcolor);
                                    child.setDirtyCanvas(true, true);
                                });
                                node.setDirtyCanvas(true, true);
                            },
                        }), ], ), ]), );
                    }
                } catch (err) {
                    console.debug('[Eclipse.colors] Error adding custom color pickers:', err);
                }
                return result;
            }) : console.debug('[Eclipse.colors] LGraphCanvas.onMenuNodeColors not available');
        },
    }), !LGraphCanvas.prototype.eclipseSetNodeDimension)) {
    if (((window.eclipse_newNodeMenuAPIUsed = false), !document.getElementById('eclipse-dialog-style'))) {
        const styleEl = document.createElement('style');
        styleEl.id = 'eclipse-dialog-style';
        styleEl.innerHTML = '\n    .eclipse-dialog {\n      position: fixed;\n      top: 10px;\n      left: 10px;\n      min-height: 1em;\n      background-color: var(--comfy-menu-bg, #222);\n      color: var(--descrip-text, #ddd);\n      font-size: 1.0rem;\n      box-shadow: 0 0 7px black !important;\n      z-index: 10000;\n      display: grid;\n      border-radius: 7px;\n      padding: 7px 7px;\n    }\n    .eclipse-dialog .name { display:inline-block; font-size:14px; padding:0; justify-self:center; }\n    .eclipse-dialog input, .eclipse-dialog textarea, .eclipse-dialog select { margin:3px; min-width:60px; min-height:1.5em; background-color: var(--comfy-input-bg, #333); border:2px solid var(--border-color, #444); color: var(--input-text, #fff); border-radius:14px; padding-left:10px; outline:none; }\n    .eclipse-dialog button { margin-top:3px; vertical-align:top; background-color:#999; border:0; padding:4px 18px; border-radius:20px; cursor:pointer; }\n    ';
        document.head.appendChild(styleEl);
    }
    var _eclipseLastMouse = {
        x: 0,
        y: 0
    };
    document.addEventListener('pointerdown', function (ev) {
        _eclipseLastMouse.x = ev.clientX;
        _eclipseLastMouse.y = ev.clientY;
    }, true, );
    LGraphCanvas.prototype.eclipseCreateDialog = function (html, onOk, onCancel) {
        var dialog = document.createElement('div');
        dialog.is_modified = false;
        dialog.className = 'eclipse-dialog';
        dialog.innerHTML = html + "<button id='ok'>OK</button>";
        dialog.close = function () {
            dialog.parentNode && dialog.parentNode.removeChild(dialog);
        };
        var inputs = Array.from(dialog.querySelectorAll('input, select'));
        inputs.forEach((inp) => {
            inp.addEventListener('keydown', function (ev) {
                if (((dialog.is_modified = true), 27 == ev.keyCode))(onCancel && onCancel(), dialog.close());
                else if (13 == ev.keyCode)
                    (onOk && onOk(dialog, inputs.map((inp) => inp.value), ), dialog.close());
                else if (13 != ev.keyCode && 'textarea' != ev.target.localName) return;
                ev.preventDefault();
                ev.stopPropagation();
            });
        });
        if (_eclipseLastMouse.x || _eclipseLastMouse.y) {
            dialog.style.left = _eclipseLastMouse.x - 20 + 'px';
            dialog.style.top = _eclipseLastMouse.y - 20 + 'px';
        } else {
            dialog.style.left = 0.5 * window.innerWidth - 60 + 'px';
            dialog.style.top = 0.5 * window.innerHeight - 20 + 'px';
        }
        dialog.querySelector('#ok').addEventListener('click', function () {
            onOk && onOk(dialog, inputs.map((inp) => inp.value), );
            dialog.close();
        });
        document.body.appendChild(dialog);
        inputs && inputs[0].focus();
        var leaveTimeout = null;
        return (dialog.addEventListener('mouseleave', function (ev) {
            LiteGraph.dialog_close_on_mouse_leave && !dialog.is_modified && LiteGraph.dialog_close_on_mouse_leave && (leaveTimeout = setTimeout(dialog.close, LiteGraph.dialog_close_on_mouse_leave_delay));
        }), dialog.addEventListener('mouseenter', function (ev) {
            LiteGraph.dialog_close_on_mouse_leave && leaveTimeout && clearTimeout(leaveTimeout);
        }), dialog);
    };
    LGraphCanvas.prototype.eclipseSetNodeDimension = function (targetNode) {
        const curWidth = targetNode.size[0],
            curHeight = targetNode.size[1];
        let html = "<input type='text' class='width' value='" + curWidth + "'></input>";
        html += "<input type='text' class='height' value='" + curHeight + "'></input>";
        LGraphCanvas.prototype.eclipseCreateDialog("<span class='name'>Width/Height</span>" + html, function (dialog, values) {
            var newWidth = Number(values[0]) || curWidth,
                newHeight = Number(values[1]) || curHeight;
            let minSize = targetNode.computeSize();
            var finalWidth = Math.max(minSize[0], newWidth),
                finalHeight = Math.max(minSize[1], newHeight);
            targetNode.setSize([finalWidth, finalHeight]);
            var vueEl = document.querySelector('[data-node-id="' + targetNode.id + '"]');
            if (vueEl) {
                var titleHeight = ('undefined' != typeof LiteGraph && LiteGraph.NODE_TITLE_HEIGHT) || 30;
                vueEl.style.setProperty('--node-width', finalWidth + 'px');
                vueEl.style.setProperty('--node-height', finalHeight + titleHeight + 'px');
            }
            dialog.parentNode && dialog.parentNode.removeChild(dialog);
            targetNode.setDirtyCanvas(true, true);
        }, null, );
    };
    LGraphCanvas.prototype.eclipseReloadNode = function (sourceNode) {
        try {
            const CONVERTED_TYPE = 'converted-widget',
                CONVERTED_MARKER = Symbol();

            function getInputDef(widgetName, node) {
                const {
                    nodeData
                } = node.constructor;
                return nodeData?.input?.required[widgetName] ?? nodeData?.input?.optional?.[widgetName];
            }

            function hideWidget(node, widget, suffix = '') {
                widget.origType = widget.type;
                widget.origComputeSize = widget.computeSize;
                widget.origSerializeValue = widget.serializeValue;
                widget.computeSize = () => [0, -4];
                widget.type = CONVERTED_TYPE + suffix;
                widget.serializeValue = () => {
                    if (!node.inputs) return;
                    const inputSlot = node.inputs.find((inp) => inp.widget?.name === widget.name);
                    return inputSlot && inputSlot.link ? (widget.origSerializeValue ? widget.origSerializeValue() : widget.value) : undefined;
                };
                if (widget.linkedWidgets) {
                    for (const linked of widget.linkedWidgets) hideWidget(node, linked, ':' + widget.name);
                }
            }

            function convertToInput(node, widget, inputDef) {
                hideWidget(node, widget);
                const {
                    type: inputType
                } = (function (def) {
                    let first = def[0];
                    return (first instanceof Array && (first = 'COMBO'), {
                        type: first
                    });
                })(inputDef), prevSize = node.size;
                node.addInput(widget.name, inputType, {
                    widget: {
                        name: widget.name,
                        [CONVERTED_MARKER]: () => inputDef
                    }
                });
                for (const w of node.widgets) w.last_y += LiteGraph.NODE_SLOT_HEIGHT;
                node.setSize([Math.max(prevSize[0], node.size[0]), Math.max(prevSize[1], node.size[1])]);
                patchNodeCSSSize(node);
            }
            const {
                title,
                color,
                bgcolor
            } = sourceNode.properties.origVals || sourceNode, savedProps = {
                size: [...sourceNode.size],
                color,
                bgcolor,
                pos: [...sourceNode.pos]
            }, origNode = sourceNode, inputConns = [], outputConns = [];
            if (sourceNode.inputs) {
                for (const inp of sourceNode.inputs ?? []) {
                    if (inp.link) {
                        const inputName = inp.name,
                            slotIdx = sourceNode.findInputSlot(inputName),
                            srcNode = sourceNode.getInputNode(slotIdx),
                            linkInfo = sourceNode.getInputLink(slotIdx);
                        inputConns.push([linkInfo.origin_slot, srcNode, inputName]);
                    }
                }
            }
            if (sourceNode.outputs) {
                for (const output of sourceNode.outputs) {
                    if (output.links) {
                        const outputName = output.name;
                        for (const linkId of output.links) {
                            const linkInfo = app.graph.links[linkId],
                                targetNode = app.graph._nodes_by_id[linkInfo.target_id];
                            outputConns.push([outputName, targetNode, linkInfo.target_slot]);
                        }
                    }
                }
            }
            app.graph.remove(sourceNode);
            const newNode = app.graph.add(LiteGraph.createNode(origNode.constructor.type, title, savedProps));
            if (newNode?.constructor?.hasOwnProperty('ttNnodeVersion')) {
                newNode.properties.ttNnodeVersion = newNode.constructor.ttNnodeVersion;
            }
            let widgetValues = origNode.widgets_values;
            if (widgetValues) {
                let foundValid = false;
                const isAscending = widgetValues.length <= newNode.widgets.length;
                let searchIdx = isAscending ? 0 : widgetValues.length - 1;
                const validateValue = (val, widget) => !['', null].includes(val) || ('button' !== widget.type && 'converted-widget' !== widget.type) ? ('boolean' == typeof val && widget.options?.on && widget.options?.off) || widget.options?.values?.includes(val) ? {
                    value: val,
                    isValid: true
                } : !widget.inputEl || ('string' != typeof val && val !== widget.value) ? !isNaN(val) && ((val = parseFloat(val)), widget.options?.min <= val && val <= widget.options?.max) ? {
                    value: val,
                    isValid: true
                } : {
                    value: widget.value,
                    isValid: false
                } : {
                    value: val,
                    isValid: true
                } : {
                    value: val,
                    isValid: true
                };

                function applyWidgetValue(idx) {
                    const origWidget = origNode.widgets[idx];
                    let newWidget = newNode.widgets[idx],
                        pos = searchIdx;
                    if (newWidget.name === origWidget.name && (newWidget.type === origWidget.type || 'ttNhidden' === origWidget.type || 'ttNhidden' === newWidget.type)) {
                        for (;
                            (isAscending ? pos < widgetValues.length : pos >= 0) && !foundValid;) {
                            let result = validateValue(widgetValues[pos], newWidget),
                                val = result.value;
                            if (((foundValid = result.isValid), foundValid && NaN !== val)) {
                                newWidget.value = val;
                                break;
                            }
                            pos += isAscending ? 1 : -1;
                        }
                        if (isAscending) {
                            if (pos === searchIdx) searchIdx++;
                            if (pos === searchIdx + 1) {
                                searchIdx++;
                                searchIdx++;
                            }
                        } else {
                            if (pos === searchIdx) searchIdx--;
                            if (pos === searchIdx - 1) {
                                searchIdx--;
                                searchIdx--;
                            }
                        }
                    }
                }
                if (isAscending) {
                    for (let idx = 0; idx < newNode.widgets.length; idx++) applyWidgetValue(idx);
                } else {
                    for (let idx = newNode.widgets.length - 1; idx >= 0; idx--) applyWidgetValue(idx);
                }
            } else {
                newNode.widgets.forEach((widget, idx) => {
                    let found = false;
                    for (; idx < origNode.widgets.length && !found;) {
                        const origWidget = origNode.widgets[idx];
                        if (widget.type === origWidget.type) {
                            widget.value = origWidget.value;
                            found = true;
                        }
                        idx++;
                    }
                });
            }
            (function () {
                for (const widget of origNode.widgets) {
                    if (widget.type === CONVERTED_TYPE) {
                        const inputDef = getInputDef(widget.name, origNode),
                            targetWidget = newNode.widgets.find((w) => w.name === widget.name);
                        if (targetWidget && !newNode?.inputs?.find((inp) => inp.name === widget.name)) {
                            convertToInput(newNode, targetWidget, inputDef);
                        }
                    }
                }
                for (const conn of inputConns) {
                    const [originSlot, srcNode, inputName] = conn;
                    srcNode.connect(originSlot, newNode.id, inputName);
                }
                for (const conn of outputConns) {
                    const [outputName, targetNode, targetSlot] = conn;
                    newNode.connect(outputName, targetNode, targetSlot);
                }
            })();
            newNode.setSize(savedProps.size);
            patchNodeCSSSize(newNode);
            if (typeof newNode.onResize === 'function') newNode.onResize([0, 0]);
            newNode.setDirtyCanvas(true, true);
        } catch (err) {
            console.debug('eclipse: eclipseReloadNode exception', err);
        }
    };
}
app.registerExtension({
    name: 'Eclipse.nodeMenuItems',
    getNodeMenuItems(node) {
        // Node menu via hook (no position control, but no duplication issues)
        const cleaned = adaptNestedMenuItems(window._eclipseBuildNodeMenuItems?.(node) || []);
        if (!cleaned.length) return [];
        return [null, {
            content: '🌒 Eclipse',
            has_submenu: true,
            submenu: { title: 'Eclipse', options: cleaned },
        }];
    },
    getCanvasMenuItems() {
        // Collect from canvas menu providers
        const providerItems = [];
        const providers = window._eclipseCanvasMenuProviders || [];
        for (const fn of providers) {
            try {
                const items = fn();
                if (items?.length) providerItems.push(...items);
            } catch (e) {
                console.debug('eclipse: canvas menu provider error', e);
            }
        }
        // Clean separators
        const cleaned = [];
        for (const item of providerItems) {
            if (item === null) {
                if (cleaned.length > 0 && cleaned[cleaned.length - 1] !== null) cleaned.push(null);
            } else {
                cleaned.push(item);
            }
        }
        while (cleaned.length && cleaned[cleaned.length - 1] === null) cleaned.pop();
        if (!cleaned.length) return [];
        const rendererItems = adaptNestedMenuItems(cleaned);
        return [null, {
            content: '🌒 Eclipse',
            has_submenu: true,
            submenu: { title: 'Eclipse', options: rendererItems },
        }];
    },
    setup() {
        // Shared helper: build Eclipse submenu items for a node
        function buildNodeMenuItems(node) {
            const items = [{
                content: 'Node Dimensions',
                callback: () => {
                    LGraphCanvas.prototype.eclipseSetNodeDimension(node);
                },
            }, {
                content: 'Reload Node',
                callback: () => {
                    try {
                        LGraphCanvas.prototype.eclipseReloadNode(node);
                    } catch (err) {
                        console.debug('eclipse: Reload Node failed', err);
                    }
                },
            }];
            if (isVueMode() && !(node instanceof LGraphGroup)) {
                items.push(null, {
                    content: 'Custom Title',
                    callback: () => {
                        openCustomColorInput(node, (target, color) => {
                            target.color = shadeHexColor(color);
                        });
                    },
                }, {
                    content: 'Custom BG',
                    callback: () => {
                        openCustomColorInput(node, (target, color) => {
                            target.bgcolor = color;
                        });
                    },
                }, {
                    content: 'Custom All',
                    callback: () => {
                        openCustomColorInput(node, (target, color) => {
                            target.bgcolor = color;
                            target.color = shadeHexColor(color);
                        });
                    },
                });
            }
            const providers = window._eclipseMenuProviders || [];
            for (const fn of providers) {
                try {
                    const providerItems = fn(node);
                    if (providerItems?.length) items.push(...providerItems);
                } catch (e) {
                    console.debug('eclipse: menu provider error', e);
                }
            }
            // Clean separators
            const cleaned = [];
            for (const item of items) {
                if (item === null) {
                    if (cleaned.length > 0 && cleaned[cleaned.length - 1] !== null) cleaned.push(null);
                } else {
                    cleaned.push(item);
                }
            }
            while (cleaned.length && cleaned[cleaned.length - 1] === null) cleaned.pop();
            return cleaned;
        }
        window._eclipseBuildNodeMenuItems = buildNodeMenuItems;
    },
}), app.registerExtension({
    name: 'Eclipse.socketlessFix',
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (!nodeData?.name?.includes('[Eclipse]') && !nodeData?.name?.includes('[SmartLML]') && !nodeData?.name?.includes('[RvTools]')) return;
        const origOnCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origOnCreated?.apply(this, arguments);
            removeSocketlessInputs(this);
        };
    },
});
app.registerExtension({
    name: 'Eclipse.appearance',
    nodeCreated(node) {
        try {
            const title = node.title || node.constructor?.title || '',
                comfyClass = node.comfyClass || '',
                constructorType = node.constructor?.type || '',
                nodeType = node.type || '',
                isEclipseNode = (str) => typeof str === 'string' && (str.includes('[Eclipse]') || str.includes('[RvTools]')),
                isRvNode = (str) => typeof str === 'string' && (str.startsWith('Rv') || str.includes('Rv') || str.toLowerCase().includes('rv'));
            const _catColors = {
                loader: '#8131d0',
                text: '#007d52',
                image: '#95541e',
                settings: '#4e4e4e',
                pipe: '#000000',
                router: '#000000',
                video: '#4a2636',
                folder: '#2a3c5a',
                bridge: '#005a88',
                tools: '#4e4e4e',
            };
            const _catBgColors = {
                pipe: '#000000',
                router: '#000000',
                bridge: '#0070a8',
            };

            function _getCat(id) {
                if (!id || typeof id !== 'string') return 'tools';
                if (/^Pipe |^Pipe IO |^IO |^Context |Concat Pipe|Generation Data|^Pipe In |DeDuplicate/i.test(id)) return 'pipe';
                if (/Language Model|Smart LM/i.test(id)) return 'text';
                if (/Loader/i.test(id)) return 'loader';
                if (/Mode Bridge/i.test(id)) return 'bridge';
                if (/Repeater|Node Collector|Join|Calculator/i.test(id)) return 'router';
                if (/Lora Stack|Block Swap|VRAM|RAM Cleanup|^Fast |Muter|Bypasser|^Stop |Show Any|Nunchaku PuLID/i.test(id)) return 'tools';
                if (/Resolution|Sampler|Custom Size|WanVideo Setup|ControlNet|Sampler Selection|Load Directory|Filename Generator|VHS Input|Aspect Ratio/i.test(id)) return 'settings';
                if (/String|Prompt|Wildcard|Replace String|Multiline|^Seed /i.test(id)) return 'text';
                if (/Image|Mask|Watermark|Bboxes|Detection|Convert To Batch|To List|To Batch/i.test(id)) return 'image';
                if (/Video Clip|Seamless Join/i.test(id)) return 'video';
                if (/Passer|Switch|IF A|^Boolean |^Float |^Integer |Multi-Switch/i.test(id)) return 'router';
                if (/Folder|Filename Prefix|^Add Folder|^Project Folder/i.test(id)) return 'folder';
                return 'tools';
            }

            function applyColors() {
                const cat = _getCat(comfyClass) !== 'tools' ? _getCat(comfyClass) : _getCat(nodeType) !== 'tools' ? _getCat(nodeType) : _getCat(title);
                node.color = _catColors[cat] || _catColors.tools;
                node.bgcolor = _catBgColors[cat] || '#3a3a3a';
                node.shape = 'default';
                node.setDirtyCanvas?.(true, true);
                node._Eclipse_appearance_applied = true;
            }
            if (isEclipseNode(title) || isEclipseNode(comfyClass) || isEclipseNode(node.constructor?.title) || isEclipseNode(nodeType) || isEclipseNode(constructorType) || isRvNode(comfyClass) || isRvNode(constructorType) || isRvNode(nodeType)) {
                if (!node._Eclipse_appearance_applied) {
                    if (undefined === node._Eclipse_initial_bgcolor) node._Eclipse_initial_bgcolor = node.bgcolor;
                    if (undefined === node._Eclipse_initial_color) node._Eclipse_initial_color = node.color;
                    if (node.bgcolor === node._Eclipse_initial_bgcolor && node.color === node._Eclipse_initial_color) applyColors();
                    setTimeout(() => {
                        if (node._Eclipse_appearance_applied) return;
                        if (node.bgcolor === node._Eclipse_initial_bgcolor && node.color === node._Eclipse_initial_color) applyColors();
                    }, 50);
                }
            }
        } catch (err) {}
    },
});

// --- SML (Smart LML) Settings ---
const HF_TOKEN_MASK = "••••••••";
app.registerExtension({
    name: "Eclipse.SMLSettings",
    async init(app) {
        let config = {
            llm_models_path: "LLM",
            retry_download_attempts: 2,
            hf_token_configured: false
        };
        try {
            const response = await fetch("/smartlml/config/all");
            if (response.ok) {
                const data = await response.json();
                config.llm_models_path = data.llm_models_path || "LLM";
                config.retry_download_attempts = data.retry_download_attempts ?? 2;
                config.hf_token_configured = data.hf_token_configured === true;
            }
        } catch (error) {
            console.error("[Eclipse/SML] Failed to fetch config:", error);
        }
        app.ui.settings.addSetting({
            id: "Eclipse.SML.ModelsPath",
            name: "📁 LLM Models Path",
            type: "text",
            tooltip: "Path to LLM models folder. Can be:\n- Relative to ComfyUI models folder (e.g., 'LLM' → models/LLM)\n- Absolute path (e.g., 'D:/AI/models/LLM')\n\nAbsolute path is auto-derived from this setting.",
            defaultValue: config.llm_models_path,
            async onChange(value) {
                try {
                    const response = await fetch("/smartlml/config/update", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ llm_models_path: value }),
                    });
                    if (response.ok) {
                        const data = await response.json();
                        if (data.success) console.log(`[Eclipse/SML] LLM models path updated to: ${value}`);
                        else console.error("[Eclipse/SML] Failed to update LLM models path:", data.error);
                    }
                } catch (error) {
                    console.error("[Eclipse/SML] Failed to update LLM models path:", error);
                }
            },
        });
        app.ui.settings.addSetting({
            id: "Eclipse.SML.RetryDownloadAttempts",
            name: "🔄 SML Retry Download Attempts",
            type: "number",
            tooltip: "Number of times to retry download if hash verification fails (0 to disable auto-retry).",
            defaultValue: config.retry_download_attempts,
            async onChange(value) {
                const numValue = parseInt(value);
                if (isNaN(numValue) || numValue < 0) return;
                try {
                    const response = await fetch("/smartlml/config/update", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ retry_download_attempts: numValue }),
                    });
                    if (response.ok) {
                        const data = await response.json();
                        if (data.success) console.log(`[Eclipse/SML] Retry download attempts updated to: ${numValue}`);
                        else console.error("[Eclipse/SML] Failed to update retry download attempts:", data.error);
                    }
                } catch (error) {
                    console.error("[Eclipse/SML] Failed to update retry download attempts:", error);
                }
            },
        });
        app.ui.settings.addSetting({
            id: "Eclipse.SML.HFToken",
            name: "🔑 SML HuggingFace Token",
            type: "text",
            tooltip: "Optional HuggingFace token for faster downloads. Existing tokens are masked and never returned by the server. Replace the mask to update, or clear it to remove the token.",
            defaultValue: config.hf_token_configured ? HF_TOKEN_MASK : "",
            async onChange(value) {
                if (value === HF_TOKEN_MASK) return;
                try {
                    const response = await fetch("/smartlml/config/update", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ hf_token: value }),
                    });
                    if (response.ok) {
                        const data = await response.json();
                        if (data.success) {
                            console.log(`[Eclipse/SML] HuggingFace token ${value ? 'updated' : 'cleared'}`);
                            // Replace the locally persisted setting immediately so the
                            // credential is not retained in browser settings storage.
                            app.ui.settings.setSettingValue?.(
                                "Eclipse.SML.HFToken",
                                value ? HF_TOKEN_MASK : ""
                            );
                        } else console.error("[Eclipse/SML] Failed to update HuggingFace token:", data.error);
                    }
                } catch (error) {
                    console.error("[Eclipse/SML] Failed to update HuggingFace token:", error);
                }
            },
        });
        // Overwrite any token value persisted by older frontend versions.
        app.ui.settings.setSettingValue?.(
            "Eclipse.SML.HFToken",
            config.hf_token_configured ? HF_TOKEN_MASK : ""
        );
    },
});
