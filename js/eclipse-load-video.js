/** Persistent video playlist editor. Copyright (c) 2026 r-vage. MIT License. */
import { app, api } from './comfy/index.js';
import { createWidgetVisibilityManager, isConfiguringGraph, isVueMode, onVueModeChange } from './eclipse-widget-performance-utils.js';
import { markEclipseContextMenuOwner } from './eclipse-context-menu-ownership.js';
import { createVideoBrowser, videoElement as el, videoFileKey, videoPath, videoURL } from './eclipse-video-browser.js';

const NODE_NAME = 'Load Video [Eclipse]';
const NATIVE_WIDGETS = ['playlist', 'width', 'height', 'fit', 'timing_mode', 'fps'];
const acceptsVideo = file => /\.(mp4|webm|mkv|mov|avi|m4v|mpg|mpeg|ts)$/i.test(file.name);
const uuid = () => globalThis.crypto?.randomUUID?.() || `clip-${Date.now()}-${Math.random().toString(16).slice(2)}`;
let styled = false;
function injectStyles() {
    if (styled) return;
    styled = true;
    const style = document.createElement('style');
    style.textContent = `
.eclipse-load-video{box-sizing:border-box;width:100%;height:100%;min-width:0;display:flex;flex-direction:column;gap:8px;padding:10px;background:var(--comfy-menu-bg,#202226);color:var(--fg-color,#ddd);font:12px sans-serif;border:1px solid #7775;border-radius:8px;overflow:hidden;container-type:inline-size}
.eclipse-load-video.evl-vue{contain:size;min-height:0}.eclipse-load-video button,.eclipse-load-video input{font:inherit;background:var(--comfy-input-bg,#303238);color:inherit;border:1px solid #7778;border-radius:5px;padding:5px;box-sizing:border-box}.eclipse-load-video button{cursor:pointer}.eclipse-load-video button:disabled{opacity:.45;cursor:default}.eclipse-load-video input[type=number]{width:88px}.eclipse-load-video input[type=checkbox]{accent-color:#849cff}
.eclipse-load-video .evl-toolbar,.eclipse-load-video .evl-tabs,.eclipse-load-video .evl-actions{display:flex;gap:5px;flex-wrap:wrap;align-items:center}.eclipse-load-video .evl-body{display:grid;grid-template-columns:minmax(150px,32%) minmax(0,1fr);gap:8px;flex:1;min-height:130px}.eclipse-load-video .evl-list{overflow:auto;min-height:100px;border:1px solid #7774;border-radius:5px;padding:4px}.eclipse-load-video .evl-row{display:flex;gap:4px;align-items:center;margin-bottom:4px;border:1px solid transparent;border-radius:5px}.eclipse-load-video .evl-row[aria-current=true]{border-color:#92abff;background:#92abff18}.eclipse-load-video .evl-row button{min-width:0;flex:1;text-align:left;border:0;background:transparent;padding:7px;overflow:hidden}.eclipse-load-video .evl-row strong,.eclipse-load-video .evl-row small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.eclipse-load-video .evl-row small{font-size:10px;opacity:.75;margin-top:4px}
.eclipse-load-video .evl-right{display:flex;flex-direction:column;gap:6px;min-width:0;min-height:0}.eclipse-load-video .evl-tabs button[aria-pressed=true]{border-color:#92abff;background:#92abff26}.eclipse-load-video video{width:100%;flex:1;min-height:80px;object-fit:contain;background:#111;border-radius:5px}.eclipse-load-video pre{white-space:pre-wrap;overflow:auto;margin:0;min-height:80px;flex:1;font:11px monospace;user-select:text}.eclipse-load-video .evl-source{font-size:11px;opacity:.85;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.eclipse-load-video .evl-editor{border-top:1px solid #7774;padding-top:7px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}.eclipse-load-video label{display:flex;align-items:center;gap:5px}.eclipse-load-video .evl-status{font-size:11px;min-height:14px;max-height:42px;overflow:auto;white-space:pre-wrap}.eclipse-load-video .evl-empty{opacity:.6;padding:12px;line-height:1.5}.eclipse-load-video .evl-dragover{outline:2px dashed #92abff}.eclipse-load-video [hidden]{display:none!important}
@container (max-width:460px){.eclipse-load-video .evl-body{grid-template-columns:1fr}.eclipse-load-video .evl-list{max-height:130px}.eclipse-load-video .evl-right{min-height:130px}}`;
    document.head.append(style);
}

