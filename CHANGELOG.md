# Changelog

All notable changes to ComfyUI Eclipse are documented in this file.

Entries follow conventional commit prefixes:

## 2026-07-06

### Version: 4.0.0

- **feat: new** `Show Any Stop [Eclipse]` node (`py/RvTools_ShowAnyStop.py`) which acts as a text/data viewer with a socketless "Stop (Result Review)" review interruption toggle.
- **feat: new** Image Filter Adjustments Advanced [Eclipse] node (`py/RvImage_FilterAdjustmentsAdvanced.py`) with Hue Shift, Temperature, Tint, Solarize, Vignette, Chromatic Aberration, and GPU-accelerated LUT (.cube) loading.
- **feat: new** Workflow Migration Tool [Eclipse] node (`py/RvTools_WorkflowMigration.py`) supporting workflow JSON scanning, validation, and in-place upgrade of legacy [Eclipse] and [RvTools] nodes.
- **feat: new** standalone `Preview Image (DOM) [Stop]` (`py/RvImage_PreviewDom_Stop.py`) and `Show Text [Stop]` (`py/RvTools_ShowText_Stop.py`) nodes with socketless active/bypass stop toggles to halt execution for manual review.
- **feat:** refactored `Show Any [Eclipse]` node (`py/RvTools_ShowAny.py`) to act as a simple text/data viewer (matching `Show Any Stop` but without the stop review button).
- **feat:** added legacy // RvTools node naming support to the workflow migration systems, splitting mappings into v1 and v2 files, and adding explicit legacy mappings.
- **feat:** added standalone command-line Python script `tools/migrate_workflow.py` for headless workflow JSON folder/file migration.
- **feat:** added interactive large preview overlay to Image Selector with cycling, Space/S/Enter selection toggles, and viewport focus sync.
- **feat:** added grid layout modes, toolbar status layout, preview mode combo dropdown, and double-click high-resolution zoom gesture to Image Selector.
- **feat:** structured Image Selector preview outputs in uid-specific temp folders with automatic cleanup to prevent filesystem bloat.
- **feat:** added native list-of-images and list-of-masks input support to various nodes (Resize, Rescale, Soften, Filter Adjustments, Inset & Crop, Color Match, Upscale, Save, Smart Model Loader, Batch Slice/Strip/Interleave).
- **feat:** added progress bar support (`comfy.utils.ProgressBar`) inside the workflow migration execute loops.
- **feat:** added floating close button in DOM Preview single image mode to return to grid layout.
- **feat:** updated repository reset scripts under `scripts/` to include safety confirmation prompts and preserve `config.json` option.
- **fix:** resolved a PIL data type `TypeError: Cannot handle this data type: (1, 1, 832, 3)` in `Preview Image (DOM)` (`RvImage_PreviewDom.py`) by implementing robust list/batch flattening and squeezing logic.
- **fix:** updated `Save Images [Eclipse]` (`RvImage_Save.py`) to declare `is_input_list=True` and `is_output_list=True` on outputs, wrapping single output elements into lists to resolve downstream list compatibility issues.
- **fix:** resolved a list nesting bug in `Save Images [Eclipse]` (`RvImage_Save.py`) where returning single-element lists wrapped the output tensor in a nested list `[[tensor]]`, causing downstream SaveImage nodes to fail.
- **fix:** resolved execution crashes and schema mapping issues for list connections on Image Selector, Image Comparer, Save Images, Mask to SEGS, and the Stop node.
- **fix:** resolved a frontend configuration bug in Seed node where clicking "New Fixed Random" or randomizing generated a 64-bit seed value when in 32-bit mode.
- **fix:** resolved image display inside Image Selector's One Image per Row mode to calculate dynamic cell heights based on natural aspect ratios.
- **docs:** added descriptive parameter tooltips with recommended values for advanced photographic parameters to ensure ease of use.
- **chore:** completely removed all hardcoded static fallback node name mappings from the Python codebase, relying fully on loading external configuration files.
- **chore:** renamed `RvImage_Preview_Mask` to `RvMask_Preview` and moved it under a new Mask node category.
- **chore:** reorganized all `RvImage_` nodes into Loaders, Save & Preview, Batch Operations, Transforms, and FX & Color subcategory menus.
- **chore:** renamed video nodes starting with `RvImage_` to start with `RvVideo_`.
- **chore:** complete removal of all deprecated legacy nodes, legacy files, and registry imports for the v4.0.0 cleanup.
- **BREAKING:** removed all deprecated legacy nodes, breaking older saved workflows using legacy nodes.
- **BREAKING:** removed compatibility configurations and output connection checks for the rgthree-comfy node pack.
- **BREAKING:** combined Seed and Seed 32-bit nodes into one using a bit_depth combo selector (defaulting to 64-bit).
- **BREAKING:** removed Lora Stack to String node from the codebase.
- **BREAKING:** removed VRAM CleanUp custom node and JS helper.
- **BREAKING:** removed old v21 and v22 Pipe IO Sampler Settings nodes, renamed v23 to Pipe IO Sampler Settings, and removed `prompt_seed`, `upscale_steps`, and `upscale_denoise` options.
- **BREAKING:** removed deprecated Pipe Out VC Name Generator node.
- **BREAKING:** removed Sampler Selection and Custom Size nodes.
- **BREAKING:** removed RAM Cleanup node.
- **BREAKING:** removed version tag suffixes from active node IDs: Smart Model Loader v2 [Eclipse] -> Smart Model Loader [Eclipse], IO Checkpoint Loader v2 [Eclipse] -> IO Checkpoint Loader [Eclipse], Smart Folder v2 [Eclipse] -> Smart Folder [Eclipse], and Save Images v2 [Eclipse] -> Save Images [Eclipse].

