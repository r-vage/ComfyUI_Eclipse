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
import {
    chooseAdjacentImage,
    createEclipseImageBrowser,
    invalidateImageBrowserThumbnail,
    invalidateImageBrowserThumbnailSource
} from './eclipse-image-browser.js';
import { migrateLoadImageWorkflow } from './eclipse-load-image-workflow-migration.js';
const NODE_CONFIGS = {
    'Load Image (Metadata Pipe) [Eclipse]': {
        extName: 'Eclipse.LoadImage',
        logPrefix: 'LoadImage',
        widgetName: '_li_source'
    },
    'Load Image (Pipe) [Eclipse]': {
        extName: 'Eclipse.LoadImagePipe',
        logPrefix: 'LoadImagePipe',
        widgetName: '_lip_source'
    },
};
const MODE_OPTIONS = ['input', 'output'];

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
    let disposed = false;
    const dispose = () => {
        if (disposed) return;
        disposed = true;
        unsubscribeModeChange();
    };
    return dispose;
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

function buildThumbnailURL(rel, type) {
    const {
        filename,
        subfolder
    } = parseImagePath(rel);
    const params = new URLSearchParams({
        filename,
        type,
        subfolder
    });
    return `/eclipse/load_image/thumbnail?${params.toString()}`;
}
async function loadPreview(node, rel, type) {
    const requestId = (node._eclipseLoadImagePreviewRequest || 0) + 1;
    node._eclipseLoadImagePreviewRequest = requestId;
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
        if (node._eclipseLoadImagePreviewRequest !== requestId) return;
        node.imgs = [img];
        node.imageIndex = 0;
    } catch {
        if (node._eclipseLoadImagePreviewRequest !== requestId) return;
        node.imgs = null;
        node.imageIndex = null;
    }
    if (node._eclipseLoadImagePreviewRequest !== requestId) return;
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
    const previous = document.querySelector('.eclipse-li-context-menu');
    previous?._eclipseDispose?.();
    previous?.remove();
    const currentImg = node.imgs?.[node.imageIndex ?? 0];
    if (!currentImg?.src) return () => {};
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
            dispose();
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
    let closerTimer = 0;
    const closer = (ev) => {
        if (!menu.contains(ev.target)) {
            dispose();
        }
    };
    const dispose = () => {
        if (closerTimer) clearTimeout(closerTimer);
        closerTimer = 0;
        document.removeEventListener('pointerdown', closer, true);
        menu.remove();
    };
    menu._eclipseDispose = dispose;
    closerTimer = setTimeout(() => {
        closerTimer = 0;
        document.addEventListener('pointerdown', closer, true);
    }, 0);
    return dispose;
}
app.registerExtension({
    name: 'Eclipse.LoadImageWorkflowMigration',
    beforeConfigureGraph(graphData) {
        migrateLoadImageWorkflow(graphData);
    },
});

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
                // Keep all schema combos as serialized backing widgets. The Eclipse
                // browser below is the only visible filename control in input/output
                // modes, while folder_source remains the serialized source value.
                vis.hideInitially(['image', 'output_image', 'folder_source']);
                createDOMPreview(node, {
                    minHeight: 50,
                    freeResize: true,
                    restoreLegacyHost: restoreLegacyHostPreview,
                });
                const getWidget = (name) => node.widgets?.find(w => w.name === name);
                const getSourceWidget = () => getWidget('folder_source');
                const getInputCombo = () => getWidget('image');
                const getOutputCombo = () => getWidget('output_image');
                const normalizeSource = source => source === 'output' ? 'output' : 'input';
                const getCurrentSource = () => normalizeSource(getSourceWidget()?.value);
                const comboForSource = source => normalizeSource(source) === 'output'
                    ? getOutputCombo()
                    : getInputCombo();
                const getActiveCombo = () => comboForSource(currentMode);
                let currentMode = getCurrentSource();
                let compatibilitySource = currentMode;
                let imageBrowser = null;
                let _maskEditorPending = false;
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
                                void activateSource('input', rel);
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
                        syncSourceState(source);
                        getCachedFileList(source).then(files => applyFileList(files, source));
                    }
                };
                document.addEventListener('eclipse-filelist-changed', onFileListChanged);

                function syncSourceState(source) {
                    const normalizedSource = normalizeSource(source);
                    currentMode = normalizedSource;
                    compatibilitySource = normalizedSource;
                    const sourceWidget = getSourceWidget();
                    if (sourceWidget && sourceWidget.value !== normalizedSource) {
                        sourceWidget.value = normalizedSource;
                    }
                    return normalizedSource;
                }
                async function applyFileList(files, source, selectFile) {
                    source = normalizeSource(source);
                    const combo = comboForSource(source);
                    if (!combo || !combo.options) return;
                    combo.options.values = files;
                    if (selectFile && files.includes(selectFile)) {
                        combo.value = selectFile;
                    } else if (!files.includes(combo.value)) {
                        combo.value = files.length > 0 ? files[0] : '';
                    }
                    if (currentMode === source) {
                        imageBrowser?.setSource(source);
                        imageBrowser?.setFiles(files, combo.value);
                        await loadPreview(node, combo.value, source);
                    }
                }
                async function fetchAndApply(source, selectFile) {
                    const files = await getCachedFileList(source);
                    await applyFileList(files, source, selectFile);
                }
                function syncBrowserSource(source) {
                    source = normalizeSource(source);
                    const combo = comboForSource(source);
                    imageBrowser?.setSource(source);
                    imageBrowser?.setFiles(combo?.options?.values || [], combo?.value || '');
                }
                async function activateSource(source, selectFile) {
                    source = syncSourceState(source);
                    const combo = comboForSource(source);
                    if (combo && selectFile) combo.value = selectFile;
                    syncBrowserSource(source);
                    await loadPreview(node, combo?.value || '', source);
                    await fetchAndApply(source, selectFile);
                }
                async function selectImage(source, filename) {
                    source = normalizeSource(source);
                    const combo = comboForSource(source);
                    if (!combo) return;
                    combo.value = filename || '';
                    if (currentMode !== source) return;
                    imageBrowser?.setSource(source);
                    imageBrowser?.setSelected(combo.value);
                    await loadPreview(node, combo.value, source);
                }
                async function restoreLegacyHostPreview({ promotedWidgets }) {
                    const filenames = {
                        input: promotedWidgets.image?.value,
                        output: promotedWidgets.output_image?.value,
                    };
                    const populated = source => typeof filenames[source] === 'string'
                        && filenames[source] !== '' && filenames[source] !== 'none';
                    const promotedSource = promotedWidgets.folder_source?.value;
                    let source = promotedSource === 'output' ? 'output'
                        : promotedSource === 'input' ? 'input'
                            : getCurrentSource();
                    if (!populated(source)) {
                        const available = ['input', 'output'].filter(populated);
                        if (available.length !== 1) return;
                        [source] = available;
                    }
                    const filename = filenames[source];
                    const combo = comboForSource(source);
                    if (!combo) return;
                    combo.value = filename;
                    await activateSource(source, filename);
                }
                const sourceW = getSourceWidget();
                const origIdx = sourceW ? node.widgets.indexOf(sourceW) : 0;
                vis.setVisible('folder_source', false);
                const compatibilityElement = document.createElement('div');
                compatibilityElement.setAttribute('aria-hidden', 'true');
                const modeWidget = node.addDOMWidget(cfg.widgetName, 'custom', compatibilityElement, {
                    getValue: () => compatibilitySource,
                    setValue: (v) => {
                        if (MODE_OPTIONS.includes(v)) {
                            compatibilitySource = v;
                            currentMode = v;
                        }
                    },
                    getMinHeight: () => 0,
                    getMaxHeight: () => 0,
                    serialize: false,
                });
                const newIdx = node.widgets.indexOf(modeWidget);
                if (newIdx >= 0 && newIdx !== origIdx) {
                    node.widgets.splice(newIdx, 1);
                    node.widgets.splice(origIdx, 0, modeWidget);
                }
                vis.setVisible(cfg.widgetName, false);
                imageBrowser = createEclipseImageBrowser({
                    source: currentMode === 'output' ? 'output' : 'input',
                    selected: getActiveCombo()?.value || '',
                    buildThumbnailURL,
                    fetchThumbnail: (url) => api.fetchApi(url, { cache: 'no-store' }),
                    onSourceChange: (source) => activateSource(source),
                    onSelect: (filename, source) => selectImage(source, filename),
                    onUpload: (files) => handleDroppedFiles(files),
                    onRefresh: async (source) => {
                        invalidateImageBrowserThumbnailSource(source);
                        invalidateFileListCache(source);
                        await fetchAndApply(source);
                        document.dispatchEvent(new CustomEvent('eclipse-filelist-changed', {
                            detail: { source }
                        }));
                        const count = comboForSource(source)?.options?.values?.length || 0;
                        return { message: `Refreshed ${count} image${count === 1 ? '' : 's'}` };
                    },
                    onDelete: (filename, source) => handleDelete(filename, source),
                });
                const browserWidget = node.addDOMWidget('_image_browser', 'custom', imageBrowser.element, {
                    getValue: () => getActiveCombo()?.value || '',
                    setValue: (value) => imageBrowser?.setSelected(value || ''),
                    getMinHeight: () => 32,
                    getMaxHeight: () => 32,
                    serialize: false,
                });
                const disposeBrowserWidgetHeight = keepDOMWidgetFixedHeight(node, browserWidget, 32);
                const unsubscribeBrowserModeChange = onVueModeChange(() => imageBrowser?.close());
                const browserIdx = node.widgets.indexOf(browserWidget);
                const currentModeIdx = node.widgets.indexOf(modeWidget);
                if (browserIdx >= 0 && currentModeIdx >= 0 && browserIdx !== currentModeIdx + 1) {
                    node.widgets.splice(browserIdx, 1);
                    node.widgets.splice(currentModeIdx + 1, 0, browserWidget);
                }
                const _imgFilter = (f) => f.type?.startsWith('image/') || /\.(png|jpe?g|webp|bmp|gif|tiff?)$/i.test(f.name);
                async function handleDroppedFiles(files) {
                    const imageFiles = Array.from(files).filter(_imgFilter);
                    if (!imageFiles.length) {
                        return { success: false, files: [], errors: ['No supported image files'], message: 'No supported image files' };
                    }
                    const formData = new FormData();
                    for (const f of imageFiles) formData.append('images', f, f.name);
                    try {
                        const resp = await api.fetchApi('/eclipse/load_image/upload', {
                            method: 'POST',
                            body: formData,
                        });
                        const result = await resp.json();
                        const saved = Array.isArray(result.files) ? result.files : [];
                        const errors = Array.isArray(result.errors) ? result.errors.slice() : [];
                        if (!result.success && result.error) errors.push(result.error);
                        if (saved.length) {
                            const lastFile = saved[saved.length - 1];
                            console.log(`[Eclipse ${cfg.logPrefix}] ✓ Uploaded ${saved.length} file(s)`);
                            invalidateFileListCache('input');
                            await activateSource('input', lastFile);
                            document.dispatchEvent(new CustomEvent('eclipse-filelist-changed', {
                                detail: {
                                    source: 'input'
                                }
                            }));
                        }
                        if (errors.length) {
                            const summary = saved.length
                                ? `Uploaded ${saved.length} image${saved.length === 1 ? '' : 's'}, but ${errors.length} failed:\n\n${errors.join('\n')}`
                                : `Upload failed:\n\n${errors.join('\n')}`;
                            console.warn(`[Eclipse ${cfg.logPrefix}] ${summary}`);
                            alert(summary);
                        }
                        return {
                            success: saved.length > 0 && errors.length === 0,
                            files: saved,
                            errors,
                            message: errors.length
                                ? `${saved.length} uploaded, ${errors.length} failed`
                                : `Uploaded ${saved.length} image${saved.length === 1 ? '' : 's'}`,
                        };
                    } catch (e) {
                        console.error(`[Eclipse ${cfg.logPrefix}] Drop upload failed:`, e);
                        alert('Upload failed. Check console for details.');
                        return { success: false, files: [], errors: [String(e)], message: 'Upload failed' };
                    }
                }
                const originalOnDragOver = node.onDragOver;
                const originalOnDragDrop = node.onDragDrop;
                const onNodeDragOver = function (e) {
                    if (e?.dataTransfer?.items) {
                        return Array.from(e.dataTransfer.items).some(item => item.kind === 'file');
                    }
                    return false;
                };
                const onNodeDragDrop = function (e) {
                    if (e?.dataTransfer?.files?.length) {
                        const valid = Array.from(e.dataTransfer.files).filter(_imgFilter);
                        if (valid.length) {
                            handleDroppedFiles(valid);
                            return true;
                        }
                    }
                    return false;
                };
                node.onDragOver = onNodeDragOver;
                node.onDragDrop = onNodeDragDrop;
                const previewEl = node._eclipseDomPreview?.container;
                let previewMenuDispose = null;
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
                        previewMenuDispose?.();
                        previewMenuDispose = showPreviewContextMenu(e, node);
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
                        void selectImage('input', value);
                    };
                }
                const outputCombo = getOutputCombo();
                if (outputCombo) {
                    outputCombo.callback = function (value) {
                        void selectImage('output', value);
                    };
                }
                const handleDelete = async (requestedFilename, requestedSource) => {
                    const source = normalizeSource(requestedSource || currentMode);
                    const combo = comboForSource(source);
                    if (!combo) return { success: false, message: 'Image control unavailable' };
                    const filename = requestedFilename || combo.value;
                    if (!filename || filename === 'none') return { success: false, message: 'No image selected' };
                    if (!confirm(`Delete "${filename}"?`)) return { success: false, cancelled: true };
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
                            invalidateImageBrowserThumbnail(filename, source);
                            invalidateFileListCache(source);
                            const files = await getCachedFileList(source);
                            combo.options.values = files;
                            if (!files.includes(combo.value)) combo.value = chooseAdjacentImage(files, deletedIndex);
                            if (currentMode === source) imageBrowser?.setFiles(files, combo.value);
                            await loadPreview(node, combo.value || '', source);
                            document.dispatchEvent(new CustomEvent('eclipse-filelist-changed', {
                                detail: {
                                    source
                                }
                            }));
                            return { success: true, message: `Deleted "${filename}"` };
                        } else {
                            console.error(`[Eclipse ${cfg.logPrefix}] Delete failed: ${result.error}`);
                            alert(`Failed to delete: ${result.error}`);
                            return { success: false, message: `Delete failed: ${result.error}` };
                        }
                    } catch (e) {
                        console.error(`[Eclipse ${cfg.logPrefix}] Delete request failed:`, e);
                        alert('Delete request failed. Check console for details.');
                        return { success: false, message: 'Delete request failed' };
                    }
                };

                function initFromRestoredState() {
                    const source = getCurrentSource();
                    void activateSource(source);
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
                    unsubscribeBrowserModeChange();
                    disposeBrowserWidgetHeight();
                    previewMenuDispose?.();
                    if (node.onDragOver === onNodeDragOver) node.onDragOver = originalOnDragOver;
                    if (node.onDragDrop === onNodeDragDrop) node.onDragDrop = originalOnDragDrop;
                    imageBrowser?.destroy();
                    return origOnRemoved?.apply(this, arguments);
                };
            };
        },
    });
}
