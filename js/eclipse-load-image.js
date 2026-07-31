import {
    app,
    api
} from './comfy/index.js';
import {
    createWidgetVisibilityManager,
    isVueMode,
    notifyVue,
    onVueModeChange
} from './eclipse-widget-performance-utils.js';
import {
    createDOMPreview,
    feedDOMPreview,
    clearDOMPreview
} from './eclipse-dom-preview.js';
import { markEclipseContextMenuOwner } from './eclipse-context-menu-ownership.js';
const NODE_CONFIGS = {
    'Load Image (Metadata Pipe) [Eclipse]': {
        extName: 'Eclipse.LoadImage',
        cssPrefix: 'li',
        logPrefix: 'LoadImage',
        widgetName: '_li_source'
    },
    'Load Image (Pipe) [Eclipse]': {
        extName: 'Eclipse.LoadImagePipe',
        cssPrefix: 'lip',
        logPrefix: 'LoadImagePipe',
        widgetName: '_lip_source'
    },
};
const NODE_NAMES = Object.keys(NODE_CONFIGS);
const MODE_OPTIONS = ['input', 'output', 'url'];
const MODE_TOOLTIPS = {
    input: 'Load from ComfyUI input/ folder',
    output: 'Load from ComfyUI output/ folder',
    url: 'Download an image from a remote URL and save it into the ComfyUI input/ folder',
};
const _cssInjectedPrefixes = new Set();

function keepDOMWidgetFixedHeight(node, widget, height) {
    const originalComputeLayoutSize = widget.computeLayoutSize;
    const applyRendererMode = () => {
        widget.computeLayoutSize = isVueMode() ? undefined : originalComputeLayoutSize;
        widget.computedHeight = height;
    };
    applyRendererMode();
    const unsubscribeModeChange = onVueModeChange(() => {
        applyRendererMode();
        notifyVue(node);
        node.setDirtyCanvas?.(true, true);
    });
    const originalOnRemove = widget.onRemove;
    widget.onRemove = function () {
        unsubscribeModeChange();
        return originalOnRemove?.apply(this, arguments);
    };
}

function injectModeBarCSS(prefix) {
    if (_cssInjectedPrefixes.has(prefix)) return;
    _cssInjectedPrefixes.add(prefix);
    const style = document.createElement('style');
    style.textContent = `
.eclipse-${prefix}-mode-bar {
    display: flex; align-items: center; gap: 4px;
    width: 100%; height: 100%; padding: 0 6px 6px; box-sizing: border-box;
}
.eclipse-${prefix}-mode-chip {
    cursor: pointer; padding: 2px 10px; border-radius: 4px;
    font-size: 0.75rem; font-family: sans-serif; user-select: none;
    background: #2a2a2a; color: #888; border: 1px solid #444;
    flex: 1; text-align: center;
    transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.eclipse-${prefix}-mode-chip.selected {
    background: #2a5a3a; color: #ddd; border-color: #4a8a5a;
}
.eclipse-${prefix}-url-container {
    display: flex; align-items: center; gap: 4px;
    width: 100%; height: 100%; padding: 0 6px; box-sizing: border-box;
}
.eclipse-${prefix}-url-input {
    flex: 1; min-width: 0; padding: 3px 8px; border-radius: 4px;
    border: 1px solid #444; background: #1a1a1a; color: #ccc;
    font-size: 11px; font-family: monospace; outline: none;
}
.eclipse-${prefix}-url-input:focus { border-color: #4a8a5a; }
.eclipse-${prefix}-url-btn {
    flex-shrink: 0; padding: 3px 10px; border-radius: 4px;
    border: 1px solid #4a8a5a; background: #2a5a3a; color: #ddd;
    cursor: pointer; font-size: 12px; white-space: nowrap;
}
.eclipse-${prefix}-url-btn:hover { background: #3a6a4a; }
.eclipse-${prefix}-url-btn:disabled { opacity: 0.5; cursor: default; }`;
    document.head.appendChild(style);
}
for (const cfg of Object.values(NODE_CONFIGS)) {
    injectModeBarCSS(cfg.cssPrefix);
}
async function fetchImageList(source) {
    const url = source === 'output' ? '/eclipse/load_image/list_output' : '/eclipse/load_image/list';
    try {
        const resp = await api.fetchApi(url, {
            cache: 'no-store'
        });
        const data = await resp.json();
        return data.success ? data.files : [];
    } catch (e) {
        console.error('[Eclipse LoadImage] Failed to fetch image list:', e);
        return [];
    }
}

