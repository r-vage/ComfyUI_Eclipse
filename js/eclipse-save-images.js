import {
    app
} from './comfy/index.js';
import {
    createWidgetVisibilityManager,
} from './eclipse-widget-performance-utils.js';
import {
    injectComboChipCSS,
    createComboChipWidget as _createComboChipWidget
} from './eclipse-combo-chip.js';
const NODE_NAME = 'Save Images [Eclipse]';
const FEATURE_OPTIONS = [
    { label: 'save', tooltip: 'Save images to disk (off = preview only)' },
    { label: 'optimize', tooltip: 'Optimize image (PNG: smaller files; JPEG/WebP: progressive)' },
    { label: 'lossless_webp', tooltip: 'WebP only — use lossless encoding' },
    { label: 'embed_workflow', tooltip: 'Embed the ComfyUI workflow JSON in the image metadata' },
    { label: 'save_gen_data', tooltip: 'Save generation parameters (model, sampler, seed, prompts) to image metadata' },
    { label: 'remove_prompts', tooltip: 'Strip prompts from saved metadata (privacy)' },
    { label: 'save_json', tooltip: 'Save the workflow as a separate .json file alongside the image' },
    { label: 'loras_to_prompt', tooltip: 'Append used LoRA names + weights to the prompt metadata' },
    { label: 'show_previews', tooltip: 'Show preview images in the node body' },
    { label: 'quality', tooltip: 'Show the quality slider (JPEG/WebP)' },
    { label: 'dpi', tooltip: 'Show the DPI input (image metadata)' },
    { label: 'output', tooltip: 'Show the output folder selector' },
    { label: 'filename', tooltip: 'Show the filename pattern input' },
];
const DEFAULT_FEATURES = ['save', 'embed_workflow', 'save_gen_data', 'output', 'filename', ];
const CHIP_TO_BACKING = {
    'save': 'save_to_disk',
    'optimize': 'optimize_image',
    'lossless_webp': 'lossless_webp',
    'embed_workflow': 'embed_workflow',
    'save_gen_data': 'save_generation_data',
    'remove_prompts': 'remove_prompts',
    'save_json': 'save_workflow_as_json',
    'loras_to_prompt': 'add_loras_to_prompt',
    'show_previews': 'show_previews',
    'quality': 'use_quality',
    'dpi': 'use_dpi',
    'output': 'use_output',
    'filename': 'use_filename',
};
const BACKING_WIDGETS = Object.values(CHIP_TO_BACKING);
const VISIBILITY_MAP = {
    'output': ['output_path'],
    'filename': ['filename_prefix', 'filename_delimiter', 'filename_number_padding', 'filename_number_start', 'extension'],
    'quality': ['quality'],
    'dpi': ['dpi'],
};
injectComboChipCSS('si');

function syncChipsToBacking(selectedSet, node) {
    for (const [chip, backing] of Object.entries(CHIP_TO_BACKING)) {
        const w = node.widgets?.find((w) => w.name === backing);
        if (w && w.value !== selectedSet.has(chip)) w.value = selectedSet.has(chip);
    }
}

function readChipsFromBacking(node) {
    const chips = new Set();
    for (const [chip, backing] of Object.entries(CHIP_TO_BACKING)) {
        const w = node.widgets?.find((w) => w.name === backing);
        if (w && w.value) chips.add(chip);
    }
    return chips;
}

function createComboChipWidget(node, initialSet, origIdx) {
    return _createComboChipWidget({
        node,
        options: FEATURE_OPTIONS,
        savedValue: initialSet,
        origIdx,
        widgetName: '_si_features',
        cssPrefix: 'si',
        serialize: false,
    });
}

function updateVisibility(node, vis) {
    const featW = node.widgets?.find((w) => w.name === '_si_features');
    const selected = featW ? new Set(featW.value) : readChipsFromBacking(node);
    for (const name of BACKING_WIDGETS) vis.setVisible(name, false);
    for (const [chip, widgetNames] of Object.entries(VISIBILITY_MAP)) {
        const isActive = selected.has(chip);
        for (const wName of widgetNames) {
            vis.setVisible(wName, isActive);
        }
    }
    vis.setVisible('_eclipse_dom_preview', selected.has('show_previews'));
}
app.registerExtension({
    name: 'Eclipse.SaveImagesV2',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;
        // Belt-and-suspenders: coerce any array widget value to string before LiteGraph draws it.
        // Old workflows (pre-chips) stored `features` as an array; ComboWidget.drawWidget calls
        // .substring() on the value and throws if it's not a string.
        const origDrawWidgets = nodeType.prototype.drawWidgets ?? null;
        nodeType.prototype.drawWidgets = function (...args) {
            if (this.widgets) {
                for (const w of this.widgets) {
                    if (w.hidden && Array.isArray(w.value)) {
                        w.value = w.value.join(',');
                    }
                }
            }
            return origDrawWidgets ? origDrawWidgets.apply(this, args) : undefined;
        };
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : void 0;
            const node = this;
            const vis = createWidgetVisibilityManager(node);
            node._Eclipse_vis = vis;
            // Pre-hide conditional widgets hidden at DEFAULT_FEATURES
            // (['save','embed_workflow','save_gen_data','output','filename']):
            // all BACKING_WIDGETS are always hidden; quality+dpi chips are
            // inactive by default.
            vis.hideInitially([...BACKING_WIDGETS, 'quality', 'dpi']);
            const initialSet = readChipsFromBacking(node);
            const hasAnyBacking = BACKING_WIDGETS.some((name) => {
                const w = node.widgets?.find((w) => w.name === name);
                return w && w.value === true;
            });
            const chipSet = hasAnyBacking ? [...initialSet] : DEFAULT_FEATURES.slice();
            const autoFeaturesW = node.widgets?.find(w => w.name === 'features');
            let featWidget;
            const origIdx = autoFeaturesW ? node.widgets.indexOf(autoFeaturesW) : 0;
            if (autoFeaturesW) {
                autoFeaturesW.hidden = true;
                if (autoFeaturesW.options) autoFeaturesW.options.hidden = true;
                // Prevent crash when old workflow loads array value into this ComboWidget
                // (workflows saved before chips migration stored features as array)
                autoFeaturesW.drawWidget = () => {};
            }
            featWidget = createComboChipWidget(node, chipSet, origIdx);
            featWidget.callback = () => {
                const selected = new Set(featWidget.value);
                if ((selected.has('output') || selected.has('filename')) && !selected.has('save')) {
                    selected.add('save');
                    featWidget.value = [...selected];
                }
                syncChipsToBacking(selected, node);
                if (autoFeaturesW) autoFeaturesW.value = [...selected].join(',');
                vis.markUserDriven();
                updateVisibility(node, vis);
            };
            syncChipsToBacking(new Set(chipSet), node);
            updateVisibility(node, vis);
            const origConfigure = node.onConfigure;
            node.onConfigure = function (data) {
                origConfigure?.apply(this, arguments);
                // Coerce old array value to string (backward compat with pre-chips workflows)
                if (autoFeaturesW && Array.isArray(autoFeaturesW.value)) {
                    autoFeaturesW.value = autoFeaturesW.value.join(',');
                }
                vis.clearCache?.();
                const chips = readChipsFromBacking(node);
                featWidget.value = [...chips];
                updateVisibility(node, vis);
            };
            return ret;
        };
    },
});
