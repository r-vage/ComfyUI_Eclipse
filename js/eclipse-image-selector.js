/**
 * eclipse-image-selector.js
 *
 * Image Selector [Eclipse] — interactive image picker that pauses a workflow.
 *
 * First run: node shows all images in a grid with selection overlays.
 *   - Click image = toggle selection (highlighted border + checkmark)
 *   - Ctrl+A = select all, Escape = deselect all
 *   - Toolbar: [Discard ✕]  [Confirm (N) →]
 *   - Confirm POSTs indices to /eclipse/image_selector/confirm
 *     → user manually re-queues
 *   - Discard POSTs to /eclipse/image_selector/discard → fresh state
 *
 * Second run: node outputs selected images; widget shows mini-preview.
 */

import { app, api } from './comfy/index.js';
import { createDOMPreview, feedDOMPreview } from './eclipse-dom-preview.js';

const NODE_NAME = 'Image Selector [Eclipse]';

// ─────────────────────────────────────────────────────────────────────────────
// CSS injected once
// ─────────────────────────────────────────────────────────────────────────────

let _cssInjected = false;
function _injectCSS() {
    if (_cssInjected) return;
    _cssInjected = true;
    const style = document.createElement('style');
    style.textContent = `
.eclipse-sel-cell { position:relative; cursor:pointer; border:2px solid transparent;
    border-radius:3px; overflow:hidden; background:#222; transition:border-color 0.1s; }
.eclipse-sel-cell:hover { border-color:#aaa; }
.eclipse-sel-cell.selected { border-color:#4caf50; }
.eclipse-sel-check { position:absolute; top:3px; right:3px; width:18px; height:18px;
    border-radius:50%; background:rgba(76,175,80,0.95); display:none;
    align-items:center; justify-content:center; font-size:11px; color:#fff;
    pointer-events:none; font-weight:bold; }
.eclipse-sel-cell.selected .eclipse-sel-check { display:flex; }
.eclipse-sel-toolbar { display:flex; align-items:center; justify-content:space-between;
    padding:4px 6px; background:#141414; border-top:1px solid #333; gap:6px; z-index:10; }
.eclipse-sel-status { font:11px sans-serif; color:#ccc; flex:1; }
.eclipse-sel-btn { font:11px sans-serif; padding:3px 10px; border:none;
    border-radius:3px; cursor:pointer; white-space:nowrap; }
.eclipse-sel-btn-discard { background:#c62828; color:#fff; }
.eclipse-sel-btn-discard:hover { background:#e53935; }
.eclipse-sel-btn-confirm { background:#2e7d32; color:#fff; }
.eclipse-sel-btn-confirm:hover:not(:disabled) { background:#43a047; }
.eclipse-sel-btn-confirm:disabled { background:#444; color:#888; cursor:default; }
.eclipse-sel-topbar { display:flex; align-items:center; padding:0 8px; background:#141414;
    border-bottom:1px solid rgba(255,255,255,0.1); z-index:10; gap:6px; height:30px; box-sizing:border-box; }
.eclipse-sel-label { font:11px sans-serif; color:#aaa; user-select:none; }
.eclipse-sel-select { font:11px sans-serif; background:#2a2a2a; color:#ccc;
    border:1px solid #444; border-radius:3px; padding:2px 4px; outline:none;
    cursor:pointer; transition:border-color 0.2s, color 0.2s; }
.eclipse-sel-select:hover { border-color:#666; color:#fff; }
`;
    document.head.appendChild(style);
}

// ─────────────────────────────────────────────────────────────────────────────
// Build the selector widget (replaces the DOM preview's content in grid mode)
// ─────────────────────────────────────────────────────────────────────────────

