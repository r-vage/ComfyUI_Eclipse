/**
 * ComfyUI Import Hub - Eclipse Edition
 *
 * Centralizes all imports from ComfyUI core via window.comfyAPI.
 * All consumer files import from this hub — when ComfyUI updates
 * their public API, only this file needs to change.
 *
 * Last Updated: April 10, 2026
 * Source: window.comfyAPI (stable public API)
 */

// ── Stable public API ───────────────────────────────────────────────────
const { app } = window.comfyAPI.app;
const { api } = window.comfyAPI.api;
const { ComfyWidgets } = window.comfyAPI.widgets;

export { app, api, ComfyWidgets };
