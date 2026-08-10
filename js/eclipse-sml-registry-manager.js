import { app, api } from './comfy/index.js';
import { emitSmlRegistryChanged } from './eclipse-sml-registry-events.js';

const COMMAND_ID = 'Eclipse.LMRegistry.Open';
const SIDEBAR_TAB_ID = 'eclipse-lm-registry';
const CSS_ID = 'eclipse-sml-registry-manager-css';
const BACKENDS = ['transformers', 'gguf', 'llamacpp', 'ollama', 'vllm', 'vllm_native', 'sglang', 'wd14'];
const FAMILIES = ['Qwen', 'Mistral', 'Florence', 'LLaVA', 'LLM_TEXT', 'VLM', 'WD14'];
const SERVER_QUANTIZATIONS = {
    vllm: ['auto', 'fp8', 'awq', 'gptq', 'bitsandbytes'],
    vllm_native: ['auto', 'fp8', 'awq', 'gptq', 'bitsandbytes', 'squeezellm'],
    sglang: ['auto', 'fp8', 'awq', 'gptq'],
};

const CSS = `
.eclipse-smlrm-backdrop{position:fixed;inset:0;z-index:1200;display:flex;align-items:center;justify-content:center;padding:24px;background:rgba(0,0,0,.68);box-sizing:border-box}
.eclipse-smlrm-dialog{width:min(1120px,96vw);height:min(780px,92vh);display:flex;flex-direction:column;overflow:hidden;border:1px solid var(--border-color,#555);border-radius:10px;background:var(--comfy-menu-bg,#303030);color:var(--input-text,#ddd);box-shadow:0 18px 60px rgba(0,0,0,.55);font:13px sans-serif}
.eclipse-smlrm-header,.eclipse-smlrm-footer{display:flex;align-items:center;gap:8px;padding:10px 14px;border-color:var(--border-color,#555);flex:0 0 auto}
.eclipse-smlrm-header{border-bottom:1px solid}.eclipse-smlrm-header h2{flex:1;margin:0;font-size:17px}.eclipse-smlrm-footer{border-top:1px solid;flex-wrap:wrap}
.eclipse-smlrm-body{display:grid;grid-template-columns:minmax(260px,32%) 1fr;min-height:0;flex:1}
.eclipse-smlrm-sidebar{display:flex;flex-direction:column;min-height:0;border-right:1px solid var(--border-color,#555);padding:10px;gap:8px}
.eclipse-smlrm-list{overflow:auto;min-height:0;flex:1;border:1px solid var(--border-color,#555);border-radius:6px}
.eclipse-smlrm-item{display:block;width:100%;padding:8px 10px;border:0;border-bottom:1px solid var(--border-color,#444);background:transparent;color:inherit;text-align:left;cursor:pointer}
.eclipse-smlrm-item:hover,.eclipse-smlrm-item[aria-selected=true]{background:var(--comfy-input-bg,#202020)}
.eclipse-smlrm-item small{display:block;margin-top:3px;color:var(--descrip-text,#aaa)}
.eclipse-smlrm-form{overflow:auto;padding:14px;display:grid;grid-template-columns:repeat(2,minmax(190px,1fr));gap:11px 14px;align-content:start}
.eclipse-smlrm-field{display:flex;flex-direction:column;gap:4px;min-width:0}.eclipse-smlrm-field[hidden]{display:none!important}.eclipse-smlrm-field--wide{grid-column:1/-1}.eclipse-smlrm-field--check{flex-direction:row;align-items:center;padding-top:19px}
.eclipse-smlrm-field label{font-size:11px;color:var(--descrip-text,#aaa);text-transform:uppercase;letter-spacing:.03em}
.eclipse-smlrm-field input,.eclipse-smlrm-field select,.eclipse-smlrm-field textarea,.eclipse-smlrm-sidebar input{box-sizing:border-box;width:100%;padding:7px 8px;border:1px solid var(--border-color,#555);border-radius:5px;background:var(--comfy-input-bg,#222);color:var(--input-text,#ddd)}
.eclipse-smlrm-field textarea{min-height:70px;resize:vertical}.eclipse-smlrm-field--check input{width:auto}.eclipse-smlrm-field--check label{text-transform:none;font-size:13px;color:inherit}
.eclipse-smlrm-button{padding:7px 10px;border:1px solid var(--border-color,#666);border-radius:5px;background:var(--comfy-input-bg,#222);color:inherit;cursor:pointer}.eclipse-smlrm-button:hover{filter:brightness(1.18)}.eclipse-smlrm-button:disabled{opacity:.45;cursor:default}
.eclipse-smlrm-danger{border-color:#a44;color:#f1b8b8}.eclipse-smlrm-status{flex:1;min-width:220px;color:var(--descrip-text,#aaa)}.eclipse-smlrm-status[data-kind=error]{color:#ffaaaa}.eclipse-smlrm-status[data-kind=success]{color:#aee6ae}
.eclipse-smlrm-classic{width:100%;margin:6px 0;padding:7px;border:1px solid var(--border-color,#555);border-radius:7px;background:var(--comfy-input-bg,#222);color:var(--input-text,#ddd);cursor:pointer}
@media(max-width:760px){.eclipse-smlrm-body{grid-template-columns:1fr}.eclipse-smlrm-sidebar{max-height:230px;border-right:0;border-bottom:1px solid var(--border-color,#555)}.eclipse-smlrm-form{grid-template-columns:1fr}.eclipse-smlrm-field--wide{grid-column:auto}}
`;

