import {
    app,
    api
} from './comfy/index.js';
import { isVueMode, onVueModeChange } from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'Image Comparer [Eclipse]';
const NAV_HEIGHT = 22;
const NAV_GAP = 3;
const NAV_ARROW_WIDTH = 20;
const NAV_NUMBER_MIN_WIDTH = 20;
const SLOT_CONTROL_PADDING = 24;
const COMPARER_FREE_RESIZE_CLASS = 'eclipse-image-comparer-vue-free-resize';

let textMeasureContext = null;
let comparerFreeResizeCSSInjected = false;

function injectComparerFreeResizeCSS() {
    if (comparerFreeResizeCSSInjected) return;
    comparerFreeResizeCSSInjected = true;
    const style = document.createElement('style');
    style.textContent = `
.${COMPARER_FREE_RESIZE_CLASS} {
    contain: size;
    min-width: 0;
    min-height: 0;
}
.${COMPARER_FREE_RESIZE_CLASS} > .eclipse-image-comparer-image {
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

function imageDataToUrl(data) {
    return api.apiURL(`/view?filename=${encodeURIComponent(data.filename)}&type=${data.type}&subfolder=${data.subfolder}`);
}

function measureDomText(text, font = '12px sans-serif') {
    if (!textMeasureContext) {
        textMeasureContext = document.createElement('canvas').getContext('2d');
    }
    if (!textMeasureContext) return String(text).length * 7;
    textMeasureContext.font = font;
    return textMeasureContext.measureText(String(text)).width;
}

function slotLabel(slot) {
    return String(slot?.label ?? slot?.localized_name ?? slot?.name ?? '');
}

function getSlotSafeInsets(node, measureText) {
    const widestLabel = (slots) => Math.max(0, ...(slots || []).map(slot => measureText(slotLabel(slot))));
    return {
        left: Math.ceil(widestLabel(node.inputs) + SLOT_CONTROL_PADDING),
        right: Math.ceil(widestLabel(node.outputs) + SLOT_CONTROL_PADDING),
    };
}

function getSideImages(images, side) {
    return images.filter(image => image.side === side);
}

function getPairCount(images) {
    return Math.max(getSideImages(images, 'a').length, getSideImages(images, 'b').length);
}

function clampPairIndex(images, index) {
    const count = getPairCount(images);
    return count > 0 ? Math.max(0, Math.min(count - 1, index)) : 0;
}

function getPairSelection(images, index) {
    const aImages = getSideImages(images, 'a');
    const bImages = getSideImages(images, 'b');
    return [aImages[index] || aImages[0] || null, bImages[index] || bImages[0] || null];
}

function getPagerLayout(pairCount, selectedIndex, availableWidth, measureText) {
    if (pairCount <= 1 || availableWidth < NAV_ARROW_WIDTH * 2 + NAV_GAP) {
        return null;
    }
    const numberWidth = Math.max(NAV_NUMBER_MIN_WIDTH, Math.ceil(measureText(String(pairCount))) + 10);
    const arrowOnlyWidth = NAV_ARROW_WIDTH * 2 + NAV_GAP;
    const widthPerNumber = numberWidth + NAV_GAP;
    const numberCapacity = Math.max(0, Math.floor((availableWidth - arrowOnlyWidth - NAV_GAP) / widthPerNumber));
    const visibleCount = Math.min(pairCount, numberCapacity);
    let start = 0;
    if (visibleCount > 0) {
        start = Math.max(0, Math.min(pairCount - visibleCount, selectedIndex - Math.floor(visibleCount / 2)));
    }
    return {
        numberWidth,
        indexes: Array.from({ length: visibleCount }, (_, offset) => start + offset),
    };
}

function buildImageList(output) {
    const aImages = output.a_images || [];
    const bImages = output.b_images || [];
    const list = [];
    for (let i = 0; i < aImages.length; i++) {
        list.push({
            side: 'a',
            url: imageDataToUrl(aImages[i]),
            img: null,
        });
    }
    for (let i = 0; i < bImages.length; i++) {
        list.push({
            side: 'b',
            url: imageDataToUrl(bImages[i]),
            img: null,
        });
    }
    return list;
}

function setupVueMode(node) {
    injectComparerFreeResizeCSS();
    const state = {
        node,
        images: [],
        selectedA: null,
        selectedB: null,
        pairIndex: 0,
        mode: node.properties?.['comparer_mode'] || 'Slide',
        sliderPos: 50,
        isPointerDown: false,
        isPointerOver: false,
    };
    node._eclipse_comparer = state;
    const container = document.createElement('div');
    container.style.cssText = 'position:relative; width:100%; height:100%; overflow:hidden;' + 'background:#1a1a1a; user-select:none; touch-action:none;';
    container.addEventListener('wheel', (e) => {
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
    });
    const imgB = document.createElement('img');
    imgB.className = 'eclipse-image-comparer-image';
    imgB.style.cssText = 'width:100%; height:100%; object-fit:contain; display:none; pointer-events:none;';
    imgB.draggable = false;
    container.appendChild(imgB);
    const imgA = document.createElement('img');
    imgA.className = 'eclipse-image-comparer-image';
    imgA.style.cssText = 'position:absolute; inset:0; width:100%; height:100%; object-fit:contain;' + 'display:none; pointer-events:none;';
    imgA.draggable = false;
    container.appendChild(imgA);
    const slider = document.createElement('div');
    slider.style.cssText = 'position:absolute; top:0; bottom:0; width:2px; background:white;' + 'pointer-events:none; mix-blend-mode:difference; z-index:10; display:none;';
    container.appendChild(slider);
    const labelBar = document.createElement('div');
    labelBar.style.cssText = 'position:absolute; top:2px; height:18px; z-index:20; display:none;' +
        'align-items:center; justify-content:center; gap:3px; overflow:hidden; pointer-events:auto;';
    for (const eventName of ['pointerdown', 'pointerup', 'pointermove', 'click']) {
        labelBar.addEventListener(eventName, event => event.stopPropagation());
    }
    container.appendChild(labelBar);
    const dimLabelA = document.createElement('div');
    dimLabelA.style.cssText = 'position:absolute; bottom:4px; left:4px; font:11px sans-serif;' +
        'color:#ccc; pointer-events:none; background:rgba(0,0,0,0.6);' +
        'padding:1px 5px; border-radius:3px; z-index:20;';
    container.appendChild(dimLabelA);
    const dimLabelB = document.createElement('div');
    dimLabelB.style.cssText = 'position:absolute; bottom:4px; right:4px; font:11px sans-serif;' +
        'color:#ccc; pointer-events:none; background:rgba(0,0,0,0.6);' +
        'padding:1px 5px; border-radius:3px; z-index:20;';
    container.appendChild(dimLabelB);
    state.dom = {
        container,
        imgA,
        imgB,
        slider,
        labelBar,
        dimLabelA,
        dimLabelB
    };
    const applySlideClip = () => {
        if (!state.isPointerOver) return;
        const pos = state.sliderPos;
        imgA.style.clipPath = `inset(0 ${100 - pos}% 0 0)`;
        slider.style.left = `${pos}%`;
        slider.style.display = (state.selectedA && state.selectedB) ? 'block' : 'none';
    };
    container.addEventListener('pointermove', (e) => {
        if (state.mode !== 'Slide') return;
        if (!state.selectedA || !state.selectedB) return;
        const rect = container.getBoundingClientRect();
        if (rect.width === 0) return;
        state.sliderPos = Math.max(0, Math.min(100, ((e.clientX - rect.left) / rect.width) * 100));
        applySlideClip();
    });
    container.addEventListener('pointerdown', () => {
        state.isPointerDown = true;
        if (state.mode === 'Click' && state.selectedB) {
            imgA.style.display = 'none';
        }
    });
    container.addEventListener('pointerup', () => {
        state.isPointerDown = false;
        if (state.mode === 'Click') _vueShowBoth(state);
    });
    container.addEventListener('pointerenter', () => {
        state.isPointerOver = true;
        if (state.mode === 'Slide' && state.selectedA && state.selectedB) {
            applySlideClip();
        }
    });
    container.addEventListener('pointerleave', () => {
        state.isPointerOver = false;
        state.isPointerDown = false;
        if (state.mode === 'Click') _vueShowBoth(state);
        if (state.mode === 'Slide') {
            slider.style.display = 'none';
            imgA.style.clipPath = '';
        }
    });
    const widget = node.addDOMWidget('eclipse_comparer', 'custom', container, {
        hideOnZoom: false,
    });
    widget.serialize = false;
    widget.computeLayoutSize = () => ({
        minHeight: 300,
        minWidth: 200
    });
    state.widget = widget;
    if (typeof ResizeObserver !== 'undefined') {
        state.resizeObserver = new ResizeObserver(() => _vueBuildNavigator(state));
        state.resizeObserver.observe(container);
    }
    const syncFreeResizeMode = () => {
        container.classList.toggle(COMPARER_FREE_RESIZE_CLASS, isVueMode());
        _vueBuildNavigator(state);
        node.graph?.setDirtyCanvas(true, true);
    };
    syncFreeResizeMode();
    state.modeUnsubscribe = onVueModeChange(syncFreeResizeMode);
    let disposed = false;
    state.dispose = () => {
        if (disposed) return;
        disposed = true;
        state.resizeObserver?.disconnect();
        state.modeUnsubscribe?.();
        state.modeUnsubscribe = null;
        container.classList.remove(COMPARER_FREE_RESIZE_CLASS);
    };
    const origWidgetOnRemove = widget.onRemove;
    widget.onRemove = function () {
        state.dispose();
        return origWidgetOnRemove?.apply(this, arguments);
    };
}

function _vueShowBoth(state) {
    const {
        imgA,
        imgB
    } = state.dom;
    if (state.selectedA) imgA.style.display = 'block';
    if (state.selectedB) imgB.style.display = 'block';
}

function _vueApplyMode(state) {
    const {
        imgA,
        imgB,
        slider
    } = state.dom;
    if (state.mode === 'Slide') {
        if (state.selectedA) {
            imgA.style.display = 'block';
            imgA.style.clipPath = state.isPointerOver ? `inset(0 ${100 - state.sliderPos}% 0 0)` : '';
        }
        if (state.selectedB) imgB.style.display = 'block';
        slider.style.left = `${state.sliderPos}%`;
        slider.style.display = (state.isPointerOver && state.selectedA && state.selectedB) ? 'block' : 'none';
    } else {
        if (state.selectedA) {
            imgA.style.display = 'block';
            imgA.style.clipPath = '';
        }
        if (state.selectedB) imgB.style.display = 'block';
        slider.style.display = 'none';
    }
}

function _vueFeedImages(state, imageList) {
    state.images = imageList;
    _vueSelectPair(state, clampPairIndex(imageList, state.pairIndex));
}

function _vueSelectPair(state, pairIndex) {
    const [nextA, nextB] = getPairSelection(state.images, pairIndex);
    const { imgA, imgB, dimLabelA, dimLabelB } = state.dom;
    state.pairIndex = clampPairIndex(state.images, pairIndex);
    if (nextA) {
        imgA.onload = function () { dimLabelA.textContent = `A: ${this.naturalWidth} × ${this.naturalHeight}`; };
        imgA.src = nextA.url;
        imgA.style.display = 'block';
        state.selectedA = nextA;
    } else {
        imgA.style.display = 'none';
        dimLabelA.textContent = '';
        state.selectedA = null;
    }
    if (nextB) {
        imgB.onload = function () { dimLabelB.textContent = `B: ${this.naturalWidth} × ${this.naturalHeight}`; };
        imgB.src = nextB.url;
        imgB.style.display = 'block';
        state.selectedB = nextB;
    } else {
        imgB.style.display = 'none';
        dimLabelB.textContent = '';
        state.selectedB = null;
    }
    _vueApplyMode(state);
    _vueBuildNavigator(state);
}

function _vueBuildNavigator(state) {
    const { container, labelBar } = state.dom;
    labelBar.innerHTML = '';
    const pairCount = getPairCount(state.images);
    if (pairCount <= 1) {
        labelBar.style.display = 'none';
        return;
    }
    const insets = getSlotSafeInsets(state.node, text => measureDomText(text, '12px sans-serif'));
    labelBar.style.left = `${insets.left}px`;
    labelBar.style.right = `${insets.right}px`;
    const availableWidth = Math.max(0, container.clientWidth - insets.left - insets.right);
    const layout = getPagerLayout(pairCount, state.pairIndex, availableWidth, measureDomText);
    if (!layout) {
        labelBar.style.display = 'none';
        return;
    }
    labelBar.style.display = 'flex';
    const addButton = (label, width, targetIndex, disabled, title) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = label;
        button.disabled = disabled;
        button.title = title;
        button.style.cssText = `width:${width}px; min-width:${width}px; height:18px; padding:0; border:0;` +
            'border-radius:3px; font:12px sans-serif; line-height:18px; color:white;' +
            `background:rgba(0,0,0,${targetIndex === state.pairIndex ? '0.85' : '0.6'});` +
            `opacity:${disabled ? '0.25' : targetIndex === state.pairIndex ? '1' : '0.55'};` +
            `cursor:${disabled ? 'default' : 'pointer'};`;
        button.addEventListener('click', event => {
            event.stopPropagation();
            if (!disabled) _vueSelectPair(state, targetIndex);
        });
        labelBar.appendChild(button);
    };
    addButton('‹', NAV_ARROW_WIDTH, state.pairIndex - 1, state.pairIndex === 0, 'Previous pair');
    for (const index of layout.indexes) {
        addButton(String(index + 1), layout.numberWidth, index, false, `Pair ${index + 1} of ${pairCount}`);
    }
    addButton('›', NAV_ARROW_WIDTH, state.pairIndex + 1, state.pairIndex === pairCount - 1, 'Next pair');
}

function setupCanvasMode(node) {
    node._eclipse_comparer = {
        images: [],
        selected: [null, null],
        pairIndex: 0,
        isPointerDown: false,
        isPointerOver: false,
        pointerOverPos: [0, 0],
    };
    const origComputeSize = node.computeSize;
    node.computeSize = function () {
        const sz = origComputeSize?.apply(this, arguments) || [300, 200];
        sz[0] = Math.max(sz[0], 280);
        sz[1] = Math.max(sz[1], 300);
        return sz;
    };
    node.setSize(node.computeSize());
}

function _canvasFeedImages(state, imageList) {
    state.images = imageList;
    _canvasSelectPair(state, clampPairIndex(imageList, state.pairIndex));
}

function _canvasSelectPair(state, pairIndex) {
    state.pairIndex = clampPairIndex(state.images, pairIndex);
    _canvasSetSelected(state, getPairSelection(state.images, state.pairIndex));
}

function _canvasSetSelected(state, selected) {
    for (const sel of selected) {
        if (!sel) continue;
        if (!sel.img) {
            sel.img = new Image();
            sel.img.src = sel.url;
        }
    }
    state.selected = [selected[0] || null, selected[1] || null];
}

function _canvasDrawNavigator(ctx, node, state, y) {
    const pairCount = getPairCount(state.images);
    node._eclipse_navButtons = [];
    if (pairCount <= 1) return y;
    ctx.save();
    ctx.font = '12px Arial';
    const insets = getSlotSafeInsets(node, text => ctx.measureText(text).width);
    const availableWidth = Math.max(0, node.size[0] - insets.left - insets.right);
    const layout = getPagerLayout(pairCount, state.pairIndex, availableWidth, text => ctx.measureText(text).width);
    if (!layout) {
        ctx.restore();
        return y;
    }
    const controls = [
        { label: '‹', width: NAV_ARROW_WIDTH, targetIndex: state.pairIndex - 1, disabled: state.pairIndex === 0 },
        ...layout.indexes.map(index => ({
            label: String(index + 1),
            width: layout.numberWidth,
            targetIndex: index,
            disabled: false,
        })),
        { label: '›', width: NAV_ARROW_WIDTH, targetIndex: state.pairIndex + 1, disabled: state.pairIndex === pairCount - 1 },
    ];
    const totalWidth = controls.reduce((sum, control) => sum + control.width, 0) + NAV_GAP * (controls.length - 1);
    let x = insets.left + Math.max(0, (availableWidth - totalWidth) / 2);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (const control of controls) {
        const selected = control.targetIndex === state.pairIndex && control.label !== '‹' && control.label !== '›';
        ctx.fillStyle = selected ? 'rgba(0, 0, 0, 0.85)' : 'rgba(0, 0, 0, 0.6)';
        ctx.globalAlpha = control.disabled ? 0.25 : selected ? 1 : 0.55;
        ctx.fillRect(x, y + 2, control.width, 18);
        ctx.fillStyle = '#fff';
        ctx.fillText(control.label, x + control.width / 2, y + 11);
        node._eclipse_navButtons.push({
            x,
            y: y + 2,
            w: control.width,
            h: 18,
            targetIndex: control.targetIndex,
            disabled: control.disabled,
        });
        x += control.width + NAV_GAP;
    }
    ctx.restore();
    return y + NAV_HEIGHT;
}

function _canvasDrawImage(ctx, imageData, nodeWidth, nodeHeight, y, cropX) {
    if (!imageData?.img?.naturalWidth || !imageData?.img?.naturalHeight) return;
    const img = imageData.img;
    const imageAspect = img.naturalWidth / img.naturalHeight;
    const height = nodeHeight - y;
    const widgetAspect = nodeWidth / height;
    let targetWidth, targetHeight, offsetX = 0;
    if (imageAspect > widgetAspect) {
        targetWidth = nodeWidth;
        targetHeight = nodeWidth / imageAspect;
    } else {
        targetHeight = height;
        targetWidth = height * imageAspect;
        offsetX = (nodeWidth - targetWidth) / 2;
    }
    const widthMultiplier = img.naturalWidth / targetWidth;
    const sourceWidth = cropX != null ? (cropX - offsetX) * widthMultiplier : img.naturalWidth;
    const destX = (nodeWidth - targetWidth) / 2;
    const destY = y + (height - targetHeight) / 2;
    const destWidth = cropX != null ? cropX - offsetX : targetWidth;
    const destHeight = targetHeight;
    if (sourceWidth <= 0 || destWidth <= 0) return;
    ctx.save();
    if (cropX != null) {
        ctx.beginPath();
        ctx.rect(destX, destY, destWidth, destHeight);
        ctx.clip();
    }
    ctx.drawImage(img, 0, 0, sourceWidth, img.naturalHeight, destX, destY, destWidth, destHeight);
    if (cropX != null && cropX >= offsetX && cropX <= targetWidth + offsetX) {
        const prevComp = ctx.globalCompositeOperation;
        ctx.beginPath();
        ctx.moveTo(cropX, destY);
        ctx.lineTo(cropX, destY + destHeight);
        ctx.globalCompositeOperation = 'difference';
        ctx.strokeStyle = 'rgba(255, 255, 255, 1)';
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.globalCompositeOperation = prevComp;
    }
    ctx.restore();
}
app.registerExtension({
    name: 'Eclipse.ImageComparer',
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origOnNodeCreated?.apply(this, arguments);
            if (!this.properties) this.properties = {};
            if (isVueMode()) {
                setupVueMode(this);
            } else {
                setupCanvasMode(this);
            }
        };
        const origGetExtraMenuOptions = nodeType.prototype.getExtraMenuOptions;
        nodeType.prototype.getExtraMenuOptions = function (_canvas, options) {
            origGetExtraMenuOptions?.apply(this, arguments);
            const state = this._eclipse_comparer;
            if (!state) return;
            // Default-output toggle (A or B). Persisted in node.properties.default_output.
            if (!this.properties) this.properties = {};
            if (this.properties.default_output !== 'a' && this.properties.default_output !== 'b') {
                this.properties.default_output = 'b';
            }
            const node = this;
            const defaultOutputActions = [
                {
                    content: `Default output: A${this.properties.default_output === 'a' ? '  ✓' : ''}`,
                    callback: () => { node.properties.default_output = 'a'; node.setDirtyCanvas(true, true); }
                },
                {
                    content: `Default output: B${this.properties.default_output === 'b' ? '  ✓' : ''}`,
                    callback: () => { node.properties.default_output = 'b'; node.setDirtyCanvas(true, true); }
                },
                null,
            ];
            options.unshift(...defaultOutputActions);
            // Image open/save options
            const selA = state.dom ? state.selectedA : state.selected?.[0];
            const selB = state.dom ? state.selectedB : state.selected?.[1];
            if (selA || selB) {
                const imgActions = [];
                const base = window.location.origin;
                for (const [label, sel] of [['A', selA], ['B', selB]]) {
                    if (!sel?.url) continue;
                    imgActions.push({
                        content: `Open Image ${label}`,
                        callback: () => {
                            const url = new URL(sel.url, base);
                            url.searchParams.delete('preview');
                            window.open(url.toString(), '_blank');
                        }
                    });
                    imgActions.push({
                        content: `Save Image ${label}`,
                        callback: () => {
                            const url = new URL(sel.url, base);
                            url.searchParams.delete('preview');
                            const fname = url.searchParams.get('filename') || `image_${label}.png`;
                            const a = document.createElement('a');
                            a.href = url.toString();
                            a.download = fname;
                            a.click();
                        }
                    });
                }
                if (imgActions.length) {
                    options.unshift(...imgActions, null);
                }
            }
        };
        const origOnExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (output) {
            origOnExecuted?.apply(this, arguments);
            const state = this._eclipse_comparer;
            if (!state) return;
            const imageList = buildImageList(output);
            if (state.dom) {
                _vueFeedImages(state, imageList);
            } else {
                _canvasFeedImages(state, imageList);
                this.setDirtyCanvas(true, true);
            }
        };
        const origOnConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (config) {
            origOnConfigure?.apply(this, arguments);
            const state = this._eclipse_comparer;
            if (state && config?.properties?.comparer_mode) {
                if (state.dom) state.mode = config.properties.comparer_mode;
            }
        };
        const origOnRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            const state = this._eclipse_comparer;
            state?.dispose?.();
            state?.resizeObserver?.disconnect();
            origOnRemoved?.apply(this, arguments);
        };
        const origOnMouseDown = nodeType.prototype.onMouseDown;
        nodeType.prototype.onMouseDown = function (event, pos) {
            origOnMouseDown?.apply(this, arguments);
            const state = this._eclipse_comparer;
            if (!state || state.dom) return;
            for (const button of (this._eclipse_navButtons || [])) {
                const insideX = pos[0] >= button.x && pos[0] <= button.x + button.w;
                const insideY = pos[1] >= button.y && pos[1] <= button.y + button.h;
                if (insideX && insideY) {
                    if (!button.disabled) {
                        _canvasSelectPair(state, button.targetIndex);
                        this.setDirtyCanvas(true, true);
                    }
                    return;
                }
            }
            state.isPointerDown = true;
            state.pointerOverPos = [...pos];
            this.setDirtyCanvas(true, false);
        };
        const origOnMouseUp = nodeType.prototype.onMouseUp;
        nodeType.prototype.onMouseUp = function () {
            origOnMouseUp?.apply(this, arguments);
            const state = this._eclipse_comparer;
            if (state && !state.dom) {
                state.isPointerDown = false;
                this.setDirtyCanvas(true, false);
            }
        };
        const origOnMouseEnter = nodeType.prototype.onMouseEnter;
        nodeType.prototype.onMouseEnter = function () {
            origOnMouseEnter?.apply(this, arguments);
            const state = this._eclipse_comparer;
            if (state && !state.dom) {
                state.isPointerOver = true;
                this.setDirtyCanvas(true, false);
            }
        };
        const origOnMouseLeave = nodeType.prototype.onMouseLeave;
        nodeType.prototype.onMouseLeave = function () {
            origOnMouseLeave?.apply(this, arguments);
            const state = this._eclipse_comparer;
            if (state && !state.dom) {
                state.isPointerOver = false;
                state.isPointerDown = false;
                this.setDirtyCanvas(true, false);
            }
        };
        const origOnMouseMove = nodeType.prototype.onMouseMove;
        nodeType.prototype.onMouseMove = function (event, pos) {
            origOnMouseMove?.apply(this, arguments);
            const state = this._eclipse_comparer;
            if (state && !state.dom && state.isPointerOver) {
                state.pointerOverPos = [...pos];
                if (!this._eclipse_rafPending) {
                    this._eclipse_rafPending = true;
                    requestAnimationFrame(() => {
                        this._eclipse_rafPending = false;
                        this.setDirtyCanvas(true, false);
                    });
                }
            }
        };
        const origOnDrawForeground = nodeType.prototype.onDrawForeground;
        nodeType.prototype.onDrawForeground = function (ctx) {
            origOnDrawForeground?.apply(this, arguments);
            const state = this._eclipse_comparer;
            if (!state || state.dom || state.images.length === 0) return;
            if (this.flags?.collapsed) return;
            const [nodeWidth, nodeHeight] = this.size;
            let y = (this.widgets?.length || 0) > 0 ? (this.widgets[this.widgets.length - 1].last_y ?? 0) + 30 : 0;
            y = _canvasDrawNavigator(ctx, this, state, y);
            const baseImage = state.selected[0] || state.selected[1];
            _canvasDrawImage(ctx, baseImage, nodeWidth, nodeHeight, y);
            if (state.isPointerOver && state.selected[0] && state.selected[1]) {
                const cropX = state.pointerOverPos[0];
                _canvasDrawImage(ctx, state.selected[1], nodeWidth, nodeHeight, y, cropX);
            }
            // Draw dimension labels
            ctx.save();
            ctx.font = '11px sans-serif';
            const pad = 4;
            const selA = state.selected[0];
            const selB = state.selected[1];
            if (selA?.img?.naturalWidth) {
                const txtA = `A: ${selA.img.naturalWidth} × ${selA.img.naturalHeight}`;
                const mA = ctx.measureText(txtA);
                const txA = pad + 2;
                const tyA = nodeHeight - pad - 4;
                ctx.fillStyle = 'rgba(0,0,0,0.6)';
                ctx.fillRect(txA - 2, tyA - 11, mA.width + 6, 15);
                ctx.fillStyle = '#ccc';
                ctx.fillText(txtA, txA, tyA);
            }
            if (selB?.img?.naturalWidth) {
                const txtB = `B: ${selB.img.naturalWidth} × ${selB.img.naturalHeight}`;
                const mB = ctx.measureText(txtB);
                const txB = nodeWidth - mB.width - pad - 2;
                const tyB = nodeHeight - pad - 4;
                ctx.fillStyle = 'rgba(0,0,0,0.6)';
                ctx.fillRect(txB - 2, tyB - 11, mB.width + 6, 15);
                ctx.fillStyle = '#ccc';
                ctx.fillText(txtB, txB, tyB);
            }
            ctx.restore();
        };
    }
});
