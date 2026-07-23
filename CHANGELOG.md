# Changelog

All notable changes to ComfyUI Eclipse are documented in this file.

Entries follow conventional commit prefixes:

## 2026-07-23

### Version: 4.2.0

- **Feat (New)**
  - **Indices to List:** Convert comma- or newline-separated integer indices from an editable string widget into the LIST format used by IO Slice & Dice.
  - **WAN LipSync Timeline Planning:** Add Timeline Planner and Plan Step nodes for building frame-exact InfiniteTalk extension tasks from manual or automatically spaced transitions, with optional Wav2Vec2 activity-gap alignment and accumulated-frame timing.

- **Feat**
  - **Filter Prompt:** Filter tag lists and natural-language prompts with exact word sequences and count-based wildcard slots; each `*` matches one complete word, spaces and underscores are equivalent, and tag-list removal preserves whole tags.
  - **Loop Image Selector:** Pass a single image source or one-frame batch and existing loop context through unchanged without redundant selection or transition blending.
  - **Save Video:** Report throttled ComfyUI progress while encoding frames and finalizing the output container.
  - **UI Enhancements:** Add an enabled-by-default, server-backed option to hide Muted and Bypassed badges in Nodes 2.0 without changing node state, serialization, or execution.

- **Fix**
  - **Nodes 2.0 sizing:** Preserve compact, collapsed, saved, and manually resized dimensions across fresh and loaded workflows, renderer switches, and root or nested subgraphs; keep logical bounds, connection positions, stacking, and CSS width aligned with rendered nodes.
  - **Image and video previews:** Contain intrinsic media sizing for Image Selector, Load Image variants, Preview Image, Preview Mask, Save Images, video previews, and Image Comparer while retaining aspect ratios, minimum sizes, internal scrolling, independent folder-preview resizing, and classic-renderer behavior.
  - **Image Selector and combo-chip controls:** Restore immediate focused wheel scrolling with canvas navigation at grid boundaries, clean up interaction listeners during rebuild/removal, and match combo-chip bar height between Nodes 2.0 and classic rendering.
  - **Context menus and mode controls:** Restore multi-node Custom Title/BG/All actions, hoverable deep navigation and reorder submenus, and reliable Fast Mode Switcher and Toggle promotion for every subgraph instance after workflow load.
  - **Dynamic type links:** Preserve wildcard, reordered-union, compatible-subset, and subgraph links across workflow reloads for Convert Primitive, Any Multi-Switch, and Set/Get using deterministic type restoration and LiteGraph-compatible validation.
  - **Network and API security:** Block image-download SSRF across DNS and redirects, enforce component-aware CivitAI paths, keep Hugging Face tokens write-only, and use safe LoRA deserialization.
  - **Frontend lifecycle:** Remove Load Image document listeners with their nodes and always unwind shared graph-to-prompt state after serialization failures.
  - **Image and video correctness:** Preserve sanitized Color Match targets, clamp resized image and mask outputs, convert normalized Smart Detection coordinates to original-image pixels, and normalize loop-mode Save Video frames before resizing.
  - **VRAM purge:** Synchronize aggressive model unloading, garbage collection, and accelerator cache clearing while failing safely without interrupting workflows.
  - **Smart LM and progress titles:** Preserve compact Smart LM Loader height across reloads and restore serialized custom or schema display names after progress updates in Smart LM and folder batch loaders.
  - **Image Convert:** Match the V3 `remove_alpha` input keyword and defer optional pilgram dependency failures until a style filter is selected.
  - **Image Comparer:** Replace overflowing A/B batch labels with a responsive matching-pair navigator that remains clear of sockets and clickable in both renderers.

- **Perf**
  - **Nodes 2.0 low-zoom rendering:** Add enabled-by-default, configurable detail reduction that preserves titles, shells, sockets, links, execution indicators, and layout while suppressing expensive controls and previews below the cutoff.
  - **Bounded I/O:** Stream image transfers within the 100 MB limit, reuse timed Smart LM responses, and move CivitAI resolution/hash work and audio slicing to bounded worker tasks.
  - **Save Video:** Reuse homogeneous input batches during loop processing instead of concatenating duplicate full-size tensors.