function injectCSS() {
    if (document.getElementById(CSS_ID)) return;
    const style = document.createElement('style');
    style.id = CSS_ID;
    style.textContent = CSS;
    document.head.appendChild(style);
}

async function request(path, body = null) {
    const response = await api.fetchApi(path, body === null ? undefined : {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    let payload;
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok || payload?.success === false) {
        throw new Error(payload?.error || `Request failed (${response.status})`);
    }
    return payload;
}

function element(tag, className = '', text = '') {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
}

function field(form, id, labelText, type = 'text', options = {}) {
    const wrap = element('div', `eclipse-smlrm-field${options.wide ? ' eclipse-smlrm-field--wide' : ''}${type === 'checkbox' ? ' eclipse-smlrm-field--check' : ''}`);
    wrap.dataset.field = id;
    if (options.backends) wrap.dataset.backends = options.backends.join(',');
    const label = element('label', '', labelText);
    label.htmlFor = `eclipse-smlrm-${id}`;
    let input;
    if (type === 'select') {
        input = document.createElement('select');
        for (const value of options.values || []) {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = value;
            input.appendChild(option);
        }
    } else if (type === 'textarea') {
        input = document.createElement('textarea');
    } else {
        input = document.createElement('input');
        input.type = type;
    }
    input.id = `eclipse-smlrm-${id}`;
    input.name = id;
    input.dataset.testid = `sml-registry-${id}`;
    if (options.placeholder) input.placeholder = options.placeholder;
    if (type === 'checkbox') {
        wrap.append(input, label);
    } else {
        wrap.append(label, input);
    }
    form.appendChild(wrap);
    return input;
}

class RegistryManager {
    constructor() {
        this.models = [];
        this.selected = null;
        this.originalFocus = null;
        this.busy = false;
        this.inputs = {};
    }

    open() {
        if (this.backdrop?.isConnected) return;
        injectCSS();
        this.originalFocus = document.activeElement;
        this.build();
        document.body.appendChild(this.backdrop);
        this.filter.focus();
        this.refresh();
    }

    close() {
        this.backdrop?.remove();
        this.backdrop = null;
        if (this.originalFocus instanceof HTMLElement) this.originalFocus.focus();
    }

    build() {
        const backdrop = element('div', 'eclipse-smlrm-backdrop');
        backdrop.dataset.testid = 'sml-registry-manager';
        const dialog = element('section', 'eclipse-smlrm-dialog');
        dialog.setAttribute('role', 'dialog');
        dialog.setAttribute('aria-modal', 'true');
        dialog.setAttribute('aria-labelledby', 'eclipse-smlrm-title');
        backdrop.appendChild(dialog);
        backdrop.addEventListener('mousedown', event => {
            if (event.target === backdrop && !this.busy) this.close();
        });
        backdrop.addEventListener('keydown', event => {
            if (event.key === 'Escape' && !this.busy) this.close();
        });

        const header = element('header', 'eclipse-smlrm-header');
        const title = element('h2', '', 'Smart LM Registry Editor (Beta)');
        title.id = 'eclipse-smlrm-title';
        const refresh = this.button('Refresh', () => this.refresh(), 'sml-registry-refresh');
        const add = this.button('New Model', () => this.newEntry(), 'sml-registry-new');
        const close = this.button('Close', () => this.close(), 'sml-registry-close');
        header.append(title, refresh, add, close);

        const body = element('div', 'eclipse-smlrm-body');
        const sidebar = element('aside', 'eclipse-smlrm-sidebar');
        this.filter = document.createElement('input');
        this.filter.type = 'search';
        this.filter.placeholder = 'Filter registry models';
        this.filter.setAttribute('aria-label', 'Filter registry models');
        this.filter.addEventListener('input', () => this.renderList());
        this.list = element('div', 'eclipse-smlrm-list');
        this.list.setAttribute('role', 'listbox');
        sidebar.append(this.filter, this.list);

        this.form = element('form', 'eclipse-smlrm-form');
        this.form.addEventListener('submit', event => { event.preventDefault(); this.save(); });
        this.inputs.name = field(this.form, 'name', 'Registry name');
        this.inputs.backend = field(this.form, 'backend', 'Backend', 'select', { values: BACKENDS });
        this.inputs.family = field(this.form, 'family', 'Family', 'select', { values: FAMILIES });
        this.inputs.repo_id = field(this.form, 'repo_id', 'Repository / model ID', 'text', { wide: true, placeholder: 'owner/model or ollama/model:tag' });
        this.inputs.source = field(this.form, 'source', 'Download source', 'select', { values: ['huggingface', 'modelscope'] });
        this.inputs.revision = field(this.form, 'revision', 'Revision', 'text', { placeholder: 'Resolved to an immutable commit on Inspect/Save' });
        this.inputs.local_only = field(this.form, 'local_only', 'Use an existing local model only', 'checkbox');
        this.inputs.local_path = field(this.form, 'local_path', 'Local path below configured LLM folder', 'text', { wide: true, placeholder: 'ModelFolder or ModelFolder/model.gguf' });
        this.inputs.has_vision = field(this.form, 'has_vision', 'Vision-capable model', 'checkbox');
        this.inputs.trust_remote_code = field(this.form, 'trust_remote_code', 'Allow pinned repository Python code', 'checkbox');
        this.inputs.file_pattern = field(this.form, 'file_pattern', 'GGUF file pattern', 'text', { backends: ['gguf', 'llamacpp'], placeholder: 'Model.{quant}.gguf' });
        this.inputs.mmproj = field(this.form, 'mmproj', 'Vision mmproj filename', 'text', { backends: ['gguf', 'llamacpp'] });
        this.inputs.quantizations = field(this.form, 'quantizations', 'Quantizations (comma-separated)', 'text', { wide: true, backends: ['gguf', 'llamacpp'] });
        this.inputs.download_quantization = field(this.form, 'download_quantization', 'Download quantization', 'select', { backends: ['gguf', 'llamacpp'], values: [] });
        this.inputs.quantization = field(this.form, 'quantization', 'Server quantization', 'select', { backends: ['vllm', 'vllm_native', 'sglang'], values: ['auto', 'fp8', 'awq', 'gptq', 'bitsandbytes', 'squeezellm'] });
        this.inputs.tensor_parallel = field(this.form, 'tensor_parallel', 'Tensor parallel', 'number', { backends: ['vllm', 'sglang'] });
        this.inputs.data_parallel = field(this.form, 'data_parallel', 'Data parallel', 'number', { backends: ['sglang'] });
        this.inputs.expected_sha256 = field(this.form, 'expected_sha256', 'Expected SHA-256 (one filename=digest per line)', 'textarea', { wide: true });
        this.inputs.description = field(this.form, 'description', 'Description', 'textarea', { wide: true });
        this.inputs.backend.addEventListener('change', () => this.updateVisibility());
        this.inputs.local_only.addEventListener('change', () => this.updateVisibility());
        this.inputs.quantizations.addEventListener('input', () => this.syncDownloadQuantizations());
        body.append(sidebar, this.form);

        const footer = element('footer', 'eclipse-smlrm-footer');
        this.status = element('div', 'eclipse-smlrm-status', 'Select a model or create a new entry.');
        this.inspectButton = this.button('Inspect', () => this.inspect(), 'sml-registry-inspect');
        this.saveButton = this.button('Save Entry', () => this.save(), 'sml-registry-save');
        this.downloadButton = this.button('Download', () => this.download(), 'sml-registry-download');
        this.verifyButton = this.button('Verify Local Files', () => this.verify(), 'sml-registry-verify');
        this.deleteButton = this.button('Delete Local Files', () => this.deleteLocal(), 'sml-registry-delete-local', true);
        this.removeButton = this.button('Remove Registry Entry', () => this.removeEntry(), 'sml-registry-remove-entry', true);
        footer.append(this.status, this.inspectButton, this.saveButton, this.downloadButton, this.verifyButton, this.deleteButton, this.removeButton);
        dialog.append(header, body, footer);
        this.backdrop = backdrop;
        this.setForm({});
    }

    button(label, handler, testid, danger = false) {
        const button = element('button', `eclipse-smlrm-button${danger ? ' eclipse-smlrm-danger' : ''}`, label);
        button.type = 'button';
        button.dataset.testid = testid;
        button.addEventListener('click', handler);
        return button;
    }

    setStatus(message, kind = '') {
        this.status.textContent = message;
        this.status.dataset.kind = kind;
    }

    setBusy(value) {
        this.busy = value;
        for (const button of this.backdrop.querySelectorAll('button')) button.disabled = value;
        if (!value) this.updateVisibility();
    }

    async action(label, callback) {
        if (this.busy) return;
        this.setBusy(true);
        this.setStatus(label);
        try {
            const result = await callback();
            this.setStatus(`${label} complete.`, 'success');
            return result;
        } catch (error) {
            this.setStatus(error?.message || `${label} failed.`, 'error');
            return null;
        } finally {
            this.setBusy(false);
        }
    }

    async refresh(preferred = this.selected) {
        const result = await this.action('Refreshing registry', () => request('/smartlml/model_list'));
        if (!result) return;
        this.models = Array.isArray(result) ? result : [];
        this.renderList();
        if (preferred && this.models.some(model => model.display_name === preferred)) {
            await this.select(preferred);
        } else if (!this.selected && this.models.length) {
            await this.select(this.models[0].display_name);
        }
    }

    renderList() {
        const filter = this.filter.value.trim().toLowerCase();
        this.list.replaceChildren();
        for (const model of this.models) {
            if (filter && !`${model.display_name} ${model.backend} ${model.family}`.toLowerCase().includes(filter)) continue;
            const item = element('button', 'eclipse-smlrm-item', model.display_name);
            item.type = 'button';
            item.setAttribute('role', 'option');
            item.setAttribute('aria-selected', String(model.display_name === this.selected));
            item.dataset.testid = 'sml-registry-list-item';
            const detail = element('small', '', `${model.backend} · ${model.origin} · ${model.local_status}`);
            item.appendChild(detail);
            item.addEventListener('click', () => this.select(model.display_name));
            this.list.appendChild(item);
        }
        if (!this.list.childElementCount) this.list.appendChild(element('p', '', 'No matching models.'));
    }

    async select(displayName) {
        const entry = await this.action('Loading registry entry', () => request(`/smartlml/model_entry?name=${encodeURIComponent(displayName)}`));
        if (!entry) return;
        this.selected = displayName;
        this.setForm(entry);
        this.renderList();
        this.setStatus(`Editing ${displayName}.`, 'success');
    }

    newEntry() {
        this.selected = null;
        this.setForm({ backend: 'transformers', family: 'Qwen', source: 'huggingface', quantization: 'auto', tensor_parallel: 1, data_parallel: 1 });
        this.renderList();
        this.inputs.name.focus();
        this.setStatus('Creating a user registry entry.');
    }

    setForm(entry) {
        for (const [name, input] of Object.entries(this.inputs)) {
            let value = entry[name];
            if (name === 'expected_sha256') {
                value = Object.entries(value || {}).map(([file, digest]) => `${file}=${digest}`).join('\n');
            } else if (name === 'quantizations') {
                value = Array.isArray(value) ? value.join(', ') : '';
            } else if (name === 'download_quantization') {
                continue;
            }
            if (input.type === 'checkbox') input.checked = Boolean(value);
            else input.value = value ?? (name === 'source' ? 'huggingface' : '');
        }
        this.inputs.backend.disabled = Boolean(this.selected);
        this.syncDownloadQuantizations();
        this.updateVisibility();
    }

    syncDownloadQuantizations() {
        const select = this.inputs.download_quantization;
        const previous = select.value;
        const values = this.inputs.quantizations.value.split(',').map(value => value.trim()).filter(Boolean);
        select.replaceChildren();
        for (const value of values) {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = value;
            select.appendChild(option);
        }
        if (values.includes(previous)) select.value = previous;
    }

    syncServerQuantizations() {
        const select = this.inputs.quantization;
        const values = SERVER_QUANTIZATIONS[this.inputs.backend.value] || ['auto'];
        const previous = select.value;
        select.replaceChildren();
        for (const value of values) {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = value;
            select.appendChild(option);
        }
        select.value = values.includes(previous) ? previous : 'auto';
    }

    updateVisibility() {
        const backend = this.inputs.backend.value;
        const localOnly = this.inputs.local_only.checked;
        const ollama = backend === 'ollama';
        this.syncServerQuantizations();
        for (const wrap of this.form.querySelectorAll('[data-backends]')) {
            wrap.hidden = !wrap.dataset.backends.split(',').includes(backend);
        }
        this.inputs.repo_id.closest('.eclipse-smlrm-field').hidden = localOnly;
        for (const name of ['source', 'revision', 'trust_remote_code', 'expected_sha256']) {
            this.inputs[name].closest('.eclipse-smlrm-field').hidden = localOnly || ollama;
        }
        this.inputs.local_path.closest('.eclipse-smlrm-field').hidden = !localOnly;
        if (backend === 'wd14') {
            this.inputs.family.value = 'WD14';
            this.inputs.has_vision.checked = true;
        }
        this.downloadButton.disabled = localOnly || this.busy || !this.selected;
        // Ollama owns and verifies its content-addressed model store; there are
        // no registry-managed local files for this endpoint to verify.
        this.verifyButton.disabled = !this.selected || this.busy || ollama;
        this.deleteButton.disabled = !this.selected || this.busy;
        this.removeButton.disabled = !this.selected || this.busy;
    }

    collect() {
        const hashes = {};
        for (const line of this.inputs.expected_sha256.value.split(/\r?\n/)) {
            if (!line.trim()) continue;
            const index = line.indexOf('=');
            if (index < 1) throw new Error('Expected hashes must use filename=digest lines.');
            hashes[line.slice(0, index).trim()] = line.slice(index + 1).trim();
        }
        const entry = {
            name: this.inputs.name.value.trim(), backend: this.inputs.backend.value,
            family: this.inputs.family.value, repo_id: this.inputs.repo_id.value.trim(),
            source: this.inputs.source.value, revision: this.inputs.revision.value.trim(),
            local_only: this.inputs.local_only.checked, local_path: this.inputs.local_path.value.trim(),
            has_vision: this.inputs.has_vision.checked, trust_remote_code: this.inputs.trust_remote_code.checked,
            file_pattern: this.inputs.file_pattern.value.trim(), mmproj: this.inputs.mmproj.value.trim(),
            quantizations: this.inputs.quantizations.value.split(',').map(value => value.trim()).filter(Boolean),
            quantization: this.inputs.quantization.value,
            tensor_parallel: Number(this.inputs.tensor_parallel.value || 1), data_parallel: Number(this.inputs.data_parallel.value || 1),
            expected_sha256: hashes, description: this.inputs.description.value.trim(),
        };
        if (entry.backend === 'ollama') {
            delete entry.source;
            delete entry.revision;
            delete entry.expected_sha256;
            entry.trust_remote_code = false;
        }
        return entry;
    }

    async inspect() {
        let entry;
        try { entry = this.collect(); } catch (error) { this.setStatus(error.message, 'error'); return; }
        const result = await this.action('Inspecting model source', () => request('/smartlml/registry/inspect', { entry, original_display_name: this.selected }));
        if (result?.entry) {
            const selected = this.selected;
            this.setForm(result.entry);
            this.selected = selected;
            this.inputs.backend.disabled = Boolean(selected);
            this.setStatus(`Validated as ${result.entry.display_name}.`, 'success');
        }
    }

    async save() {
        let entry;
        try { entry = this.collect(); } catch (error) { this.setStatus(error.message, 'error'); return; }
        const result = await this.action('Saving registry entry', () => request('/smartlml/registry/upsert', { entry, original_display_name: this.selected }));
        const displayName = result?.entry?.display_name;
        if (!displayName) return;
        this.selected = displayName;
        emitSmlRegistryChanged();
        await this.refresh(displayName);
    }

    selectedQuantization() {
        return this.inputs.download_quantization.value || null;
    }

    async download() {
        if (!this.selected) return;
        const result = await this.action('Downloading model', () => request('/smartlml/model/download', { display_name: this.selected, quantization: this.selectedQuantization() }));
        if (result) await this.refresh(this.selected);
    }

    async verify() {
        if (!this.selected) return;
        await this.action('Verifying local model files', () => request('/smartlml/model/verify', { display_name: this.selected, quantization: this.selectedQuantization() }));
    }

    async deleteLocal() {
        if (!this.selected) return;
        if (!window.confirm(`Delete local model files for "${this.selected}"?\n\nThe registry entry will remain. This cannot be undone.`)) return;
        const result = await this.action('Deleting local model files', () => request('/smartlml/model/delete', { display_name: this.selected }));
        if (result) await this.refresh(this.selected);
    }

    async removeEntry() {
        if (!this.selected) return;
        const selected = this.selected;
        if (!window.confirm(`Remove registry entry "${selected}"?\n\nLocal model files will not be deleted. This cannot be undone.`)) return;
        const result = await this.action('Removing registry entry', () => request('/smartlml/registry/remove', { display_name: selected }));
        if (!result) return;
        this.selected = null;
        emitSmlRegistryChanged();
        this.newEntry();
        await this.refresh();
    }
}

const manager = new RegistryManager();
let sidebarLauncherRegistered = false;

function isNewMenuActive() {
    try {
        return app.ui?.settings?.getSettingValue?.('Comfy.UseNewMenu') !== 'Disabled';
    } catch (_) { return true; }
}

function injectClassicButton() {
    const existing = document.querySelector('[data-eclipse-sml-registry-classic]');
    if (isNewMenuActive()) { existing?.remove(); return; }
    if (existing?.isConnected) return;
    const host = app.ui?.menuContainer;
    if (!host) return;
    const button = element('button', 'eclipse-smlrm-classic', 'Edit Smart LM Registry (Beta)');
    button.type = 'button';
    button.dataset.eclipseSmlRegistryClassic = 'true';
    button.addEventListener('click', () => manager.open());
    host.appendChild(button);
}

function registerSidebarLauncher() {
    if (sidebarLauncherRegistered) return true;
    const extensionManager = app.extensionManager;
    if (!extensionManager || typeof extensionManager.registerSidebarTab !== 'function') return false;
    extensionManager.registerSidebarTab({
        id: SIDEBAR_TAB_ID,
        icon: 'pi pi-database',
        title: 'LM Registry (Beta)',
        tooltip: 'Edit the Smart LM model registry (Beta)',
        type: 'custom',
        render: (host) => {
            host.replaceChildren();
            manager.open();
            // This tab is a left-toolbar launcher, not a persistent panel.
            // Close the empty sidebar immediately while leaving the modal open.
            queueMicrotask(() => {
                try {
                    const closeRequest = extensionManager.command?.execute?.(
                        `Workspace.ToggleSidebarTab.${SIDEBAR_TAB_ID}`
                    );
                    Promise.resolve(closeRequest).catch(() => {});
                } catch (_) { /* noop */ }
            });
        },
    });
    sidebarLauncherRegistered = true;
    return true;
}

app.registerExtension({
    name: 'Eclipse.SML.RegistryManager',
    commands: [{
        id: COMMAND_ID,
        label: 'Edit Smart LM Registry (Beta)',
        icon: 'pi pi-database',
        tooltip: 'Add, edit, download, verify, or remove Smart LM registry models (Beta)',
        function: () => manager.open(),
    }],
    menuCommands: [{ path: ['Eclipse'], commands: [COMMAND_ID] }],
    async init() { injectCSS(); },
    async setup() {
        if (!registerSidebarLauncher()) {
            let tries = 0;
            const timer = setInterval(() => {
                tries += 1;
                if (registerSidebarLauncher() || tries > 20) clearInterval(timer);
            }, 100);
        }
        injectClassicButton();
        queueMicrotask(injectClassicButton);
        setTimeout(injectClassicButton, 250);
        const observer = new MutationObserver(injectClassicButton);
        observer.observe(document.body, { childList: true, subtree: true });
    },
});
