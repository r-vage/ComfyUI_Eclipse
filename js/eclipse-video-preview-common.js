/**
 * Shared DOM video preview used by Save Video / Preview Video.
 *
 * The widget fills the node's remaining vertical space. We use the y position
 * LiteGraph itself assigns to the widget (`widget.last_y`) so we don't have
 * to guess header / slot / gap pixel constants — guaranteed exact.
 */
import { app, api } from './comfy/index.js';
import { isVueMode, onVueModeChange } from './eclipse-widget-performance-utils.js';

const VIDEO_FREE_RESIZE_CLASS = 'eclipse-video-preview-vue-free-resize';
let videoFreeResizeCSSInjected = false;

function injectVideoFreeResizeCSS() {
    if (videoFreeResizeCSSInjected) return;
    videoFreeResizeCSSInjected = true;
    const style = document.createElement('style');
    style.textContent = `
.${VIDEO_FREE_RESIZE_CLASS} {
    contain: size;
    min-width: 0;
    min-height: 0;
}
.${VIDEO_FREE_RESIZE_CLASS} > video {
    width: 100%;
    height: 100%;
    min-width: 0;
    min-height: 0;
    max-width: none;
    max-height: none;
    object-fit: contain;
}`;
    document.head.appendChild(style);
}

export function attachVideoPreview(node, {
    minHeight = 100,
    sourceType = 'output',
} = {}) {
    if (node._eclipse_videoPreview) return node._eclipse_videoPreview;

    injectVideoFreeResizeCSS();
    const wrap = document.createElement('div');
    wrap.style.width = '100%';
    wrap.style.height = '100%';
    wrap.style.boxSizing = 'border-box';
    wrap.style.overflow = 'hidden';
    wrap.style.display = 'flex';
    wrap.style.alignItems = 'stretch';
    wrap.style.justifyContent = 'center';

    const video = document.createElement('video');
    video.controls = true;
    video.loop = true;
    video.muted = false;
    video.volume = 0.5;
    video.playsInline = true;
    video.style.width = '100%';
    video.style.height = '100%';
    video.style.objectFit = 'contain';
    video.style.display = 'none';
    wrap.appendChild(video);

    // Same pattern as eclipse-dom-preview.js — let LiteGraph do the math via
    // getMinHeight/getMaxHeight. This lets the user drag the node smaller or
    // larger without the widget fighting the resize.
    const widget = node.addDOMWidget('eclipse_preview', 'preview', wrap, {
        serialize: false,
        hideOnZoom: false,
        getMinHeight: () => minHeight,
        getMaxHeight: () => 4096,
        getValue: () => video.src || '',
        setValue: (v) => { if (typeof v === 'string') video.src = v; },
    });

    const syncFreeResizeMode = () => {
        wrap.classList.toggle(VIDEO_FREE_RESIZE_CLASS, isVueMode());
        node.graph?.setDirtyCanvas(true, true);
    };
    syncFreeResizeMode();
    let modeUnsubscribe = onVueModeChange(syncFreeResizeMode);
    let disposed = false;
    const dispose = () => {
        if (disposed) return;
        disposed = true;
        modeUnsubscribe?.();
        modeUnsubscribe = null;
        wrap.classList.remove(VIDEO_FREE_RESIZE_CLASS);
    };
    const origWidgetOnRemove = widget.onRemove;
    widget.onRemove = function () {
        dispose();
        return origWidgetOnRemove?.apply(this, arguments);
    };

    const refreshVisibility = () => {
        video.style.display = video.src ? 'block' : 'none';
    };

    video.addEventListener('loadedmetadata', () => {
        refreshVisibility();
        node.graph?.setDirtyCanvas(true, true);
    });
    video.addEventListener('error', () => {
        video.removeAttribute('src');
        try { video.load(); } catch (_e) { /* ignore */ }
        refreshVisibility();
        node.graph?.setDirtyCanvas(true, true);
    });

    // Stop canvas mouse events while interacting with the video controls.
    ['contextmenu', 'pointerdown', 'pointerup', 'pointermove'].forEach(ev => {
        wrap.addEventListener(ev, (e) => e.stopPropagation(), true);
    });

    wrap.addEventListener('wheel', (e) => {
        const canvas = app.canvas?.canvas;
        if (canvas) {
            e.preventDefault();
            e.stopPropagation();
            canvas.dispatchEvent(new WheelEvent('wheel', {
                clientX: e.clientX,
                clientY: e.clientY,
                deltaX: e.deltaX,
                deltaY: e.deltaY,
                ctrlKey: e.ctrlKey,
                metaKey: e.metaKey,
                shiftKey: e.shiftKey,
                bubbles: true,
                cancelable: true
            }));
        }
    }, true);

    node._eclipse_videoEl = video;
    node._eclipse_videoPreview = widget;
    node._eclipse_videoPreviewDispose = dispose;
    node._eclipse_videoSourceType = sourceType;
    return widget;
}

export function setVideoPreviewSource(node, info) {
    if (!info || !info.filename) return;
    const video = node._eclipse_videoEl;
    if (!video) return;
    const params = new URLSearchParams({
        filename: info.filename,
        subfolder: info.subfolder || '',
        type: info.type || node._eclipse_videoSourceType || 'output',
        t: String(Date.now()),
    });
    video.src = api.apiURL('/view?' + params.toString());
    try { video.load(); } catch (_e) { /* ignore */ }
    // Force layout recompute now that the widget will switch from hidden to visible.
    node.graph?.setDirtyCanvas(true, true);
}

export function stopVideoPreview(node) {
    node._eclipse_videoPreviewDispose?.();
    try {
        node._eclipse_videoEl?.pause?.();
        if (node._eclipse_videoEl) node._eclipse_videoEl.src = '';
    } catch (_e) { /* ignore */ }
}
