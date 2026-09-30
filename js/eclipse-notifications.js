/**
 * Native ComfyUI notifications for Eclipse user-facing failures.
 * SPDX-License-Identifier: Apache-2.0
 */
import { app } from './comfy/index.js';

const recent = new Map();

export function showEclipseToast(summary, detail, severity = 'error') {
    const message = typeof detail === 'string' ? detail : detail?.message;
    if (typeof message !== 'string' || !message) return;
    const toast = app.extensionManager?.toast;
    if (typeof toast?.add !== 'function') return;
    const text = message.length > 1200 ? `${message.slice(0, 1200)}…` : message;
    const key = JSON.stringify([severity, summary, text]);
    const now = Date.now();
    if (recent.has(key) && now - recent.get(key) < 3000) return;
    recent.delete(key);
    recent.set(key, now);
    if (recent.size > 64) recent.delete(recent.keys().next().value);
    // Notification failures must never replace the original execution failure.
    try {
        toast.add({ severity, summary, detail: text, life: severity === 'error' ? 12000 : 8000 });
    } catch {
        recent.delete(key);
    }
}
