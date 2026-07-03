import {
    app,
    api
} from './comfy/index.js';
const NODE_NAME = 'Image Comparer [Eclipse]';

function imageDataToUrl(data) {
    return api.apiURL(`/view?filename=${encodeURIComponent(data.filename)}&type=${data.type}&subfolder=${data.subfolder}`);
}

function isVueMode() {
    try {
        return !!LiteGraph.vueNodesMode;
    } catch {
        return false;
    }
}

function buildImageList(output) {
    const aImages = output.a_images || [];
    const bImages = output.b_images || [];
    const list = [];
    const multi = aImages.length + bImages.length > 2;
    for (let i = 0; i < aImages.length; i++) {
        list.push({
            name: aImages.length > 1 || multi ? `A${i + 1}` : 'A',
            side: 'a',
            selected: i === 0,
            url: imageDataToUrl(aImages[i]),
            img: null,
        });
    }
    for (let i = 0; i < bImages.length; i++) {
        list.push({
            name: bImages.length > 1 || multi ? `B${i + 1}` : 'B',
            side: 'b',
            selected: i === 0,
            url: imageDataToUrl(bImages[i]),
            img: null,
        });
    }
    return list;
}

function setupVueMode(node) {
    const state = {
        images: [],
        selectedA: null,
        selectedB: null,
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
    imgB.style.cssText = 'width:100%; height:100%; object-fit:contain; display:none; pointer-events:none;';
    imgB.draggable = false;
    container.appendChild(imgB);
    const imgA = document.createElement('img');
    imgA.style.cssText = 'position:absolute; inset:0; width:100%; height:100%; object-fit:contain;' + 'display:none; pointer-events:none;';
    imgA.draggable = false;
    container.appendChild(imgA);
    const slider = document.createElement('div');
    slider.style.cssText = 'position:absolute; top:0; bottom:0; width:2px; background:white;' + 'pointer-events:none; mix-blend-mode:difference; z-index:10; display:none;';
    container.appendChild(slider);
    const labelBar = document.createElement('div');
    labelBar.style.cssText = 'position:absolute; top:4px; left:0; right:0; text-align:center; z-index:20; display:none;';
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
    const prevSelAName = state.selectedA?.name;
    const prevSelBName = state.selectedB?.name;
    state.images = imageList;
    const {
        imgA,
        imgB,
        labelBar
    } = state.dom;
    let nextA = prevSelAName ? imageList.find(d => d.side === 'a' && d.name === prevSelAName) : null;
    if (!nextA) nextA = imageList.find(d => d.side === 'a');
    let nextB = prevSelBName ? imageList.find(d => d.side === 'b' && d.name === prevSelBName) : null;
    if (!nextB) nextB = imageList.find(d => d.side === 'b');
    const { dimLabelA, dimLabelB } = state.dom;
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
    _vueBuildLabels(state);
}

function _vueBuildLabels(state) {
    const {
        labelBar
    } = state.dom;
    labelBar.innerHTML = '';
    if (state.images.length <= 2) {
        labelBar.style.display = 'none';
        return;
    }
    labelBar.style.display = 'block';
    for (const img of state.images) {
        const btn = document.createElement('span');
        btn.textContent = img.name;
        const isSel = (img === state.selectedA || img === state.selectedB);
        btn.style.cssText = 'cursor:pointer; padding:2px 6px; margin:0 2px; border-radius:3px;' + 'font-size:12px; font-family:sans-serif; color:white; background:rgba(0,0,0,0.6);' + `opacity:${isSel ? '1' : '0.4'};`;
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const {
                imgA,
                imgB,
                dimLabelA,
                dimLabelB
            } = state.dom;
            const sideList = state.images.filter(d => d.side === img.side);
            const index = sideList.indexOf(img);
            const otherSide = img.side === 'a' ? 'b' : 'a';
            const otherList = state.images.filter(d => d.side === otherSide);
            const matchingOther = otherList[index] || otherList[0];
            if (img.side === 'a') {
                imgA.onload = function () { dimLabelA.textContent = `A: ${this.naturalWidth} × ${this.naturalHeight}`; };
                imgA.src = img.url;
                state.selectedA = img;
                if (matchingOther) {
                    imgB.onload = function () { dimLabelB.textContent = `B: ${this.naturalWidth} × ${this.naturalHeight}`; };
                    imgB.src = matchingOther.url;
                    state.selectedB = matchingOther;
                }
            } else {
                imgB.onload = function () { dimLabelB.textContent = `B: ${this.naturalWidth} × ${this.naturalHeight}`; };
                imgB.src = img.url;
                state.selectedB = img;
                if (matchingOther) {
                    imgA.onload = function () { dimLabelA.textContent = `A: ${this.naturalWidth} × ${this.naturalHeight}`; };
                    imgA.src = matchingOther.url;
                    state.selectedA = matchingOther;
                }
            }
            _vueApplyMode(state);
            _vueUpdateLabelOpacities(state);
        });
        labelBar.appendChild(btn);
    }
}