function _buildSelectorUI(node, container, imageData, totalCount) {
    _injectCSS();
    container.innerHTML = '';
    container.style.cssText = 'position:relative;width:100%;height:100%;overflow:hidden;' +
        'background:#1a1a1a;display:flex;flex-direction:column;border-radius:4px;';

    // ── Top Bar (display mode select) ────────────────────────────────────────
    const topbar = document.createElement('div');
    topbar.className = 'eclipse-sel-topbar';

    const label = document.createElement('span');
    label.className = 'eclipse-sel-label';
    label.textContent = 'Preview Mode:';
    topbar.appendChild(label);

    const select = document.createElement('select');
    select.className = 'eclipse-sel-select';

    const modes = [
        { value: 'auto', label: 'Auto (Grid)' },
        { value: '1_image_per_row', label: '1 Image per Row' },
        { value: '2_images_per_row', label: '2 Images per Row' },
        { value: '3_images_per_row', label: '3 Images per Row' },
        { value: '4_images_per_row', label: '4 Images per Row' },
        { value: '5_images_per_row', label: '5 Images per Row' },
        { value: '6_images_per_row', label: '6 Images per Row' }
    ];

    modes.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.value;
        opt.textContent = m.label;
        select.appendChild(opt);
    });

    select.value = node.properties?.display_mode || 'auto';
    topbar.appendChild(select);
    container.appendChild(topbar);

    // Store dropdown on node for external (context menu) updates
    node._eclipseSelectorDropdown = select;

    // Prevent interactions with select dropdown from zooming/dragging the canvas or triggering hotkeys
    const stopPropagation = (e) => e.stopPropagation();
    select.addEventListener('click', stopPropagation);
    select.addEventListener('mousedown', stopPropagation);
    select.addEventListener('wheel', stopPropagation);
    select.addEventListener('keydown', stopPropagation);

    select.addEventListener('change', () => {
        node.properties.display_mode = select.value;
        node.setDirtyCanvas(true, true);
        applyLayout();
    });

    // Selection state
    const selected = new Set();
    let lastClickedIdx = null;
    let activeOverlay = null;

    function openLargePreview(initialIdx) {
        if (activeOverlay) return;

        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:absolute;top:0;left:0;right:0;bottom:0;' +
            'background:rgba(10,10,10,0.95);z-index:100;display:flex;flex-direction:column;' +
            'align-items:center;justify-content:center;outline:none;';
        overlay.tabIndex = 0;

        const overlayTopbar = document.createElement('div');
        overlayTopbar.style.cssText = 'width:100%;height:40px;' +
            'display:flex;align-items:center;justify-content:space-between;padding:0 12px;' +
            'background:#141414;border-bottom:1px solid rgba(255,255,255,0.1);z-index:110;box-sizing:border-box;';

        const selBtn = document.createElement('button');
        selBtn.style.cssText = 'font:11px sans-serif;padding:4px 10px;border:none;border-radius:3px;' +
            'cursor:pointer;color:#fff;font-weight:bold;transition:background 0.2s, color 0.2s;outline:none;';

        const overlayStatus = document.createElement('div');
        overlayStatus.style.cssText = 'font:12px sans-serif;color:#ccc;user-select:none;';

        const closeBtn = document.createElement('div');
        closeBtn.style.cssText = 'width:24px;height:24px;border-radius:50%;' +
            'background:rgba(255,255,255,0.1);color:#fff;display:flex;align-items:center;' +
            'justify-content:center;cursor:pointer;font-family:sans-serif;font-size:14px;' +
            'font-weight:bold;user-select:none;transition:background 0.2s;';
        closeBtn.textContent = '✕';
        closeBtn.title = 'Close preview (automatically selects this image)';

        closeBtn.addEventListener('mouseenter', () => closeBtn.style.background = 'rgba(255,255,255,0.25)');
        closeBtn.addEventListener('mouseleave', () => closeBtn.style.background = 'rgba(255,255,255,0.1)');

        const imageWrapper = document.createElement('div');
        imageWrapper.style.cssText = 'flex:1;width:100%;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative;';

        const largeImg = document.createElement('img');
        largeImg.draggable = false;
        largeImg.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain;user-select:none;cursor:pointer;';
        imageWrapper.appendChild(largeImg);

        let currentIdx = initialIdx;

        const getUrl = (idx) => {
            const data = imageData[idx];
            return api.apiURL(
                `/view?filename=${encodeURIComponent(data.filename)}` +
                `&type=${encodeURIComponent(data.type || 'temp')}` +
                `&subfolder=${encodeURIComponent(data.subfolder || '')}`
            );
        };

        function updateOverlay() {
            largeImg.src = getUrl(currentIdx);
            overlayStatus.textContent = `Image ${currentIdx + 1} of ${imageData.length}`;
            
            const isSel = selected.has(currentIdx);
            selBtn.textContent = isSel ? '✓ Selected' : 'Select';
            selBtn.style.background = isSel ? '#2e7d32' : '#444';
        }

        function toggleSelection() {
            if (selected.has(currentIdx)) {
                selected.delete(currentIdx);
                cells[currentIdx].classList.remove('selected');
            } else {
                selected.add(currentIdx);
                cells[currentIdx].classList.add('selected');
            }
            updateToolbar();
            syncSelection();
            updateOverlay();
        }

        selBtn.addEventListener('mouseenter', () => {
            const isSel = selected.has(currentIdx);
            selBtn.style.background = isSel ? '#388e3c' : '#555';
        });
        selBtn.addEventListener('mouseleave', () => {
            const isSel = selected.has(currentIdx);
            selBtn.style.background = isSel ? '#2e7d32' : '#444';
        });

        selBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleSelection();
        });

        largeImg.addEventListener('click', (e) => {
            e.stopPropagation();
            currentIdx = (currentIdx + 1) % imageData.length;
            updateOverlay();
        });

        const closeOverlay = () => {
            lastClickedIdx = currentIdx;
            overlay.remove();
            activeOverlay = null;
            container.focus({ preventScroll: true });
        };

        closeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            closeOverlay();
        });

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay || e.target === imageWrapper) {
                closeOverlay();
            }
        });

        overlay.addEventListener('keydown', (e) => {
            const key = e.key;
            if (key === 'Escape') {
                e.stopPropagation();
                e.preventDefault();
                closeOverlay();
            } else if (key === 'ArrowRight' || key === 'd' || key === 'D') {
                e.stopPropagation();
                e.preventDefault();
                currentIdx = (currentIdx + 1) % imageData.length;
                updateOverlay();
            } else if (key === 'ArrowLeft' || key === 'a' || key === 'A') {
                e.stopPropagation();
                e.preventDefault();
                currentIdx = (currentIdx - 1 + imageData.length) % imageData.length;
                updateOverlay();
            } else if (key === ' ' || key === 's' || key === 'S' || key === 'Enter') {
                e.stopPropagation();
                e.preventDefault();
                toggleSelection();
            }
        });

        overlayTopbar.appendChild(selBtn);
        overlayTopbar.appendChild(overlayStatus);
        overlayTopbar.appendChild(closeBtn);
        overlay.appendChild(overlayTopbar);
        overlay.appendChild(imageWrapper);

        container.appendChild(overlay);
        activeOverlay = overlay;

        updateOverlay();
        overlay.focus({ preventScroll: true });
    }

    async function syncSelection() {
        const indices = [...selected];
        const triggerWidget = node.widgets?.find(w => w.name === 'execution_trigger');
        if (triggerWidget) {
            triggerWidget.value = Date.now() % 2147483647;
            node.graph?.setDirtyCanvas(true, true);
        }
        if (indices.length > 0) {
            try {
                await api.fetchApi('/eclipse/image_selector/confirm', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ node_id: String(node.id), indices }),
                });
            } catch (err) {
                console.error('[Eclipse] Auto-confirm selection failed:', err);
            }
        } else {
            try {
                await api.fetchApi('/eclipse/image_selector/discard', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ node_id: String(node.id) }),
                });
            } catch (err) {
                console.error('[Eclipse] Auto-discard selection failed:', err);
            }
        }
    }

    // ── Grid area with vertical scroll ──────────────────────────────────────
    const grid = document.createElement('div');
    grid.style.cssText = 'flex:1;overflow-y:auto;overflow-x:hidden;padding:2px;gap:2px;display:grid;';
    container.appendChild(grid);

    // Stop propagation of wheel events on the grid when scrollbar is active, but bubble at boundaries
    grid.addEventListener('wheel', (e) => {
        if (grid.scrollHeight > grid.clientHeight) {
            const isScrollingDown = e.deltaY > 0;
            const isScrollingUp = e.deltaY < 0;
            const atBottom = grid.scrollTop + grid.clientHeight >= grid.scrollHeight - 1;
            const atTop = grid.scrollTop <= 0;

            if ((isScrollingDown && atBottom) || (isScrollingUp && atTop)) {
                // Let the event bubble up to container (canvas zoom/pan)
                return;
            }
            e.stopPropagation();
        }
    });

    const cells = imageData.map((data, i) => {
        const url = api.apiURL(
            `/view?filename=${encodeURIComponent(data.filename)}` +
            `&type=${encodeURIComponent(data.type || 'temp')}` +
            `&subfolder=${encodeURIComponent(data.subfolder || '')}`
        );
        const cell = document.createElement('div');
        cell.className = 'eclipse-sel-cell';

        const img = document.createElement('img');
        img.src = url;
        img.draggable = false;
        img.style.cssText = 'width:100%;height:100%;object-fit:contain;display:block;';

        const check = document.createElement('div');
        check.className = 'eclipse-sel-check';
        check.textContent = '✓';

        cell.appendChild(img);
        cell.appendChild(check);
        grid.appendChild(cell);

        cell.addEventListener('click', (e) => {
            if (e.shiftKey && lastClickedIdx !== null) {
                // Shift+click: range selection from lastClickedIdx to i
                const lo = Math.min(lastClickedIdx, i);
                const hi = Math.max(lastClickedIdx, i);
                const allSelected = Array.from({ length: hi - lo + 1 }, (_, k) => lo + k)
                    .every(k => selected.has(k));
                // If all in range are already selected, deselect the range; otherwise select all
                for (let k = lo; k <= hi; k++) {
                    if (allSelected) {
                        selected.delete(k);
                        cells[k].classList.remove('selected');
                    } else {
                        selected.add(k);
                        cells[k].classList.add('selected');
                    }
                }
            } else {
                // Normal click: toggle single image
                if (selected.has(i)) {
                    selected.delete(i);
                    cell.classList.remove('selected');
                } else {
                    selected.add(i);
                    cell.classList.add('selected');
                }
                lastClickedIdx = i;
            }
            updateToolbar();
            syncSelection();
        });

        cell.addEventListener('dblclick', (e) => {
            e.stopPropagation();
            openLargePreview(i);
        });
        return cell;
    });

    // ── Layout (same algorithm as eclipse-dom-preview.js) ───────────────────
    const aspects = { data: null };

    function applyLayout() {
        const n = imageData.length;
        if (n === 0) return;

        const gap = 2, pad = 4;
        const w = (grid.clientWidth || 200) - pad;
        const h = (grid.clientHeight || 200) - pad;

        const displayMode = node.properties?.display_mode || 'auto';

        let cols = null;
        if (displayMode === 'one_image_per_row') cols = 1;
        else if (displayMode.endsWith('_images_per_row')) {
            const m = displayMode.match(/^(\d+)_images_per_row$/);
            if (m) cols = parseInt(m[1], 10);
        }

        if (cols !== null) {
            grid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
            if (cols === 1) {
                grid.style.gridAutoRows = 'auto';
                cells.forEach((cell, idx) => {
                    const img = cell.querySelector('img');
                    const aspect = aspects.data?.[idx];
                    if (aspect) {
                        const ar = aspect.w / aspect.h;
                        const cellW = (grid.clientWidth || 200) - pad;
                        const cellH = cellW / ar;
                        cell.style.height = `${cellH}px`;
                        if (img) {
                            img.style.height = '100%';
                            img.style.maxHeight = '100%';
                            img.style.objectFit = 'contain';
                        }
                    } else {
                        // Fallback before aspects load
                        cell.style.height = '200px';
                        if (img) {
                            img.style.height = '100%';
                            img.style.maxHeight = '';
                            img.style.objectFit = 'contain';
                        }
                    }
                });
            } else {
                let avgAR = null;
                if (aspects.data && aspects.data.length > 0) {
                    avgAR = aspects.data.reduce((s, a) => s + (a.w || 1) / (a.h || 1), 0) / aspects.data.length;
                }
                const MIN_CELL_H = 100;
                const totalGaps = (cols - 1) * gap;
                const cellW = (w - totalGaps) / cols;
                const rowH = Math.max(MIN_CELL_H, Math.floor(cellW / (avgAR || 1)));

                grid.style.gridAutoRows = `${rowH}px`;
                cells.forEach(cell => {
                    cell.style.height = '';
                    const img = cell.querySelector('img');
                    if (img) {
                        img.style.height = '100%';
                        img.style.maxHeight = '';
                        img.style.objectFit = 'contain';
                    }
                });
            }
            return;
        }

        // Auto mode: reset custom cell and image height styling
        cells.forEach(cell => {
            cell.style.height = '';
            const img = cell.querySelector('img');
            if (img) {
                img.style.height = '100%';
                img.style.maxHeight = '';
                img.style.objectFit = 'contain';
            }
        });

        let avgAR = null;
        if (aspects.data) {
            avgAR = aspects.data.reduce((s, a) => s + (a.w || 1) / (a.h || 1), 0) / aspects.data.length;
        }

        // Define minimum cell dimensions for scrolling triggers
        const MIN_CELL_W = 100;
        const MIN_CELL_H = 100;

        // Compute virtual height based on minimum dimensions and image aspects
        const avg = avgAR || 1;
        const cellMinW = Math.max(MIN_CELL_W, MIN_CELL_H * avg);
        const minArea = cellMinW * MIN_CELL_H;
        const h_virtual = Math.max(h, n * minArea / w);

        const idealFloat = avgAR ? Math.sqrt(n * w / h_virtual / avgAR) : Math.sqrt(n * w / h_virtual);
        const ideal = Math.max(1, Math.round(idealFloat));
        const maxCols = Math.min(n, Math.max(ideal + 2, 4));
        let bestCols = 1, bestScore = 0;
        for (let c = 1; c <= maxCols; c++) {
            const rows = Math.ceil(n / c);
            const cellW = (w - (c - 1) * gap) / c;
            const cellH = (h_virtual - (rows - 1) * gap) / rows;
            if (cellW <= 0 || cellH <= 0) continue;  // gaps exceed container — skip
            let score;
            if (avgAR !== null) {
                score = (avgAR <= cellW / cellH)
                    ? cellH * avgAR * cellH
                    : cellW * (cellW / avgAR);
            } else {
                score = Math.min(cellW, cellH);
            }
            if (score > bestScore) { bestScore = score; bestCols = c; }
        }
        const numRows = Math.ceil(n / bestCols);
        const rowH = Math.max(MIN_CELL_H, Math.floor((h_virtual - (numRows - 1) * gap) / numRows));
        grid.style.gridTemplateColumns = `repeat(${bestCols}, 1fr)`;
        grid.style.gridAutoRows = `${rowH}px`;
    }

    const ro = new ResizeObserver(applyLayout);
    ro.observe(grid);
    applyLayout();

    // Probe aspects for better layout after images load
    Promise.all(imageData.map(data => new Promise(resolve => {
        const p = new Image();
        p.onload = () => resolve({ w: p.naturalWidth, h: p.naturalHeight });
        p.onerror = () => resolve({ w: 1, h: 1 });
        p.src = api.apiURL(
            `/view?filename=${encodeURIComponent(data.filename)}` +
            `&type=${encodeURIComponent(data.type || 'temp')}` +
            `&subfolder=${encodeURIComponent(data.subfolder || '')}`
        );
    }))).then(probed => {
        aspects.data = probed;
        applyLayout();
    });

    // ── Keyboard shortcuts ───────────────────────────────────────────────────
    container.tabIndex = 0;
    container.style.outline = 'none';
    container.addEventListener('keydown', e => {
        if (activeOverlay) {
            if (e.key === 'Escape') {
                e.stopPropagation();
                e.preventDefault();
                activeOverlay.remove();
                activeOverlay = null;
                container.focus({ preventScroll: true });
                return;
            }
        }
        if (e.key === 'a' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault(); e.stopPropagation();
            imageData.forEach((_, i) => { selected.add(i); cells[i].classList.add('selected'); });
            lastClickedIdx = imageData.length - 1;
            updateToolbar();
            syncSelection();
        } else if (e.key === 'Escape') {
            selected.clear();
            cells.forEach(c => c.classList.remove('selected'));
            lastClickedIdx = null;
            updateToolbar();
            syncSelection();
        }
    });
    container.addEventListener('click', () => container.focus({ preventScroll: true }));

    // ── Toolbar ──────────────────────────────────────────────────────────────
    const toolbar = document.createElement('div');
    toolbar.className = 'eclipse-sel-toolbar';

    const status = document.createElement('div');
    status.className = 'eclipse-sel-status';

    const btnDiscard = document.createElement('button');
    btnDiscard.className = 'eclipse-sel-btn eclipse-sel-btn-discard';
    btnDiscard.textContent = 'Discard ✕';
    btnDiscard.title = 'Clear selection and server state. Next queue shows selector again.';

    const btnConfirm = document.createElement('button');
    btnConfirm.className = 'eclipse-sel-btn eclipse-sel-btn-confirm';
    btnConfirm.disabled = true;
    btnConfirm.title = 'Confirm selection and re-queue the workflow to continue.';

    function updateVisualOrder() {
        const selectedArray = [...selected];
        cells.forEach((cell, idx) => {
            const check = cell.querySelector('.eclipse-sel-check');
            if (check) {
                const sIdx = selectedArray.indexOf(idx);
                if (sIdx !== -1) {
                    check.textContent = String(sIdx + 1);
                } else {
                    check.textContent = '';
                }
            }
        });
    }

    function updateToolbar() {
        const n = selected.size;
        status.textContent = n === 0
            ? `${totalCount} image${totalCount !== 1 ? 's' : ''}`
            : `${n} of ${totalCount} selected`;
        btnConfirm.textContent = n === 0 ? 'Confirm →' : `Confirm (${n}) →`;
        btnConfirm.disabled = n === 0;
        updateVisualOrder();
    }
    updateToolbar();

    btnDiscard.addEventListener('click', async () => {
        btnDiscard.disabled = true;
        try {
            await api.fetchApi('/eclipse/image_selector/discard', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ node_id: String(node.id) }),
            });
            // Clear visual selection
            selected.clear();
            cells.forEach(c => c.classList.remove('selected'));
            updateToolbar();
            status.textContent = 'Discarded — re-queue to restart';

            // Update execution_trigger widget so fingerprint changes on next queue
            const triggerWidget = node.widgets?.find(w => w.name === 'execution_trigger');
            if (triggerWidget) {
                triggerWidget.value = Date.now() % 2147483647;
                node.graph?.setDirtyCanvas(true, true);
            }
        } catch (err) {
            console.error('[Eclipse] ImageSelector discard error', err);
            status.textContent = 'Error discarding — check console';
        } finally {
            btnDiscard.disabled = false;
        }
    });

    btnConfirm.addEventListener('click', async () => {
        if (selected.size === 0) return;
        const indices = [...selected];
        btnConfirm.disabled = true;
        btnDiscard.disabled = true;
        try {
            const resp = await api.fetchApi('/eclipse/image_selector/confirm', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ node_id: String(node.id), indices }),
            });
            const result = await resp.json();
            if (result.ok) {
                status.textContent = `✓ ${indices.length} image${indices.length !== 1 ? 's' : ''} confirmed — re-queuing…`;
                btnDiscard.disabled = false;
                // Update execution_trigger widget so fingerprint_inputs returns a new value,
                // forcing ComfyUI to actually re-execute the node (not serve from cache).
                const triggerWidget = node.widgets?.find(w => w.name === 'execution_trigger');
                if (triggerWidget) {
                    triggerWidget.value = Date.now() % 2147483647;
                    node.graph?.setDirtyCanvas(true, true);
                }
                // Auto-requeue so workflow continues with selected images
                app.queuePrompt(0);
            } else {
                status.textContent = `Error: ${result.error}`;
                btnConfirm.disabled = false;
                btnDiscard.disabled = false;
            }
        } catch (err) {
            console.error('[Eclipse] ImageSelector confirm error', err);
            status.textContent = 'Network error — check console';
            btnConfirm.disabled = false;
            btnDiscard.disabled = false;
        }
    });

    toolbar.appendChild(status);
    toolbar.appendChild(btnDiscard);
    toolbar.appendChild(btnConfirm);
    container.appendChild(toolbar);

    node._eclipseSelectorRefreshLayout = applyLayout;
}