function parseImagePath(rel) {
    const parts = (rel || '').split('/');
    const filename = parts.pop();
    const subfolder = parts.join('/');
    return {
        filename,
        subfolder
    };
}

function buildViewURL(rel, type) {
    const {
        filename,
        subfolder
    } = parseImagePath(rel);
    const params = new URLSearchParams({
        filename,
        type,
        subfolder
    });
    return api.apiURL(`/view?${params.toString()}`);
}
async function loadPreview(node, rel, type) {
    const nodeId = String(node.id);
    if (app.nodeOutputs?.[nodeId]?.images) {
        delete app.nodeOutputs[nodeId].images;
    }
    if (!rel || rel === 'none') {
        node.imgs = null;
        node.imageIndex = null;
        clearDOMPreview(node);
        node.setDirtyCanvas(true, true);
        return;
    }
    const {
        filename,
        subfolder
    } = parseImagePath(rel);
    const imageData = [{
        filename,
        type: type || 'input',
        subfolder
    }];
    try {
        const url = buildViewURL(rel, type);
        const img = new Image();
        img.crossOrigin = 'anonymous';
        await new Promise((resolve, reject) => {
            img.onload = resolve;
            img.onerror = reject;
            img.src = url + `&cb=${Date.now()}`;
        });
        node.imgs = [img];
        node.imageIndex = 0;
    } catch {
        node.imgs = null;
        node.imageIndex = null;
    }
    if (node._eclipseDomPreview) {
        feedDOMPreview(node, {
            images: imageData
        });
    }
    node.setDirtyCanvas(true, true);
}
const _fileListCache = window._eclipseFileListCache || (window._eclipseFileListCache = {
    data: {},
    pending: {},
});
async function getCachedFileList(source) {
    if (_fileListCache.data[source]) return _fileListCache.data[source];
    if (!_fileListCache.pending[source]) {
        _fileListCache.pending[source] = fetchImageList(source).then(files => {
            _fileListCache.data[source] = files;
            _fileListCache.pending[source] = null;
            return files;
        });
    }
    return _fileListCache.pending[source];
}

function invalidateFileListCache(source) {
    delete _fileListCache.data[source];
    delete _fileListCache.pending[source];
}