export function createVideoEditor(node) {
    injectStyles();
    const widget = name => node.widgets?.find(item => item.name === name);
    const visibility = createWidgetVisibilityManager(node);
    let disposed = false, revision = 0, probeRevision = 0, job = null, jobRunning = false, poll = null, selectedSource = null;
    let joined = null, joinedStale = false, report = '', uploadChain = Promise.resolve();
    let sourceRange = null, rangeKey = null, seekPending = false, playbackFrame = null;
    const probes = new Map(), controllers = new Set();
    const root = el('div', null, 'eclipse-load-video');
    root.setAttribute('aria-label', 'Video playlist editor');
    markEclipseContextMenuOwner(root);
    const toolbar = el('div', null, 'evl-toolbar');
    const browse = el('button', 'Browse videos');
    const upload = el('button', 'Upload videos');
    const fileInput = document.createElement('input'); fileInput.type = 'file'; fileInput.multiple = true;
    fileInput.accept = 'video/*,.mkv,.avi,.m4v,.ts'; fileInput.hidden = true;
    const body = el('div', null, 'evl-body');
    const list = el('div', null, 'evl-list'); list.setAttribute('aria-label', 'Video clips');
    const right = el('div', null, 'evl-right');
    const tabs = el('div', null, 'evl-tabs');
    const sourceTab = el('button', 'Source'), joinedTab = el('button', 'Joined'), dataTab = el('button', 'GenData');
    const sourceLabel = el('div', 'Select or drop a video', 'evl-source');
    const video = document.createElement('video'); video.controls = true; video.playsInline = true; video.preload = 'metadata';
    const metadata = document.createElement('pre'); metadata.hidden = true;
    const editor = el('div', null, 'evl-editor');
    const status = el('div', 'Add videos to begin. Controls are available before queueing.', 'evl-status');
    status.setAttribute('role', 'status');
    const controls = {};
    for (const [name, title, type] of [['start_frame', 'Start frame', 'number'], ['load_cap', 'Load cap', 'number'], ['enabled', 'Include', 'checkbox'], ['mute', 'Mute', 'checkbox'], ['use_metadata', 'Use metadata', 'checkbox']]) {
        const input = document.createElement('input'); input.type = type; input.disabled = true;
        input.setAttribute('aria-label', title);
        if (type === 'number') { input.min = '0'; input.max = String(2 ** 31 - 1); input.step = '1'; input.value = '0'; }
        if (name === 'start_frame') input.title = 'Seek the Source preview using this clip’s reported FPS. Preview selected trim checks the exact source frames.';
        if (name === 'load_cap') input.title = '0 = all remaining source frames. Limits Source preview playback using this clip’s reported FPS.';
        const label = el('label', title); label.append(input); editor.append(label); controls[name] = input;
        input.addEventListener(type === 'number' ? 'input' : 'change', () => {
            const row = currentRow();
            if (!row) return;
            const value = type === 'checkbox' ? input.checked : Number(input.value);
            if (type === 'number' && (!Number.isInteger(value) || value < 0 || value > 2 ** 31 - 1 || input.value === '')) return;
            editRow(row.id, { [name]: value });
        });
    }
    const actions = el('div', null, 'evl-actions');
    const up = el('button', '↑'), down = el('button', '↓'), duplicate = el('button', 'Duplicate'), remove = el('button', 'Remove');
    up.title = 'Move selected clip up'; down.title = 'Move selected clip down';
    const build = el('button', 'Build joined preview'), trimPreview = el('button', 'Preview selected trim'), cancel = el('button', 'Cancel preview'); cancel.hidden = true;
    actions.append(up, down, duplicate, remove, trimPreview, build, cancel);
    toolbar.append(browse, upload, fileInput); tabs.append(sourceTab, joinedTab, dataTab);
    right.append(tabs, sourceLabel, video, metadata); body.append(list, right);
    root.append(toolbar, body, editor, actions, status);

    const presentation = () => node.properties?.eclipseVideo || {};
    function changeGraph(action) {
        node.graph?.beforeChange?.();
        action();
        node.graph?.afterChange?.();
        node.setDirtyCanvas?.(true, true);
    }
    function present(patch) {
        const value = { ...presentation(), ...patch };
        if (JSON.stringify(value) === JSON.stringify(presentation())) return;
        changeGraph(() => { node.properties ||= {}; node.properties.eclipseVideo = value; });
    }
    function rows() {
        try {
            const manifest = JSON.parse(widget('playlist')?.value || '{"version":1,"clips":[]}');
            if (manifest.version !== 1 || !Array.isArray(manifest.clips)) throw new Error('Unsupported playlist');
            return manifest.clips;
        } catch (error) { status.textContent = `Invalid playlist: ${error.message}`; return []; }
    }
    const currentRow = () => rows().find(row => row.id === presentation().activeId);
    function persist(next, mediaChanged = true) {
        changeGraph(() => { widget('playlist').value = JSON.stringify({ version: 1, clips: next }); });
        if (mediaChanged) markStale();
        else status.textContent = 'Metadata source saved.';
        render();
    }
    function editRow(id, patch) {
        const next = rows().map(row => {
            let value = row.id === id ? { ...row, ...patch } : { ...row };
            if (patch.use_metadata && row.id !== id) value.use_metadata = false;
            if (!value.enabled) value.use_metadata = false;
            return value;
        });
        if (id === presentation().activeId && ('start_frame' in patch || 'load_cap' in patch)) {
            present({ tab: 'source', previewFile: null });
        }
        persist(next, Object.keys(patch).some(name => name !== 'use_metadata'));
    }
    function addFiles(files) {
        const next = rows();
        const existing = new Set(next.map(row => videoFileKey(row.file)));
        let added = null;
        for (const file of files) {
            if (next.length >= 128) { status.textContent = 'A playlist supports at most 128 clips.'; break; }
            const ref = { type: file.type || 'input', subfolder: file.subfolder || '', filename: file.filename || file.name };
            if (existing.has(videoFileKey(ref))) continue;
            existing.add(videoFileKey(ref));
            added = { id: uuid(), file: ref, start_frame: 0, load_cap: 0, enabled: true, mute: false, use_metadata: false };
            next.push(added);
        }
        if (added) { present({ activeId: added.id, tab: 'source', previewFile: null }); persist(next); }
        void probeRows();
    }
    function move(delta) {
        const next = rows(), index = next.findIndex(row => row.id === presentation().activeId);
        if (index < 0 || index + delta < 0 || index + delta >= next.length) return;
        [next[index], next[index + delta]] = [next[index + delta], next[index]];
        persist(next);
    }
    function setTab(tab) { present({ tab }); updatePlayer(); }
    sourceTab.onclick = () => setTab('source'); joinedTab.onclick = () => setTab('joined'); dataTab.onclick = () => setTab('metadata');
    up.onclick = () => move(-1); down.onclick = () => move(1);
    duplicate.onclick = () => {
        const row = currentRow(), next = rows(); if (!row || next.length >= 128) return;
        const copy = { ...row, id: uuid(), use_metadata: false };
        next.splice(next.findIndex(item => item.id === row.id) + 1, 0, copy);
        present({ activeId: copy.id, previewFile: null }); persist(next);
    };
    remove.onclick = () => {
        const row = currentRow(); if (!row) return;
        const next = rows().filter(item => item.id !== row.id);
        present({ activeId: next[0]?.id || null, previewFile: null }); persist(next);
    };
    function source(file) {
        const key = file ? videoFileKey(file) : null;
        if (key === selectedSource) return;
        selectedSource = key;
        rangeKey = null; sourceRange = null; seekPending = false;
        video.pause();
        if (file) { video.src = videoURL('/view', file); video.load(); }
        else { video.removeAttribute('src'); video.load(); }
    }
    function stopPlaybackWatch() {
        if (playbackFrame !== null) cancelAnimationFrame(playbackFrame);
        playbackFrame = null;
    }
    function seekRangeStart() {
        if (!sourceRange || video.readyState < 1) return;
        seekPending = false;
        // Seek just inside the frame: media timestamps are quantized to µs,
        // and floating-point subtraction can otherwise show the preceding one.
        video.currentTime = sourceRange.start + 1e-6;
    }
    function applySourceRange(row, info) {
        // HTML video seeks by seconds, not frame index. The exact trim button
        // uses decoded source timestamps when a file has variable frame timing.
        const [numerator, denominator = 1] = String(info?.fps || '').split('/').map(Number);
        const rate = numerator / denominator;
        const key = row && rate > 0 && Number.isFinite(rate) ? `${row.id}:${row.start_frame}:${row.load_cap}:${rate}` : null;
        if (key === rangeKey) return;
        rangeKey = key; sourceRange = null; seekPending = false;
        if (!key) return;
        const duration = Number.isFinite(info.duration) ? info.duration : Infinity;
        const start = Math.min(row.start_frame / rate, Math.max(0, duration - 1 / rate));
        const end = Math.min(row.load_cap ? (row.start_frame + row.load_cap) / rate : duration, duration);
        sourceRange = { start, end, last: Math.max(start, end - 1 / rate), capped: end < duration };
        video.pause(); seekPending = true; seekRangeStart();
    }
    function checkRangeEnd() {
        if (!sourceRange?.capped || video.paused || video.currentTime < sourceRange.end) return;
        video.pause();
        video.currentTime = sourceRange.last + 1e-6;
    }
    function watchPlayback() {
        playbackFrame = null;
        if (disposed || video.paused) return;
        checkRangeEnd();
        if (sourceRange && !video.paused) playbackFrame = requestAnimationFrame(watchPlayback);
    }
    video.addEventListener('loadedmetadata', () => { if (seekPending) seekRangeStart(); });
    video.addEventListener('play', () => {
        if (!sourceRange) return;
        if (video.currentTime < sourceRange.start || video.currentTime >= sourceRange.last) seekRangeStart();
        stopPlaybackWatch(); playbackFrame = requestAnimationFrame(watchPlayback);
    });
    video.addEventListener('pause', stopPlaybackWatch);
    video.addEventListener('timeupdate', checkRangeEnd);
    function updatePlayer() {
        const tab = presentation().tab || 'source', row = currentRow();
        for (const [button, value] of [[sourceTab, 'source'], [joinedTab, 'joined'], [dataTab, 'metadata']]) button.setAttribute('aria-pressed', String(tab === value));
        video.hidden = tab === 'metadata'; metadata.hidden = tab !== 'metadata';
        if (tab === 'metadata') {
            rangeKey = null; sourceRange = null; seekPending = false;
            video.pause();
            const info = row ? probes.get(videoFileKey(row.file)) : null;
            metadata.textContent = info?.parameters ? `${info.parameters.status}\n\n${JSON.stringify(info.parameters.data, null, 2)}` : 'Select a clip to inspect saved prompts and sampling settings.';
            sourceLabel.textContent = row ? `${row.file.filename}${row.use_metadata ? ' • downstream GenData source' : ''}` : 'No selected clip';
        } else if (tab === 'joined') {
            source(joined);
            applySourceRange(null, null);
            sourceLabel.textContent = joined ? (joinedStale ? 'Joined preview is stale — rebuild after edits' : 'Joined result') : 'Build a preview or queue the node';
        } else {
            const candidate = presentation().previewFile, file = candidate || row?.file;
            source(file);
            const info = file ? probes.get(videoFileKey(file)) : null;
            applySourceRange(candidate ? null : row, info);
            sourceLabel.textContent = file ? `${candidate ? 'Browser preview: ' : ''}${file.filename}${info?.width ? ` · ${info.width}×${info.height} · ${info.duration?.toFixed(2) || '?'} s · ${info.fps || '?'} fps` : ''}` : 'Drop videos here or browse existing files';
        }
    }
    function render() {
        if (disposed) return;
        const all = rows(), active = currentRow();
        list.replaceChildren();
        if (!all.length) list.append(el('div', 'Drop videos here, upload files, or browse Input / Output.', 'evl-empty'));
        for (const [index, row] of all.entries()) {
            const entry = el('div', null, 'evl-row'); entry.setAttribute('aria-current', String(row.id === active?.id)); entry.draggable = true;
            const button = el('button'); button.title = `${row.file.type}/${row.file.subfolder}/${row.file.filename}`;
            button.append(el('strong', `${index + 1}. ${row.file.filename}`));
            const info = probes.get(videoFileKey(row.file));
            button.append(el('small', `${row.enabled ? '' : 'Excluded · '}start ${row.start_frame}, cap ${row.load_cap || 'all'}${row.mute ? ' · muted' : ''}${row.use_metadata ? ' · GenData' : ''}${info?.error ? ' · unavailable' : ''}`));
            button.onclick = () => { present({ activeId: row.id, tab: 'source', previewFile: null }); render(); };
            button.onkeydown = event => { if (event.altKey && ['ArrowUp', 'ArrowDown'].includes(event.key)) { event.preventDefault(); present({ activeId: row.id }); move(event.key === 'ArrowUp' ? -1 : 1); } };
            entry.ondragstart = event => { event.dataTransfer.setData('text/eclipse-video', row.id); event.stopPropagation(); };
            entry.ondragover = event => { if (event.dataTransfer.types.includes('text/eclipse-video')) event.preventDefault(); };
            entry.ondrop = event => {
                const id = event.dataTransfer.getData('text/eclipse-video'); if (!id) return;
                event.preventDefault(); event.stopPropagation();
                const next = rows(), from = next.findIndex(item => item.id === id), to = next.findIndex(item => item.id === row.id);
                if (from >= 0 && to >= 0) { next.splice(to, 0, next.splice(from, 1)[0]); persist(next); }
            };
            entry.append(button); list.append(entry);
        }
        for (const [name, input] of Object.entries(controls)) {
            input.disabled = !active || (name === 'use_metadata' && !active.enabled);
            if (input.type === 'checkbox') input.checked = Boolean(active?.[name]);
            else if (document.activeElement !== input) input.value = String(active?.[name] || 0);
        }
        for (const button of [up, down, duplicate, remove, trimPreview]) button.disabled = !active;
        trimPreview.disabled ||= jobRunning;
        build.disabled = !all.some(row => row.enabled) || jobRunning;
        updatePlayer();
    }
    async function probeRows() {
        const requestedRevision = probeRevision;
        for (const row of rows()) {
            if (disposed || requestedRevision !== probeRevision) return;
            const key = videoFileKey(row.file);
            if (probes.has(key)) continue;
            probes.set(key, { loading: true });
            const controller = new AbortController(); controllers.add(controller);
            try {
                const response = await api.fetchApi(videoPath('/eclipse/load_video/probe', row.file), { signal: controller.signal, cache: 'no-store' });
                const info = await response.json();
                if (disposed || controller.signal.aborted || requestedRevision !== probeRevision) return;
                probes.set(key, info); render();
                if (info.error) status.textContent = `${row.file.filename}: ${info.error}`;
            } catch (error) { if (!disposed && requestedRevision === probeRevision && error.name !== 'AbortError') { probes.set(key, { error: error.message }); render(); } }
            finally { controllers.delete(controller); }
        }
    }
    const browser = createVideoBrowser({
        getState: () => presentation().browser || {},
        onStateChange: value => present({ browser: value }),
        onAdd: addFiles,
        getExisting: () => rows().map(row => row.file),
        onPreview: file => { present({ tab: 'source', previewFile: { type: file.type, subfolder: file.subfolder, filename: file.filename } }); updatePlayer(); },
    });
    browse.onclick = () => browser.open(); upload.onclick = () => fileInput.click();
    function uploadFiles(files) {
        const selected = [...files].filter(acceptsVideo);
        uploadChain = uploadChain.then(async () => {
            const failures = [];
            for (const [index, file] of selected.entries()) {
                if (disposed) return;
                status.textContent = `Uploading ${index + 1}/${selected.length}: ${file.name}`;
                const controller = new AbortController(); controllers.add(controller);
                try {
                    const data = new FormData(); data.append('image', file, file.name); data.append('type', 'input'); data.append('overwrite', 'false');
                    const response = await api.fetchApi('/upload/image', { method: 'POST', body: data, signal: controller.signal });
                    const saved = await response.json();
                    if (!response.ok) throw new Error(saved.error || 'Upload failed');
                    if (!disposed) addFiles([{ type: saved.type || 'input', subfolder: saved.subfolder || '', filename: saved.name }]);
                } catch (error) { if (!disposed) failures.push(`${file.name}: ${error.message}`); }
                finally { controllers.delete(controller); }
            }
            if (!disposed) status.textContent = failures.length ? failures.join('\n') : 'Uploads finished. Start frame and load cap count original frames; 0 cap means all remaining.';
        });
    }
    fileInput.onchange = () => { uploadFiles(fileInput.files); fileInput.value = ''; };
    root.ondragover = event => { if (event.dataTransfer.files?.length || event.dataTransfer.types.includes('Files')) { event.preventDefault(); root.classList.add('evl-dragover'); } };
    root.ondragleave = () => root.classList.remove('evl-dragover');
    root.ondrop = event => { root.classList.remove('evl-dragover'); if (event.dataTransfer.files.length) { event.preventDefault(); event.stopPropagation(); uploadFiles(event.dataTransfer.files); } };
    async function releaseJob() {
        if (poll) clearTimeout(poll); poll = null;
        const previous = job; job = null; jobRunning = false; cancel.hidden = true;
        if (previous) { try { await api.fetchApi(`/eclipse/load_video/preview/${previous}`, { method: 'DELETE', keepalive: true }); } catch { /* server expiry also cleans abandoned jobs */ } }
    }
    function markStale() {
        revision++; joinedStale = Boolean(joined);
        if (jobRunning) void releaseJob();
        status.textContent = 'Settings saved. Queue to join videos, or build a viewing preview.';
    }
    async function buildPreview(selectedOnly) {
        const row = currentRow(); if (selectedOnly && !row) return;
        await releaseJob();
        const requestedRevision = ++revision;
        const payload = Object.fromEntries(NATIVE_WIDGETS.filter(name => name !== 'playlist').map(name => [name, widget(name)?.value]));
        payload.playlist = selectedOnly ? JSON.stringify({ version: 1, clips: [{ ...row, enabled: true, use_metadata: false }] }) : widget('playlist').value;
        status.textContent = 'Building preview…'; jobRunning = true; cancel.hidden = false; render();
        try {
            const response = await api.fetchApi('/eclipse/load_video/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Preview request failed');
            if (disposed || requestedRevision !== revision) { await api.fetchApi(`/eclipse/load_video/preview/${data.id}`, { method: 'DELETE' }); return; }
            job = data.id; jobRunning = true; cancel.hidden = false;
            async function check() {
                if (!job || disposed || requestedRevision !== revision) return;
                try {
                    const response = await api.fetchApi(`/eclipse/load_video/preview/${job}`);
                    const data = await response.json();
                    if (disposed || requestedRevision !== revision) return;
                    if (!response.ok || data.status === 'error') throw new Error(data.error || 'Preview failed');
                    if (data.status === 'complete') {
                        joined = data.result; joinedStale = false; jobRunning = false; cancel.hidden = true;
                        status.textContent = selectedOnly ? 'Selected trim preview ready.' : 'Joined preview ready.';
                        present({ tab: 'joined' }); render();
                    } else poll = setTimeout(check, 500);
                } catch (error) { status.textContent = error.message; await releaseJob(); render(); }
            }
            void check();
        } catch (error) { if (!disposed && requestedRevision === revision) { status.textContent = error.message; await releaseJob(); render(); } }
    }
    build.onclick = () => void buildPreview(false); trimPreview.onclick = () => void buildPreview(true);
    cancel.onclick = () => { revision++; void releaseJob().then(render); status.textContent = 'Preview cancelled.'; };
    video.addEventListener('error', () => { if (!disposed) status.textContent = 'Source cannot play in this browser or preview expired. Build a compatible preview.'; });
    for (const event of ['pointerdown', 'pointerup', 'pointermove', 'keydown', 'keyup', 'wheel', 'contextmenu']) root.addEventListener(event, e => e.stopPropagation());
    const domWidget = node.addDOMWidget('eclipse_video_loader', 'video_loader', root, { serialize: false, hideOnZoom: false, getMinHeight: () => 330, getMaxHeight: () => 2400 });
    visibility.hideInitially(['playlist', 'fps']);
    const syncMode = () => { root.classList.toggle('evl-vue', isVueMode()); browser.close(); node.setDirtyCanvas?.(true, true); };
    syncMode(); const unsubscribe = onVueModeChange(syncMode);
    function refreshSettings() {
        if (node.id === -1) return;
        visibility.setVisible('fps', widget('timing_mode')?.value === 'fixed');
        // The flexible preview absorbs control-row changes inside the user's
        // current/saved size. Fitting to content here would shrink every edit.
        node.setDirtyCanvas?.(true, true);
    }
    for (const name of NATIVE_WIDGETS) {
        const item = widget(name); if (!item) continue;
        const original = item.callback;
        item.callback = function () { original?.apply(this, arguments); markStale(); refreshSettings(); render(); if (name === 'playlist') void probeRows(); };
    }
    const originalDragOver = node.onDragOver, originalDrop = node.onDragDrop;
    node.onDragOver = function (event) { return [...(event.dataTransfer?.files || [])].some(acceptsVideo) || originalDragOver?.apply(this, arguments); };
    node.onDragDrop = function (event) { if ([...(event.dataTransfer?.files || [])].some(acceptsVideo)) { uploadFiles(event.dataTransfer.files); return true; } return originalDrop?.apply(this, arguments); };
    const pageHide = () => {
        revision++; void releaseJob(); joined = null; selectedSource = undefined;
        render();
    };
    window.addEventListener('pagehide', pageHide);
    render();
    if (!isConfiguringGraph()) requestAnimationFrame(() => { if (!disposed) { refreshSettings(); void probeRows(); } });
    let cleaned = false;
    function destroy() {
        if (cleaned) return;
        cleaned = disposed = true; revision++;
        for (const controller of controllers) controller.abort();
        controllers.clear(); void releaseJob(); browser.destroy(); unsubscribe();
        window.removeEventListener('pagehide', pageHide);
        stopPlaybackWatch();
        video.pause(); video.removeAttribute('src'); video.load();
    }
    const originalRemove = domWidget.onRemove;
    domWidget.onRemove = function () { destroy(); return originalRemove?.apply(this, arguments); };
    return {
        element: root, rows, addFiles, editRow, destroy,
        restore(data) {
            revision++; probeRevision++; void releaseJob(); joined = null; selectedSource = undefined;
            for (const controller of controllers) controller.abort();
            probes.clear();
            const named = data?.widgets_values_named;
            for (const [index, name] of NATIVE_WIDGETS.entries()) {
                const value = named && name in named ? named[name] : data?.widgets_values?.[index];
                if (value !== undefined && widget(name)) widget(name).value = value;
            }
            refreshSettings(); render(); void probeRows();
        },
        serialize(data) {
            data.widgets_values = NATIVE_WIDGETS.map(name => widget(name)?.value);
            data.widgets_values_named = Object.fromEntries(NATIVE_WIDGETS.map(name => [name, widget(name)?.value]));
        },
        executed(message) {
            void releaseJob(); joined = message?.eclipse_video?.[0] || null; joinedStale = false;
            report = message?.eclipse_report?.[0] || '';
            status.textContent = joined ? 'Joined output ready.' : 'Joined preview unavailable.';
            status.title = report.slice(-2000);
            if (joined) present({ tab: 'joined' });
            render();
        },
    };
}

app.registerExtension({
    name: 'Eclipse.LoadVideo',
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            this._eclipseVideo = createVideoEditor(this);
            if (!isConfiguringGraph()) this.setSize?.([740, 760]);
            return result;
        };
        const configure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (data) { configure?.apply(this, arguments); this._eclipseVideo?.restore(data); };
        const serialize = nodeType.prototype.onSerialize;
        nodeType.prototype.onSerialize = function (data) { serialize?.apply(this, arguments); this._eclipseVideo?.serialize(data); };
        const executed = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) { executed?.apply(this, arguments); this._eclipseVideo?.executed(message); };
        const removed = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () { this._eclipseVideo?.destroy(); return removed?.apply(this, arguments); };
    },
});