function _vueUpdateLabelOpacities(state) {
    const btns = state.dom.labelBar.children;
    for (let i = 0; i < btns.length && i < state.images.length; i++) {
        const img = state.images[i];
        btns[i].style.opacity = (img === state.selectedA || img === state.selectedB) ? '1' : '0.4';
    }
}

function setupCanvasMode(node) {
    node._eclipse_comparer = {
        images: [],
        selected: [null, null],
        isPointerDown: false,
        isPointerOver: false,
        pointerOverPos: [0, 0],
    };
    const origComputeSize = node.computeSize;
    node.computeSize = function () {
        const sz = origComputeSize?.apply(this, arguments) || [300, 200];
        sz[1] = Math.max(sz[1], 300);
        return sz;
    };
    node.setSize(node.computeSize());
}

function _canvasFeedImages(state, imageList) {
    const prevSelAName = state.selected?.[0]?.name;
    const prevSelBName = state.selected?.[1]?.name;
    state.images = imageList;
    let nextA = prevSelAName ? imageList.find(d => d.side === 'a' && d.name === prevSelAName) : null;
    if (!nextA) nextA = imageList.find(d => d.side === 'a');
    let nextB = prevSelBName ? imageList.find(d => d.side === 'b' && d.name === prevSelBName) : null;
    if (!nextB) nextB = imageList.find(d => d.side === 'b');
    _canvasSetSelected(state, [nextA, nextB]);
}

function _canvasSetSelected(state, selected) {
    state.images.forEach(d => d.selected = false);
    for (const sel of selected) {
        if (!sel) continue;
        if (!sel.img) {
            sel.img = new Image();
            sel.img.src = sel.url;
        }
        sel.selected = true;
    }
    state.selected = [selected[0] || null, selected[1] || null];
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
        const origOnMouseDown = nodeType.prototype.onMouseDown;
        nodeType.prototype.onMouseDown = function (event, pos) {
            origOnMouseDown?.apply(this, arguments);
            const state = this._eclipse_comparer;
            if (!state || state.dom) return;
            if (state.images.length > 2) {
                const labelY = this._eclipse_labelY || 0;
                if (pos[1] >= labelY && pos[1] <= labelY + 20) {
                    for (const btn of (this._eclipse_labelBtns || [])) {
                        if (pos[0] >= btn.x && pos[0] <= btn.x + btn.w) {
                            const sideList = state.images.filter(d => d.side === btn.data.side);
                            const index = sideList.indexOf(btn.data);
                            const otherSide = btn.data.side === 'a' ? 'b' : 'a';
                            const otherList = state.images.filter(d => d.side === otherSide);
                            const matchingOther = otherList[index] || otherList[0];
                            const sel = [
                                btn.data.side === 'a' ? btn.data : matchingOther,
                                btn.data.side === 'b' ? btn.data : matchingOther
                            ];
                            _canvasSetSelected(state, sel);
                            this.setDirtyCanvas(true, true);
                            return;
                        }
                    }
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
            this._eclipse_labelBtns = [];
            if (state.images.length > 2) {
                this._eclipse_labelY = y;
                ctx.save();
                ctx.textAlign = 'left';
                ctx.textBaseline = 'top';
                ctx.font = '14px Arial';
                const spacing = 5;
                const drawData = [];
                let totalW = 0;
                for (const img of state.images) {
                    const w = ctx.measureText(img.name).width + 4;
                    drawData.push({
                        img,
                        w
                    });
                    totalW += w + spacing;
                }
                let x = (nodeWidth - (totalW - spacing)) / 2;
                for (const d of drawData) {
                    ctx.fillStyle = d.img.selected ? 'rgba(180, 180, 180, 1)' : 'rgba(180, 180, 180, 0.5)';
                    ctx.fillText(d.img.name, x, y);
                    this._eclipse_labelBtns.push({
                        x,
                        w: d.w,
                        data: d.img
                    });
                    x += d.w + spacing;
                }
                ctx.restore();
                y += 20;
            }
            _canvasDrawImage(ctx, state.selected[0], nodeWidth, nodeHeight, y);
            if (state.isPointerOver && state.selected[1]) {
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
