/**
 * Eclipse Image Browser
 *
 * Framework-independent image picker used by Eclipse image-loader nodes.
 * It deliberately depends only on browser DOM APIs and a caller-provided
 * preview URL builder so it remains stable across ComfyUI renderers.
 */

export const ECLIPSE_IMAGE_BROWSER_STORAGE_KEY = 'Eclipse.ImageBrowser.preferences';

const VALID_LAYOUTS = new Set(['grid', 'list']);
const VALID_SORTS = new Set(['default', 'name-asc']);
const IMAGE_ACCEPT = 'image/png,image/jpeg,image/webp,image/bmp,image/gif,image/tiff,.tif,.tiff';
const GRID_ITEM_HEIGHT = 126;
const GRID_ITEM_WIDTH = 96;
const LIST_ITEM_HEIGHT = 58;
const RENDER_BUFFER_ROWS = 2;
const DEFAULT_POPOVER_WIDTH = 420;
const DEFAULT_POPOVER_HEIGHT = 590;
const MIN_POPOVER_WIDTH = 320;
const MIN_POPOVER_HEIGHT = 260;
const MAX_POPOVER_WIDTH = 1200;
const MAX_POPOVER_HEIGHT = 1000;
let _styleInjected = false;
let _browserCounter = 0;

function normalizePopoverDimension(value, fallback, minimum, maximum) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return fallback;
    return Math.round(Math.min(maximum, Math.max(minimum, value)));
}

export function normalizeImageBrowserPreferences(value) {
    return {
        layout: VALID_LAYOUTS.has(value?.layout) ? value.layout : 'grid',
        sort: VALID_SORTS.has(value?.sort) ? value.sort : 'default',
        width: normalizePopoverDimension(value?.width, DEFAULT_POPOVER_WIDTH, MIN_POPOVER_WIDTH, MAX_POPOVER_WIDTH),
        height: normalizePopoverDimension(value?.height, DEFAULT_POPOVER_HEIGHT, MIN_POPOVER_HEIGHT, MAX_POPOVER_HEIGHT),
    };
}

export function loadImageBrowserPreferences(storage = globalThis.localStorage) {
    try {
        const raw = storage?.getItem(ECLIPSE_IMAGE_BROWSER_STORAGE_KEY);
        return normalizeImageBrowserPreferences(raw ? JSON.parse(raw) : null);
    } catch {
        return normalizeImageBrowserPreferences(null);
    }
}

export function saveImageBrowserPreferences(value, storage = globalThis.localStorage) {
    const normalized = normalizeImageBrowserPreferences(value);
    try {
        storage?.setItem(ECLIPSE_IMAGE_BROWSER_STORAGE_KEY, JSON.stringify(normalized));
    } catch {
        // Preferences are optional; private browsing and quota failures are harmless.
    }
    return normalized;
}

export function filterAndSortImageFiles(files, query = '', sort = 'default') {
    const words = String(query).trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    let result = Array.from(files || []).filter((file) => {
        const name = String(file).toLocaleLowerCase();
        return words.every((word) => name.includes(word));
    });
    if (sort === 'name-asc') {
        result = result.slice().sort((a, b) => String(a).localeCompare(String(b), undefined, {
            numeric: true,
            sensitivity: 'base',
        }));
    }
    return result;
}

export function chooseAdjacentImage(files, deletedIndex) {
    if (!files?.length) return '';
    if (!Number.isInteger(deletedIndex) || deletedIndex < 0) return files[0];
    const adjacentIndex = deletedIndex > 0 ? deletedIndex - 1 : 0;
    return files[Math.min(adjacentIndex, files.length - 1)];
}