- **Docs**
  - **RIFE Multiplier:** Clarify that interpolation targets should be at least twice the source FPS because lower targets may round to an unsupported 1× no-op.

**Changed files:**
- `.defaults/config.json.example`
- `core/common.py`
- `core/network_security.py` (new)
- `core/nunchaku_wrapper.py`
- `core/server_endpoints.py`
- `core/sml/model_files.py`
- `core/sml/server_endpoints.py`
- `core/sml/vlm_detection.py`
- `js/eclipse-combo-chip.js`
- `js/eclipse-context-menu-utils.js` (new)
- `js/eclipse-conversion-nodes.js`
- `js/eclipse-dom-preview-nodes.js`
- `js/eclipse-dom-preview.js`
- `js/eclipse-dynamic-inputs.js`
- `js/eclipse-getallactive.js`
- `js/eclipse-getfirst.js`
- `js/eclipse-image-comparer.js`
- `js/eclipse-image-selector.js`
- `js/eclipse-load-image-folder.js`
- `js/eclipse-load-image.js`
- `js/eclipse-mode-nodes.js`
- `js/eclipse-node-size-fix.js`
- `js/eclipse-seed-utils.js`
- `js/eclipse-seed.js`
- `js/eclipse-set-get.js`
- `js/eclipse-smart-folder.js`
- `js/eclipse-smart-model-loader.js`
- `js/eclipse-smart-prompt-v2.js`
- `js/eclipse-smart-prompt.js`
- `js/eclipse-smart-sampler-settings.js`
- `js/eclipse-sml-loader.js`
- `js/eclipse-ui-enhancements.js`
- `js/eclipse-video-preview-common.js`
- `js/eclipse-wildcard-processor.js`
- `py/RvConversion_ConvertPrimitive.py`
- `py/RvConversion_ImageConvert.py`
- `py/RvConversion_IndicesToList.py` (new)
- `py/RvConversion_RIFEMultiplier.py`
- `py/RvImage_ColorMatch.py`
- `py/RvImage_Comparer.py`
- `py/RvImage_LoadBatchFromFolder.py`
- `py/RvImage_LoadBatchFromFolderStepAdvanced.py`
- `py/RvImage_LoopImageSelector.py`
- `py/RvImage_Resize.py`
- `py/RvLoader_SmartDetection.py`
- `py/RvLoader_SmartModelLoader_LM.py`
- `py/RvText_FilterPrompt.py`
- `py/RvVideo_Save.py`
- `py/RvVideo_WanLipSyncPlanStep.py` (new)
- `py/RvVideo_WanLipSyncTimelinePlanner.py` (new)
- `py/_legacy/legacy_LoadBatchFromFolderAdvanced.py`
- `pyproject.toml`

---

## 2026-07-07

### Version: 4.1.0

- **Feat (New)**
  - **Image Resolution Pipes:** Added `Image Resolution Pipe` and `Image Resolution Simple Pipe` nodes for passing custom resolution and batch settings through workflows.
  - **List/Batch Conversion:** Added MatchType-based `To List` and `To Batch` nodes for converting and consolidating list and batch inputs.
  - **Load Batch From Folder (Step Advanced):** Added advanced resizing, sorting, paged `batch_index` execution, automatic queue advancement, and source-file list output.
  - **IO Slice & Dice:** Added direct index-based slicing for connected text, conditioning, latent, image, and list inputs, with the selected indices passed downstream.
  - **Filter Prompt:** Added tag, string, parenthesis, and wildcard-expression filtering while preserving the remaining prompt formatting.
  - **Image Batch Extend With RIFE:** Added an experimental gradient-MSE pyramid transition node with optional RIFE replacement of center frames and safe fallback behavior.

