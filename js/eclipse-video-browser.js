/** Video file grid with ordered multi-selection. Copyright (c) 2026 r-vage. MIT License. */
import { api } from './comfy/index.js';
import { markEclipseContextMenuOwner } from './eclipse-context-menu-ownership.js';

export const videoFileKey = (file) => JSON.stringify([file.type, file.subfolder || '', file.filename]);
export const videoPath = (route, file) => `${route}?${new URLSearchParams({
    type: file.type, subfolder: file.subfolder || '', filename: file.filename,
})}`;
export const videoURL = (route, file) => api.apiURL(videoPath(route, file));

export function videoElement(tag, text, className) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (className) element.className = className;
    return element;
}

let styled = false;
function styleBrowser() {
    if (styled) return;
    styled = true;
    const style = document.createElement('style');
    style.textContent = `
.eclipse-video-browser{position:fixed;z-index:10010;inset:12vh auto auto 15vw;width:min(720px,85vw);height:min(580px,78vh);min-width:340px;min-height:260px;max-width:95vw;max-height:90vh;resize:both;overflow:hidden;display:flex;flex-direction:column;background:var(--comfy-menu-bg,#25272b);color:var(--fg-color,#ddd);border:1px solid #686c75;border-radius:10px;box-shadow:0 16px 60px #0009;font:13px sans-serif;padding:12px;box-sizing:border-box}
.eclipse-video-browser header,.eclipse-video-browser footer{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:4px 0 10px}
.eclipse-video-browser header strong{flex:1}.eclipse-video-browser button,.eclipse-video-browser input,.eclipse-video-browser select{font:inherit;border:1px solid #6668;border-radius:5px;background:var(--comfy-input-bg,#333);color:inherit;padding:6px}.eclipse-video-browser button{cursor:pointer}.eclipse-video-browser button:disabled{opacity:.5;cursor:default}
.eclipse-video-browser input[type=search]{min-width:100px;flex:1}.eclipse-video-browser .evb-scroll{flex:1;min-height:80px;overflow:auto;position:relative}.eclipse-video-browser .evb-space{position:relative;width:100%}
.eclipse-video-browser .evb-tile{position:absolute;box-sizing:border-box;padding:5px;display:flex;flex-direction:column;gap:4px;border:1px solid transparent;overflow:hidden}.eclipse-video-browser .evb-tile:focus-within{border-color:#88afff}.eclipse-video-browser .evb-preview{flex:1;min-height:0;padding:0;overflow:hidden;display:flex;flex-direction:column;text-align:left;width:100%}.eclipse-video-browser .evb-preview img{width:100%;height:92px;object-fit:contain;background:#131417}.eclipse-video-browser .evb-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;width:100%;padding:3px;box-sizing:border-box}.eclipse-video-browser .evb-select{display:flex;align-items:center;gap:5px;font-size:11px;white-space:nowrap}.eclipse-video-browser .evb-list .evb-tile{flex-direction:row}.eclipse-video-browser .evb-list .evb-preview{flex-direction:row;align-items:center}.eclipse-video-browser .evb-list .evb-preview img{width:74px;height:48px}.eclipse-video-browser .evb-list .evb-select{min-width:84px}.eclipse-video-browser .evb-status{flex:1;font-size:12px;opacity:.85}`;
    style.textContent += '\n.eclipse-video-browser .evb-list .evb-tile{padding-bottom:22px}.eclipse-video-browser .evb-list .evb-detail{position:absolute;bottom:5px;left:86px;right:5px}';
    document.head.append(style);
}