**Changed files:**

- `py/RvImage_FilterAdjustmentsAdvanced.py`
- `py/RvTools_WorkflowMigration.py`
- `tools/migrate_workflow.py`
- `tools/migration_rvtoolsv1.txt`
- `tools/migration_rvtoolsv2.txt`
- `py/RvImage_PreviewDom_Stop.py`
- `py/RvTools_ShowAnyStop.py`
- `js/eclipse-show-any.js`
- `__init__.py`
- `py/RvImage_PreviewDom.py`
- `py/RvImage_Save.py`
- `pyproject.toml`
- `core/__init__.py`
- `js/eclipse-image-selector.js`
- `js/eclipse-dom-preview.js`
- `js/eclipse-seed.js`
- `py/RvConversion_ConvertToList.py`
- `py/RvImage_Selector.py`
- `py/RvImage_UpscaleWithModel.py`
- `py/RvImage_UpscaleWithModel_v2.py`
- `py/RvImage_Resize.py`
- `py/RvImage_Rescale.py`
- `py/RvImage_Soften.py`
- `py/RvImage_FilterAdjustments.py`
- `py/RvImage_InsetCrop.py`
- `py/RvImage_ColorMatch.py`
- `py/RvImage_Comparer.py`
- `py/RvImage_BatchSlice.py`
- `py/RvImage_BatchStrip.py`
- `py/RvImage_BatchInterleave.py`
- `py/RvLoader_SmartModelLoader_LM.py`
- `py/RvMask_ToSEGS.py`
- `py/RvTools_Stop.py`
- `py/RvVideo_Preview.py`
- `py/RvVideo_Save.py`
- `py/RvSampler_DetailerForEach.py`
- `py/RvTools_ShowAny.py`
- `py/RvTools_ShowText.py`
- `py/RvTools_ShowText_Stop.py`
- `js/eclipse-show-text.js`

---

## 2026-07-02

### Version: 3.7.28

- **feat:** renamed text conditioning zero out node to RvCond_ConditioningZeroOut and expanded model token mappings to support SD20, SDXLRefiner, HunyuanDiT, GenmoMochi, Cosmos, and CogVideoX.
- **feat:** renamed KSampler (Pipe Data) to KSampler (Kargim), outputting a merged pipe context alongside individual pass-through slots, resolving parameters with custom widget overwrite and external link priorities.
- **chore:** deprecated and moved Basic Pipe, From Basic Pipe, and To Basic Pipe nodes into the legacy module under the Deprecated category.
- **chore:** deleted standard non-pipe KSampler node and cleaned up all backend/frontend references.

**Changed files:**

- `js/eclipse-sampler-tiled-decode.js`
- `js/eclipse-seed.js`
- `py/RvCond_ConditioningZeroOut.py`
- `py/RvSampler_KSamplerKargim.py`
- `py/legacy/legacy_BasicPipe.py`
- `py/legacy/legacy_FromBasicPipe.py`
- `py/legacy/legacy_ToBasicPipe.py`
- `__init__.py`
- `pyproject.toml`
- `CHANGELOG.md`

---

## 2026-06-12

### Version: 3.7.0

