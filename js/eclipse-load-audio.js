/**
 * Eclipse — Load Audio
 *
 * Adds an HTML5 <audio controls> preview widget and an upload button to
 * `Load Audio [Eclipse]`, mirroring the built-in ComfyUI LoadAudio UI (the
 * frontend hardcodes its own audio UI to a whitelist of class names, so we
 * provide ours).
 *
 * The preview URL contains the selected start_time / duration slice. A precise
 * clip-relative seek slider and readout supplement the browser-native controls
 * so exact timestamps can be copied directly into timeline-planning nodes.
 */

import { app, api } from './comfy/index.js';
import { markEclipseContextMenuOwner } from './eclipse-context-menu-ownership.js';
import { createWidgetVisibilityManager, isConfiguringGraph, smartResize } from './eclipse-widget-performance-utils.js';
import { findSetterByName, getLink, isSetterPathToRootActive } from './eclipse-set-get-utils.js';

const NODE_NAME = 'Load Audio [Eclipse]';
const VIDEO_CONTAINER_EXTENSIONS = new Set([
    '3g2', '3gp', 'avi', 'flv', 'm2ts', 'm4v', 'mkv', 'mov', 'mp4',
    'mpeg', 'mpg', 'mts', 'ogv', 'ts', 'webm', 'wmv',
]);

// Direct mode assignments have no dependable per-node change event in both
// renderers. One lightweight observer serves only the mounted audio nodes.
const sourceWatchers = new Set();
let sourceWatcherTimer = null;
function watchSource(callback) {
    sourceWatchers.add(callback);
    sourceWatcherTimer ??= setInterval(() => {
        if (!isConfiguringGraph()) for (const refresh of sourceWatchers) refresh();
    }, 200);
    return () => {
        sourceWatchers.delete(callback);
        if (!sourceWatchers.size) {
            clearInterval(sourceWatcherTimer);
            sourceWatcherTimer = null;
        }
    };
}

function audioInputRoute(node) {
    let graph = node.graph;
    const input = node.inputs?.find(i => i.name === 'audio_in');
    const path = [input?.link ?? 'disconnected'];
    const result = available => ({ available, key: JSON.stringify(path) });
    if (input?.link == null) return result(false);
    if (!graph) return result(null);
    let link = getLink(graph, input.link);
    const visited = new Set();
    for (let depth = 0; depth < 64 && link; depth++) {
        const source = graph.getNodeById?.(link.origin_id);
        if (!source) return result(null); // May be an unresolved subgraph boundary.
        const slot = link.origin_slot;
        const key = `${graph.id ?? 'root'}:${source.id}:${slot}:${source.mode}`;
        path.push(key);
        if (visited.has(key)) return result(null);
        visited.add(key);
        if (source.mode === 2) return result(false);
        if (source.mode === 4) {
            // Mirror ComfyUI's public type matching: opposite slot, exact AUDIO,
            // then a compatible input. A bypass without such an input is empty.
            const inputs = source.inputs || [];
            const outputType = source.outputs?.[slot]?.type;
            const matches = input => input && LiteGraph.isValidConnection(input.type, outputType)
                && LiteGraph.isValidConnection(input.type, 'AUDIO');
            const match = matches(inputs[slot]) ? inputs[slot]
                : inputs.find(i => i.type === 'AUDIO') || inputs.find(matches);
            link = getLink(graph, match?.link);
        } else if (source.type === 'GetNode [Eclipse]') {
            // Pure lookup avoids virtual getters' UI alerts during observation.
            const setter = findSetterByName(graph, source.widgets?.[0]?.value);
            if (!setter || (setter.graph !== graph && !isSetterPathToRootActive(setter.graph))) return result(false);
            graph = setter.graph;
            path.push(`setter:${graph.id ?? 'root'}:${setter.node.id}`);
            link = getLink(graph, setter.node.inputs?.[slot]?.link);
        } else if (source.isVirtualNode && source.type === 'Reroute') {
            link = getLink(graph, source.inputs?.[0]?.link);
        } else {
            // Arbitrary routers may return None at execution. Until then keep
            // the input pending; Selected file is an immediate manual override.
            return result(true);
        }
    }
    return result(link ? null : false);
}

