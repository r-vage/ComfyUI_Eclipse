import {
    app,
    api
} from './comfy/index.js';
const WIDGET_NAME = '_eclipse_dom_preview';

function imageUrl(data) {
    return api.apiURL(`/view?filename=${encodeURIComponent(data.filename)}` + `&type=${encodeURIComponent(data.type || 'temp')}` + `&subfolder=${encodeURIComponent(data.subfolder || '')}`);
}

// Probe natural dimensions of all images. Returns Promise<Array<{w,h}>>.
// Images are already in the browser cache from the grid cells, so this is near-instant.
function _probeAspects(images) {
    return Promise.all(images.map(data => new Promise(resolve => {
        const probe = new Image();
        probe.onload = () => resolve({ w: probe.naturalWidth, h: probe.naturalHeight });
        probe.onerror = () => resolve({ w: 1, h: 1 });
        probe.src = imageUrl(data);
    })));
}

export function createDOMPreview(node, opts = {}) {
    const minH = opts.minHeight ?? 100;
    const container = document.createElement('div');
    container.style.cssText = 'position:relative;width:100%;height:100%;' + 'overflow:hidden;background:#1a1a1a;user-select:none;display:flex;' + 'align-items:center;justify-content:center;' + 'border-radius:4px;';
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
    const img = document.createElement('img');
    img.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain;display:none;';
    img.draggable = false;
    container.appendChild(img);
    const grid = document.createElement('div');
    grid.style.cssText = 'display:none;width:100%;height:100%;' + 'gap:2px;overflow-y:auto;overflow-x:hidden;padding:2px;';
    container.appendChild(grid);

    // Stop propagation of wheel events on the grid when scrolling, but bubble at boundaries
    grid.addEventListener('wheel', (e) => {
        if (grid.scrollHeight > grid.clientHeight) {
            const isScrollingDown = e.deltaY > 0;
            const isScrollingUp = e.deltaY < 0;
            const atBottom = grid.scrollTop + grid.clientHeight >= grid.scrollHeight - 1;
            const atTop = grid.scrollTop <= 0;
            
            if ((isScrollingDown && atBottom) || (isScrollingUp && atTop)) {
                // Allow event to bubble up to container/canvas zoom-pan
                return;
            }
            e.stopPropagation();
        }
    });

    const dimLabel = document.createElement('div');
    dimLabel.style.cssText = 'position:absolute;bottom:4px;right:4px;font:11px sans-serif;' + 'color:#ccc;pointer-events:none;background:rgba(0,0,0,0.6);' + 'padding:1px 5px;border-radius:3px;';
    container.appendChild(dimLabel);
    const idxLabel = document.createElement('div');
    idxLabel.style.cssText = 'position:absolute;bottom:4px;left:4px;font:11px sans-serif;' + 'color:#ccc;pointer-events:none;background:rgba(0,0,0,0.6);' + 'padding:1px 5px;border-radius:3px;display:none;';
    container.appendChild(idxLabel);
    const state = {
        images: [],
        index: 0,
        mode: 'single',
        aspects: null,  // Array<{w,h}> — populated once after probing, reset on new image set
    };

    // AR-aware column selection: score = actual rendered area of object-fit:contain
    // using the average image AR. Correctly picks portrait-optimised columns.
    function applyLayout() {
        if (state.mode !== 'grid' || state.images.length <= 1) return;
        const n = state.images.length;
        const gap = 2;
        const pad = 4;
        const w = (container.clientWidth || 200) - pad;
        const h = (container.clientHeight || 200) - pad;

        // Average AR from probed aspects, or null (fall back to size-only scoring)
        let avgAR = null;
        if (state.aspects && state.aspects.length > 0) {
            avgAR = state.aspects.reduce((s, a) => s + (a.w || 1) / (a.h || 1), 0) / state.aspects.length;
        }

        // Define minimum cell dimensions for scrolling triggers
        const MIN_CELL_W = 100;
        const MIN_CELL_H = 100;

        // Compute virtual height if content is dense
        const avg = avgAR || 1;
        const cellMinW = Math.max(MIN_CELL_W, MIN_CELL_H * avg);
        const minArea = cellMinW * MIN_CELL_H;
        const h_virtual = Math.max(h, n * minArea / w);

        const idealFloat = avgAR
            ? Math.sqrt(n * w / h_virtual / avgAR)
            : Math.sqrt(n * w / h_virtual);
        const ideal = Math.max(1, Math.round(idealFloat));
        const maxCols = Math.min(n, Math.max(ideal + 2, 4));

        let bestCols = 1;
        let bestScore = 0;
        for (let c = 1; c <= maxCols; c++) {
            const rows = Math.ceil(n / c);
            const cellW = (w - (c - 1) * gap) / c;
            const cellH = (h_virtual - (rows - 1) * gap) / rows;
            if (cellW <= 0 || cellH <= 0) continue;  // gaps exceed container — skip
            let score;
            if (avgAR !== null) {
                const cellAR = cellW / cellH;
                if (avgAR <= cellAR) {
                    // Portrait/square in a wide cell — height-constrained
                    score = cellH * avgAR * cellH;   // (cellH × AR) × cellH
                } else {
                    // Landscape in a tall cell — width-constrained
                    score = cellW * (cellW / avgAR);  // cellW × (cellW / AR)
                }
            } else {
                score = Math.min(cellW, cellH);
            }
            if (score > bestScore) {
                bestScore = score;
                bestCols = c;
            }
        }
        const numRows = Math.ceil(n / bestCols);
        const rowH = Math.max(MIN_CELL_H, Math.floor((h_virtual - (numRows - 1) * gap) / numRows));
        grid.style.gridTemplateColumns = `repeat(${bestCols}, 1fr)`;
        grid.style.gridAutoRows = `${rowH}px`;
    }
    state._applyLayout = applyLayout;
    const resizeObserver = new ResizeObserver(applyLayout);
    resizeObserver.observe(container);
    container.addEventListener('click', (e) => {
        e.stopPropagation();
        if (state.images.length <= 1) return;
        if (state.mode === 'single') {
            state.index = (state.index + 1) % state.images.length;
            showSingle(state, img, dimLabel, idxLabel);
        }
        opts.onClick?.(state.index);
    });
    container.addEventListener('dblclick', (e) => {
        e.stopPropagation();
        if (state.images.length <= 1) return;
        state.mode = state.mode === 'single' ? 'grid' : 'single';
        if (state.mode === 'grid') {
            showGrid(state, img, grid, dimLabel, idxLabel);
        } else {
            showSingle(state, img, dimLabel, idxLabel);
            grid.style.display = 'none';
        }
    });
    // Keyboard arrow navigation: click to focus, then left/right to cycle frames.
    container.tabIndex = 0;
    container.style.outline = 'none';
    container.addEventListener('click', () => {
        container.focus({ preventScroll: true });
    });
    container.addEventListener('keydown', (e) => {
        if (state.images.length <= 1) return;
        if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
        e.stopPropagation();
        e.preventDefault();
        // Exit grid view on first arrow press
        if (state.mode === 'grid') {
            state.mode = 'single';
            grid.style.display = 'none';
        }
        state.index = e.key === 'ArrowRight'
            ? (state.index + 1) % state.images.length
            : (state.index - 1 + state.images.length) % state.images.length;
        showSingle(state, img, dimLabel, idxLabel);
    });
    if (opts.noWidget) {
        node._eclipseDomPreview = {
            container,
            img,
            grid,
            dimLabel,
            idxLabel,
            state,
            widget: null
        };
        return container;
    }
    const widget = node.addDOMWidget(WIDGET_NAME, 'custom', container, {
        hideOnZoom: false,
        serialize: false,
        getMinHeight: () => state.hidden ? 0 : minH,
        getMaxHeight: () => state.hidden ? 0 : 4096,
    });
    const compact = node.computeSize();
    node.size[0] = Math.max(node.size[0], compact[0]);
    node.size[1] = compact[1];
    node._eclipseDomPreview = {
        container,
        img,
        grid,
        dimLabel,
        idxLabel,
        state,
        widget
    };
    return widget;
}
export function feedDOMPreview(node, output) {
    const preview = node._eclipseDomPreview;
    if (!preview) return;
    const imageData = output?.images;
    if (!imageData || imageData.length === 0) {
        clearDOMPreview(node);
        return;
    }
    const {
        img,
        grid,
        dimLabel,
        idxLabel,
        state
    } = preview;
    state.images = imageData;
    state.index = 0;
    state.aspects = null;  // reset so grid reprobes dimensions for the new image set
    if (imageData.length > 1) {
        state.mode = 'grid';
        showGrid(state, img, grid, dimLabel, idxLabel);
    } else {
        state.mode = 'single';
        showSingle(state, img, dimLabel, idxLabel);
        grid.style.display = 'none';
    }
    node.images = imageData;
}
export function clearDOMPreview(node) {
    const preview = node._eclipseDomPreview;
    if (!preview) return;
    const {
        img,
        grid,
        dimLabel,
        idxLabel,
        state
    } = preview;
    state.images = [];
    state.index = 0;
    state.aspects = null;
    img.style.display = 'none';
    img.removeAttribute('src');
    grid.style.display = 'none';
    grid.innerHTML = '';
    dimLabel.textContent = '';
    idxLabel.style.display = 'none';
}
export function hasDOMPreview(node) {
    return !!node._eclipseDomPreview;
}