// ─────────────────────────────────────────────────────────────────────────────
// Extension registration
// ─────────────────────────────────────────────────────────────────────────────

app.registerExtension({
    name: 'Eclipse.ImageSelector',

    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated?.apply(this, arguments);
            // Initialize display_mode property
            if (!this.properties) this.properties = {};
            const VALID_MODES = ['auto', '1_image_per_row', '2_images_per_row', '3_images_per_row', '4_images_per_row', '5_images_per_row', '6_images_per_row'];
            if (!VALID_MODES.includes(this.properties.display_mode)) {
                this.properties.display_mode = 'auto';
            }
            // Hide execution_trigger — internal widget, not for manual editing
            const triggerWidget = this.widgets?.find(w => w.name === 'execution_trigger');
            if (triggerWidget) {
                triggerWidget.hidden = true;
                if (triggerWidget.options) triggerWidget.options.hidden = true;
            }
            // Create a standard DOM preview widget (used after second run)
            createDOMPreview(this, { minHeight: 220 });
            return ret;
        };


        // Reset execution_trigger AFTER onConfigure restores the saved workflow value.
        // onNodeCreated fires first, then onConfigure overwrites widget values from the
        // saved JSON — so we must hook onConfigure to ensure the trigger is always fresh
        // on page reload, forcing a new fingerprint and preventing ComfyUI from serving
        // stale cached output (which would leave the selector empty).
        const origOnConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (data) {
            origOnConfigure?.apply(this, arguments);
            // Reset trigger so fingerprint changes, forcing re-execution after reload.
            const triggerWidget = this.widgets?.find(w => w.name === 'execution_trigger');
            if (triggerWidget) {
                triggerWidget.value = Date.now() % 2147483647;
            }
            // Clear server-side state so next queue acts as first run (fresh selector).
            api.fetchApi('/eclipse/image_selector/discard', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ node_id: String(this.id) }),
            }).catch(() => { });
        };

        const origOnExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (output) {
            const savedImages = output.images;
            delete output.images;
            origOnExecuted?.apply(this, arguments);
            if (savedImages) output.images = savedImages;
            this.imgs = null;

            const preview = this._eclipseDomPreview;
            if (!preview) return;

            if (output.eclipseSelector?.[0] === true) {
                // First run: build interactive selector over the container
                _buildSelectorUI(this, preview.container, savedImages || [], output.totalCount?.[0] || 0);
            }
            // Second+ run: leave the selector UI untouched — full grid + Discard toolbar
            // remain visible. Selected images are passed to downstream nodes; the selector
            // itself does not update its own display.

            // Suppress ComfyUI's native image display
            const nodeOutputs = app.nodeOutputs?.[this.id];
            if (nodeOutputs?.images) delete nodeOutputs.images;
        };

        // Clean up server state when node is removed from graph
        const origOnRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            origOnRemoved?.apply(this, arguments);
            delete this._eclipseSelectorRefreshLayout;
            delete this._eclipseSelectorDropdown;
            api.fetchApi('/eclipse/image_selector/discard', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ node_id: String(this.id) }),
            }).catch(() => { });
        };
    },
});
