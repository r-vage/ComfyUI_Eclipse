/**
 * Shared automatic-queue controls for Eclipse frontend extensions.
 *
 * Disarms future automatic submissions without interrupting the prompt that is
 * currently running or removing jobs that were queued manually.
 *
 * Copyright (c) 2026 r-vage. MIT License.
 */

import { app } from './comfy/index.js';

function stopModernAutomaticQueue() {
    const queueSettings = app.extensionManager?.queueSettings;
    if (queueSettings && typeof queueSettings.mode === 'string') {
        if (queueSettings.mode === 'instant-running') {
            queueSettings.mode = 'instant-idle';
            return true;
        }
        if (queueSettings.mode === 'change') {
            queueSettings.mode = 'disabled';
            return true;
        }
        return false;
    }

    if (typeof document === 'undefined') return false;
    const stopButton = document.querySelector(
        'button[data-testid="queue-button"][data-variant="destructive"]'
    );
    if (!stopButton) return false;
    stopButton.click();
    return true;
}

function stopLegacyAutomaticQueue() {
    const legacyUi = app.ui;
    const checkbox = typeof document === 'undefined'
        ? null
        : document.getElementById('autoQueueCheckbox');
    const wasEnabled = legacyUi?.autoQueueEnabled === true || checkbox?.checked === true;

    if (legacyUi && legacyUi.autoQueueEnabled !== undefined) {
        legacyUi.autoQueueEnabled = false;
    }
    if (checkbox && wasEnabled) {
        checkbox.checked = false;
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    }
    return wasEnabled;
}

export function stopAutomaticQueue() {
    const modernStopped = stopModernAutomaticQueue();
    const legacyStopped = stopLegacyAutomaticQueue();
    return modernStopped || legacyStopped;
}