- **feat: new** model integrity system — `core/model_integrity.py` provides shared SHA256 hashing with canonical `<file.ext>.sha256` sidecars, legacy `<file-no-ext>.sha256` read compatibility, stale-sidecar invalidation by file/sidecar mtime, an expected-metadata store in `<file.ext>.eclipse.json` (read/write), and `verify()` with `ok`/`mismatch`/`no-expected`/`missing`/`unverifiable` statuses.
- **feat: new** CivitAI client — `core/civitai_client.py` resolves files by AIR (`urn:air:...`) or SHA256 (`by-hash`), parses AIR (model/version/file id), picks the file by fileId/SHA/primary, and streams downloads with auth + `.part` atomic rename.
- **feat: new** locator-first download endpoint — `POST /eclipse/civitai/download` validates API key + AIR/SHA, resolves the canonical filename from CivitAI metadata, enforces safe pathing, supports conflict policy (`skip`/`overwrite`/`rename`), verifies post-download, and writes `.eclipse.json`.
- **feat: new** integrity verify endpoint — `POST /eclipse/integrity/verify` resolves a present file by role, persists the entered SHA/AIR to `.eclipse.json`, hashes the file, and returns `ok`/`mismatch`/`no-expected`/`missing`/`unverifiable`.
- **feat: new** bulk hash CLI — `scripts/hash_model_library.py` (+ `.sh`/`.bat` launchers) walks ComfyUI model-weight roots and writes `.sha256` sidecars; optional `--air-lookup` resolves AIR/SHA via CivitAI `by-hash` and writes `.eclipse.json`.
- **feat:** Smart Model Loader v2 — add `verify_file` (`off`/`sidecar`/`verify`), `expected_hashes` map, a single `expected_sha_or_air` editor field (annotates the file in `expected_for`, or drives a locator-only download when `expected_for` is empty), `download_locators`, and `download_target_role`; `execute()` warms SHA sidecars and warn+continue verifies active files (`.eclipse.json` first, `expected_hashes` fallback).
- **feat:** Smart Model Loader v2 — CivitAI download button (filename resolved from metadata), dynamic `expected_for` target list, switching `expected_for` loads that file's stored value (no stale carry-over), missing-file auto-reveal of the integrity editor + download button independent of verify mode, and pending/missing file-selection preservation across template load + list refresh.
- **feat:** Smart Model Loader v2 — dual-mode action button: present file → `✓ Verify now` (hashes + compares immediately, shows ok/mismatch inline), missing file or locator → `⬇ Download from CivitAI`; label/action swap automatically with the `expected_for` selection.
- **feat:** loader templates — on Save, `expected_hashes` is overlaid from each present file's trusted `.eclipse.json` (authoritative) while preserving manual/pending entries for shippable templates.
- **fix:** block_swap chip disabling — update dynamic VRAM detection for ComfyUI 0.23.0+; `ModelPatcherDynamic` subclass replaces the `model_mmap_residency` attribute from 0.18.x; detection now checks `hasattr(module, 'ModelPatcherDynamic')` with 0.18.x fallback.
- **fix:** CivitAI download — add HTTP range-resume support; partial `.part` file is resumed with `Range: bytes=N-` on retry; transient network errors (`IncompleteRead`, `ConnectionError`, `Timeout`) preserve `.part` file; permanent errors still clean up; tqdm bar starts from partial offset.
- **fix:** CivitAI download progress bar — `download_file` was called synchronously inside the async aiohttp handler, blocking the event loop and preventing WebSocket progress events from flushing until completion; fixed by running download in `run_in_executor`.
- **feat:** Smart Model Loader v2 — auto-save current template after verify is clicked (always, regardless of result) and after a successful download; persists `expected_hashes` and `sha256` to disk without requiring a manual Save action.
- **feat:** Smart Model Loader v2 — missing-file UX: `verify_file` auto-promoted from `off` → `verify` when a file is missing; `download_target_role` shown and auto-filled (via `getRoleForTarget`) when the selected `expected_for` file is absent.
- **refactor:** Save Images v2 — `get_sha256` now delegates to `core.model_integrity.sha256_for`, unifying hashing + sidecar conventions and adding size/mtime-aware cache invalidation.

**Changed files:**

- `core/model_integrity.py`
- `core/civitai_client.py`
- `core/server_endpoints.py`
- `js/eclipse-smart-model-loader.js`
- `py/RvLoader_SmartModelLoader.py`
- `py/RvImage_SaveImages.py`
- `scripts/hash_model_library.py`
- `scripts/hash_model_library.sh`
- `scripts/hash_model_library.bat`