function injectBrowserCSS() {
    if (_styleInjected || typeof document === 'undefined') return;
    _styleInjected = true;
    const style = document.createElement('style');
    style.id = 'eclipse-image-browser-styles';
    style.textContent = `
.eclipse-image-browser-widget{display:flex;width:100%;height:100%;min-width:0;padding:0 0 4px;box-sizing:border-box;font:12px sans-serif;color:#ddd}
.eclipse-image-browser-trigger{display:flex;align-items:center;width:100%;height:28px;min-width:0;border:1px solid #454545;border-radius:5px;background:#242424;color:#ddd;overflow:hidden}
.eclipse-image-browser-trigger-main{display:flex;align-items:center;justify-content:space-between;gap:6px;min-width:0;flex:1;height:100%;padding:0 8px;border:0;background:transparent;color:inherit;cursor:pointer}
.eclipse-image-browser-trigger-main:hover,.eclipse-image-browser-tool:hover{background:#343434}
.eclipse-image-browser-trigger-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left}
.eclipse-image-browser-chevron{flex:none;color:#999}
.eclipse-image-browser-tool{position:relative;display:flex;align-items:center;justify-content:center;width:30px;height:100%;flex:none;border:0;border-left:1px solid #454545;background:transparent;color:#ccc;cursor:pointer}
.eclipse-image-browser-file{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer}
.eclipse-image-browser-popover{position:fixed;display:flex;flex-direction:column;box-sizing:border-box;min-width:min(320px,calc(100vw - 16px));min-height:min(260px,calc(100vh - 16px));max-width:calc(100vw - 16px);max-height:calc(100vh - 16px);z-index:100000;border:1px solid #555;border-radius:8px;background:#202020;color:#ddd;box-shadow:0 12px 32px rgba(0,0,0,.62);font:12px sans-serif;overflow:hidden;resize:both}
.eclipse-image-browser-popover::after{content:"";position:absolute;right:3px;bottom:3px;width:9px;height:9px;border-right:2px solid #888;border-bottom:2px solid #888;pointer-events:none;opacity:.8}
.eclipse-image-browser-toolbar{display:flex;align-items:center;gap:6px;padding:9px;border-bottom:1px solid #3f3f3f}
.eclipse-image-browser-search{min-width:0;flex:1;height:30px;box-sizing:border-box;border:1px solid #484848;border-radius:5px;background:#151515;color:#eee;padding:0 9px;outline:none}
.eclipse-image-browser-search:focus{border-color:#4a8a5a}
.eclipse-image-browser-action{height:30px;min-width:30px;padding:0 8px;border:1px solid #484848;border-radius:5px;background:#2a2a2a;color:#ddd;cursor:pointer;white-space:nowrap}
.eclipse-image-browser-action:hover,.eclipse-image-browser-action.active{background:#365a42;border-color:#4a8a5a}
.eclipse-image-browser-action:disabled{opacity:.45;cursor:default}
.eclipse-image-browser-status{min-height:17px;padding:4px 10px 0;color:#aaa;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.eclipse-image-browser-status.error{color:#ffaaaa}
.eclipse-image-browser-viewport{position:relative;min-height:0;flex:1;overflow-y:auto;overscroll-behavior:contain;outline:none;scrollbar-gutter:stable}
.eclipse-image-browser-resize-gutter{height:16px;flex:none;pointer-events:none}
.eclipse-image-browser-virtual{position:relative;width:100%}
.eclipse-image-browser-option{position:absolute;box-sizing:border-box;border:1px solid transparent;border-radius:5px;background:#292929;color:#ddd;cursor:pointer;overflow:hidden;text-align:left}
.eclipse-image-browser-option:hover,.eclipse-image-browser-option.candidate{background:#383838;border-color:#666}
.eclipse-image-browser-option.selected{border-color:#65a875;box-shadow:inset 0 0 0 1px #65a875;background:#314237}
.eclipse-image-browser-option.grid{display:flex;flex-direction:column;align-items:stretch;padding:4px}
.eclipse-image-browser-option.list{display:flex;align-items:center;gap:9px;padding:4px 8px}
.eclipse-image-browser-thumb{display:block;flex:none;background:#151515;object-fit:cover;border-radius:3px}
.eclipse-image-browser-option.grid .eclipse-image-browser-thumb{width:100%;height:84px}
.eclipse-image-browser-option.list .eclipse-image-browser-thumb{width:46px;height:46px}
.eclipse-image-browser-label{min-width:0;overflow:hidden;text-overflow:ellipsis;color:#ddd}
.eclipse-image-browser-option.grid .eclipse-image-browser-label{display:-webkit-box;margin-top:5px;font-size:11px;line-height:14px;-webkit-line-clamp:2;-webkit-box-orient:vertical;white-space:normal;word-break:break-word;text-align:center}
.eclipse-image-browser-option.list .eclipse-image-browser-label{white-space:nowrap}
.eclipse-image-browser-empty{display:flex;align-items:center;justify-content:center;height:100%;color:#888}
.eclipse-image-browser-drop{outline:2px dashed #65a875;outline-offset:-5px}
`;
    document.head.appendChild(style);
}

