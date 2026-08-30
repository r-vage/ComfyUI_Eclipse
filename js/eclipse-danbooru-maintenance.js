/** String-backed combo-chip selectors for Danbooru Corpus Maintenance. */

import { app, api } from './comfy/index.js';
import {
    createWidgetVisibilityManager,
    isConfiguringGraph,
    isVueMode,
    notifyVue,
    smartResize,
} from './eclipse-widget-performance-utils.js';
import {
    createComboChipWidget,
    injectComboChipCSS,
} from './eclipse-combo-chip.js';

const NODE_NAME = 'Danbooru Corpus Maintenance [Eclipse]';
const PAGE_PROGRESS_EVENT = 'eclipse/danbooru_page_progress';
const SCAN_STATE_EVENT = 'eclipse/danbooru_scan_state';
const STOP_ENDPOINT = '/eclipse/danbooru/stop';
const STOP_LABEL_IDLE = 'Stop requests → categorize';
const STOP_LABEL_REQUESTING = 'Requesting stop…';
const STOP_LABEL_PENDING = 'Stopping after current request…';
const RESET_ENDPOINT = '/eclipse/danbooru/reset-categorization';
const RESET_LABEL_IDLE = 'Reset categorized tags + backlog';
const RESET_LABEL_WORKING = 'Resetting categorization…';
const ACTION_OPTIONS = [
    {
        label: 'refresh_ratings',
        tooltip: 'Scrape Danbooru posts to build or update the selected rating pools used by Prompt Forge',
    },
    {
        label: 'refresh_catalog',
        tooltip: 'Run catalog enrichment without collecting posts; refresh_ratings already performs this phase before SmartLLM',
    },
    {
        label: 'prepare_ai',
        tooltip: 'Prepare uncategorized general tags discovered on newly accepted posts for the two SmartLLM passes',
    },
    {
        label: 'manual_categorization',
        tooltip: 'Export the complete pending backlog as provider-neutral numbered files for an external two-pass workflow',
    },
    {
        label: 'resume',
        tooltip: 'Resume each rating from its saved score band and logical request; disable to rewind from post_start_page when its checkpoint prefix exists',
    },
];
const RATING_OPTIONS = [
    { label: 'general', tooltip: 'Include general-rated posts when refreshing Prompt Forge post pools' },
    { label: 'sensitive', tooltip: 'Include sensitive-rated posts when refreshing Prompt Forge post pools' },
    { label: 'questionable', tooltip: 'Include questionable-rated posts when refreshing Prompt Forge post pools' },
    { label: 'explicit', tooltip: 'Include explicit-rated posts when refreshing Prompt Forge post pools' },
];
const MAINTENANCE_OPTIONS = [...ACTION_OPTIONS, ...RATING_OPTIONS];
const ACTION_NAMES = ACTION_OPTIONS.map(({ label }) => label);
const RATING_NAMES = RATING_OPTIONS.map(({ label }) => label);
const HIDDEN_BACKINGS = ['actions', 'ratings'];
const CUSTOM_SCORE_WIDGETS = ['custom_score_min', 'custom_score_max'];
const MAX_DANBOORU_SCORE = 5000;
const CATALOG_WIDGETS = [
    'minimum_tag_post_count',
    'tag_start_page',
    'tag_stop_page',
    'maximum_tag_pages_per_queue',
];
const RATING_REFRESH_WIDGETS = ['excluded_post_tags'];
const MANUAL_CATEGORIZATION_WIDGETS = ['maximum_ai_batches'];

injectComboChipCSS('dcm');

function decodeSelection(value, allowedNames, fallback = allowedNames) {
    const rawValues = typeof value === 'string'
        ? value.split(',').map((item) => item.trim()).filter(Boolean)
        : value;
    if (!Array.isArray(rawValues)) return fallback.slice();
    const allowed = new Set(allowedNames);
    return [...new Set(rawValues.filter((item) => allowed.has(item)))];
}

