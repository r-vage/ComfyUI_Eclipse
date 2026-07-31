const BOOLEAN_SETTING = (value) => typeof value === 'boolean';

function normalizeFullDetailZoom(value) {
    const zoom = Number(value);
    return Number.isFinite(zoom)
        ? Math.min(100, Math.max(10, zoom))
        : 95;
}

export const VUE_NODE_SETTING_DEFINITIONS = Object.freeze({
    compactCollapsedNodes: Object.freeze({
        id: 'Comfy.VueNodes.CompactCollapsedNodes',
        legacyId: 'Eclipse.VueSizeFix',
        serverKey: 'vue_size_fix',
        category: ['Comfy', 'Nodes 2.0', 'CompactCollapsedNodes'],
        name: 'Compact collapsed nodes',
        type: 'boolean',
        tooltip: 'Size collapsed nodes to their titles and hide header badges while collapsed.',
        defaultValue: false,
        sortOrder: 85,
        experimental: true,
        versionAdded: '1.49.0',
        isValid: BOOLEAN_SETTING,
        normalize: (value) => value,
    }),
    hideStatusBadges: Object.freeze({
        id: 'Comfy.VueNodes.HideStatusBadges',
        legacyId: 'Eclipse.HideNodeStateBadges',
        serverKey: 'hide_node_state_badges',
        category: ['Comfy', 'Nodes 2.0', 'HideStatusBadges'],
        name: 'Hide node status badges',
        type: 'boolean',
        tooltip: 'Hide Muted and Bypassed badges on Nodes 2.0.',
        defaultValue: false,
        sortOrder: 80,
        experimental: true,
        versionAdded: '1.49.0',
        isValid: BOOLEAN_SETTING,
        normalize: (value) => value,
    }),
    lowZoomLOD: Object.freeze({
        id: 'Comfy.VueNodes.LowZoomLOD',
        legacyId: 'Eclipse.VueLowZoomLOD',
        serverKey: 'vue_low_zoom_lod',
        category: ['Comfy', 'Nodes 2.0', 'LowZoomLOD'],
        name: 'Low-zoom level of detail',
        type: 'boolean',
        tooltip: 'Hide expensive node details below the full-detail zoom threshold while preserving node shells, titles, sockets, links, and layout.',
        defaultValue: true,
        sortOrder: 75,
        experimental: true,
        versionAdded: '1.49.0',
        isValid: BOOLEAN_SETTING,
        normalize: (value) => value,
    }),
    fullDetailZoom: Object.freeze({
        id: 'Comfy.VueNodes.FullDetailZoom',
        legacyId: 'Eclipse.VueFullDetailZoom',
        serverKey: 'vue_full_detail_zoom',
        category: ['Comfy', 'Nodes 2.0', 'FullDetailZoom'],
        name: 'Full-detail zoom',
        type: 'slider',
        tooltip: 'Nodes use full detail at and above this zoom percentage. Low detail is used only below it.',
        attrs: Object.freeze({
            min: 10,
            max: 100,
            step: 5,
        }),
        defaultValue: 95,
        sortOrder: 70,
        experimental: true,
        versionAdded: '1.49.0',
        isValid: (value) => typeof value === 'number' && Number.isFinite(value),
        normalize: normalizeFullDetailZoom,
    }),
});

let legacyConfigPromise;

function hasOwn(object, key) {
    return Object.prototype.hasOwnProperty.call(object, key);
}

export function hasNativeVueNodeSetting(appRef, definition) {
    const settings = appRef.ui?.settings;
    return Boolean(
        settings?.settingsLookup?.[definition.id] ??
        settings?.settingsParamLookup?.[definition.id]
    );
}

export function loadLegacyVueNodeConfig() {
    if (legacyConfigPromise) return legacyConfigPromise;
    legacyConfigPromise = (async () => {
        try {
            const response = await fetch('/eclipse/config/all');
            if (response.ok) return await response.json();
        } catch (error) {
            console.error('[Eclipse] Failed to load legacy Vue node settings:', error);
        }
        return {};
    })();
    return legacyConfigPromise;
}

export function resolveCanonicalVueNodeSetting(
    appRef,
    definition,
    legacyConfig
) {
    const persisted = appRef.ui?.settings?.settingsValues || {};
    if (hasOwn(persisted, definition.id) &&
        definition.isValid(persisted[definition.id])) {
        return {
            value: definition.normalize(persisted[definition.id]),
            shouldPersist: false,
        };
    }
    if (hasOwn(persisted, definition.legacyId) &&
        definition.isValid(persisted[definition.legacyId])) {
        return {
            value: definition.normalize(persisted[definition.legacyId]),
            shouldPersist: true,
        };
    }
    const serverValue = legacyConfig?.[definition.serverKey];
    if (definition.isValid(serverValue)) {
        return {
            value: definition.normalize(serverValue),
            shouldPersist: true,
        };
    }
    return {
        value: definition.defaultValue,
        shouldPersist: false,
    };
}

export async function persistCanonicalVueNodeSetting(
    appRef,
    definition,
    resolved
) {
    if (!resolved.shouldPersist) return;
    const settings = appRef.ui?.settings;
    if (typeof settings?.setSettingValueAsync === 'function') {
        await settings.setSettingValueAsync(definition.id, resolved.value);
    } else {
        await settings?.setSettingValue?.(definition.id, resolved.value);
    }
}

export function createCanonicalVueNodeSetting(definition, onChange) {
    const {
        legacyId,
        serverKey,
        isValid,
        normalize,
        ...setting
    } = definition;
    return {
        ...setting,
        category: [...setting.category],
        ...(setting.attrs ? { attrs: { ...setting.attrs } } : {}),
        onChange,
    };
}