function formatPlaybackTime(currentTime, duration) {
    const current = Number.isFinite(currentTime) && currentTime >= 0
        ? currentTime.toFixed(3)
        : '0.000';
    const total = Number.isFinite(duration) && duration >= 0
        ? duration.toFixed(3)
        : '--.---';
    return `${current} / ${total} sec`;
}

function buildViewURL(filename) {
    if (!filename) return '';
    // Files live under input/. Subfolder optional via "sub/file.mp3".
    const parts = String(filename).split('/');
    const name = parts.pop();
    const subfolder = parts.join('/');
    const params = new URLSearchParams({ filename: name, type: 'input', subfolder });
    return api.apiURL(`/view?${params.toString()}`);
}

function isVideoContainer(filename) {
    const cleanName = String(filename || '').split(/[?#]/, 1)[0];
    const leafName = cleanName.split(/[\\/]/).pop() || '';
    const extensionIndex = leafName.lastIndexOf('.');
    if (extensionIndex < 0) return false;
    return VIDEO_CONTAINER_EXTENSIONS.has(leafName.slice(extensionIndex + 1).toLowerCase());
}

async function uploadFile(file) {
    const fd = new FormData();
    fd.append('image', file, file.name);
    fd.append('type', 'input');
    fd.append('overwrite', 'true');
    const resp = await api.fetchApi('/upload/image', { method: 'POST', body: fd });
    if (resp.status !== 200) throw new Error(`Upload failed: ${resp.status} ${resp.statusText}`);
    const data = await resp.json();
    return data.subfolder ? `${data.subfolder}/${data.name}` : data.name;
}

app.registerExtension({
    name: 'Eclipse.LoadAudio',
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = origOnNodeCreated?.apply(this, arguments);
            const node = this;

            const audioW = node.widgets?.find((w) => w.name === 'audio');
            const startW = node.widgets?.find((w) => w.name === 'start_time');
            const durW = node.widgets?.find((w) => w.name === 'duration');
            const sourceW = node.widgets?.find((w) => w.name === 'source');
            if (!audioW) return r;
            node.properties ??= {};
            const selectedSource = () => sourceW?.value || 'Auto';
            const sourceLink = () => node.inputs?.find((input) => input.name === 'audio_in')?.link ?? null;
            const sourceState = route => {
                const state = node.properties.eclipseAudioSource;
                return state?.link === sourceLink() && (state.route == null || state.route === route.key) ? state : null;
            };
            const incomingPreview = (route = audioInputRoute(node)) => {
                const state = sourceState(route);
                return selectedSource() !== 'Selected file' && route.available !== false
                    && state?.kind === 'incoming' ? state.preview : null;
            };

            // <audio> DOM widget with a precise clip-relative time readout.
            const preview = document.createElement('div');
            markEclipseContextMenuOwner(preview);
            preview.style.display = 'flex';
            preview.style.flexDirection = 'column';
            preview.style.width = '100%';
            preview.style.gap = '2px';
            preview.addEventListener('contextmenu', (event) => event.stopPropagation());

            const el = document.createElement('audio');
            markEclipseContextMenuOwner(el);
            el.controls = true;
            el.preload = 'metadata';
            el.classList.add('comfy-audio');
            el.style.width = '100%';
            el.addEventListener('contextmenu', (event) => event.stopPropagation());

            const seekSlider = document.createElement('input');
            seekSlider.type = 'range';
            seekSlider.min = '0';
            seekSlider.max = '0';
            seekSlider.step = '0.001';
            seekSlider.value = '0';
            seekSlider.disabled = true;
            seekSlider.setAttribute('aria-label', 'Precise clip playback position');
            seekSlider.title = 'Precise clip playback position';
            seekSlider.style.alignSelf = 'center';
            seekSlider.style.width = 'calc(100% - 8px)';
            seekSlider.style.height = '22px';
            seekSlider.style.margin = '0';
            seekSlider.style.cursor = 'pointer';
            seekSlider.style.accentColor = 'var(--p-primary-color, #9b7cff)';

            const timeReadout = document.createElement('output');
            timeReadout.setAttribute('aria-label', 'Clip playback position and duration');
            timeReadout.style.alignSelf = 'flex-end';
            timeReadout.style.color = 'var(--fg-color, #ccc)';
            timeReadout.style.font = '12px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
            timeReadout.style.fontVariantNumeric = 'tabular-nums';
            timeReadout.style.lineHeight = '16px';
            timeReadout.style.padding = '0 4px';
            timeReadout.textContent = formatPlaybackTime(0, NaN);

            preview.appendChild(el);
            preview.appendChild(seekSlider);
            preview.appendChild(timeReadout);
            const sourceReadout = document.createElement('div');
            sourceReadout.setAttribute('aria-label', 'Audio preview source');
            sourceReadout.style.font = '12px sans-serif';
            sourceReadout.style.color = 'var(--fg-color, #ccc)';
            sourceReadout.style.padding = '0 4px';
            preview.appendChild(sourceReadout);
            const audioUI = node.addDOMWidget('audioUI', 'audio', preview, { serialize: false });
            audioUI.computeSize = function (width) { return [width, 114]; };

            const refreshPlaybackUI = () => {
                const durationAvailable = Number.isFinite(el.duration) && el.duration > 0;
                const current = Number.isFinite(el.currentTime) && el.currentTime >= 0
                    ? el.currentTime
                    : 0;
                seekSlider.disabled = !durationAvailable;
                seekSlider.max = durationAvailable ? String(el.duration) : '0';
                seekSlider.value = String(durationAvailable ? Math.min(current, el.duration) : 0);
                timeReadout.textContent = formatPlaybackTime(el.currentTime, el.duration);
            };
            const resetPlaybackUI = () => {
                seekSlider.disabled = true;
                seekSlider.max = '0';
                seekSlider.value = '0';
                timeReadout.textContent = formatPlaybackTime(0, NaN);
            };
            const handleSeekInput = () => {
                const seconds = Number(seekSlider.value);
                if (seekSlider.disabled || !Number.isFinite(seconds)) return;
                try { el.currentTime = seconds; } catch (_) {}
                refreshPlaybackUI();
            };
            const handleEnded = () => {
                try { el.currentTime = 0; } catch (_) {}
                refreshPlaybackUI();
            };
            const readoutEvents = [
                'loadedmetadata', 'durationchange', 'timeupdate', 'seeking',
                'seeked', 'play', 'pause'
            ];
            readoutEvents.forEach((eventName) => {
                el.addEventListener(eventName, refreshPlaybackUI);
            });
            seekSlider.addEventListener('input', handleSeekInput);
            seekSlider.addEventListener('change', handleSeekInput);
            el.addEventListener('emptied', resetPlaybackUI);
            el.addEventListener('ended', handleEnded);

            const buildDecodedURL = (route = audioInputRoute(node)) => {
                const incoming = incomingPreview(route);
                const v = incoming
                    ? `${incoming.subfolder ? `${incoming.subfolder}/` : ''}${incoming.filename} [temp]`
                    : audioW.value;
                if (!v || v === 'none') return '';
                const s = Math.max(0, Number(startW?.value || 0));
                const d = Math.max(0, Number(durW?.value || 0));
                const params = new URLSearchParams({
                    filename: v,
                    start_time: s,
                    duration: d
                });
                return api.apiURL(`/eclipse/audio_slice?${params.toString()}`);
            };
            const buildPreviewSource = route => {
                const choice = selectedSource();
                const state = sourceState(route);
                const wantsInput = choice === 'Incoming audio' || (choice === 'Auto'
                    && route.available !== false && !(state?.kind === 'file' && state.selection !== 'Selected file'));
                if (wantsInput) {
                    if (incomingPreview(route)) return { kind: 'incoming', url: buildDecodedURL(route) };
                    return { kind: route.available === false ? 'unavailable' : 'pending', url: '' };
                }
                const v = audioW.value;
                if (!v || v === 'none') return { kind: 'none', url: '' };
                const s = Math.max(0, Number(startW?.value || 0));
                const d = Math.max(0, Number(durW?.value || 0));
                if (s > 0 || d > 0 || isVideoContainer(v)) {
                    return { kind: 'decoded', url: buildDecodedURL(route) };
                }
                return { kind: 'raw', url: buildViewURL(v) };
            };

            let pendingStartPlay = null;
            let decodedFallbackAttempted = false;
            let activeSourceKind = 'none';
            let observedRoute = null;
            let visibility;
            let fileControlsVisible = null;
            let disposed = false;
            let stopWatching;
            const clearPendingStartPlay = () => {
                if (!pendingStartPlay) return;
                el.removeEventListener('loadedmetadata', pendingStartPlay);
                pendingStartPlay = null;
            };

            const applySrc = () => {
                if (disposed) return;
                clearPendingStartPlay();
                decodedFallbackAttempted = false;
                const route = audioInputRoute(node);
                observedRoute = route.key;
                const source = buildPreviewSource(route);
                activeSourceKind = source.kind;
                const showFile = !['incoming', 'pending', 'unavailable'].includes(source.kind);
                if (visibility && node.id !== -1 && showFile !== fileControlsVisible) {
                    visibility.setVisible('audio', showFile);
                    visibility.setVisible('choose audio file', showFile);
                    fileControlsVisible = showFile;
                    smartResize(node);
                }
                sourceReadout.textContent = source.kind === 'incoming'
                    ? 'Incoming audio (first batch item)'
                    : source.kind === 'pending' ? 'Incoming audio: queue to preview'
                        : source.kind === 'unavailable' ? 'Incoming audio unavailable: enable/connect its source or select a file'
                            : selectedSource() === 'Selected file' ? 'Selected file (manual)'
                                : 'Selected file (fallback)';
                if (source.url) {
                    if (el.src !== source.url) {
                        resetPlaybackUI();
                        el.src = source.url;
                        el.load();
                    }
                } else {
                    resetPlaybackUI();
                    el.removeAttribute('src');
                    el.load();
                }
            };

            // Browser support varies by codec/container. Retry a failed raw
            // preview once through Eclipse's PyAV decoder, which serves WAV.
            const handlePlaybackError = () => {
                if (activeSourceKind === 'incoming') {
                    sourceReadout.textContent = 'Incoming preview unavailable: queue again (check excerpt range)';
                    resetPlaybackUI();
                    return;
                }
                if (activeSourceKind !== 'raw' || decodedFallbackAttempted) return;
                const fallbackURL = buildDecodedURL();
                if (!fallbackURL) return;
                decodedFallbackAttempted = true;
                activeSourceKind = 'decoded';
                clearPendingStartPlay();
                resetPlaybackUI();
                el.src = fallbackURL;
                el.load();
            };
            el.addEventListener('error', handlePlaybackError);

            // Re-apply the src (and reset playback to start) whenever start_time / duration change.
            const applyWindow = () => {
                const wasPlaying = !el.paused;
                applySrc();
                const startPlay = () => {
                    if (wasPlaying) el.play().catch(() => {});
                    el.removeEventListener('loadedmetadata', startPlay);
                    if (pendingStartPlay === startPlay) pendingStartPlay = null;
                };
                pendingStartPlay = startPlay;
                el.addEventListener('loadedmetadata', startPlay);
            };

            // Initial src and combo callback chain
            const origCb = audioW.callback;
            audioW.callback = function () {
                const ret = origCb?.apply(this, arguments);
                applySrc();
                return ret;
            };
            // React to start_time / duration edits.
            if (startW) {
                const origStartCb = startW.callback;
                startW.callback = function () {
                    const ret = origStartCb?.apply(this, arguments);
                    applyWindow();
                    return ret;
                };
            }
            if (durW) {
                const origDurCb = durW.callback;
                durW.callback = function () {
                    const ret = origDurCb?.apply(this, arguments);
                    applyWindow();
                    return ret;
                };
            }
            if (sourceW) {
                const original = sourceW.callback;
                sourceW.callback = function () {
                    const result = original?.apply(this, arguments);
                    applySrc();
                    return result;
                };
            }

            // Restore preview after workflow load
            const origCfg = node.onGraphConfigured;
            node.onGraphConfigured = function () {
                origCfg?.apply(this, arguments);
                const stopW = node.widgets?.find((w) => w.name === 'stop_review');
                // Older workflows stored an upload-button placeholder here.
                if (stopW && typeof stopW.value !== 'boolean') stopW.value = false;
                if (sourceW && !['Auto', 'Selected file', 'Incoming audio'].includes(sourceW.value)) sourceW.value = 'Auto';
                const state = node.properties.eclipseAudioSource;
                if (state && state.link !== sourceLink()) delete node.properties.eclipseAudioSource;
                applySrc();
            };

            const origExecuted = node.onExecuted;
            node.onExecuted = function (message) {
                origExecuted?.apply(this, arguments);
                const kind = message?.audio_source?.[0];
                if (!kind) return;
                node.properties.eclipseAudioSource = {
                    kind, link: sourceLink(), preview: message.audio_preview?.[0],
                    route: audioInputRoute(node).key, selection: selectedSource(),
                };
                applySrc();
            };
            const origConnections = node.onConnectionsChange;
            node.onConnectionsChange = function (type, index) {
                origConnections?.apply(this, arguments);
                if (type !== 1 || node.inputs?.[index]?.name !== 'audio_in' || isConfiguringGraph()) return;
                delete node.properties.eclipseAudioSource;
                applySrc();
                // Link maps can commit after this callback returns.
                queueMicrotask(applySrc);
            };

            // Upload button
            const fileInput = document.createElement('input');
            fileInput.type = 'file';
            fileInput.accept = 'audio/*,video/*';
            fileInput.style.display = 'none';
            fileInput.addEventListener('change', async () => {
                const f = fileInput.files?.[0];
                if (!f) return;
                try {
                    const rel = await uploadFile(f);
                    if (!audioW.options.values.includes(rel)) audioW.options.values.push(rel);
                    audioW.value = rel;
                    audioW.callback?.(rel);
                } catch (e) {
                    console.error('[Eclipse LoadAudio] upload failed:', e);
                } finally {
                    fileInput.value = '';
                }
            });
            document.body.appendChild(fileInput);
            const btn = node.addWidget('button', 'choose audio file', '', () => fileInput.click(), { serialize: false });
            btn.label = 'choose audio file to upload';

            // Move the upload button to sit directly above the audio player widget
            const widgets = node.widgets;
            const btnIdx = widgets.indexOf(btn);
            if (btnIdx >= 0) widgets.splice(btnIdx, 1);
            const uiIdx = widgets.findIndex((w) => w.name === 'audioUI');
            widgets.splice(uiIdx >= 0 ? uiIdx : widgets.length, 0, btn);

            visibility = createWidgetVisibilityManager(node);
            visibility.hideInitially(['audio', 'choose audio file']);
            const refreshRoute = () => {
                if (node.id !== -1 && audioInputRoute(node).key !== observedRoute) applySrc();
            };
            const originalAdded = node.onAdded;
            node.onAdded = function () {
                const result = originalAdded?.apply(this, arguments);
                if (!isConfiguringGraph()) applySrc();
                stopWatching ??= watchSource(refreshRoute);
                return result;
            };
            if (!isConfiguringGraph()) applySrc();

            // Cleanup
            const origRemoved = node.onRemoved;
            node.onRemoved = function () {
                disposed = true;
                stopWatching?.();
                readoutEvents.forEach((eventName) => {
                    el.removeEventListener(eventName, refreshPlaybackUI);
                });
                seekSlider.removeEventListener('input', handleSeekInput);
                seekSlider.removeEventListener('change', handleSeekInput);
                el.removeEventListener('emptied', resetPlaybackUI);
                el.removeEventListener('ended', handleEnded);
                el.removeEventListener('error', handlePlaybackError);
                clearPendingStartPlay();
                try { el.pause(); el.removeAttribute('src'); el.load(); } catch (_) {}
                try { fileInput.remove(); } catch (_) {}
                origRemoved?.apply(this, arguments);
            };

            // Drag-and-drop support
            node.previewMediaType = 'audio';

            return r;
        };
    },
});