function stopNodePointer(event) {
    event.stopPropagation();
}

function setButtonText(button, name) {
    const label = button.querySelector('.eclipse-image-browser-trigger-name');
    if (label) label.textContent = name || 'Select image…';
    button.title = name || 'Select image';
}

export function createEclipseImageBrowser(options) {
    injectBrowserCSS();
    const id = `eclipse-image-browser-${++_browserCounter}`;
    const prefs = loadImageBrowserPreferences(options.storage);
    let source = options.source === 'output' ? 'output' : 'input';
    let files = [];
    let selected = options.selected || '';
    let filtered = [];
    let query = '';
    let layout = prefs.layout;
    let sort = prefs.sort;
    let popoverWidth = prefs.width;
    let popoverHeight = prefs.height;
    let appliedPopoverWidth = 0;
    let appliedPopoverHeight = 0;
    let candidateIndex = -1;
    let popover = null;
    let viewport = null;
    let virtual = null;
    let searchInput = null;
    let statusEl = null;
    let sortButton = null;
    let gridButton = null;
    let listButton = null;
    let uploadAction = null;
    let resizeObserver = null;
    let renderFrame = 0;
    let destroyed = false;
    let outsidePointerHandler = null;
    let repositionHandler = null;

    const root = document.createElement('div');
    root.className = 'eclipse-image-browser-widget';
    root.dataset.eclipseImageBrowser = id;
    const trigger = document.createElement('div');
    trigger.className = 'eclipse-image-browser-trigger';
    const mainButton = document.createElement('button');
    mainButton.type = 'button';
    mainButton.className = 'eclipse-image-browser-trigger-main';
    mainButton.setAttribute('aria-haspopup', 'listbox');
    mainButton.setAttribute('aria-expanded', 'false');
    mainButton.innerHTML = '<span class="eclipse-image-browser-trigger-name"></span><span class="eclipse-image-browser-chevron" aria-hidden="true">▾</span>';
    setButtonText(mainButton, selected);
    const quickUpload = document.createElement('label');
    quickUpload.className = 'eclipse-image-browser-tool';
    quickUpload.title = 'Upload image(s)';
    quickUpload.setAttribute('aria-label', 'Upload image(s)');
    quickUpload.textContent = '＋';
    const quickFileInput = document.createElement('input');
    quickFileInput.type = 'file';
    quickFileInput.multiple = true;
    quickFileInput.accept = IMAGE_ACCEPT;
    quickFileInput.className = 'eclipse-image-browser-file';
    quickUpload.appendChild(quickFileInput);
    trigger.append(mainButton, quickUpload);
    root.appendChild(trigger);

    const isImageFile = (file) => file?.type?.startsWith('image/') || /\.(png|jpe?g|webp|bmp|gif|tiff?)$/i.test(file?.name || '');

    function persistPreferences() {
        saveImageBrowserPreferences({
            layout,
            sort,
            width: popoverWidth,
            height: popoverHeight,
        }, options.storage);
    }

    function setStatus(message = '', isError = false) {
        if (!statusEl) return;
        statusEl.textContent = message;
        statusEl.title = message;
        statusEl.classList.toggle('error', !!isError);
    }

    function recompute({ keepCandidate = false } = {}) {
        filtered = filterAndSortImageFiles(files, query, sort);
        if (!keepCandidate) {
            candidateIndex = Math.max(0, filtered.indexOf(selected));
            if (!filtered.length) candidateIndex = -1;
        } else if (candidateIndex >= filtered.length) {
            candidateIndex = filtered.length - 1;
        }
        if (viewport) viewport.scrollTop = 0;
        scheduleRender();
    }

    function layoutMetrics() {
        const width = Math.max(180, viewport?.clientWidth || 400);
        if (layout === 'list') {
            return { columns: 1, rowHeight: LIST_ITEM_HEIGHT, itemWidth: width - 12, gap: 0, pad: 6 };
        }
        const pad = 8;
        const gap = 8;
        const columns = Math.max(1, Math.floor((width - pad * 2 + gap) / (GRID_ITEM_WIDTH + gap)));
        return { columns, rowHeight: GRID_ITEM_HEIGHT, itemWidth: GRID_ITEM_WIDTH, gap, pad };
    }

    function optionId(index) {
        return `${id}-option-${index}`;
    }

    function render() {
        renderFrame = 0;
        if (!virtual || !viewport || destroyed) return;
        virtual.replaceChildren();
        if (!filtered.length) {
            virtual.style.height = '100%';
            const empty = document.createElement('div');
            empty.className = 'eclipse-image-browser-empty';
            empty.textContent = query ? 'No matching images' : 'No images';
            virtual.appendChild(empty);
            viewport.removeAttribute('aria-activedescendant');
            return;
        }
        const metrics = layoutMetrics();
        const rows = Math.ceil(filtered.length / metrics.columns);
        virtual.style.height = `${metrics.pad * 2 + rows * metrics.rowHeight}px`;
        const viewportHeight = Math.max(1, viewport.clientHeight || 500);
        const firstRow = Math.max(0, Math.floor(viewport.scrollTop / metrics.rowHeight) - RENDER_BUFFER_ROWS);
        const lastRow = Math.min(rows, Math.ceil((viewport.scrollTop + viewportHeight) / metrics.rowHeight) + RENDER_BUFFER_ROWS);
        const start = firstRow * metrics.columns;
        const end = Math.min(filtered.length, lastRow * metrics.columns);
        for (let index = start; index < end; index++) {
            const filename = filtered[index];
            const row = Math.floor(index / metrics.columns);
            const col = index % metrics.columns;
            const option = document.createElement('button');
            option.type = 'button';
            option.id = optionId(index);
            option.className = `eclipse-image-browser-option ${layout}`;
            option.classList.toggle('selected', filename === selected);
            option.classList.toggle('candidate', index === candidateIndex);
            option.setAttribute('role', 'option');
            option.setAttribute('aria-selected', filename === selected ? 'true' : 'false');
            option.dataset.index = String(index);
            option.style.left = `${metrics.pad + col * (metrics.itemWidth + metrics.gap)}px`;
            option.style.top = `${metrics.pad + row * metrics.rowHeight}px`;
            option.style.width = `${metrics.itemWidth}px`;
            option.style.height = `${metrics.rowHeight - (layout === 'grid' ? 8 : 4)}px`;
            const img = document.createElement('img');
            img.className = 'eclipse-image-browser-thumb';
            img.alt = '';
            img.loading = 'lazy';
            img.decoding = 'async';
            img.draggable = false;
            img.src = options.buildPreviewURL(filename, source);
            const label = document.createElement('span');
            label.className = 'eclipse-image-browser-label';
            label.textContent = filename;
            label.title = filename;
            option.append(img, label);
            option.addEventListener('pointerdown', stopNodePointer);
            option.addEventListener('click', () => selectIndex(index));
            virtual.appendChild(option);
        }
        if (candidateIndex >= 0) viewport.setAttribute('aria-activedescendant', optionId(candidateIndex));
    }

    function scheduleRender() {
        if (renderFrame || destroyed || !popover) return;
        renderFrame = requestAnimationFrame(render);
    }

    function ensureCandidateVisible() {
        if (!viewport || candidateIndex < 0) return;
        const metrics = layoutMetrics();
        const row = Math.floor(candidateIndex / metrics.columns);
        const top = metrics.pad + row * metrics.rowHeight;
        const bottom = top + metrics.rowHeight;
        if (top < viewport.scrollTop) viewport.scrollTop = top;
        else if (bottom > viewport.scrollTop + viewport.clientHeight) viewport.scrollTop = bottom - viewport.clientHeight;
    }

    async function selectIndex(index) {
        const filename = filtered[index];
        if (!filename) return;
        candidateIndex = index;
        selected = filename;
        setButtonText(mainButton, selected);
        scheduleRender();
        await options.onSelect?.(filename, source);
        close();
    }

    function moveCandidate(delta, absolute = false) {
        if (!filtered.length) return;
        const metrics = layoutMetrics();
        if (absolute) candidateIndex = delta < 0 ? 0 : filtered.length - 1;
        else candidateIndex = Math.max(0, Math.min(filtered.length - 1, (candidateIndex < 0 ? 0 : candidateIndex) + delta));
        viewport?.setAttribute('aria-activedescendant', optionId(candidateIndex));
        ensureCandidateVisible();
        scheduleRender();
    }

    function onKeyDown(event) {
        const columns = layoutMetrics().columns;
        const moves = {
            ArrowLeft: -1,
            ArrowRight: 1,
            ArrowUp: -columns,
            ArrowDown: columns,
        };
        const editingSearch = event.target === searchInput;
        if (editingSearch && (event.key === 'ArrowLeft' || event.key === 'ArrowRight' || event.key === 'Home' || event.key === 'End')) {
            return;
        }
        if (event.key in moves) {
            event.preventDefault();
            event.stopPropagation();
            moveCandidate(moves[event.key]);
        } else if (event.key === 'Home' || event.key === 'End') {
            event.preventDefault();
            event.stopPropagation();
            moveCandidate(event.key === 'Home' ? -1 : 1, true);
        } else if (event.key === 'Enter' && candidateIndex >= 0) {
            event.preventDefault();
            event.stopPropagation();
            void selectIndex(candidateIndex);
        } else if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            close();
            mainButton.focus({ preventScroll: true });
        }
    }

    function positionPopover() {
        if (!popover) return;
        const anchor = trigger.getBoundingClientRect();
        const margin = 8;
        const availableWidth = Math.max(1, window.innerWidth - margin * 2);
        const availableHeight = Math.max(1, window.innerHeight - margin * 2);
        const width = Math.min(availableWidth, Math.max(Math.min(MIN_POPOVER_WIDTH, availableWidth), popoverWidth));
        const height = Math.min(availableHeight, Math.max(Math.min(MIN_POPOVER_HEIGHT, availableHeight), popoverHeight));
        let left = Math.min(Math.max(margin, anchor.left), window.innerWidth - width - margin);
        let top = anchor.bottom + 6;
        if (top + height > window.innerHeight - margin) top = Math.max(margin, anchor.top - height - 6);
        popover.style.width = `${width}px`;
        popover.style.height = `${height}px`;
        popover.style.left = `${left}px`;
        popover.style.top = `${top}px`;
        appliedPopoverWidth = width;
        appliedPopoverHeight = height;
    }

    function onPopoverResize() {
        if (!popover) return;
        const rect = popover.getBoundingClientRect();
        const widthChanged = Math.abs(rect.width - appliedPopoverWidth) > 1;
        const heightChanged = Math.abs(rect.height - appliedPopoverHeight) > 1;
        if (widthChanged || heightChanged) {
            popoverWidth = normalizePopoverDimension(rect.width, popoverWidth, MIN_POPOVER_WIDTH, MAX_POPOVER_WIDTH);
            popoverHeight = normalizePopoverDimension(rect.height, popoverHeight, MIN_POPOVER_HEIGHT, MAX_POPOVER_HEIGHT);
            persistPreferences();
            positionPopover();
        }
        scheduleRender();
    }

    function setLayout(nextLayout) {
        if (!VALID_LAYOUTS.has(nextLayout) || layout === nextLayout) return;
        layout = nextLayout;
        persistPreferences();
        gridButton?.classList.toggle('active', layout === 'grid');
        listButton?.classList.toggle('active', layout === 'list');
        recompute({ keepCandidate: true });
    }

    function toggleSort() {
        sort = sort === 'default' ? 'name-asc' : 'default';
        persistPreferences();
        if (sortButton) {
            sortButton.classList.toggle('active', sort !== 'default');
            sortButton.textContent = sort === 'name-asc' ? 'A–Z' : 'Source';
            sortButton.title = sort === 'name-asc' ? 'Sort: filename A–Z' : 'Sort: source order';
        }
        recompute();
    }

    async function runUpload(fileList) {
        const accepted = Array.from(fileList || []).filter(isImageFile);
        if (!accepted.length || source !== 'input') return false;
        setStatus(`Uploading ${accepted.length} image${accepted.length === 1 ? '' : 's'}…`);
        const result = await options.onUpload?.(accepted);
        if (result?.errors?.length) {
            setStatus(result.message || `${result.errors.length} upload${result.errors.length === 1 ? '' : 's'} failed`, true);
        } else if (result?.message) {
            setStatus(result.message);
        }
        return true;
    }

    async function runRefresh() {
        setStatus('Refreshing…');
        const result = await options.onRefresh?.(source);
        setStatus(result?.message || `${files.length} image${files.length === 1 ? '' : 's'}`);
    }

    async function runDelete() {
        if (!selected) return;
        const result = await options.onDelete?.(selected, source);
        if (result?.message) setStatus(result.message, result.success === false);
    }

    function addToolbarButton(label, title, callback) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'eclipse-image-browser-action';
        button.textContent = label;
        button.title = title;
        button.setAttribute('aria-label', title);
        button.addEventListener('pointerdown', stopNodePointer);
        button.addEventListener('click', callback);
        return button;
    }

    function open() {
        if (destroyed || popover) return;
        popover = document.createElement('div');
        popover.className = 'eclipse-image-browser-popover';
        popover.dataset.eclipseImageBrowserPopover = id;
        popover.addEventListener('pointerdown', stopNodePointer);
        popover.addEventListener('keydown', onKeyDown);
        const toolbar = document.createElement('div');
        toolbar.className = 'eclipse-image-browser-toolbar';
        searchInput = document.createElement('input');
        searchInput.type = 'search';
        searchInput.className = 'eclipse-image-browser-search';
        searchInput.placeholder = 'Search filenames…';
        searchInput.setAttribute('aria-label', 'Search image filenames');
        searchInput.value = query;
        searchInput.addEventListener('input', () => {
            query = searchInput.value;
            recompute();
        });
        sortButton = addToolbarButton(sort === 'name-asc' ? 'A–Z' : 'Source', 'Change image ordering', toggleSort);
        sortButton.classList.toggle('active', sort !== 'default');
        listButton = addToolbarButton('☷', 'List view', () => setLayout('list'));
        gridButton = addToolbarButton('▦', 'Grid view', () => setLayout('grid'));
        listButton.classList.toggle('active', layout === 'list');
        gridButton.classList.toggle('active', layout === 'grid');
        const refreshButton = addToolbarButton('↻', 'Refresh images', () => void runRefresh());
        const deleteButton = addToolbarButton('⌫', 'Delete selected image', () => void runDelete());
        uploadAction = document.createElement('label');
        uploadAction.className = 'eclipse-image-browser-action';
        uploadAction.style.display = 'flex';
        uploadAction.style.alignItems = 'center';
        uploadAction.style.justifyContent = 'center';
        uploadAction.style.position = 'relative';
        uploadAction.textContent = '＋';
        uploadAction.title = 'Upload image(s)';
        const input = document.createElement('input');
        input.type = 'file';
        input.multiple = true;
        input.accept = IMAGE_ACCEPT;
        input.className = 'eclipse-image-browser-file';
        input.addEventListener('change', () => {
            void runUpload(input.files).finally(() => { input.value = ''; });
        });
        uploadAction.appendChild(input);
        toolbar.append(searchInput, sortButton, listButton, gridButton, refreshButton, deleteButton, uploadAction);
        statusEl = document.createElement('div');
        statusEl.className = 'eclipse-image-browser-status';
        viewport = document.createElement('div');
        viewport.className = 'eclipse-image-browser-viewport';
        viewport.setAttribute('role', 'listbox');
        viewport.setAttribute('aria-label', `${source} images`);
        viewport.tabIndex = 0;
        viewport.dataset.captureWheel = 'true';
        virtual = document.createElement('div');
        virtual.className = 'eclipse-image-browser-virtual';
        viewport.appendChild(virtual);
        const resizeGutter = document.createElement('div');
        resizeGutter.className = 'eclipse-image-browser-resize-gutter';
        resizeGutter.setAttribute('aria-hidden', 'true');
        viewport.addEventListener('scroll', scheduleRender, { passive: true });
        viewport.addEventListener('dragover', (event) => {
            if (source !== 'input') return;
            event.preventDefault();
            event.stopPropagation();
            popover?.classList.add('eclipse-image-browser-drop');
        });
        viewport.addEventListener('dragleave', () => popover?.classList.remove('eclipse-image-browser-drop'));
        viewport.addEventListener('drop', (event) => {
            event.preventDefault();
            event.stopPropagation();
            popover?.classList.remove('eclipse-image-browser-drop');
            void runUpload(event.dataTransfer?.files);
        });
        popover.append(toolbar, statusEl, viewport, resizeGutter);
        document.body.appendChild(popover);
        mainButton.setAttribute('aria-expanded', 'true');
        quickUpload.style.display = source === 'input' ? '' : 'none';
        uploadAction.style.display = source === 'input' ? 'flex' : 'none';
        deleteButton.disabled = !selected;
        setStatus(`${filtered.length} image${filtered.length === 1 ? '' : 's'}`);
        outsidePointerHandler = (event) => {
            if (!popover?.contains(event.target) && !root.contains(event.target)) close();
        };
        repositionHandler = () => positionPopover();
        document.addEventListener('pointerdown', outsidePointerHandler, true);
        window.addEventListener('resize', repositionHandler);
        window.addEventListener('scroll', repositionHandler, true);
        resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(onPopoverResize) : null;
        resizeObserver?.observe(viewport);
        resizeObserver?.observe(popover);
        positionPopover();
        recompute();
        queueMicrotask(() => searchInput?.focus({ preventScroll: true }));
    }

    function close() {
        if (!popover) return;
        if (renderFrame) cancelAnimationFrame(renderFrame);
        renderFrame = 0;
        resizeObserver?.disconnect();
        resizeObserver = null;
        if (outsidePointerHandler) document.removeEventListener('pointerdown', outsidePointerHandler, true);
        if (repositionHandler) {
            window.removeEventListener('resize', repositionHandler);
            window.removeEventListener('scroll', repositionHandler, true);
        }
        outsidePointerHandler = null;
        repositionHandler = null;
        popover.remove();
        popover = null;
        viewport = null;
        virtual = null;
        searchInput = null;
        statusEl = null;
        sortButton = null;
        gridButton = null;
        listButton = null;
        uploadAction = null;
        appliedPopoverWidth = 0;
        appliedPopoverHeight = 0;
        mainButton.setAttribute('aria-expanded', 'false');
    }

    function setFiles(nextFiles, nextSelected = selected) {
        files = Array.from(nextFiles || []);
        selected = nextSelected || '';
        setButtonText(mainButton, selected);
        recompute();
    }

    function setSelected(nextSelected) {
        selected = nextSelected || '';
        setButtonText(mainButton, selected);
        candidateIndex = filtered.indexOf(selected);
        scheduleRender();
    }

    function setSource(nextSource) {
        const normalizedSource = nextSource === 'output' ? 'output' : 'input';
        const changed = normalizedSource !== source;
        source = normalizedSource;
        quickUpload.style.display = source === 'input' ? '' : 'none';
        if (uploadAction) uploadAction.style.display = source === 'input' ? 'flex' : 'none';
        viewport?.setAttribute('aria-label', `${source} images`);
        if (changed) {
            query = '';
            if (searchInput) searchInput.value = '';
        }
        recompute();
    }

    function destroy() {
        if (destroyed) return;
        destroyed = true;
        close();
        mainButton.removeEventListener('pointerdown', stopNodePointer);
        mainButton.removeEventListener('click', onMainClick);
        root.removeEventListener('dragover', onRootDragOver);
        root.removeEventListener('dragleave', onRootDragLeave);
        root.removeEventListener('drop', onRootDrop);
        quickFileInput.removeEventListener('change', onQuickFileChange);
        root.remove();
    }

    function onRootDragOver(event) {
        if (source !== 'input' || !Array.from(event.dataTransfer?.items || []).some((item) => item.kind === 'file')) return;
        event.preventDefault();
        event.stopPropagation();
        root.classList.add('eclipse-image-browser-drop');
    }

    function onRootDrop(event) {
        if (source !== 'input') return;
        event.preventDefault();
        event.stopPropagation();
        root.classList.remove('eclipse-image-browser-drop');
        void runUpload(event.dataTransfer?.files);
    }

    function onQuickFileChange() {
        void runUpload(quickFileInput.files).finally(() => { quickFileInput.value = ''; });
    }

    function onMainClick() {
        if (popover) close();
        else open();
    }

    function onRootDragLeave() {
        root.classList.remove('eclipse-image-browser-drop');
    }

    mainButton.addEventListener('pointerdown', stopNodePointer);
    mainButton.addEventListener('click', onMainClick);
    quickFileInput.addEventListener('change', onQuickFileChange);
    root.addEventListener('dragover', onRootDragOver);
    root.addEventListener('dragleave', onRootDragLeave);
    root.addEventListener('drop', onRootDrop);
    quickUpload.style.display = source === 'input' ? '' : 'none';

    return {
        element: root,
        open,
        close,
        destroy,
        setFiles,
        setSelected,
        setSource,
        setStatus,
        isOpen: () => !!popover,
        getState: () => ({
            source,
            files: files.slice(),
            filtered: filtered.slice(),
            selected,
            query,
            layout,
            sort,
            width: popoverWidth,
            height: popoverHeight,
            candidateIndex,
            renderedCount: virtual?.querySelectorAll('[role="option"]').length || 0,
        }),
    };
}
