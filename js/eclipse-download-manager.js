/**
 * Eclipse Download Manager (Beta).
 * Standalone modal, grid, state, API client, events, and launcher surfaces.
 */

import { app, api } from './comfy/index.js';

const COMMAND_ID = 'Eclipse.DownloadManager.Open';
const SIDEBAR_TAB_ID = 'eclipse-download-manager';
const CSS_ID = 'eclipse-download-manager-css';
const PAGE_SIZE = 50;
const BULK_HELP_TEXT = 'Bulk values apply only to files that are already selected. Select rows first, then choose bulk values.';

const CSS = `
.eclipse-dlm-backdrop{position:fixed;inset:0;z-index:1300;display:flex;align-items:center;justify-content:center;padding:18px;background:rgba(0,0,0,.7);box-sizing:border-box}
.eclipse-dlm-dialog{box-sizing:border-box;width:min(1540px,98vw);height:min(900px,95vh);min-width:min(720px,calc(100vw - 36px));min-height:min(480px,calc(100vh - 36px));max-width:calc(100vw - 36px);max-height:calc(100vh - 36px);display:flex;flex-direction:column;overflow:hidden;resize:both;border:1px solid var(--border-color,#555);border-radius:10px;background:var(--comfy-menu-bg,#303030);color:var(--input-text,#ddd);box-shadow:0 20px 64px rgba(0,0,0,.6);font:13px sans-serif}
.eclipse-dlm-header,.eclipse-dlm-footer{display:flex;align-items:center;gap:8px;padding:10px 14px;flex:0 0 auto}.eclipse-dlm-header{border-bottom:1px solid var(--border-color,#555)}.eclipse-dlm-footer{border-top:1px solid var(--border-color,#555);flex-wrap:wrap}.eclipse-dlm-header h2{flex:1;margin:0;font-size:18px}
.eclipse-dlm-body{display:flex;flex-direction:column;min-height:0;flex:1}.eclipse-dlm-inspector,.eclipse-dlm-filters,.eclipse-dlm-bulk,.eclipse-dlm-pager{display:flex;align-items:end;gap:8px;padding:8px 12px;flex-wrap:wrap;border-bottom:1px solid var(--border-color,#444)}
.eclipse-dlm-field{display:flex;flex-direction:column;gap:3px;min-width:120px}.eclipse-dlm-field--grow{flex:1;min-width:260px}.eclipse-dlm-field label{font-size:11px;color:var(--descrip-text,#aaa)}
.eclipse-dlm-bulk-help{align-self:center;color:var(--descrip-text,#aaa);font-size:11px;cursor:help;outline-offset:3px}
.eclipse-dlm-dialog input,.eclipse-dlm-dialog select{box-sizing:border-box;min-height:31px;padding:5px 7px;border:1px solid var(--border-color,#555);border-radius:5px;background:var(--comfy-input-bg,#222);color:inherit}.eclipse-dlm-button{min-height:31px;padding:5px 10px;border:1px solid var(--border-color,#666);border-radius:5px;background:var(--comfy-input-bg,#222);color:inherit;cursor:pointer}.eclipse-dlm-button:hover{filter:brightness(1.18)}.eclipse-dlm-button:disabled{opacity:.45;cursor:default}
.eclipse-dlm-status{flex:1;min-width:260px;color:var(--descrip-text,#aaa)}.eclipse-dlm-status[data-kind=error]{color:#ffaaaa}.eclipse-dlm-status[data-kind=success]{color:#aee6ae}
.eclipse-dlm-table-wrap{overflow:auto;min-height:190px;flex:1}.eclipse-dlm-table{width:max-content;min-width:100%;border-collapse:separate;border-spacing:0}.eclipse-dlm-table th,.eclipse-dlm-table td{padding:6px 7px;border-right:1px solid var(--border-color,#444);border-bottom:1px solid var(--border-color,#444);vertical-align:top;text-align:left;max-width:330px}.eclipse-dlm-table th{position:sticky;top:0;z-index:2;background:var(--comfy-menu-bg,#303030);white-space:nowrap}.eclipse-dlm-table th button{border:0;background:transparent;color:inherit;cursor:pointer}.eclipse-dlm-table tr[aria-disabled=true]{opacity:.62}.eclipse-dlm-table td input[type=text]{width:145px}.eclipse-dlm-table td select{max-width:180px}.eclipse-dlm-remote{word-break:break-word;min-width:230px}.eclipse-dlm-reason,.eclipse-dlm-filename-note{display:block;margin-top:4px;font-size:11px}.eclipse-dlm-reason{color:#ffb3b3}.eclipse-dlm-filename-note,.eclipse-dlm-muted{color:var(--descrip-text,#aaa)}.eclipse-dlm-muted{font-size:11px}.eclipse-dlm-digest{font-family:monospace;font-size:11px;word-break:break-all;max-width:180px}
.eclipse-dlm-tabs{display:flex;gap:4px;padding:7px 12px;border-bottom:1px solid var(--border-color,#444)}.eclipse-dlm-tab[aria-selected=true]{border-color:#6aa6ff;background:#24466f}.eclipse-dlm-panel{display:flex;flex:1;min-height:0;flex-direction:column}.eclipse-dlm-panel[hidden]{display:none!important}.eclipse-dlm-queue-actions{display:flex;gap:6px}.eclipse-dlm-progress{min-width:150px}.eclipse-dlm-progress progress{width:145px}.eclipse-dlm-classic{width:100%;margin:6px 0;padding:7px;border:1px solid var(--border-color,#555);border-radius:7px;background:var(--comfy-input-bg,#222);color:inherit;cursor:pointer}
@media(max-width:760px){.eclipse-dlm-backdrop{padding:4px}.eclipse-dlm-dialog{width:100%;height:98vh;min-width:0;min-height:0;max-width:calc(100vw - 8px);max-height:calc(100vh - 8px);resize:none}.eclipse-dlm-inspector,.eclipse-dlm-filters,.eclipse-dlm-bulk{align-items:stretch}.eclipse-dlm-field,.eclipse-dlm-field--grow{min-width:calc(50% - 8px);flex:1}.eclipse-dlm-header h2{font-size:15px}}
`;

