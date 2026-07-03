import {
    app
} from './comfy/index.js';
import {
    debounce,
    smartResize,
    createWidgetVisibilityManager,
    onVueModeChange,
    isConfiguringGraph,
} from './eclipse-widget-performance-utils.js';
import {
    injectComboChipCSS,
    createComboChipWidget as _createComboChipWidget
} from './eclipse-combo-chip.js';
const NODE_NAME = 'Replace String v3 [Eclipse]';
const FEATURE_OPTIONS = [
    { label: 'instructions', tooltip: 'Strip LLM-style instruction prefixes (e.g., "Generate a prompt for...")' },
    { label: 'list_first', tooltip: 'Keep only the first item from a numbered/bulleted list' },
    { label: 'list_to_string', tooltip: 'Flatten a numbered/bulleted list into a single sentence' },
    { label: 'image_style', tooltip: 'Remove image-style descriptors (e.g., "digital illustration", "3d render")' },
    { label: 'shot_style', tooltip: 'Remove camera/shot phrases (e.g., "close-up", "low angle")' },
    { label: 'subject', tooltip: 'Remove subject descriptors (people, poses, attributes)' },
    { label: 'background', tooltip: 'Remove background/location descriptions' },
    { label: 'mood', tooltip: 'Remove mood/atmosphere phrases (preserves subject emotions)' },
    { label: 'lighting', tooltip: 'Remove lighting descriptors' },
    { label: 'age', tooltip: 'Adjust age terms (configurable via the age widget)' },
    { label: 'watermark', tooltip: 'Remove watermark/signature mentions' },
    { label: 'cleanup', tooltip: 'Final pass: normalize spaces, punctuation, and stray commas' },
];
const DEFAULT_FEATURES = [];
injectComboChipCSS('rsv3');
const FEATURE_WIDGETS = {
    age: ['age'],
};

function createComboChipWidget(node, savedValue, origIdx) {
    return _createComboChipWidget({
        node,
        options: FEATURE_OPTIONS,
        savedValue,
        origIdx,
        cssPrefix: 'rsv3'
    });
}
app.registerExtension({
    name: 'Eclipse.ReplaceStringV3',
    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== NODE_NAME) return;
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = origOnNodeCreated ? origOnNodeCreated.apply(this, arguments) : void 0;
            const node = this;
            const vis = createWidgetVisibilityManager(node);
            node._Eclipse_vis = vis;
            // Pre-hide conditional widgets — DEFAULT_FEATURES=[] so age chip is off.
            vis.hideInitially(['age']);
            const d = (name, show) => vis.setVisible(name, show);
            const autoFeaturesW = node.widgets?.find(w => w.name === 'features');
            let featWidget;
            const origIdx = autoFeaturesW ? node.widgets.indexOf(autoFeaturesW) : 0;
            let savedValue = DEFAULT_FEATURES.slice();
            if (autoFeaturesW) {
                const v = autoFeaturesW.value;
                if (typeof v === 'string' && v.trim()) {
                    savedValue = v.split(',').map(s => s.trim()).filter(Boolean);
                } else if (Array.isArray(v) && v.length > 0) {
                    savedValue = v.slice();
                }
                autoFeaturesW.onRemove?.();
                node.widgets.splice(origIdx, 1);
            }
            featWidget = createComboChipWidget(node, savedValue, 0);
            const updateVisibility = () => {
                const raw = vis.getValue('features');
                const feats = new Set(Array.isArray(raw) ? raw : []);
                d('age', feats.has('age'));
                smartResize(node);
            };
            const debouncedUpdate = debounce(updateVisibility, 100);
            const origFeatCallback = featWidget?.callback;
            if (featWidget) {
                featWidget.callback = function (value) {
                    origFeatCallback?.call(this, value);
                    if (autoFeaturesW) autoFeaturesW.value = (Array.isArray(featWidget.value) ? featWidget.value : []).join(',');
                    vis.markUserDriven();
                    updateVisibility();
                };
            }
            if (!isConfiguringGraph()) {
                updateVisibility();
            }
            const origOnConfigure = node.onConfigure;
            node.onConfigure = function (config) {
                origOnConfigure && origOnConfigure.apply(this, arguments);
                updateVisibility();
            };
            return ret;
        };
    },
    async setup() {
        onVueModeChange(() => {
            app.graph?.setDirtyCanvas?.(true, true);
        });
    },
});