export function createVideoBrowser({ getState, onStateChange, onAdd, onPreview, getExisting }) {
    styleBrowser();
    let root = null, scroll, spacer, status, add, files = [], filtered = [], returnFocus;
    let destroyed = false, generation = 0, frame = 0, observer;
    const controllers = new Set();
    const posters = new Map();
    const pendingLoads = new Set();
    let activeLoads = 0;
    const work = [];
    const state = () => ({ source: 'input', query: '', layout: 'grid', sort: 'name', pending: [], ...getState() });
    const change = (patch) => onStateChange({ ...state(), ...patch });
    const fetchJSON = async (url) => {
        const controller = new AbortController();
        controllers.add(controller);
        try {
            const response = await api.fetchApi(url, { signal: controller.signal, cache: 'no-store' });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || response.statusText);
            return data;
        } finally { controllers.delete(controller); }
    };
    function drawSoon() {
        if (!frame) frame = requestAnimationFrame(() => { frame = 0; draw(); });
    }
    function loadPoster(file, key) {
        if (posters.has(key) || pendingLoads.has(key)) return;
        pendingLoads.add(key);
        work.push(async () => {
            const controller = new AbortController();
            controllers.add(controller);
            try {
                const response = await api.fetchApi(videoPath('/eclipse/load_video/thumbnail', file), { signal: controller.signal });
                if (!response.ok) throw new Error('Poster unavailable');
                const blob = await response.blob();
                if (destroyed || controller.signal.aborted) return;
                const poster = { url: URL.createObjectURL(blob) };
                posters.set(key, poster);
                updateTile(key, poster);
                const probe = await api.fetchApi(videoPath('/eclipse/load_video/probe', file), { signal: controller.signal });
                if (probe.ok && !controller.signal.aborted) {
                    const info = await probe.json();
                    poster.detail = `${info.width}×${info.height} · ${info.duration?.toFixed(2) || '?'} s · ${info.fps || '?'} fps${info.audio ? ' · audio' : ''}`;
                    updateTile(key, poster);
                }
                while (posters.size > 96) {
                    const oldest = posters.keys().next().value;
                    URL.revokeObjectURL(posters.get(oldest)?.url || '');
                    posters.delete(oldest);
                }
            } catch (error) {
                if (error.name !== 'AbortError' && !posters.has(key)) {
                    const failed = { error: true, detail: 'Preview unavailable' };
                    posters.set(key, failed); updateTile(key, failed);
                }
            } finally {
                controllers.delete(controller);
                pendingLoads.delete(key);
            }
        });
        pump();
    }
    function pump() {
        while (!destroyed && activeLoads < 4 && work.length) {
            activeLoads++;
            work.shift()().finally(() => { activeLoads--; pump(); });
        }
    }
    function updateTile(key, poster) {
        for (const tile of spacer?.children || []) {
            if (!root || tile.dataset.cacheKey !== key) continue;
            if (poster.url) tile.querySelector('img').src = poster.url;
            if (poster.detail) {
                tile.querySelector('.evb-detail').textContent = poster.detail;
                tile.querySelector('.evb-preview').title = `${tile.dataset.filename}\n${poster.detail}`;
            }
        }
    }
    function filter() {
        const settings = state();
        const query = settings.query.toLowerCase();
        filtered = files.filter(file => `${file.subfolder}/${file.filename}`.toLowerCase().includes(query));
        filtered.sort(settings.sort === 'newest'
            ? (a, b) => b.modified - a.modified
            : (a, b) => `${a.subfolder}/${a.filename}`.localeCompare(`${b.subfolder}/${b.filename}`, undefined, { numeric: true }));
        scroll.scrollTop = 0;
        draw();
    }
    function draw() {
        if (!root || destroyed) return;
        const settings = state();
        const focused = spacer.contains(document.activeElement) ? document.activeElement.dataset.focusKey : null;
        const pending = settings.pending || [];
        const existing = new Set(getExisting().map(videoFileKey));
        const columns = settings.layout === 'list' ? 1 : Math.max(1, Math.floor(scroll.clientWidth / 165));
        const cellHeight = settings.layout === 'list' ? 82 : 178;
        const firstRow = Math.max(0, Math.floor(scroll.scrollTop / cellHeight) - 1);
        const lastRow = Math.ceil((scroll.scrollTop + scroll.clientHeight) / cellHeight) + 1;
        spacer.style.height = `${Math.ceil(filtered.length / columns) * cellHeight}px`;
        spacer.classList.toggle('evb-list', settings.layout === 'list');
        spacer.replaceChildren();
        for (let index = firstRow * columns; index < Math.min(filtered.length, lastRow * columns); index++) {
            const file = filtered[index], key = videoFileKey(file);
            const cacheKey = `${key}:${file.modified}:${file.size}`;
            const tile = videoElement('div', null, 'evb-tile');
            tile.dataset.cacheKey = cacheKey; tile.dataset.filename = file.filename;
            Object.assign(tile.style, { left: `${index % columns * 100 / columns}%`, top: `${Math.floor(index / columns) * cellHeight}px`, width: `${100 / columns}%`, height: `${cellHeight}px` });
            const preview = videoElement('button', null, 'evb-preview');
            preview.type = 'button'; preview.title = `Preview ${file.subfolder ? file.subfolder + '/' : ''}${file.filename}`;
            preview.dataset.focusKey = `preview:${key}`;
            const image = document.createElement('img'); image.alt = '';
            const cached = posters.get(cacheKey);
            if (cached?.url) image.src = cached.url;
            else loadPoster(file, cacheKey);
            preview.append(image, videoElement('span', file.filename, 'evb-name'));
            const detail = videoElement('small', cached?.detail || 'Reading video…', 'evb-detail');
            detail.style.cssText = 'font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
            preview.onclick = () => onPreview(file);
            const label = videoElement('label', null, 'evb-select');
            const checkbox = document.createElement('input'); checkbox.type = 'checkbox';
            checkbox.setAttribute('aria-label', `Add ${file.filename}`);
            checkbox.dataset.focusKey = `check:${key}`;
            const order = pending.findIndex(ref => videoFileKey(ref) === key);
            checkbox.checked = order >= 0;
            checkbox.disabled = existing.has(key);
            checkbox.onchange = () => {
                const next = state().pending.filter(ref => videoFileKey(ref) !== key);
                if (checkbox.checked) next.push({ type: file.type, subfolder: file.subfolder, filename: file.filename });
                change({ pending: next }); draw();
            };
            const text = existing.has(key) ? 'Already added' : order >= 0 ? `Selected #${order + 1}` : 'Add';
            label.append(checkbox, videoElement('span', text));
            tile.append(preview, detail, label); spacer.append(tile);
            tile.onkeydown = event => {
                const delta = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -columns, ArrowDown: columns }[event.key];
                if (delta === undefined) return;
                event.preventDefault();
                const target = Math.max(0, Math.min(filtered.length - 1, index + delta));
                const top = Math.floor(target / columns) * cellHeight;
                if (top < scroll.scrollTop || top + cellHeight > scroll.scrollTop + scroll.clientHeight) scroll.scrollTop = top;
                draw();
                const targetKey = `preview:${videoFileKey(filtered[target])}`;
                [...spacer.querySelectorAll('button')].find(button => button.dataset.focusKey === targetKey)?.focus({ preventScroll: true });
            };
        }
        if (focused) [...spacer.querySelectorAll('[data-focus-key]')].find(element => element.dataset.focusKey === focused)?.focus({ preventScroll: true });
        add.textContent = `Add selected (${pending.length})`;
        add.disabled = pending.length === 0;
        if (status.dataset.busy !== 'true') status.textContent = `${filtered.length} videos`;
    }
    async function refresh() {
        const revision = ++generation;
        status.textContent = 'Reading files…'; status.dataset.busy = 'true';
        try {
            const data = await fetchJSON(`/eclipse/load_video/list?type=${state().source}`);
            if (revision !== generation || !root) return;
            files = data.files || [];
            status.dataset.busy = 'false';
            filter();
            if (data.truncated) status.textContent += ' (first 10,000)';
        } catch (error) {
            if (root && revision === generation && error.name !== 'AbortError') status.textContent = error.message;
        }
    }
    function close() {
        generation++;
        observer?.disconnect(); observer = null;
        root?.remove(); root = null;
        for (const controller of controllers) controller.abort();
        work.length = 0; pendingLoads.clear();
        if (frame) cancelAnimationFrame(frame);
        frame = 0;
        if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    }
    function open() {
        if (root || destroyed) return;
        returnFocus = document.activeElement;
        root = videoElement('section', null, 'eclipse-video-browser');
        root.setAttribute('aria-label', 'Browse videos'); root.setAttribute('role', 'dialog');
        markEclipseContextMenuOwner(root);
        const settings = state();
        if (settings.width) root.style.width = `${settings.width}px`;
        if (settings.height) root.style.height = `${settings.height}px`;
        const header = document.createElement('header');
        const closeButton = videoElement('button', 'Close'); closeButton.onclick = close;
        header.append(videoElement('strong', 'Browse videos'), closeButton);
        const toolbar = document.createElement('header');
        const source = document.createElement('select'); source.setAttribute('aria-label', 'Video folder');
        for (const value of ['input', 'output']) { const option = videoElement('option', value === 'input' ? 'Input' : 'Output'); option.value = value; source.append(option); }
        source.value = settings.source;
        source.onchange = () => { change({ source: source.value }); void refresh(); };
        const search = document.createElement('input'); search.type = 'search'; search.placeholder = 'Search filenames'; search.value = settings.query;
        search.oninput = () => { change({ query: search.value }); filter(); };
        const sort = document.createElement('select'); sort.setAttribute('aria-label', 'Video ordering');
        for (const [value, label] of [['name', 'Filename'], ['newest', 'Newest']]) { const option = videoElement('option', label); option.value = value; sort.append(option); }
        sort.value = settings.sort; sort.onchange = () => { change({ sort: sort.value }); filter(); };
        const layout = videoElement('button', settings.layout === 'grid' ? 'List' : 'Grid');
        layout.onclick = () => { const next = state().layout === 'grid' ? 'list' : 'grid'; change({ layout: next }); layout.textContent = next === 'grid' ? 'List' : 'Grid'; draw(); };
        const reload = videoElement('button', 'Refresh'); reload.onclick = () => void refresh();
        toolbar.append(source, search, sort, layout, reload);
        scroll = videoElement('div', null, 'evb-scroll');
        spacer = videoElement('div', null, 'evb-space'); scroll.append(spacer);
        scroll.onscroll = drawSoon;
        const footer = document.createElement('footer'); status = videoElement('span', '', 'evb-status');
        add = videoElement('button', 'Add selected (0)');
        add.onclick = () => { onAdd(state().pending); change({ pending: [] }); draw(); };
        footer.append(status, add);
        root.append(header, toolbar, scroll, footer);
        root.addEventListener('keydown', event => { event.stopPropagation(); if (event.key === 'Escape') close(); });
        for (const event of ['pointerdown', 'pointerup', 'wheel', 'contextmenu']) root.addEventListener(event, e => e.stopPropagation());
        document.body.append(root);
        search.focus();
        observer = new ResizeObserver(() => { if (root) { change({ width: Math.round(root.offsetWidth), height: Math.round(root.offsetHeight) }); drawSoon(); } });
        observer.observe(root);
        void refresh();
    }
    return { open, close, destroy() {
        destroyed = true; close();
        for (const poster of posters.values()) if (poster.url) URL.revokeObjectURL(poster.url);
        posters.clear();
    } };
}