function showPreviewContextMenu(e, node) {
    e.preventDefault();
    e.stopPropagation();
    document.querySelector('.eclipse-li-context-menu')?.remove();
    const currentImg = node.imgs?.[node.imageIndex ?? 0];
    if (!currentImg?.src) return;
    const menu = document.createElement('div');
    menu.className = 'eclipse-li-context-menu';
    menu.style.cssText = `position:fixed;left:${e.clientX}px;top:${e.clientY}px;` + 'background:#2a2a2a;border:1px solid #555;border-radius:4px;' + 'padding:4px 0;z-index:100000;min-width:200px;' + 'font:13px sans-serif;color:#ddd;box-shadow:2px 4px 12px rgba(0,0,0,0.6);';

    function addItem(label, action) {
        const row = document.createElement('div');
        row.textContent = label;
        row.style.cssText = 'padding:5px 16px;cursor:pointer;white-space:nowrap;';
        row.addEventListener('mouseenter', () => {
            row.style.background = '#3a3a5a';
        });
        row.addEventListener('mouseleave', () => {
            row.style.background = 'transparent';
        });
        row.addEventListener('click', () => {
            action();
            menu.remove();
        });
        menu.appendChild(row);
    }

    function addSeparator() {
        const sep = document.createElement('div');
        sep.style.cssText = 'height:1px;background:#444;margin:4px 0;';
        menu.appendChild(sep);
    }
    addItem('Open Image', () => {
        const url = new URL(currentImg.src);
        url.searchParams.delete('preview');
        url.searchParams.delete('cb');
        window.open(url.toString(), '_blank');
    });
    addItem('Save Image', () => {
        const url = new URL(currentImg.src);
        url.searchParams.delete('preview');
        url.searchParams.delete('cb');
        const fname = url.searchParams.get('filename') || 'image.png';
        const a = document.createElement('a');
        a.href = url.toString();
        a.download = fname;
        a.click();
    });
    addSeparator();
    addItem('Open in MaskEditor', () => {
        const items = [];
        node.getExtraMenuOptions?.(app.canvas, items);
        const maskItem = items.find(i => i?.content?.includes('MaskEditor'));
        if (maskItem) {
            maskItem.callback();
        } else {
            console.warn('[Eclipse LoadImage] MaskEditor not available for this node');
        }
    });
    document.body.appendChild(menu);
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth) menu.style.left = `${window.innerWidth - rect.width - 4}px`;
    if (rect.bottom > window.innerHeight) menu.style.top = `${window.innerHeight - rect.height - 4}px`;
    const closer = (ev) => {
        if (!menu.contains(ev.target)) {
            menu.remove();
            document.removeEventListener('pointerdown', closer, true);
        }
    };
    setTimeout(() => document.addEventListener('pointerdown', closer, true), 0);
}
for (const [nodeName, cfg] of Object.entries(NODE_CONFIGS)) {
    app.registerExtension({
        name: cfg.extName,
        async beforeRegisterNodeDef(nodeType, nodeData, _app) {
            if (nodeData.name !== nodeName) return;
            const origOnNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                origOnNodeCreated?.apply(this, arguments);
                const node = this;
                const vis = createWidgetVisibilityManager(node);
                // Pre-hide schema widgets hidden at defaults (folder_source='input'):
                // output_image is hidden, folder_source is the always-hidden backing
                // widget. DOM widgets (_btn_*, _url_input) are added later by this
                // extension and are not present at onNodeCreated time.
                vis.hideInitially(['output_image', 'folder_source']);
                createDOMPreview(node, {
                    minHeight: 50,
                    freeResize: true
                });
                let _maskEditorPending = false;
                const _origImgsSetter = Object.getOwnPropertyDescriptor(node, 'imgs');
                let _imgsValue = node.imgs;
                Object.defineProperty(node, 'imgs', {
                    get() {
                        return _imgsValue;
                    },
                    set(val) {
                        _imgsValue = val;
                        if (val && val.length > 0) {
                            _maskEditorPending = true;
                        }
                        if ((val === undefined || val === null) && _maskEditorPending) {
                            _maskEditorPending = false;
                            const ref = node.images?.[0];
                            if (ref?.subfolder === 'clipspace') {
                                const rel = (ref.subfolder ? ref.subfolder + '/' : '') + ref.filename;
                                console.log(`[Eclipse ${cfg.logPrefix}] MaskEditor saved → ${rel}`);
                                invalidateFileListCache('input');
                                if (currentMode !== 'input') {
                                    currentMode = 'input';
                                    for (const c of chipEls) c.classList.toggle('selected', c.textContent === 'input');
                                    syncSourceToBacking('input');
                                    updateModeUI('input');
                                }
                                fetchAndApply('input', rel);
                                document.dispatchEvent(new CustomEvent('eclipse-filelist-changed', {
                                    detail: {
                                        source: 'input'
                                    }
                                }));
                            }
                        }
                    },
                    configurable: true,
                    enumerable: true,
                });
                const origOnDrawBackground = node.onDrawBackground;
                node.onDrawBackground = function (ctx) {
                    const saved = _imgsValue;
                    _imgsValue = null;
                    origOnDrawBackground?.call(this, ctx);
                    _imgsValue = saved;
                };
                const onFileListChanged = (e) => {
                    const source = e.detail?.source;
                    if (source && getCurrentSource() === source) {
                        getCachedFileList(source).then(files => applyFileList(files, source));
                    }
                };
                document.addEventListener('eclipse-filelist-changed', onFileListChanged);
                const getWidget = (name) => node.widgets?.find(w => w.name === name);
                const getSourceWidget = () => getWidget('folder_source');
                const getInputCombo = () => getWidget('image');
                const getOutputCombo = () => getWidget('output_image');
                const getCurrentSource = () => {
                    const w = getSourceWidget();
                    return (w && w.value === 'output') ? 'output' : 'input';
                };
                const getActiveCombo = () => getCurrentSource() === 'output' ? getOutputCombo() : getInputCombo();

                function syncSourceToBacking(source) {
                    const w = getSourceWidget();
                    const backingValue = source === 'url' ? 'input' : source;
                    if (w && w.value !== backingValue) w.value = backingValue;
                }
                async function applyFileList(files, source, selectFile) {
                    const combo = source === 'output' ? getOutputCombo() : getInputCombo();
                    if (!combo || !combo.options) return;
                    combo.options.values = files;
                    if (selectFile && files.includes(selectFile)) {
                        combo.value = selectFile;
                    } else if (!files.includes(combo.value)) {
                        combo.value = files.length > 0 ? files[0] : '';
                    }
                    await loadPreview(node, combo.value, source);
                }
                async function fetchAndApply(source, selectFile) {
                    const files = await getCachedFileList(source);
                    await applyFileList(files, source, selectFile);
                }
                async function switchToMode(source, selectFile) {
                    if (source === 'url') return;
                    const files = await getCachedFileList(source);
                    await applyFileList(files, source, selectFile);
                }

                function updateModeUI(source) {
                    vis.setVisible('image', source === 'input');
                    vis.setVisible('output_image', source === 'output');
                    vis.setVisible('_btn_upload', source === 'input');
                    vis.setVisible('_btn_refresh', source !== 'url');
                    vis.setVisible('_btn_delete', source !== 'url');
                    vis.setVisible('_url_input', source === 'url');
                }
                const sourceW = getSourceWidget();
                const origIdx = sourceW ? node.widgets.indexOf(sourceW) : 0;
                vis.setVisible('folder_source', false);
                let currentMode = getCurrentSource();
                const bar = document.createElement('div');
                bar.className = `eclipse-${cfg.cssPrefix}-mode-bar`;
                const chipEls = [];
                for (const opt of MODE_OPTIONS) {
                    const chip = document.createElement('span');
                    chip.className = `eclipse-${cfg.cssPrefix}-mode-chip` + (opt === currentMode ? ' selected' : '');
                    chip.textContent = opt;
                    if (MODE_TOOLTIPS[opt]) chip.title = MODE_TOOLTIPS[opt];
                    chip.addEventListener('pointerdown', (e) => {
                        e.stopPropagation();
                        e.preventDefault();
                        if (opt === currentMode) return;
                        currentMode = opt;
                        for (const c of chipEls) c.classList.toggle('selected', c.textContent === currentMode);
                        syncSourceToBacking(currentMode);
                        updateModeUI(currentMode);
                        switchToMode(currentMode);
                    });
                    chipEls.push(chip);
                    bar.appendChild(chip);
                }
                const modeWidget = node.addDOMWidget(cfg.widgetName, 'custom', bar, {
                    getValue: () => currentMode,
                    setValue: (v) => {
                        if (MODE_OPTIONS.includes(v)) {
                            currentMode = v;
                            for (const c of chipEls) c.classList.toggle('selected', c.textContent === currentMode);
                        }
                    },
                    getMinHeight: () => 26,
                    getMaxHeight: () => 26,
                    serialize: false,
                });
                keepDOMWidgetFixedHeight(node, modeWidget, 26);
                const newIdx = node.widgets.indexOf(modeWidget);
                if (newIdx >= 0 && newIdx !== origIdx) {
                    node.widgets.splice(newIdx, 1);
                    node.widgets.splice(origIdx, 0, modeWidget);
                }
                const urlContainer = document.createElement('div');
                urlContainer.className = `eclipse-${cfg.cssPrefix}-url-container`;
                const urlInput = document.createElement('input');
                urlInput.type = 'text';
                urlInput.className = `eclipse-${cfg.cssPrefix}-url-input`;
                urlInput.placeholder = 'Paste image URL...';
                urlInput.addEventListener('pointerdown', (e) => {
                    e.stopPropagation();
                });
                urlInput.addEventListener('keydown', (e) => {
                    e.stopPropagation();
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        urlDownloadBtn.click();
                    }
                });
                const urlDownloadBtn = document.createElement('button');
                urlDownloadBtn.className = `eclipse-${cfg.cssPrefix}-url-btn`;
                urlDownloadBtn.textContent = '⬇ Download';
                urlDownloadBtn.title = 'Download image from URL to input folder';
                urlDownloadBtn.addEventListener('pointerdown', (e) => {
                    e.stopPropagation();
                });
                urlDownloadBtn.addEventListener('click', async () => {
                    const url = urlInput.value.trim();
                    if (!url) return;
                    urlDownloadBtn.disabled = true;
                    urlDownloadBtn.textContent = '⏳ Downloading...';
                    try {
                        const resp = await api.fetchApi('/eclipse/load_image/download_url', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({
                                url
                            }),
                        });
                        const result = await resp.json();
                        if (result.success) {
                            console.log(`[Eclipse ${cfg.logPrefix}] ✓ Downloaded "${result.filename}" from URL`);
                            urlInput.value = '';
                            invalidateFileListCache('input');
                            currentMode = 'input';
                            for (const c of chipEls) c.classList.toggle('selected', c.textContent === 'input');
                            syncSourceToBacking('input');
                            updateModeUI('input');
                            await fetchAndApply('input', result.filename);
                            document.dispatchEvent(new CustomEvent('eclipse-filelist-changed', {
                                detail: {
                                    source: 'input'
                                }
                            }));
                        } else {
                            alert(`Download failed: ${result.error}`);
                        }
                    } catch (e) {
                        console.error(`[Eclipse ${cfg.logPrefix}] URL download failed:`, e);
                        alert('Download failed. Check console for details.');
                    } finally {
                        urlDownloadBtn.disabled = false;
                        urlDownloadBtn.textContent = '⬇ Download';
                    }
                });
                urlContainer.appendChild(urlInput);
                urlContainer.appendChild(urlDownloadBtn);
                const urlWidget = node.addDOMWidget('_url_input', 'custom', urlContainer, {
                    getValue: () => urlInput.value,
                    setValue: (v) => {
                        urlInput.value = v || '';
                    },
                    getMinHeight: () => 28,
                    getMaxHeight: () => 28,
                    serialize: false,
                });
                keepDOMWidgetFixedHeight(node, urlWidget, 28);
                const urlWidgetIdx = node.widgets.indexOf(urlWidget);
                const modeBarIdx = node.widgets.indexOf(modeWidget);
                if (urlWidgetIdx >= 0 && modeBarIdx >= 0 && urlWidgetIdx !== modeBarIdx + 1) {
                    node.widgets.splice(urlWidgetIdx, 1);
                    node.widgets.splice(modeBarIdx + 1, 0, urlWidget);
                }
                const _imgFilter = (f) => f.type?.startsWith('image/') || /\.(png|jpe?g|webp|bmp|gif|tiff?)$/i.test(f.name);
                async function handleDroppedFiles(files) {
                    const imageFiles = Array.from(files).filter(_imgFilter);
                    if (!imageFiles.length) return false;
                    const formData = new FormData();
                    for (const f of imageFiles) formData.append('images', f, f.name);
                    try {
                        const resp = await api.fetchApi('/eclipse/load_image/upload', {
                            method: 'POST',
                            body: formData,
                        });
                        const result = await resp.json();
                        if (result.success && result.files?.length) {
                            const lastFile = result.files[result.files.length - 1];
                            console.log(`[Eclipse ${cfg.logPrefix}] ✓ Dropped ${result.files.length} file(s)`);
                            invalidateFileListCache('input');
                            if (currentMode !== 'input') {
                                currentMode = 'input';
                                for (const c of chipEls) c.classList.toggle('selected', c.textContent === 'input');
                                syncSourceToBacking('input');
                                updateModeUI('input');
                            }
                            await fetchAndApply('input', lastFile);
                            document.dispatchEvent(new CustomEvent('eclipse-filelist-changed', {
                                detail: {
                                    source: 'input'
                                }
                            }));
                        }
                    } catch (e) {
                        console.error(`[Eclipse ${cfg.logPrefix}] Drop upload failed:`, e);
                    }
                    return true;
                }
                node.onDragOver = function (e) {
                    if (e?.dataTransfer?.items) {
                        return Array.from(e.dataTransfer.items).some(item => item.kind === 'file');
                    }
                    return false;
                };
                node.onDragDrop = function (e) {
                    if (e?.dataTransfer?.files?.length) {
                        const valid = Array.from(e.dataTransfer.files).filter(_imgFilter);
                        if (valid.length) {
                            handleDroppedFiles(valid);
                            return true;
                        }
                    }
                    return false;
                };
                const previewEl = node._eclipseDomPreview?.container;
                if (previewEl) {
                    markEclipseContextMenuOwner(previewEl);
                    previewEl.addEventListener('dragover', (e) => {
                        if (e.dataTransfer?.items && Array.from(e.dataTransfer.items).some(i => i.kind === 'file')) {
                            e.preventDefault();
                            e.stopPropagation();
                        }
                    });
                    previewEl.addEventListener('drop', (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        if (e.dataTransfer?.files?.length) {
                            const valid = Array.from(e.dataTransfer.files).filter(_imgFilter);
                            if (valid.length) handleDroppedFiles(valid);
                        }
                    });
                    previewEl.addEventListener('contextmenu', (e) => {
                        showPreviewContextMenu(e, node);
                    }, true);
                }
                const onPaste = (e) => {
                    const selected = app.canvas?.selected_nodes;
                    if (!selected || !selected[node.id]) return;
                    const files = [];
                    if (e.clipboardData?.files?.length) {
                        for (const f of e.clipboardData.files) {
                            if (_imgFilter(f)) files.push(f);
                        }
                    }
                    if (files.length) {
                        e.preventDefault();
                        e.stopPropagation();
                        handleDroppedFiles(files);
                    }
                };
                document.addEventListener('paste', onPaste);
                const inputCombo = getInputCombo();
                if (inputCombo) {
                    inputCombo.callback = function (value) {
                        if (getCurrentSource() === 'input') {
                            loadPreview(node, value, 'input');
                        }
                    };
                }
                const outputCombo = getOutputCombo();
                if (outputCombo) {
                    outputCombo.callback = function (value) {
                        if (getCurrentSource() === 'output') {
                            loadPreview(node, value, 'output');
                        }
                    };
                }
                const handleDelete = async () => {
                    const source = getCurrentSource();
                    const combo = getActiveCombo();
                    if (!combo) return;
                    const filename = combo.value;
                    if (!filename || filename === 'none') return;
                    if (!confirm(`Delete "${filename}"?`)) return;
                    const oldList = combo.options?.values || [];
                    const deletedIndex = oldList.indexOf(filename);
                    try {
                        const resp = await api.fetchApi('/eclipse/load_image/delete', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({
                                filename,
                                folder: source
                            }),
                        });
                        const result = await resp.json();
                        if (result.success) {
                            console.log(`[Eclipse ${cfg.logPrefix}] ✓ Deleted "${filename}" from ${source}`);
                            invalidateFileListCache(source);
                            const files = await getCachedFileList(source);
                            combo.options.values = files;
                            if (!files.includes(combo.value)) {
                                let pick = '';
                                if (files.length > 0 && deletedIndex >= 0) {
                                    const idx = deletedIndex > 0 ? deletedIndex - 1 : 0;
                                    pick = files[Math.min(idx, files.length - 1)];
                                }
                                combo.value = pick;
                            }
                            await loadPreview(node, combo.value || '', source);
                            document.dispatchEvent(new CustomEvent('eclipse-filelist-changed', {
                                detail: {
                                    source
                                }
                            }));
                        } else {
                            console.error(`[Eclipse ${cfg.logPrefix}] Delete failed: ${result.error}`);
                            alert(`Failed to delete: ${result.error}`);
                        }
                    } catch (e) {
                        console.error(`[Eclipse ${cfg.logPrefix}] Delete request failed:`, e);
                        alert('Delete request failed. Check console for details.');
                    }
                };
                const uploadBtn = node.addWidget('button', '_btn_upload', null, () => {
                    const input = document.createElement('input');
                    input.type = 'file';
                    input.multiple = true;
                    input.accept = 'image/png,image/jpeg,image/webp,image/bmp,image/gif,image/tiff,.tif,.tiff';
                    input.onchange = async () => {
                        if (!input.files?.length) return;
                        const formData = new FormData();
                        for (const f of input.files) formData.append('images', f, f.name);
                        try {
                            const resp = await api.fetchApi('/eclipse/load_image/upload', {
                                method: 'POST',
                                body: formData,
                            });
                            const result = await resp.json();
                            if (result.success && result.files?.length) {
                                const lastFile = result.files[result.files.length - 1];
                                console.log(`[Eclipse ${cfg.logPrefix}] ✓ Uploaded ${result.files.length} file(s)`);
                                invalidateFileListCache('input');
                                await fetchAndApply('input', lastFile);
                                document.dispatchEvent(new CustomEvent('eclipse-filelist-changed', {
                                    detail: {
                                        source: 'input'
                                    }
                                }));
                            } else {
                                console.error(`[Eclipse ${cfg.logPrefix}] Upload failed:`, result.error || result.errors);
                            }
                        } catch (e) {
                            console.error(`[Eclipse ${cfg.logPrefix}] Upload request failed:`, e);
                        }
                    };
                    input.click();
                });
                uploadBtn.label = '📁 Upload Image(s)';
                uploadBtn.serialize = false;
                const refreshBtn = node.addWidget('button', '_btn_refresh', null, () => {
                    invalidateFileListCache(getCurrentSource());
                    fetchAndApply(getCurrentSource());
                    document.dispatchEvent(new CustomEvent('eclipse-filelist-changed', {
                        detail: {
                            source: getCurrentSource()
                        }
                    }));
                });
                refreshBtn.label = '🔄 Refresh';
                refreshBtn.serialize = false;
                const deleteBtn = node.addWidget('button', '_btn_delete', null, handleDelete);
                deleteBtn.label = '🗑️ Delete Image';
                deleteBtn.serialize = false;

                function initFromRestoredState() {
                    const source = getCurrentSource();
                    currentMode = source;
                    for (const c of chipEls) c.classList.toggle('selected', c.textContent === currentMode);
                    updateModeUI(source);
                    fetchAndApply(source);
                }
                const origOnConfigure = node.onConfigure;
                node.onConfigure = function (config) {
                    origOnConfigure?.call(this, config);
                    initFromRestoredState();
                };
                initFromRestoredState();
                const origOnModeChange = node.onModeChange;
                node.onModeChange = function (newMode) {
                    origOnModeChange?.call(this, newMode);
                    if (newMode === 0) {
                        const source = getCurrentSource();
                        getCachedFileList(source).then(files => applyFileList(files, source));
                    }
                };
                const origOnRemoved = node.onRemoved;
                node.onRemoved = function () {
                    document.removeEventListener('eclipse-filelist-changed', onFileListChanged);
                    document.removeEventListener('paste', onPaste);
                    return origOnRemoved?.apply(this, arguments);
                };
            };
        },
    });
}