function injectCSS() {
    if (document.getElementById(CSS_ID)) return;
    const style = document.createElement('style');
    style.id = CSS_ID;
    style.textContent = CSS;
    document.head.appendChild(style);
}

function el(tag, className = '', text = '') {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
}

function button(label, handler, testid = '') {
    const node = el('button', 'eclipse-dlm-button', label);
    node.type = 'button';
    if (testid) node.dataset.testid = testid;
    node.addEventListener('click', handler);
    return node;
}

function labeledField(labelText, input, grow = false) {
    const wrap = el('div', `eclipse-dlm-field${grow ? ' eclipse-dlm-field--grow' : ''}`);
    const label = el('label', '', labelText);
    if (!input.id) input.id = `eclipse-dlm-${crypto.randomUUID()}`;
    label.htmlFor = input.id;
    wrap.append(label, input);
    return wrap;
}

function selectInput(values = []) {
    const select = document.createElement('select');
    for (const item of values) {
        const option = document.createElement('option');
        if (typeof item === 'string') {
            option.value = item;
            option.textContent = item;
        } else {
            option.value = item.value;
            option.textContent = item.label;
        }
        select.appendChild(option);
    }
    return select;
}

async function request(path, body = null) {
    const response = await api.fetchApi(path, body === null ? undefined : {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) { /* bounded server errors may be empty */ }
    if (!response.ok || payload.success === false) throw new Error(payload.error || `Request failed (${response.status})`);
    return payload;
}

function formatBytes(value) {
    if (!Number.isFinite(value)) return 'Unknown';
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    let amount = value;
    let index = 0;
    while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index += 1; }
    return `${amount.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function saveJSON(filename, value) {
    const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 0);
}

class DownloadManagerModal {
    constructor() {
        this.categories = [];
        this.categoryMap = new Map();
        this.rows = [];
        this.rowState = new Map();
        this.selected = new Set();
        this.excluded = new Set();
        this.allFiltered = false;
        this.inspectionId = null;
        this.page = 1;
        this.pages = 1;
        this.filteredTotal = 0;
        this.sortBy = 'supported';
        this.sortDir = 'desc';
        this.jobs = [];
        this.selectedJobs = new Set();
        this.originalFocus = null;
        this.busy = false;
    }

    async open() {
        if (this.backdrop?.isConnected) {
            this.dialog.focus();
            return;
        }
        injectCSS();
        this.originalFocus = document.activeElement;
        this.build();
        document.body.appendChild(this.backdrop);
        this.provider.focus();
        await Promise.allSettled([this.loadCategories(), this.loadQueue()]);
    }

    close() {
        this.backdrop?.remove();
        this.backdrop = null;
        if (this.originalFocus instanceof HTMLElement) this.originalFocus.focus();
    }

    setStatus(message, kind = '') {
        this.status.textContent = message;
        this.status.dataset.kind = kind;
    }

    build() {
        this.backdrop = el('div', 'eclipse-dlm-backdrop');
        this.backdrop.dataset.testid = 'eclipse-download-manager';
        this.dialog = el('section', 'eclipse-dlm-dialog');
        this.dialog.tabIndex = -1;
        this.dialog.setAttribute('role', 'dialog');
        this.dialog.setAttribute('aria-modal', 'true');
        this.dialog.setAttribute('aria-labelledby', 'eclipse-dlm-title');
        this.backdrop.appendChild(this.dialog);
        this.backdrop.addEventListener('mousedown', event => { if (event.target === this.backdrop) this.close(); });
        this.backdrop.addEventListener('keydown', event => this.handleDialogKey(event));

        const header = el('header', 'eclipse-dlm-header');
        const title = el('h2', '', 'Download Manager (Beta)');
        title.id = 'eclipse-dlm-title';
        this.filesTab = button('Files', () => this.showPanel('files'), 'eclipse-download-manager-files-tab');
        this.queueTab = button('Queue', () => this.showPanel('queue'), 'eclipse-download-manager-queue-tab');
        this.filesTab.classList.add('eclipse-dlm-tab');
        this.queueTab.classList.add('eclipse-dlm-tab');
        header.append(title, this.filesTab, this.queueTab, button('Close', () => this.close(), 'eclipse-download-manager-close'));
        this.dialog.appendChild(header);

        const body = el('div', 'eclipse-dlm-body');
        this.filesPanel = el('section', 'eclipse-dlm-panel');
        this.filesPanel.dataset.panel = 'files';
        this.queuePanel = el('section', 'eclipse-dlm-panel');
        this.queuePanel.dataset.panel = 'queue';
        this.queuePanel.hidden = true;
        body.append(this.filesPanel, this.queuePanel);
        this.dialog.appendChild(body);

        const footer = el('footer', 'eclipse-dlm-footer');
        this.status = el('span', 'eclipse-dlm-status', 'Choose a provider and inspect a locator.');
        this.status.setAttribute('role', 'status');
        this.queueButton = button('Add Selected to Queue', () => this.enqueueSelected(), 'eclipse-download-manager-enqueue');
        this.queueButton.disabled = true;
        footer.append(this.status, this.queueButton);
        this.buildFilesPanel();
        this.buildQueuePanel();
        this.dialog.appendChild(footer);
        this.showPanel('files');
    }

    handleDialogKey(event) {
        if (event.key === 'Escape') { this.close(); return; }
        if (event.key !== 'Tab') return;
        const focusable = [...this.dialog.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),[tabindex]:not([tabindex="-1"])')]
            .filter(node => !node.closest('[hidden]'));
        if (!focusable.length) { event.preventDefault(); this.dialog.focus(); return; }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }

    showPanel(name) {
        const files = name === 'files';
        this.filesPanel.hidden = !files;
        this.queuePanel.hidden = files;
        this.filesTab.setAttribute('aria-selected', String(files));
        this.queueTab.setAttribute('aria-selected', String(!files));
        this.queueButton.hidden = !files;
        if (!files) this.loadQueue();
    }

    buildFilesPanel() {
        const inspector = el('div', 'eclipse-dlm-inspector');
        this.provider = selectInput([
            { value: 'civitai', label: 'CivitAI' },
            { value: 'huggingface', label: 'Hugging Face' },
        ]);
        this.provider.id = 'eclipse-dlm-provider';
        this.provider.dataset.testid = 'eclipse-download-manager-provider';
        this.locator = document.createElement('input');
        this.locator.type = 'text';
        this.locator.id = 'eclipse-dlm-locator';
        this.locator.dataset.testid = 'eclipse-download-manager-locator';
        this.locator.placeholder = 'AIR, SHA-256, provider URL, or owner/repository';
        this.revision = document.createElement('input');
        this.revision.type = 'text';
        this.revision.id = 'eclipse-dlm-revision';
        this.revision.placeholder = 'Optional branch, tag, or commit';
        this.revisionWrap = labeledField('Revision', this.revision);
        this.inspectButton = button('Get File List', () => this.inspect(), 'eclipse-download-manager-inspect');
        this.provider.addEventListener('change', () => this.syncProvider());
        this.locator.addEventListener('keydown', event => { if (event.key === 'Enter') this.inspect(); });
        inspector.append(labeledField('Provider', this.provider), labeledField('Locator', this.locator, true), this.revisionWrap, this.inspectButton);

        const filters = el('div', 'eclipse-dlm-filters');
        this.search = document.createElement('input');
        this.search.type = 'search';
        this.search.id = 'eclipse-dlm-search';
        this.search.placeholder = 'Search files';
        this.search.setAttribute('aria-label', 'Search inspected files');
        this.compatibleOnly = document.createElement('input');
        this.compatibleOnly.type = 'checkbox';
        this.compatibleOnly.id = 'eclipse-dlm-compatible';
        this.compatibleOnly.checked = true;
        this.showUnsupported = document.createElement('input');
        this.showUnsupported.type = 'checkbox';
        this.showUnsupported.id = 'eclipse-dlm-unsupported';
        const compatibleLabel = el('label', '', 'Compatible files only');
        compatibleLabel.htmlFor = this.compatibleOnly.id;
        const unsupportedLabel = el('label', '', 'Show unsupported / informational');
        unsupportedLabel.htmlFor = this.showUnsupported.id;
        const refresh = () => { this.page = 1; this.loadPage(); };
        let searchTimer;
        this.search.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(refresh, 180); });
        this.compatibleOnly.addEventListener('change', refresh);
        this.showUnsupported.addEventListener('change', refresh);
        filters.append(labeledField('Search', this.search, true), this.compatibleOnly, compatibleLabel, this.showUnsupported, unsupportedLabel);

        const bulk = el('div', 'eclipse-dlm-bulk');
        this.bulkCategory = selectInput([{ value: '', label: 'Use row suggestions' }]);
        this.bulkCategory.id = 'eclipse-dlm-bulk-category';
        this.bulkRoot = selectInput([{ value: '', label: 'Choose root' }]);
        this.bulkRoot.id = 'eclipse-dlm-bulk-root';
        this.bulkConflict = selectInput(['skip', 'rename', 'overwrite']);
        this.bulkConflict.id = 'eclipse-dlm-bulk-conflict';
        this.bulkCategory.title = 'Apply a destination category to currently selected compatible files. Select rows first.';
        this.bulkRoot.title = 'Apply a registered root to currently selected files. Choose a bulk category first.';
        this.bulkConflict.title = 'Apply this conflict policy to currently selected files. Select rows first.';
        const bulkHelp = el('span', 'eclipse-dlm-bulk-help', 'Bulk applies to selected rows ⓘ');
        bulkHelp.id = 'eclipse-dlm-bulk-help';
        bulkHelp.tabIndex = 0;
        bulkHelp.title = BULK_HELP_TEXT;
        bulkHelp.setAttribute('role', 'note');
        bulkHelp.setAttribute('aria-label', BULK_HELP_TEXT);
        for (const control of [this.bulkCategory, this.bulkRoot, this.bulkConflict]) {
            control.setAttribute('aria-describedby', bulkHelp.id);
        }
        this.bulkCategory.addEventListener('change', () => { this.syncBulkRoots(); this.applyBulk(); });
        this.bulkRoot.addEventListener('change', () => this.applyBulk());
        this.bulkConflict.addEventListener('change', () => this.applyBulk());
        bulk.append(labeledField('Bulk category', this.bulkCategory), labeledField('Bulk root', this.bulkRoot), labeledField('Bulk conflict policy', this.bulkConflict), bulkHelp);

        this.tableWrap = el('div', 'eclipse-dlm-table-wrap');
        this.table = el('table', 'eclipse-dlm-table');
        this.table.dataset.testid = 'eclipse-download-manager-table';
        this.table.setAttribute('aria-label', 'Inspected provider files');
        this.tableWrap.appendChild(this.table);
        this.pager = el('div', 'eclipse-dlm-pager');
        this.prevButton = button('Previous', () => { if (this.page > 1) { this.page -= 1; this.loadPage(); } }, 'eclipse-download-manager-prev');
        this.nextButton = button('Next', () => { if (this.page < this.pages) { this.page += 1; this.loadPage(); } }, 'eclipse-download-manager-next');
        this.pageStatus = el('span', '', 'No results');
        this.pager.append(this.prevButton, this.nextButton, this.pageStatus);
        this.filesPanel.append(inspector, filters, bulk, this.tableWrap, this.pager);
        this.syncProvider();
        this.renderTable();
    }

    buildQueuePanel() {
        const actions = el('div', 'eclipse-dlm-filters');
        this.startSelectedButton = button('Start Selected', () => this.selectedQueueAction('start'), 'eclipse-download-manager-start-selected');
        this.removeSelectedButton = button('Remove Selected', () => this.selectedQueueAction('remove'), 'eclipse-download-manager-remove-selected');
        this.startSelectedButton.disabled = true;
        this.removeSelectedButton.disabled = true;
        actions.append(
            button('Refresh Queue', () => this.loadQueue(), 'eclipse-download-manager-refresh-queue'),
            this.startSelectedButton,
            this.removeSelectedButton,
            button('Export Bundle', () => this.exportBundle(), 'eclipse-download-manager-export-bundle'),
            button('Import Bundle', () => this.importBundleInput.click(), 'eclipse-download-manager-import-bundle'),
        );
        this.importBundleInput = document.createElement('input');
        this.importBundleInput.type = 'file';
        this.importBundleInput.accept = 'application/json,.json';
        this.importBundleInput.hidden = true;
        this.importBundleInput.addEventListener('change', () => this.importBundle());
        actions.appendChild(this.importBundleInput);
        this.queueTableWrap = el('div', 'eclipse-dlm-table-wrap');
        this.queueTable = el('table', 'eclipse-dlm-table');
        this.queueTable.dataset.testid = 'eclipse-download-manager-queue-table';
        this.queueTable.setAttribute('aria-label', 'Persistent download queue');
        this.queueTableWrap.appendChild(this.queueTable);
        this.queuePanel.append(actions, this.queueTableWrap);
    }

    syncProvider() {
        this.revisionWrap.hidden = this.provider.value !== 'huggingface';
    }

    async loadCategories() {
        try {
            const data = await request('/eclipse/download_manager/categories');
            this.categories = data.categories || [];
            this.categoryMap = new Map(this.categories.map(category => [category.id, category]));
            this.bulkCategory.replaceChildren(new Option('Use row suggestions', ''));
            for (const category of this.categories) this.bulkCategory.appendChild(new Option(category.label, category.id));
            this.syncBulkRoots();
            this.renderTable();
        } catch (error) { this.setStatus(error.message, 'error'); }
    }

    syncBulkRoots() {
        const category = this.categoryMap.get(this.bulkCategory.value);
        this.bulkRoot.replaceChildren(new Option('Choose root', ''));
        for (const root of category?.roots || []) this.bulkRoot.appendChild(new Option(root.label, root.id));
        if (category?.roots?.length === 1) this.bulkRoot.value = category.roots[0].id;
    }

    filterPayload() {
        return {
            query: this.search.value,
            compatible_only: this.compatibleOnly.checked,
            show_unsupported: this.showUnsupported.checked,
            sort_by: this.sortBy,
            sort_dir: this.sortDir,
        };
    }

    async inspect() {
        if (this.busy) return;
        this.busy = true;
        this.inspectButton.disabled = true;
        this.setStatus('Inspecting immutable provider metadata…');
        try {
            const data = await request('/eclipse/download_manager/inspect', {
                provider: this.provider.value,
                locator: this.locator.value,
                revision: this.provider.value === 'huggingface' ? this.revision.value : '',
                page: 1,
                page_size: PAGE_SIZE,
                ...this.filterPayload(),
            });
            this.inspectionId = data.inspection_id;
            this.page = 1;
            this.acceptPage(data);
            this.setStatus(`${data.inspection.label}: ${data.total} provider files inspected.`, 'success');
        } catch (error) {
            this.setStatus(error.message, 'error');
        } finally {
            this.busy = false;
            this.inspectButton.disabled = false;
        }
    }

    async loadPage() {
        if (!this.inspectionId) return;
        const params = new URLSearchParams({
            page: String(this.page), page_size: String(PAGE_SIZE),
            query: this.search.value,
            compatible_only: String(this.compatibleOnly.checked),
            show_unsupported: String(this.showUnsupported.checked),
            sort_by: this.sortBy, sort_dir: this.sortDir,
        });
        try {
            const data = await request(`/eclipse/download_manager/inspection/${encodeURIComponent(this.inspectionId)}?${params}`);
            this.acceptPage(data);
        } catch (error) { this.setStatus(error.message, 'error'); }
    }

    acceptPage(data) {
        this.rows = data.rows || [];
        this.pages = data.pages || 1;
        this.filteredTotal = data.filtered_total || 0;
        for (const row of this.rows) this.ensureRowState(row);
        this.renderTable();
        this.prevButton.disabled = this.page <= 1;
        this.nextButton.disabled = this.page >= this.pages;
        this.pageStatus.textContent = `Page ${this.page} of ${this.pages} — ${this.filteredTotal} filtered / ${data.total} total`;
    }

    ensureRowState(row) {
        if (this.rowState.has(row.key)) return this.rowState.get(row.key);
        const category = row.suggested_category || '';
        const root = this.categoryMap.get(category)?.roots?.[0]?.id || '';
        const state = {
            category, root_id: root, subfolder: '',
            filename: row.suggested_filename || row.remote_path.split('/').pop(),
            conflict_policy: this.bulkConflict?.value || 'skip', confirm_ambiguous: !row.category_ambiguous,
        };
        this.rowState.set(row.key, state);
        return state;
    }

    isSelected(key) { return this.allFiltered ? !this.excluded.has(key) : this.selected.has(key); }

    setRowSelected(key, checked) {
        if (this.allFiltered) {
            if (checked) this.excluded.delete(key); else this.excluded.add(key);
        } else if (checked) this.selected.add(key); else this.selected.delete(key);
        this.updateQueueButton();
    }

    toggleSelectAll(checked) {
        this.allFiltered = checked;
        this.selected.clear();
        this.excluded.clear();
        this.renderTable();
        this.updateQueueButton();
    }

    updateQueueButton() {
        const count = this.allFiltered ? Math.max(0, this.filteredTotal - this.excluded.size) : this.selected.size;
        this.queueButton.disabled = count === 0;
        this.queueButton.textContent = count ? `Add ${count} Selected to Queue` : 'Add Selected to Queue';
    }

    sortableHeader(row, text, field) {
        const th = document.createElement('th');
        th.scope = 'col';
        if (this.sortBy === field) th.setAttribute('aria-sort', this.sortDir === 'asc' ? 'ascending' : 'descending');
        const trigger = el('button', '', text);
        trigger.type = 'button';
        trigger.addEventListener('click', () => {
            if (this.sortBy === field) this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
            else { this.sortBy = field; this.sortDir = 'asc'; }
            this.page = 1;
            this.loadPage();
        });
        th.appendChild(trigger);
        row.appendChild(th);
    }

    renderTable() {
        this.table.replaceChildren();
        const thead = document.createElement('thead');
        const header = document.createElement('tr');
        const selectTh = document.createElement('th');
        selectTh.scope = 'col';
        const selectAll = document.createElement('input');
        selectAll.type = 'checkbox';
        selectAll.checked = this.allFiltered;
        selectAll.setAttribute('aria-label', `Select all ${this.filteredTotal} filtered files`);
        selectAll.dataset.testid = 'eclipse-download-manager-select-all';
        selectAll.addEventListener('change', () => this.toggleSelectAll(selectAll.checked));
        selectTh.appendChild(selectAll);
        header.appendChild(selectTh);
        this.sortableHeader(header, 'Remote filename / path', 'remote_path');
        this.sortableHeader(header, 'Provider / type', 'provider_type');
        this.sortableHeader(header, 'Format / precision', 'format');
        this.sortableHeader(header, 'Size', 'size');
        this.sortableHeader(header, 'Expected digest', 'supported');
        for (const text of ['Destination category', 'Destination root', 'Safe subfolder', 'Local filename', 'Conflict policy']) {
            const th = el('th', '', text); th.scope = 'col'; header.appendChild(th);
        }
        thead.appendChild(header);
        const tbody = document.createElement('tbody');
        for (const row of this.rows) tbody.appendChild(this.renderRow(row));
        if (!this.rows.length) {
            const tr = document.createElement('tr');
            const td = el('td', 'eclipse-dlm-muted', this.inspectionId ? 'No files match the current filter.' : 'Inspect a provider locator to populate the grid.');
            td.colSpan = 11; tr.appendChild(td); tbody.appendChild(tr);
        }
        this.table.append(thead, tbody);
        this.updateQueueButton();
    }

    renderRow(row) {
        const tr = document.createElement('tr');
        tr.dataset.testid = `eclipse-download-manager-row-${row.key}`;
        tr.setAttribute('aria-disabled', String(!row.supported));
        const state = this.ensureRowState(row);
        const selectTd = document.createElement('td');
        const check = document.createElement('input');
        check.type = 'checkbox';
        check.checked = this.isSelected(row.key);
        check.disabled = !row.supported;
        check.setAttribute('aria-label', `Select ${row.suggested_filename || row.remote_path}`);
        check.addEventListener('change', () => this.setRowSelected(row.key, check.checked));
        selectTd.appendChild(check);
        const remote = el('td', 'eclipse-dlm-remote', row.remote_path);
        if (row.suggested_filename && row.suggested_filename !== row.remote_path) {
            remote.appendChild(el('span', 'eclipse-dlm-filename-note', `Author filename: ${row.suggested_filename}`));
        }
        if (row.disabled_reason) remote.appendChild(el('span', 'eclipse-dlm-reason', row.disabled_reason));
        if (row.category_ambiguous) remote.appendChild(el('span', 'eclipse-dlm-muted', 'Confirm a destination category.'));
        const provider = el('td', '', `${row.provider === 'civitai' ? 'CivitAI' : 'Hugging Face'} / ${row.provider_type || 'file'}`);
        const format = el('td', '', `${row.format || 'Unknown'}${row.precision ? ` / ${row.precision}` : ''}`);
        const size = el('td', '', formatBytes(row.size));
        const digest = el('td', 'eclipse-dlm-digest');
        if (row.expected_digest?.value) {
            digest.textContent = `${row.expected_digest.algorithm}: ${row.expected_digest.value.slice(0, 12)}…`;
            digest.title = row.expected_digest.value;
        } else digest.textContent = 'Unverifiable';
        const categoryTd = document.createElement('td');
        const category = selectInput([{ value: '', label: 'Choose category' }]);
        category.setAttribute('aria-label', `Destination category for ${row.remote_path}`);
        for (const id of row.compatible_categories || []) {
            const item = this.categoryMap.get(id);
            category.appendChild(new Option(item?.label || id, id));
        }
        category.value = state.category;
        category.disabled = !row.supported;
        category.addEventListener('change', () => {
            state.category = category.value;
            state.confirm_ambiguous = Boolean(category.value);
            state.root_id = this.categoryMap.get(category.value)?.roots?.[0]?.id || '';
            this.renderTable();
        });
        categoryTd.appendChild(category);
        const rootTd = document.createElement('td');
        const root = selectInput([{ value: '', label: 'Choose root' }]);
        root.setAttribute('aria-label', `Destination root for ${row.remote_path}`);
        for (const item of this.categoryMap.get(state.category)?.roots || []) root.appendChild(new Option(item.label, item.id));
        root.value = state.root_id;
        root.disabled = !row.supported || !state.category;
        root.addEventListener('change', () => { state.root_id = root.value; });
        rootTd.appendChild(root);
        const subfolderTd = document.createElement('td');
        const subfolder = document.createElement('input');
        subfolder.type = 'text'; subfolder.value = state.subfolder;
        subfolder.placeholder = 'optional/subfolder';
        subfolder.setAttribute('aria-label', `Safe subfolder for ${row.remote_path}`);
        subfolder.disabled = !row.supported;
        subfolder.addEventListener('input', () => { state.subfolder = subfolder.value; });
        subfolderTd.appendChild(subfolder);
        const filenameTd = document.createElement('td');
        const filename = document.createElement('input');
        filename.type = 'text'; filename.value = state.filename;
        filename.setAttribute('aria-label', `Local filename for ${row.remote_path}`);
        filename.disabled = !row.supported;
        filename.addEventListener('input', () => { state.filename = filename.value; });
        filenameTd.appendChild(filename);
        const conflictTd = document.createElement('td');
        const conflict = selectInput(['skip', 'rename', 'overwrite']);
        conflict.value = state.conflict_policy;
        conflict.setAttribute('aria-label', `Conflict policy for ${row.remote_path}`);
        conflict.disabled = !row.supported;
        conflict.addEventListener('change', () => { state.conflict_policy = conflict.value; });
        conflictTd.appendChild(conflict);
        tr.append(selectTd, remote, provider, format, size, digest, categoryTd, rootTd, subfolderTd, filenameTd, conflictTd);
        return tr;
    }

    applyBulk() {
        for (const row of this.rows) {
            if (!this.isSelected(row.key)) continue;
            const state = this.ensureRowState(row);
            if (this.bulkCategory.value && row.compatible_categories.includes(this.bulkCategory.value)) {
                state.category = this.bulkCategory.value;
                state.confirm_ambiguous = true;
                state.root_id = this.bulkRoot.value || this.categoryMap.get(state.category)?.roots?.[0]?.id || '';
            }
            if (this.bulkConflict.value) state.conflict_policy = this.bulkConflict.value;
        }
        this.renderTable();
    }

    async enqueueSelected() {
        if (!this.inspectionId || this.queueButton.disabled) return;
        if (this.allFiltered && (!this.bulkCategory.value || !this.bulkRoot.value)) {
            this.setStatus('Select a bulk category and root before queueing all filtered files.', 'error');
            return;
        }
        const overrides = {};
        for (const [key, state] of this.rowState) {
            if (this.isSelected(key)) overrides[key] = { ...state };
        }
        const selection = this.allFiltered
            ? { all_filtered: true, excluded_keys: [...this.excluded], filter: this.filterPayload() }
            : { all_filtered: false, file_keys: [...this.selected] };
        try {
            const result = await request('/eclipse/download_manager/queue', {
                inspection_id: this.inspectionId,
                selection,
                bulk: {
                    category: this.bulkCategory.value || undefined,
                    root_id: this.bulkRoot.value || undefined,
                    conflict_policy: this.bulkConflict.value,
                    confirm_ambiguous: Boolean(this.bulkCategory.value),
                },
                overrides,
            });
            this.setStatus(`${result.jobs.length} file(s) added and waiting to start.`, 'success');
            this.selected.clear(); this.excluded.clear(); this.allFiltered = false;
            this.renderTable();
            await this.loadQueue();
        } catch (error) { this.setStatus(error.message, 'error'); }
    }

    async loadQueue() {
        try {
            const data = await request('/eclipse/download_manager/queue');
            this.jobs = data.jobs || [];
            const available = new Set(this.jobs.map(job => job.uuid));
            this.selectedJobs = new Set([...this.selectedJobs].filter(jobUuid => available.has(jobUuid)));
            this.renderQueue();
        } catch (error) { this.setStatus(error.message, 'error'); }
    }

    updateJob(job) {
        const index = this.jobs.findIndex(item => item.uuid === job.uuid);
        if (index >= 0) this.jobs[index] = job; else this.jobs.push(job);
        if (this.backdrop?.isConnected) this.renderQueue();
    }

    renderQueue() {
        this.queueTable.replaceChildren();
        const thead = document.createElement('thead');
        const header = document.createElement('tr');
        for (const text of ['Select', 'File', 'Provider', 'Destination', 'State / progress', 'Local SHA-256', 'Actions']) {
            const th = el('th', '', text); th.scope = 'col'; header.appendChild(th);
        }
        thead.appendChild(header);
        const tbody = document.createElement('tbody');
        for (const job of [...this.jobs].reverse()) {
            const tr = document.createElement('tr');
            tr.dataset.jobUuid = job.uuid;
            const selectedTd = document.createElement('td');
            const selected = document.createElement('input');
            selected.type = 'checkbox'; selected.checked = this.selectedJobs.has(job.uuid);
            selected.setAttribute('aria-label', `Select queue job ${job.destination?.filename}`);
            selected.addEventListener('change', () => {
                if (selected.checked) this.selectedJobs.add(job.uuid); else this.selectedJobs.delete(job.uuid);
                this.updateQueueActions();
            });
            selectedTd.appendChild(selected);
            const file = el('td', 'eclipse-dlm-remote', job.destination?.filename || 'Unknown');
            const provider = el('td', '', job.provider_identity?.provider || 'Unknown');
            const destination = el('td', '', `${job.destination?.category || ''} / ${job.destination?.relative_path || ''}`);
            const progressTd = el('td', 'eclipse-dlm-progress');
            const progress = document.createElement('progress');
            progress.max = 100; progress.value = job.progress?.percent || 0;
            progress.setAttribute('aria-label', `${job.state} ${progress.value} percent`);
            const partialNote = job.has_partial
                ? ` — ${formatBytes(job.partial_bytes)} partial retained`
                : '';
            progressTd.append(el('div', '', `${job.state}${job.error ? ` — ${job.error}` : ''}${partialNote}`), progress);
            const hash = el('td', 'eclipse-dlm-digest', job.local_sha256 ? `${job.local_sha256.slice(0, 12)}…` : '—');
            if (job.local_sha256) hash.title = job.local_sha256;
            const actions = el('td', 'eclipse-dlm-queue-actions');
            if (['queued', 'transferring'].includes(job.state)) actions.appendChild(button('Cancel', () => this.queueAction('cancel', job.uuid)));
            if (['failed', 'cancelled'].includes(job.state)) actions.appendChild(button('Retry', () => this.queueAction('retry', job.uuid)));
            if (['failed', 'cancelled'].includes(job.state) && job.has_partial) {
                actions.appendChild(button('Delete Partial', () => this.queueAction('discard-partial', job.uuid)));
            }
            tr.append(selectedTd, file, provider, destination, progressTd, hash, actions);
            tbody.appendChild(tr);
        }
        if (!this.jobs.length) {
            const tr = document.createElement('tr'); const td = el('td', 'eclipse-dlm-muted', 'The persistent queue is empty.'); td.colSpan = 7; tr.appendChild(td); tbody.appendChild(tr);
        }
        this.queueTable.append(thead, tbody);
        this.updateQueueActions();
    }

    async queueAction(action, jobUuid) {
        try {
            const data = await request(`/eclipse/download_manager/queue/${action}`, { job_uuid: jobUuid });
            if (action === 'discard-partial') {
                this.setStatus(
                    data.removed_bytes
                        ? `${formatBytes(data.removed_bytes)} of partial download data deleted.`
                        : 'No partial download data was present.',
                    'success',
                );
            }
            await this.loadQueue();
        } catch (error) { this.setStatus(error.message, 'error'); }
    }

    selectedJobIds() { return [...this.selectedJobs]; }

    updateQueueActions() {
        const selected = this.jobs.filter(job => this.selectedJobs.has(job.uuid));
        const startable = new Set(['ready', 'failed', 'cancelled']);
        const removable = new Set(['ready', 'completed', 'failed', 'cancelled']);
        this.startSelectedButton.disabled = !selected.length || selected.some(job => !startable.has(job.state));
        this.removeSelectedButton.disabled = !selected.length || selected.some(
            job => !removable.has(job.state) || job.has_partial,
        );
    }

    async selectedQueueAction(action) {
        const ids = this.selectedJobIds();
        if (!ids.length) return;
        try {
            const data = await request(`/eclipse/download_manager/queue/${action}`, { job_ids: ids });
            this.selectedJobs.clear();
            const count = action === 'start' ? data.jobs?.length || 0 : data.removed_job_ids?.length || 0;
            this.setStatus(
                action === 'start'
                    ? `${count} download(s) started.`
                    : `${count} queue ${count === 1 ? 'entry' : 'entries'} removed.`,
                'success',
            );
            await this.loadQueue();
        } catch (error) { this.setStatus(error.message, 'error'); }
    }

    async exportBundle() {
        const ids = this.selectedJobIds();
        if (!ids.length) { this.setStatus('Select queue jobs to export.', 'error'); return; }
        try {
            const data = await request('/eclipse/download_manager/bundles/export', { job_ids: ids });
            saveJSON('eclipse-download-bundle.json', data.bundle);
            this.setStatus('Download bundle exported.', 'success');
        } catch (error) { this.setStatus(error.message, 'error'); }
    }

    async importBundle() {
        const file = this.importBundleInput.files?.[0];
        this.importBundleInput.value = '';
        if (!file) return;
        try {
            if (file.size > 1024 * 1024) throw new Error('Bundle exceeds the 1 MiB client limit.');
            const bundle = JSON.parse(await file.text());
            const data = await request('/eclipse/download_manager/bundles/import', { bundle });
            this.setStatus(`${data.jobs.length} bundle item(s) added and waiting to start.`, 'success');
            await this.loadQueue();
        } catch (error) { this.setStatus(error.message, 'error'); }
    }

}

const manager = new DownloadManagerModal();
let sidebarRegistered = false;

async function isNewMenuActive() {
    try {
        const settingApi = app.extensionManager?.setting;
        if (typeof settingApi?.get === 'function') {
            return await settingApi.get('Comfy.UseNewMenu') !== 'Disabled';
        }
        return app.ui?.settings?.getSettingValue?.('Comfy.UseNewMenu') !== 'Disabled';
    } catch (_) { return true; }
}

async function injectClassicButton() {
    let existing = document.querySelector('[data-eclipse-download-manager-classic]');
    if (await isNewMenuActive()) { existing?.remove(); return; }
    existing = document.querySelector('[data-eclipse-download-manager-classic]');
    if (existing?.isConnected) return;
    const host = app.ui?.menuContainer;
    if (!host) return;
    const launch = el('button', 'eclipse-dlm-classic', 'Download Manager (Beta)');
    launch.type = 'button';
    launch.dataset.eclipseDownloadManagerClassic = 'true';
    launch.addEventListener('click', () => manager.open());
    host.appendChild(launch);
}

function registerSidebarLauncher() {
    if (sidebarRegistered) return true;
    const extensionManager = app.extensionManager;
    if (!extensionManager || typeof extensionManager.registerSidebarTab !== 'function') return false;
    extensionManager.registerSidebarTab({
        id: SIDEBAR_TAB_ID,
        icon: 'pi pi-download',
        title: 'Download Manager (Beta)',
        tooltip: 'Inspect provider files and manage verified model downloads',
        type: 'custom',
        render: host => {
            host.replaceChildren();
            manager.open();
            queueMicrotask(() => {
                try {
                    Promise.resolve(extensionManager.command?.execute?.(`Workspace.ToggleSidebarTab.${SIDEBAR_TAB_ID}`)).catch(() => {});
                } catch (_) { /* launcher close is best effort */ }
            });
        },
    });
    sidebarRegistered = true;
    return true;
}

api.addEventListener('eclipse.download_manager.progress', event => {
    if (event.detail?.job) manager.updateJob(event.detail.job);
});

app.registerExtension({
    name: 'Eclipse.DownloadManager',
    commands: [{
        id: COMMAND_ID,
        label: 'Download Manager (Beta)',
        icon: 'pi pi-download',
        tooltip: 'Open the standalone Eclipse Download Manager (Beta)',
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
        await injectClassicButton();
        queueMicrotask(() => { void injectClassicButton(); });
        setTimeout(() => { void injectClassicButton(); }, 250);
        const observer = new MutationObserver(() => { void injectClassicButton(); });
        observer.observe(document.body, { childList: true, subtree: true });
    },
});