function normalizeActionSelection(value) {
    const actions = decodeSelection(value, ACTION_NAMES, []);
    const prepareIndex = actions.indexOf('prepare_ai');
    const manualIndex = actions.indexOf('manual_categorization');
    if (prepareIndex === -1 || manualIndex === -1) return actions;
    const retained = prepareIndex > manualIndex ? 'prepare_ai' : 'manual_categorization';
    return actions.filter(
        (name) => !['prepare_ai', 'manual_categorization'].includes(name)
            || name === retained,
    );
}

function readCombinedSelection(actionsWidget, ratingsWidget) {
    return [
        ...normalizeActionSelection(actionsWidget?.value),
        ...decodeSelection(ratingsWidget?.value, RATING_NAMES),
    ];
}

function syncBackingSelection(chipWidget, actionsWidget, ratingsWidget) {
    const selectedValues = decodeSelection(
        chipWidget.value,
        [...ACTION_NAMES, ...RATING_NAMES],
        [],
    );
    const actions = normalizeActionSelection(selectedValues);
    const selected = new Set(selectedValues);
    const ratings = RATING_NAMES.filter((name) => selected.has(name));
    const normalized = [...actions, ...ratings];
    if (
        normalized.length !== selectedValues.length
        || normalized.some((name, index) => name !== selectedValues[index])
    ) {
        chipWidget.value = normalized;
    }
    actionsWidget.value = actions.join(',');
    ratingsWidget.value = RATING_NAMES.filter((name) => selected.has(name)).join(',');
}

function findGraphNode(nodeId) {
    const numericId = Number(nodeId);
    const normalizedId = Number.isFinite(numericId) ? numericId : nodeId;
    return app.graph?.getNodeById?.(normalizedId)
        ?? app.graph?._nodes?.find(
            (candidate) => String(candidate.id) === String(nodeId),
        );
}

function updateStopButton(node, label, disabled) {
    const widget = node?._Eclipse_danbooruStopWidget;
    if (!widget) return;
    widget.label = label;
    widget.disabled = disabled;
    if (isVueMode()) notifyVue(node);
    node.setDirtyCanvas?.(true, true);
}

function updateResetButton(node, label, disabled) {
    const widget = node?._Eclipse_danbooruResetWidget;
    if (!widget) return;
    widget.label = label;
    widget.disabled = disabled;
    if (isVueMode()) notifyVue(node);
    node.setDirtyCanvas?.(true, true);
}

