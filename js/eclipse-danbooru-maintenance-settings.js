/** Eclipse settings for Danbooru API identity and maintenance credentials. */

import { app, api } from './comfy/index.js';

const SETTINGS_CATEGORY = ['Eclipse', 'General', 'Danbooru Maintenance'];
const CREDENTIAL_MASK = '••••••••';

function afterInitialChange(handler) {
    let initialized = false;
    return async function (value) {
        if (!initialized) {
            initialized = true;
            return;
        }
        return handler.call(this, value);
    };
}

async function updateDanbooruSetting(key, value) {
    const response = await api.fetchApi('/eclipse/config/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [key]: value }),
    });
    const result = await response.json();
    if (!response.ok || !result.success) {
        throw new Error(result.error || 'Danbooru setting update failed');
    }
}

function registerCredentialSetting({ id, key, name, configured, tooltip, sortOrder }) {
    let configuredState = configured;
    let internalDisplayUpdate = false;
    const setMaskedDisplay = (value) => {
        internalDisplayUpdate = true;
        app.ui.settings.setSettingValue?.(id, value);
        internalDisplayUpdate = false;
    };
    app.ui.settings.addSetting({
        id,
        category: [...SETTINGS_CATEGORY, key],
        name,
        type: 'text',
        tooltip,
        defaultValue: configured ? CREDENTIAL_MASK : '',
        sortOrder,
        onChange: afterInitialChange(async (value) => {
            if (internalDisplayUpdate || value === CREDENTIAL_MASK) return;
            const credential = typeof value === 'string' ? value : '';
            if (!credential && !configuredState) return;
            setMaskedDisplay(configuredState ? CREDENTIAL_MASK : '');
            try {
                await updateDanbooruSetting(key, credential);
                configuredState = Boolean(credential);
            } catch (error) {
                console.error(`[Eclipse] Failed to update ${name}:`, error);
            } finally {
                setMaskedDisplay(configuredState ? CREDENTIAL_MASK : '');
            }
        }),
    });
    setMaskedDisplay(configured ? CREDENTIAL_MASK : '');
}

app.registerExtension({
    name: 'Eclipse.DanbooruMaintenanceSettings',
    async init() {
        let config = {
            danbooru_user_id: 0,
            danbooru_login_configured: false,
            danbooru_api_key_configured: false,
        };
        try {
            const response = await api.fetchApi('/eclipse/config/all');
            if (response.ok) config = { ...config, ...(await response.json()) };
        } catch (error) {
            console.error('[Eclipse] Failed to load Danbooru credential status:', error);
        }
        app.ui.settings.addSetting({
            id: 'Eclipse.DanbooruUserId',
            category: [...SETTINGS_CATEGORY, 'danbooru_user_id'],
            name: 'Danbooru User ID',
            type: 'text',
            tooltip: 'Account ID as unformatted digits, included in the API User-Agent as required by Danbooru.',
            defaultValue: Number.isSafeInteger(config.danbooru_user_id)
                && config.danbooru_user_id > 0
                ? String(config.danbooru_user_id)
                : '',
            sortOrder: 30,
            onChange: afterInitialChange(async (value) => {
                const digits = typeof value === 'string' ? value.trim() : '';
                if (digits && !/^\d+$/.test(digits)) return;
                const userId = digits ? Number(digits) : 0;
                if (!Number.isSafeInteger(userId) || userId < 0) return;
                try {
                    await updateDanbooruSetting('danbooru_user_id', userId);
                } catch (error) {
                    console.error('[Eclipse] Failed to update Danbooru User ID:', error);
                }
            }),
        });
        registerCredentialSetting({
            id: 'Eclipse.DanbooruLogin',
            key: 'danbooru_login',
            name: 'Danbooru Login',
            configured: config.danbooru_login_configured === true,
            tooltip: 'Write-only Danbooru login. The server never returns credential bytes.',
            sortOrder: 20,
        });
        registerCredentialSetting({
            id: 'Eclipse.DanbooruApiKey',
            key: 'danbooru_api_key',
            name: 'Danbooru API Key',
            configured: config.danbooru_api_key_configured === true,
            tooltip: 'Write-only Danbooru API key. The server never returns credential bytes.',
            sortOrder: 10,
        });
    },
});
