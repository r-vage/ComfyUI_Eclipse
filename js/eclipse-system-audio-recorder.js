/**
 * Eclipse — manually controlled system-output audio recorder.
 */

import { app, api } from './comfy/index.js';
import {
    batchedRefreshVueWidgetOptions,
    createWidgetVisibilityManager,
    isConfiguringGraph,
    notifyVue,
    smartResize,
} from './eclipse-widget-performance-utils.js';

const NODE_NAME = 'System Audio Recorder [Eclipse]';
const ENDPOINT = '/eclipse/system_audio';
const activeSessions = new Map();
const promptTokens = new Map();
const earlyTerminalPromptIds = new Set();
const CAPTURE_STATES = new Set(['starting', 'recording', 'stopping', 'captured']);
const RETAINED_STATES = new Set(['ready', 'processing_error']);

function unregisterSession(token, owner = null) {
    const session = activeSessions.get(token);
    if (owner && session?.node !== owner) return;
    if (session?.promptId) promptTokens.delete(session.promptId);
    activeSessions.delete(token);
}

function formatElapsed(value) {
    const seconds = Math.max(0, Number(value) || 0);
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainder = (seconds % 60).toFixed(1).padStart(4, '0');
    return hours > 0
        ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${remainder}`
        : `${String(minutes).padStart(2, '0')}:${remainder}`;
}

async function requestJson(path, body) {
    const response = await api.fetchApi(`${ENDPOINT}/${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    let result = {};
    try {
        result = await response.json();
    } catch (_) {}
    if (!response.ok || result.success === false) {
        throw new Error(result.error || `Recorder request failed (${response.status}).`);
    }
    return result;
}

function createStatusElement() {
    const root = document.createElement('div');
    root.className = 'eclipse-system-audio-status';
    const state = document.createElement('div');
    const elapsed = document.createElement('div');
    const error = document.createElement('div');
    state.className = 'eclipse-system-audio-state';
    elapsed.className = 'eclipse-system-audio-elapsed';
    error.className = 'eclipse-system-audio-error';
    root.append(state, elapsed, error);
    return { root, state, elapsed, error };
}

function injectStyles() {
    if (document.getElementById('eclipse-system-audio-recorder-style')) return;
    const style = document.createElement('style');
    style.id = 'eclipse-system-audio-recorder-style';
    style.textContent = `
.eclipse-system-audio-status{box-sizing:border-box;min-height:62px;padding:7px 9px;border:1px solid #3a3a3a;border-radius:5px;background:#181818;font:12px sans-serif;color:#ddd}
.eclipse-system-audio-state{font-weight:600}
.eclipse-system-audio-elapsed{margin-top:4px;color:#aaa;font-variant-numeric:tabular-nums}
.eclipse-system-audio-error{margin-top:4px;color:#ff8a80;white-space:normal;overflow-wrap:anywhere}
`;
    document.head.appendChild(style);
}

app.registerExtension({
    name: 'Eclipse.SystemAudioRecorder',
    async setup() {
        const originalQueuePrompt = api.queuePrompt;
        api.queuePrompt = async function (number, promptData, options) {
            const response = await originalQueuePrompt.apply(this, arguments);
            const output = promptData?.output || {};
            for (const nodeData of Object.values(output)) {
                if (nodeData?.class_type !== NODE_NAME) continue;
                const token = String(nodeData.inputs?.recording_token || '');
                const session = activeSessions.get(token);
                if (!session || response?.prompt_id == null) continue;
                if (session.promptId) promptTokens.delete(session.promptId);
                session.promptId = String(response.prompt_id);
                promptTokens.set(session.promptId, token);
                if (earlyTerminalPromptIds.delete(session.promptId)
                    && CAPTURE_STATES.has(session.state)) {
                    void session.abort('Workflow execution failed before recording completed.');
                }
            }
            return response;
        };
        const handleExecutionFailure = ({ detail } = {}) => {
            const promptId = detail?.prompt_id == null ? '' : String(detail.prompt_id);
            if (!promptId) return;
            const token = promptTokens.get(promptId);
            const session = token ? activeSessions.get(token) : null;
            if (session) {
                if (CAPTURE_STATES.has(session.state)) {
                    void session.abort('Workflow execution was interrupted.');
                }
                return;
            }
            earlyTerminalPromptIds.add(promptId);
            while (earlyTerminalPromptIds.size > 32) {
                earlyTerminalPromptIds.delete(earlyTerminalPromptIds.values().next().value);
            }
        };
        api.addEventListener('execution_error', handleExecutionFailure);
        api.addEventListener('execution_interrupted', handleExecutionFailure);
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = originalCreated?.apply(this, arguments);
            const node = this;
            injectStyles();

            const filenameWidget = node.widgets?.find(widget => widget.name === 'filename_prefix');
            const formatWidget = node.widgets?.find(widget => widget.name === 'format');
            const bitrateWidget = node.widgets?.find(widget => widget.name === 'mp3_bitrate');
            const deviceWidget = node.widgets?.find(widget => widget.name === 'output_device');
            const stripWidget = node.widgets?.find(widget => widget.name === 'strip_start_silence');
            const thresholdWidget = node.widgets?.find(widget => widget.name === 'silence_threshold_db');
            const tokenWidget = node.widgets?.find(widget => widget.name === 'recording_token');
            if (!filenameWidget || !formatWidget || !bitrateWidget || !deviceWidget
                || !stripWidget || !thresholdWidget || !tokenWidget) {
                return result;
            }

            const visibility = createWidgetVisibilityManager(node);
            visibility.hideInitially(['recording_token']);
            const status = createStatusElement();
            node.addDOMWidget('recording_status', 'system-audio-status', status.root, {
                serialize: false,
                hideOnZoom: false,
                computeSize: width => [width, 70],
            });

            let pollTimer = null;
            let busy = false;
            let recorderState = tokenWidget.value ? 'restoring' : 'idle';
            let deviceIds = new Map([['Default', 'default']]);
            const setStatus = (state, elapsed = 0, error = '') => {
                status.state.textContent = `State: ${state}`;
                status.elapsed.textContent = `Elapsed: ${formatElapsed(elapsed)}`;
                status.error.textContent = error ? `Error: ${error}` : '';
            };
            setStatus('Idle');

            const processingPayload = (token = '') => ({
                node_id: String(node.id),
                ...(token ? { token } : {}),
                filename_prefix: String(filenameWidget.value || ''),
                format: String(formatWidget.value || ''),
                mp3_bitrate: String(bitrateWidget.value || ''),
                strip_start_silence: Boolean(stripWidget.value),
                silence_threshold_db: Number(thresholdWidget.value),
            });
            const refreshControlState = nextState => {
                recorderState = nextState || recorderState;
                const active = Boolean(tokenWidget.value)
                    && (CAPTURE_STATES.has(recorderState) || recorderState === 'restoring');
                const ready = Boolean(tokenWidget.value) && RETAINED_STATES.has(recorderState);
                startButton.disabled = busy || active;
                stopButton.disabled = busy || !active;
                updateButton.disabled = busy || !ready;
                refreshButton.disabled = busy || active;
                filenameWidget.disabled = active;
                formatWidget.disabled = active;
                deviceWidget.disabled = active;
                stripWidget.disabled = active;
                thresholdWidget.disabled = active || !Boolean(stripWidget.value);
                bitrateWidget.disabled = active || !String(formatWidget.value).includes('mp3');
                notifyVue(node);
            };

            const stopPolling = () => {
                if (pollTimer !== null) clearInterval(pollTimer);
                pollTimer = null;
            };
            const applyRemoteStatus = remote => {
                const label = remote.state ? remote.state[0].toUpperCase() + remote.state.slice(1) : 'Idle';
                setStatus(label, remote.elapsed, remote.error || '');
                const session = activeSessions.get(String(tokenWidget.value || ''));
                if (session) session.state = remote.state;
                refreshControlState(remote.state);
                if (RETAINED_STATES.has(remote.state)) {
                    stopPolling();
                } else if (['aborted', 'error', 'released'].includes(remote.state)) {
                    stopPolling();
                    unregisterSession(String(tokenWidget.value || ''), node);
                    tokenWidget.value = '';
                    refreshControlState(remote.state);
                }
            };
            const pollStatus = async () => {
                const token = String(tokenWidget.value || '');
                if (!token) return;
                try {
                    applyRemoteStatus(await requestJson('status', {
                        node_id: String(node.id), token,
                    }));
                } catch (error) {
                    stopPolling();
                    tokenWidget.value = '';
                    setStatus('Idle', 0, error.message);
                    refreshControlState('error');
                }
            };
            const startPolling = () => {
                stopPolling();
                pollTimer = setInterval(pollStatus, 500);
                void pollStatus();
            };

            const abortActiveSession = async message => {
                const token = String(tokenWidget.value || '');
                if (!token) return;
                tokenWidget.value = '';
                unregisterSession(token, node);
                stopPolling();
                try {
                    await requestJson('abort', { node_id: String(node.id), token });
                } catch (_) {}
                setStatus('Idle', 0, message || 'Recording aborted.');
                refreshControlState('aborted');
            };

            const refreshDevices = async () => {
                const response = await api.fetchApi(`${ENDPOINT}/devices`, { cache: 'no-store' });
                const result = await response.json();
                if (!response.ok || result.success === false) {
                    throw new Error(result.error || 'Could not refresh output devices.');
                }
                const labels = [];
                const nextIds = new Map();
                for (const device of result.devices || []) {
                    let label = device.id === 'default' ? 'Default' : String(device.name || 'Output device');
                    if (nextIds.has(label)) label = `${label} (${String(device.id).slice(0, 6)})`;
                    labels.push(label);
                    nextIds.set(label, String(device.id));
                }
                if (!labels.length) throw new Error('No system output devices were detected.');
                const previous = String(deviceWidget.value || 'Default');
                deviceIds = nextIds;
                deviceWidget.options.values = labels;
                deviceWidget.value = nextIds.has(previous) ? previous : 'Default';
                batchedRefreshVueWidgetOptions(node);
                notifyVue(node);
            };

            const refreshButton = node.addWidget('button', 'Refresh Devices', '', async () => {
                const previousState = recorderState;
                try {
                    busy = true;
                    refreshControlState(previousState);
                    await refreshDevices();
                } catch (error) {
                    setStatus(previousState === 'ready' ? 'Ready' : 'Idle', 0, error.message);
                } finally {
                    busy = false;
                    refreshControlState(previousState);
                }
            }, { serialize: false });

            const startButton = node.addWidget('button', 'Start', '', async () => {
                if (busy || CAPTURE_STATES.has(recorderState)) return;
                busy = true;
                setStatus('Starting');
                refreshControlState('starting');
                let token = '';
                try {
                    const replacedToken = String(tokenWidget.value || '');
                    if (replacedToken) {
                        await requestJson('release', {
                            node_id: String(node.id), token: replacedToken,
                        });
                        unregisterSession(replacedToken, node);
                        tokenWidget.value = '';
                    }
                    const remote = await requestJson('start', {
                        ...processingPayload(),
                        output_device: deviceIds.get(String(deviceWidget.value)) || 'default',
                    });
                    token = String(remote.token || '');
                    if (!token) throw new Error('Recorder start did not return a session token.');
                    unregisterSession(String(tokenWidget.value || ''), node);
                    tokenWidget.value = token;
                    activeSessions.set(token, {
                        abort: abortActiveSession,
                        node,
                        promptId: '',
                        state: remote.state,
                    });
                    applyRemoteStatus(remote);
                    startPolling();
                    const submitted = await app.queuePrompt(0);
                    if (!submitted) throw new Error('ComfyUI did not accept the workflow submission.');
                } catch (error) {
                    stopPolling();
                    if (token) {
                        try {
                            await requestJson('abort', { node_id: String(node.id), token });
                        } catch (_) {}
                        unregisterSession(token, node);
                        tokenWidget.value = '';
                    }
                    const retained = Boolean(tokenWidget.value);
                    recorderState = retained ? 'ready' : 'idle';
                    setStatus(retained ? 'Ready' : 'Idle', 0, error.message);
                } finally {
                    busy = false;
                    refreshControlState(recorderState);
                }
            }, { serialize: false });

            const stopButton = node.addWidget('button', 'Stop', '', async () => {
                const token = String(tokenWidget.value || '');
                if (busy || !token) return;
                busy = true;
                setStatus('Finalizing');
                refreshControlState('stopping');
                try {
                    applyRemoteStatus(await requestJson('stop', processingPayload(token)));
                } catch (error) {
                    setStatus('Error', 0, error.message);
                    stopPolling();
                    const session = activeSessions.get(token);
                    if (session) session.state = 'processing_error';
                    recorderState = 'processing_error';
                } finally {
                    busy = false;
                    refreshControlState(tokenWidget.value ? recorderState : 'error');
                }
            }, { serialize: false });

            const updateButton = node.addWidget('button', 'Update Output', '', async () => {
                const token = String(tokenWidget.value || '');
                if (busy || !token || !RETAINED_STATES.has(recorderState)) return;
                busy = true;
                setStatus('Updating output');
                refreshControlState('ready');
                try {
                    const remote = await requestJson('reprocess', processingPayload(token));
                    applyRemoteStatus(remote);
                    const submitted = await app.queuePrompt(0);
                    if (!submitted) throw new Error('ComfyUI did not accept the workflow submission.');
                } catch (error) {
                    setStatus('Ready', 0, error.message);
                } finally {
                    busy = false;
                    refreshControlState('ready');
                }
            }, { serialize: false });

            const updateFormatState = () => {
                if (node.id === -1) return;
                visibility.setVisible('recording_token', false);
                refreshControlState(recorderState);
                smartResize(node);
            };
            const originalFormatCallback = formatWidget.callback;
            formatWidget.callback = function () {
                const callbackResult = originalFormatCallback?.apply(this, arguments);
                updateFormatState();
                return callbackResult;
            };
            const originalStripCallback = stripWidget.callback;
            stripWidget.callback = function () {
                const callbackResult = originalStripCallback?.apply(this, arguments);
                updateFormatState();
                return callbackResult;
            };

            const restoreStatus = async () => {
                if (tokenWidget.value) {
                    activeSessions.set(
                        String(tokenWidget.value),
                        { abort: abortActiveSession, node, promptId: '', state: 'restoring' },
                    );
                    startPolling();
                }
                try {
                    await refreshDevices();
                } catch (error) {
                    setStatus('Idle', 0, error.message);
                }
                updateFormatState();
                refreshControlState(tokenWidget.value ? recorderState : 'idle');
            };
            const claimRetainedSession = () => {
                const token = String(tokenWidget.value || '');
                if (!token) return;
                activeSessions.set(token, {
                    abort: abortActiveSession,
                    node,
                    promptId: '',
                    state: 'restoring',
                });
            };

            if (!node._Eclipse_systemAudioInitialized && !isConfiguringGraph()) {
                node._Eclipse_systemAudioInitialized = true;
                requestAnimationFrame(() => void restoreStatus());
            }
            const originalConfigure = node.onConfigure;
            node.onConfigure = function () {
                originalConfigure?.apply(this, arguments);
                claimRetainedSession();
                requestAnimationFrame(() => void restoreStatus());
            };

            const originalRemoved = node.onRemoved;
            node.onRemoved = function () {
                stopPolling();
                const token = String(tokenWidget.value || '');
                tokenWidget.value = '';
                unregisterSession(token, node);
                if (token) {
                    requestAnimationFrame(() => requestAnimationFrame(() => {
                        if (activeSessions.has(token)) return;
                        void requestJson('release', {
                            node_id: String(node.id), token,
                        }).catch(() => {});
                    }));
                }
                originalRemoved?.apply(this, arguments);
            };

            stopButton.disabled = true;
            updateButton.disabled = true;
            updateFormatState();
            smartResize(node);
            return result;
        };
    },
});