- **Feat**
  - **Convert Primitive / To List:** Added recursive list and tuple flattening, element-wise scalar conversion, boolean numeric fallbacks, and newline/comma string splitting.
  - **Image Selector:** Added an `indices` output, an "All" action, stacked status controls, stable in-memory preview caching, and improved selection reset behavior.
  - **Routers and Passers:** Added Generic MatchType and native list-wise execution across switches, conditionals, and pass-through nodes, including safe `[None]` fallbacks.
  - **List/Batch Processing:** Added native list and batch handling across image transforms, loaders, video nodes, tile nodes, batch operations, and text deduplication.
  - **Save Prompt:** Added list-wise execution and source-folder-anchored output paths while retaining containment for ordinary relative paths.
  - **Smart LM:** Added loader-template core-model deletion, newline-only Prompt Variations output, batch and WD14 progress reporting, title updates, and automatic WD14 layout resizing.
  - **Workflow Utilities:** Added multi-slot reordering menus to GetAllActive/GetFirst, batch-frame support to Tile Split, and explicit camera-angle guidance to vision prompts.

- **Fix**
  - **Tile Assembly:** Corrected batch/list reconstruction in Tile Assembly and Tile Decode & Assembly with native list input/output handling.
  - **Image Selector:** Corrected one-image-per-row and fixed-column layouts, resize loops, nested batch extraction, stale selections, fingerprint instability, and preview flashing after discard/reset.
  - **Folder Loaders:** Added memory caches tied to file-list invalidation and restored clean titles after interrupted scanning, loading, or resizing.
  - **WD14:** Added persistent CPU fallback for CUDA/cuDNN failures so batch tagging does not repeatedly retry a failing GPU provider.
  - **Save Prompt:** Handled `None` and empty text inputs and corrected `%source_base_folder` resolution when using `filename_opt` without a pipe.
  - **IO Slice & Dice:** Corrected recursive flattening, batch-size alignment, conditioning slicing, image/image-list cross-population, and mixed-size batch fitting.
  - **List/Batch Output Protocol:** Centralized list-wrapped outputs to prevent ComfyUI V3 from auto-slicing 4D tensors into invalid 3D downstream inputs; updated Image Resize accordingly.
  - **Convert Primitive:** Corrected multi-element tensor boolean conversion and added graceful fallbacks for conversion errors.
  - **Smart LM and Migration:** Restored clean node titles after interruption/errors and stopped manually deleted defaults from being recreated on startup.
  - **Save Images:** Added safe nested `filename_prefix` folders while keeping counters and workflow JSON beside the images and preventing output-path escape.
  - **Color Match:** Prevented CPU Reinhard black frames by isolating and normalizing matcher inputs, sanitizing non-finite results, and falling back to the target frame.

- **Refactor**
  - **Centralized List/Batch Helpers:** Added shared unwrapping, flattening, size fitting, batch detection, and output preparation utilities and adopted them across the node pack.
  - **Type Handling:** Modernized dynamic node types and list propagation around ComfyUI V3 MatchType.
  - **Formatting:** Reformatted the Python codebase with Black for consistent PEP 8 style.

- **Perf**
  - **Save Video:** Encodes flattened frame views directly, defers resizing until encoding, avoids copying unchanged single batches, and evaluates loop candidates in bounded inference chunks.
  - **Folder Loaders:** Reads dimensions from headers, downsizes with PIL before tensor conversion, and reuses decoded images from memory caches.
  - **Image Loaders:** Returns native image/mask lists to avoid large contiguous RAM/VRAM allocations.

- **Chore**
  - Moved superseded Convert To List, Convert To Batch, and Load Batch From Folder (Advanced) implementations into the legacy module.

- **Breaking**
  - **Smart LM Loader:** Removed the `exclude_tags` widget and backend filter in favor of Filter Prompt, and moved `seed` below `replace_underscore`.
  - **Image Selector:** Replaced the legacy secondary list output with the `indices` output used by IO Slice & Dice.