function showSingle(state, img, dimLabel, idxLabel) {
    if (state.images.length === 0) {
        img.style.display = 'none';
        dimLabel.textContent = '';
        idxLabel.style.display = 'none';
        return;
    }
    const data = state.images[state.index];
    const url = imageUrl(data);
    img.onload = function () {
        dimLabel.textContent = `${this.naturalWidth} × ${this.naturalHeight}`;
    };
    img.src = url;
    img.style.display = 'block';
    if (state.images.length > 1) {
        idxLabel.textContent = `${state.index + 1} / ${state.images.length}`;
        idxLabel.style.display = 'block';
    } else {
        idxLabel.style.display = 'none';
    }
}

function showGrid(state, singleImg, grid, dimLabel, idxLabel) {
    singleImg.style.display = 'none';
    dimLabel.textContent = '';
    idxLabel.style.display = 'none';
    grid.innerHTML = '';
    grid.style.display = 'grid';
    // Build grid cells
    const n = state.images.length;
    for (let i = 0; i < n; i++) {
        const data = state.images[i];
        const cell = document.createElement('img');
        cell.src = imageUrl(data);
        cell.style.cssText = 'width:100%;height:100%;object-fit:contain;cursor:pointer;' + 'border-radius:2px;background:#222;';
        cell.draggable = false;
        cell.addEventListener('click', (e) => {
            e.stopPropagation();
            state.index = i;
            state.mode = 'single';
            showSingle(state, singleImg, dimLabel, idxLabel);
            grid.style.display = 'none';
        });
        grid.appendChild(cell);
    }
    // Apply layout immediately (uses aspects if already known, otherwise size-only scoring)
    state._applyLayout?.();
    // Probe image dimensions if not yet cached, then refine column layout
    if (!state.aspects) {
        _probeAspects(state.images).then(aspects => {
            state.aspects = aspects;
            if (state.mode === 'grid') state._applyLayout?.();
        });
    }
}