app.registerExtension({
    name: 'Eclipse.DanbooruMaintenanceNode',
    async setup() {
        api.addEventListener(PAGE_PROGRESS_EVENT, ({ detail }) => {
            if (!detail) return;
            const node = findGraphNode(detail.node_id);
            if (!node || node.type !== NODE_NAME) return;

            const pageWidget = node.widgets?.find(
                (widget) => widget.name === (
                    detail.page_input ?? 'post_start_page'
                ),
            );
            let changed = false;

            const nextPage = Number(detail.next_page);
            if (
                pageWidget
                && Number.isInteger(nextPage)
                && nextPage >= 1
                && nextPage <= (pageWidget.options?.max ?? 1000000000)
            ) {
                pageWidget.value = nextPage;
                changed = true;
            }
            if (changed && isVueMode()) notifyVue(node);
            if (changed) node.setDirtyCanvas?.(true, true);
        });
        api.addEventListener(SCAN_STATE_EVENT, ({ detail }) => {
            if (!detail || detail.active !== false) return;
            const node = findGraphNode(detail.node_id);
            if (node?.type !== NODE_NAME) return;
            updateStopButton(node, STOP_LABEL_IDLE, false);
        });
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = originalOnNodeCreated?.apply(this, arguments);
            const node = this;
            const visibility = createWidgetVisibilityManager(node);
            const actionsWidget = node.widgets?.find(
                (widget) => widget.name === 'actions',
            );
            const ratingsWidget = node.widgets?.find(
                (widget) => widget.name === 'ratings',
            );
            const scoreRangeModeWidget = node.widgets?.find(
                (widget) => widget.name === 'score_range_mode',
            );
            const customScoreMinWidget = node.widgets?.find(
                (widget) => widget.name === 'custom_score_min',
            );
            const customScoreMaxWidget = node.widgets?.find(
                (widget) => widget.name === 'custom_score_max',
            );
            if (!actionsWidget || !ratingsWidget) return result;

            visibility.hideInitially(HIDDEN_BACKINGS);
            visibility.hideInitially(CUSTOM_SCORE_WIDGETS);
            visibility.hideInitially(CATALOG_WIDGETS);
            visibility.hideInitially(RATING_REFRESH_WIDGETS);
            visibility.hideInitially(MANUAL_CATEGORIZATION_WIDGETS);
            for (const name of HIDDEN_BACKINGS) visibility.setVisible(name, false);

            const updateScoreRangeVisibility = (resize = true) => {
                const visible = scoreRangeModeWidget?.value === 'custom';
                for (const name of CUSTOM_SCORE_WIDGETS) {
                    visibility.setVisible(name, visible);
                }
                if (isVueMode()) notifyVue(node);
                if (resize) smartResize(node);
            };

            const updateCatalogVisibility = (resize = true) => {
                const visible = decodeSelection(
                    actionsWidget.value,
                    ACTION_NAMES,
                    [],
                ).some((name) => ['refresh_ratings', 'refresh_catalog'].includes(name));
                for (const name of CATALOG_WIDGETS) {
                    visibility.setVisible(name, visible);
                }
                if (isVueMode()) notifyVue(node);
                if (resize) smartResize(node);
            };

            const updateRatingRefreshVisibility = (resize = true) => {
                const visible = decodeSelection(
                    actionsWidget.value,
                    ACTION_NAMES,
                    [],
                ).includes('refresh_ratings');
                for (const name of RATING_REFRESH_WIDGETS) {
                    visibility.setVisible(name, visible);
                }
                if (isVueMode()) notifyVue(node);
                if (resize) smartResize(node);
            };

            const updateManualCategorizationVisibility = (resize = true) => {
                const manual = normalizeActionSelection(
                    actionsWidget.value,
                ).includes('manual_categorization');
                for (const name of MANUAL_CATEGORIZATION_WIDGETS) {
                    visibility.setVisible(name, !manual);
                }
                if (isVueMode()) notifyVue(node);
                if (resize) smartResize(node);
            };

            const originalIndex = Math.min(
                node.widgets.indexOf(actionsWidget),
                node.widgets.indexOf(ratingsWidget),
            );
            const chipWidget = createComboChipWidget({
                node,
                options: MAINTENANCE_OPTIONS,
                savedValue: readCombinedSelection(actionsWidget, ratingsWidget),
                origIdx: originalIndex,
                widgetName: '_dcm_maintenance',
                cssPrefix: 'dcm',
                radioGroups: [['prepare_ai', 'manual_categorization']],
                radioToggle: true,
                serialize: false,
            });
            node._Eclipse_danbooruMaintenanceWidget = chipWidget;
            chipWidget.callback = function () {
                syncBackingSelection(chipWidget, actionsWidget, ratingsWidget);
                updateCatalogVisibility(false);
                updateRatingRefreshVisibility(false);
                updateManualCategorizationVisibility();
            };
            syncBackingSelection(chipWidget, actionsWidget, ratingsWidget);

            const stopWidget = node.addWidget(
                'button',
                STOP_LABEL_IDLE,
                null,
                async () => {
                    if (stopWidget.disabled) return;
                    updateStopButton(node, STOP_LABEL_REQUESTING, true);
                    try {
                        const response = await api.fetchApi(STOP_ENDPOINT, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ node_id: String(node.id) }),
                        });
                        if (!response.ok) throw new Error('stop request failed');
                        const payload = await response.json();
                        if (payload.active) {
                            updateStopButton(node, STOP_LABEL_PENDING, true);
                        } else {
                            updateStopButton(node, 'No active post request', true);
                            setTimeout(
                                () => updateStopButton(node, STOP_LABEL_IDLE, false),
                                1500,
                            );
                        }
                    } catch (_error) {
                        updateStopButton(node, 'Stop request failed', true);
                        setTimeout(
                            () => updateStopButton(node, STOP_LABEL_IDLE, false),
                            1500,
                        );
                    }
                },
                { serialize: false },
            );
            stopWidget.label = STOP_LABEL_IDLE;
            node._Eclipse_danbooruStopWidget = stopWidget;
            const stopIndex = node.widgets.indexOf(stopWidget);
            const chipIndex = node.widgets.indexOf(chipWidget);
            if (stopIndex !== chipIndex + 1) {
                node.widgets.splice(stopIndex, 1);
                node.widgets.splice(chipIndex + 1, 0, stopWidget);
            }

            const resetWidget = node.addWidget(
                'button',
                RESET_LABEL_IDLE,
                null,
                async () => {
                    if (resetWidget.disabled) return;
                    const confirmed = globalThis.confirm?.(
                        'Reset every model-assigned category and rebuild the full '
                        + 'backlog from the existing rating files? Artist, character, '
                        + 'copyright, and meta categories will be retained.',
                    );
                    if (!confirmed) return;
                    updateResetButton(node, RESET_LABEL_WORKING, true);
                    try {
                        const response = await api.fetchApi(RESET_ENDPOINT, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ confirmation: 'reset' }),
                        });
                        const payload = await response.json();
                        if (!response.ok || !payload.success) {
                            throw new Error(payload.error || 'categorization reset failed');
                        }
                        updateResetButton(
                            node,
                            `Reset complete — ${payload.pending_tags} pending`,
                            false,
                        );
                    } catch (_error) {
                        updateResetButton(node, 'Categorization reset failed', false);
                    }
                },
                { serialize: false },
            );
            resetWidget.label = RESET_LABEL_IDLE;
            node._Eclipse_danbooruResetWidget = resetWidget;

            if (scoreRangeModeWidget) {
                const originalScoreRangeCallback = scoreRangeModeWidget.callback;
                scoreRangeModeWidget.callback = function () {
                    const callbackResult = originalScoreRangeCallback?.apply(
                        this,
                        arguments,
                    );
                    updateScoreRangeVisibility();
                    return callbackResult;
                };
            }
            updateScoreRangeVisibility(false);
            updateCatalogVisibility(false);
            updateRatingRefreshVisibility(false);
            updateManualCategorizationVisibility(false);

            const originalOnConfigure = node.onConfigure;
            node.onConfigure = function () {
                const configureResult = originalOnConfigure?.apply(this, arguments);
                if (Number(customScoreMinWidget?.value) > MAX_DANBOORU_SCORE) {
                    customScoreMinWidget.value = MAX_DANBOORU_SCORE;
                }
                if (Number(customScoreMaxWidget?.value) > MAX_DANBOORU_SCORE) {
                    customScoreMaxWidget.value = MAX_DANBOORU_SCORE;
                }
                visibility.setLoadMode(true);
                for (const name of HIDDEN_BACKINGS) visibility.setVisible(name, false);
                updateScoreRangeVisibility(false);
                updateCatalogVisibility(false);
                updateRatingRefreshVisibility(false);
                updateManualCategorizationVisibility(false);
                visibility.setLoadMode(false);
                chipWidget.value = readCombinedSelection(actionsWidget, ratingsWidget);
                syncBackingSelection(chipWidget, actionsWidget, ratingsWidget);
                updateCatalogVisibility(false);
                updateRatingRefreshVisibility(false);
                updateManualCategorizationVisibility(false);
                smartResize(node);
                return configureResult;
            };
            if (!isConfiguringGraph()) smartResize(node);
            return result;
        };
    },
});