**Deprecated nodes:**
- Convert To List [Eclipse]
- Convert To Batch [Eclipse]
- Load Batch From Folder (Advanced) [Eclipse]

**Changed files:**
- `.defaults/config/llm_few_shot_training.json.example`
- `.defaults/config/llm_few_shot_training_nsfw.json.example`
- `.defaults/config/system_prompts.json.example`
- `.defaults/registry/defaults.json.example`
- `core/file_cache.py`
- `core/image_helpers.py`
- `core/migration.py`
- `core/server_endpoints.py`
- `core/sml/backend_wd14.py`
- `core/sml/model_registry.py`
- `js/eclipse-getallactive.js`
- `js/eclipse-getfirst.js`
- `js/eclipse-image-resolution.js`
- `js/eclipse-image-selector.js`
- `js/eclipse-load-batch-from-folder-step-advanced.js`
- `js/eclipse-smart-model-loader.js`
- `js/eclipse-sml-loader.js`
- `py/RvConversion_ConvertPrimitive.py`
- `py/RvConversion_DetectionToBboxes.py`
- `py/RvConversion_ToBatch.py`
- `py/RvConversion_ToList.py`
- `py/RvImage_BatchExtendWithOverlap.py`
- `py/RvImage_BatchExtendWithRife.py`
- `py/RvImage_BatchInterleave.py`
- `py/RvImage_BatchSlice.py`
- `py/RvImage_BatchStrip.py`
- `py/RvImage_ColorMatch.py`
- `py/RvImage_Comparer.py`
- `py/RvImage_CropByMask.py`
- `py/RvImage_FilterAdjustments.py`
- `py/RvImage_FilterAdjustmentsAdvanced.py`
- `py/RvImage_GetFirst.py`
- `py/RvImage_GetLast.py`
- `py/RvImage_InsetCrop.py`
- `py/RvImage_LoadBatchFromFolder.py`
- `py/RvImage_LoadBatchFromFolderStepAdvanced.py`
- `py/RvImage_Rescale.py`
- `py/RvImage_Resize.py`
- `py/RvImage_Save.py`
- `py/RvImage_Selector.py`
- `py/RvImage_Soften.py`
- `py/RvImage_TileAssembly.py`
- `py/RvImage_TileDecodeAssembly.py`
- `py/RvImage_TileSplit.py`
- `py/RvImage_UpscaleWithModel.py`
- `py/RvImage_UpscaleWithModel_v2.py`
- `py/RvLoader_SmartDetection.py`
- `py/RvLoader_SmartModelLoader_LM.py`
- `py/RvPipe_IO_SliceDice.py`
- `py/RvRouter_Any_DualSwitch.py`
- `py/RvRouter_Any_DualSwitch_purge.py`
- `py/RvRouter_Any_MultiSwitch.py`
- `py/RvRouter_Any_MultiSwitch_lazy.py`
- `py/RvRouter_Any_MultiSwitch_lazy_purge.py`
- `py/RvRouter_Any_MultiSwitch_purge.py`
- `py/RvRouter_Any_Passer.py`
- `py/RvRouter_Any_Passer_purge.py`
- `py/RvSettings_Image_ResolutionPipe.py`
- `py/RvSettings_Image_ResolutionSimplePipe.py`
- `py/RvText_DeDuplicate.py`
- `py/RvText_FilterPrompt.py`
- `py/RvText_SavePrompt.py`
- `py/RvVideo_FrameConsistency.py`
- `py/RvVideo_Preview.py`
- `py/RvVideo_Save.py`
- `py/RvVideo_TrimToShortest.py`
- `py/_legacy/legacy_ConvertToBatch.py`
- `py/_legacy/legacy_ConvertToList.py`
- `py/_legacy/legacy_LoadBatchFromFolderAdvanced.py`
- `pyproject.toml`
- `core/`, `extern/`, and `py/` Python sources reformatted by Black

---

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
