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

const NODE_NAME = 'Load Audio [Eclipse]';

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
            if (!audioW) return r;

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
            const audioUI = node.addDOMWidget('audioUI', 'audio', preview, { serialize: false });
            audioUI.computeSize = function (width) { return [width, 96]; };

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

            // Build the URL for slicing from the backend if start_time or duration are set.
            // Otherwise, fall back to standard ComfyUI view endpoint for full/untrimmed tracks.
            const buildSliceURL = () => {
                const v = audioW.value;
                if (!v || v === 'none') return '';
                const s = Math.max(0, Number(startW?.value || 0));
                const d = Math.max(0, Number(durW?.value || 0));
                if (s <= 0 && d <= 0) {
                    return buildViewURL(v);
                }
                const params = new URLSearchParams({
                    filename: v,
                    start_time: s,
                    duration: d
                });
                return api.apiURL(`/eclipse/audio_slice?${params.toString()}`);
            };

            let pendingStartPlay = null;
            const clearPendingStartPlay = () => {
                if (!pendingStartPlay) return;
                el.removeEventListener('loadedmetadata', pendingStartPlay);
                pendingStartPlay = null;
            };

            const applySrc = () => {
                clearPendingStartPlay();
                const v = audioW.value;
                if (typeof v === 'string' && v && v !== 'none') {
                    const newSrc = buildSliceURL();
                    if (el.src !== newSrc) {
                        resetPlaybackUI();
                        el.src = newSrc;
                        el.load();
                    }
                } else {
                    resetPlaybackUI();
                    el.removeAttribute('src');
                    el.load();
                }
            };

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
            applySrc();

            // Restore preview after workflow load
            const origCfg = node.onGraphConfigured;
            node.onGraphConfigured = function () {
                origCfg?.apply(this, arguments);
                applySrc();
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

            // Cleanup
            const origRemoved = node.onRemoved;
            node.onRemoved = function () {
                readoutEvents.forEach((eventName) => {
                    el.removeEventListener(eventName, refreshPlaybackUI);
                });
                seekSlider.removeEventListener('input', handleSeekInput);
                seekSlider.removeEventListener('change', handleSeekInput);
                el.removeEventListener('emptied', resetPlaybackUI);
                el.removeEventListener('ended', handleEnded);
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
