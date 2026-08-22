# Changelog

All notable changes to ComfyUI Eclipse are documented in this file.

Entries follow conventional commit prefixes:

## 2026-08-22

### Version: 4.3.8

- **Feat**
  - **Configurable chip accent:** Add an Eclipse-owned color picker that persists a validated hexadecimal accent in private `config.json` and applies derived hover, border, trigger, and contrast colors immediately to shared combo chips plus the standalone LoRA Stack and direct Load Image source-mode chips.
  - **Aligned chip popovers:** Size shared combo-chip popovers to the rendered trigger-bar width while preserving every chip's intrinsic dimensions.
  - **Image grid picker:** Replace the visible filename combos and standalone upload, refresh, and delete controls on both direct Load Image nodes with an Eclipse-owned, renderer-independent browser featuring a viewport-clamped resizable popover, virtualized lazy grid/list views, filename search and ordering, keyboard navigation, persistent layout, ordering, and popover-size preferences, multi-upload with partial-failure reporting, deterministic deletion, and complete lifecycle cleanup while retaining the original serialized backing values, source modes, previews, drag/drop, clipboard, and Mask Editor behavior.

**Changed files:**
- `.defaults/config.json.example`
- `.defaults/.manifest.json`
- `core/common.py`
- `core/config_store.py`
- `core/server_endpoints.py`
- `js/eclipse-combo-chip.js`
- `js/eclipse-image-browser.js` (new)
- `js/eclipse-load-image.js`
- `js/eclipse-lora-stack.js`
- `js/eclipse-ui-enhancements.js`
- `pyproject.toml`
- `tests/test_chip_color_config.py` (new)
- `tools/settings-independence-harness.mjs`
- `tools/eclipse-image-browser-harness.mjs` (new)
- `tools/eclipse-image-browser-playwright.config.ts` (new)
- `tools/eclipse-image-browser.spec.ts` (new)

## 2026-08-19

### Version: 4.3.7

- **Refactor**
  - **Smart detection postprocessor extraction:** Move Detection to Bboxes and its conditional-widget frontend to `ComfyUI_SmartLLM`, preserving its `[Eclipse]` ID, schema, list semantics, mask/bbox outputs, and workflow serialization.

- **Docs**
  - Point Detection to Bboxes to SmartLLM alongside the Smart Detection node whose structured output it consumes.

**Changed files:**
- `js/eclipse-detection-to-bboxes.js` (removed)
- `py/RvConversion_DetectionToBboxes.py` (removed)
- `README.md`
- `Readme/README.md`
- `pyproject.toml`

### Version: 4.3.6

- **Refactor**
  - **Complete pipeline extraction:** Move CLIP Text Encode, CLIP Text Encode (Advanced), Conditioning Zero Out, IO Checkpoint Loader, and Eclipse KSampler (Pipe) to `ComfyUI_SmartModelLoader` while preserving their exact `[Eclipse]` IDs, schemas, categories, pipe contracts, and workflow serialization.
  - **Single frontend ownership:** Remove the Advanced encoder extension and stop Eclipse seed/preview hooks from targeting the transferred pipe sampler, preventing double wrapping when both packs are installed.

- **Docs**
  - Point the transferred conditioning, pipe IO, and sampler nodes to their standalone provider while retaining Eclipse's generic pipe toolkit, Conditioning Passer, Kargim sampler, resolution pipes, and workflow utilities.

**Changed files:**
- `js/eclipse-clip-text-encode-advanced.js` (removed)
- `js/eclipse-seed.js`
- `js/eclipse-sampler-tiled-decode.js`
- `py/RvCond_CLIPTextEncode.py` (removed)
- `py/RvCond_CLIPTextEncodeAdvanced.py` (removed)
- `py/RvCond_ConditioningZeroOut.py` (removed)
- `py/RvPipe_IO_CheckpointLoader.py` (removed)
- `py/RvSampler_KSamplerPipe.py` (removed)
- `README.md`
- `pyproject.toml`

## 2026-08-17

### Version: 4.3.5

- **Fix**
  - **Smart-resize subgraph and renderer transitions:** Restart the existing visibility-aware smart resize when graphs or renderers change, cancel work started under the prior strategy, refresh Nodes 2.0 mount observation and cached elements, query exact escaped node IDs, reject DOM decoys, prefer the newest valid remount, and verify logical and CSS dimensions through the asynchronous layout-store settling window; this prevents external Smart Model Loader nodes and other conditional-widget nodes from reverting to oversized or cross-renderer geometry after navigation and live Nodes 2.0/classic switches.
  - **Hidden widget slot lifecycle:** Install classic drag-targeting guards even when a visibility manager is created in Nodes 2.0, synchronize hidden slot state in both renderers, disconnect every linked slot in one synchronous user-driven batch, restore prior draw state, and clean up temporary auto-target markers even when LiteGraph throws.
  - **Settings initialization safety:** Hydrate Eclipse defaults from its own redacted config endpoint and suppress ComfyUI's automatic first change callback so persisted frontend state cannot write stale values back to Eclipse configuration.
  - **Nodes 2.0 classic group menus:** Route group right-clicks through the complete classic canvas menu, retaining its existing group mode, fit, selection, extension, and third-party actions plus the standard `Edit Group` submenu, while preserving reroute and empty-canvas behavior.
  - **Explicit custom-color labels:** Rename the custom node color actions to identify title, background, and combined title/background colors clearly in both classic and Nodes 2.0 menus.
  - **Eclipse-only data reset:** Restrict both reset scripts to Eclipse-owned prompts, patterns, styles, wildcards, and root configuration so they no longer delete or promise to re-extract companion-owned templates, registries, LLM configuration, or Docker state.

- **Perf**
  - **Smart-resize lifecycle scheduling:** Track outstanding resize demand so renderer switches resume only interrupted or pending nodes instead of recomputing every stable registration, preserve settled geometry through renderer-owned layout-store writes with a bounded cached-geometry check that performs no size computation or explicit dirty call, and retain full active-graph correction for graph returns; coalesce overlapping graph events, merge graph and remount restarts, cancel inactive-graph probes, share post-apply verification across nodes, reuse stabilized measurements, and skip no-op CSS writes and canvas invalidation while retaining four-frame verification and the 60-frame safety bound.

- **Refactor**
  - **Standalone diffusion-loader pack:** Move Smart Model Loader, Model Loader, Model Loader Pipe, CLIP Loader, VAE Loader, VAE Loader Video+Audio, their templates, GGUF support, verified CivitAI acquisition, and the Download Manager into `ComfyUI_SmartModelLoader` while preserving the six `[Eclipse]` workflow node IDs and bundle schema compatibility.
  - **Standalone SmartLLM pack:** Move Smart LM Loader, Smart Detection, the Registry Manager, native and container backends, model acquisition and integrity, defaults, configuration, security, Docker tooling, and Florence-2 support into `ComfyUI_SmartLLM` 1.0.0 while preserving both `[Eclipse]` node IDs, schemas, and the `/smartlml` API contract.
  - **Neutral retained infrastructure:** Relocate Universal Block Swap to `core/blockswap.py`, retain only Image Save's required hashing helpers in `core/model_integrity.py`, preserve LoRA Stack's Nunchaku support, and keep Load Audio and cross-pack detection, image-selection, preview, and seed integrations in Eclipse.
  - **Confirmed config extraction cleanup:** Remove loader-owned fields only after value-free companion migration markers confirm examination, require both Smart Model Loader and SmartLLM before removing shared token and retry fields, delete matching comments in the same private atomic transaction, and retain all Eclipse-owned and unrelated values.

- **Docs**
  - **External loader provider:** Point Eclipse users to `ComfyUI_SmartModelLoader` for the extracted nodes, loader guides, templates, security policy, and Download Manager.
  - **External SmartLLM provider:** Point users to `ComfyUI_SmartLLM` for Smart LM Loader, Smart Detection, the Registry Manager, backend setup, Docker installation, migration, and security documentation.
  - **Independent settings ownership:** Document the separate `Eclipse`, `Smart Model Loader`, and `Smart LM Loader` categories, config files, credentials, endpoints, log levels, and retry policies.
  - **SmartLLM registry-reference ownership:** Remove Eclipse's remaining copy-paste model registry guide after transferring it to `ComfyUI_SmartLLM`, and correct Eclipse folder and file-location documentation to name the owning companion pack.

- **Chore**
  - **Loader-only cleanup:** Remove loader and Download Manager registration, routes, settings, migrations, frontend assets, defaults, GGUF vendor code, and the unused `gguf` dependency from Eclipse without deleting existing ignored user configuration, templates, queue state, or partial downloads.
  - **SmartLLM cleanup:** Remove Smart LM registration and startup, `/smartlml` routes, settings, legacy migrations, frontend assets, defaults, registries, Docker scripts, Florence-2 vendor code, audit tooling, and SmartLLM-only dependencies without deleting ignored user configuration, registry edits, prompts, Docker mappings, model files, provenance, partial downloads, or existing links.
  - **Legacy extracted credential cleanup:** Remove the former Smart LM Gemini API key and its description from Eclipse defaults and runtime configuration.

- **Breaking**
  - **Loader and Download Manager extraction:** Eclipse no longer supplies the six diffusion loader nodes or Download Manager. Install `ComfyUI_SmartModelLoader`; external HTTP clients must migrate from Eclipse-owned routes and events to `/smart-model-loader/...`.
  - **Smart LM and Detection extraction:** Eclipse no longer registers Smart LM Loader, Smart Detection, the Registry Manager, or `/smartlml` routes. Install `ComfyUI_SmartLLM`; saved workflows continue to resolve the unchanged `[Eclipse]` node IDs through that pack.

**Changed files:**
- `.defaults/config.json.example`
- `core/blockswap.py` (new)
- `core/config_store.py`
- `core/migration.py`
- `core/model_integrity.py`
- `core/request_security.py` (new)
- `core/server_endpoints.py`
- `core/civitai_client.py`, `core/loader_templates.py`, `core/model_loader_common.py`, `core/gguf_wrapper.py` (removed)
- `core/download_manager/*`, `core/model_loader/*`, `extern/gguf/*` (removed)
- `core/sml/*`, `extern/florence2/*` (removed)
- `js/eclipse-widget-performance-utils.js`
- `js/eclipse-vue-classic-node-context-menu.js`
- `js/eclipse-ui-enhancements.js`
- `js/eclipse-clip-loader.js`, `js/eclipse-download-manager.js`, `js/eclipse-loader-shared.js`, `js/eclipse-model-loader.js`, `js/eclipse-smart-model-loader-options.js`, `js/eclipse-smart-model-loader.js` (removed)
- `js/eclipse-sml-detection.js`, `js/eclipse-sml-loader.js`, `js/eclipse-sml-registry-events.js`, `js/eclipse-sml-registry-manager.js` (removed)
- `py/RvTools_BlockSwap.py`
- `py/RvLoader_ClipLoader.py`, `py/RvLoader_ModelLoader.py`, `py/RvLoader_ModelLoaderPipe.py`, `py/RvLoader_SmartModelLoader.py`, `py/RvLoader_VaeLoader.py`, `py/RvLoader_VaeLoaderVideoAudio.py` (removed)
- `py/RvLoader_SmartModelLoader_LM.py`, `py/RvLoader_SmartDetection.py` (removed)
- `Readme/Checkpoint_Loaders.md`, `Readme/Download_Manager.md`, `Readme/Model_Loader_Security.md`, `Readme/Smart_Loaders.md` (removed)
- `Readme/Smart_LM_Loader_Guide.md`, `Readme/Smart_Detection_Guide.md`, `Readme/LLM_Security_Warning.md`, `Readme/Docker_Installation_Guide*.md`, `Readme/Model_Repos_Reference_Links.md`, `Readme/Model_Repos_Reference_CP.md` (removed)
- `Readme/GetFirst_GetAllActive.md`, `Readme/Nunchaku_Installation.md`, `Readme/Replace_String_v3.md`, `Readme/Set_Get_Bridge.md`, `Readme/Smart_Folder.md`, `Readme/Smart_Prompt.md`, `Readme/Smart_Sampler_Settings_v2.md`, `Readme/Utility_Nodes.md`
- `.defaults/config/*`, `.defaults/registry/*`, `.defaults/templates/*`, `.defaults/docker_config.json.example` (removed)
- `scripts/install-docker-engine.sh`, `scripts/manage-docker-images.sh`, `scripts/remove-docker-nvidia.sh` (removed)
- `scripts/comfyui_symlinks.sh`
- `scripts/reset and re-extract repo files .bat`
- `scripts/reset and re-extract repo files .sh`
- `workflows/Detection.json`, `workflows/i2p_detection.json` (removed)
- `README.md`
- `Readme/README.md`
- `pyproject.toml`
- `requirements.txt`

---

### Version: 4.3.4

- **Feat**
  - **Image Convert alpha compositing and category:** Replace the redundant remove-alpha toggle with optional straight-alpha compositing onto a background color selected through matching classic and Nodes 2.0 native pickers with a roomier rounded Vue swatch, visibly normalize styles—including `none`—shifted by the new widget position when loading older workflows, leave non-RGBA inputs unchanged, and move the node from Conversion to Image/FX & Color.

- **Fix**
  - **Nodes 2.0 initial preview sizing:** Give freshly created dedicated image, video, audio, text, SEGS, optional wildcard, and unconstrained MatchType display outputs—including both Show Any variants—a 225 × 225 minimum expanded model size through Vue's initial settling frames while preserving larger dimensions, contained DOM widgets, classic canvas behavior, subgraph hosts, and every configured, cloned, or later manually resized size.

**Changed files:**
- `js/eclipse-conversion-nodes.js`
- `js/eclipse-node-size-fix.js`
- `py/RvConversion_ImageConvert.py`
- `pyproject.toml`

---

### Version: 4.3.3

- **Feat**
  - **Subgraph DOM previews:** Add a persistent `Eclipse -> DOM previews` submenu for exposing independent image and text preview widgets directly on current ComfyUI subgraph nodes, including nested subgraphs and classic or Nodes 2 rendering.

- **Fix**
  - **Instance-correct preview updates:** Route executed output through colon-delimited display-node paths and propagate manual preview changes one subgraph boundary at a time so multiple instances of one definition remain independent.
  - **Direct-widget compatibility, lifecycle, sizing, and culling:** Detect whether subgraph hosts retain direct custom widgets, hide the Eclipse menu and leave saved mappings intact on unsupported frontends, rebuild exposed previews after workflow load without altering saved subgraph dimensions, exclude their values from workflow serialization, cleanly remove disabled projections, and cull host-owned DOM widgets without relying on removed promoted-widget internals.
  - **Subgraph native-image suppression:** Clear standard image output through the owning graph's locator after Eclipse routing completes and mark DOM-preview nodes as custom-rendered, preventing the native background/media preview from appearing beside the Eclipse widget inside subgraphs.
  - **Frontend compatibility:** The new direct subgraph DOM-preview experience requires ComfyUI frontend 1.51.2 or newer; older frontends remain supported through capability-detected fallbacks, including legacy promoted previews where available, while unsupported direct-widget controls stay hidden and saved mappings are preserved.

**Changed files:**
- `js/eclipse-dom-preview-nodes.js`
- `js/eclipse-dom-preview.js`
- `js/eclipse-dom-text.js` (new)
- `js/eclipse-preview-culling.js`
- `js/eclipse-show-any.js`
- `js/eclipse-show-text.js`
- `js/eclipse-subgraph-dom-previews.js` (new)
- `pyproject.toml`

---

## 2026-08-12

### Version: 4.3.2

- **Feat (New)**
  - **Standalone Download Manager (Beta):** Add a separate CivitAI and Hugging Face inspection modal with its own semantic paged data grid, responsive and desktop-resizable styles, state, APIs, persistence, progress events, left-toolbar launcher, Eclipse menu command, and classic-menu button while leaving the Smart LM Registry Editor unchanged.
  - **Persistent verified transfer queue:** Add and import jobs in a manual-start ready state; provide selected start and inactive-record removal controls; process started transfers one at a time; atomically persist immutable provider identity, registered destinations, provider and local digests, conflicts, progress, timestamps, and sanitized failures; recover interrupted jobs; retain cancelled partials for validated retries with an explicit destination-locked Delete Partial action; and allow cancellation only before non-abortable hashing, verification, locking, and promotion.
  - **Download bundles:** Preserve provider provenance, expected and local digests, destination assignments, and conflict policies in portable bundles without coupling the Download Manager to Smart Model Loader templates.

- **Feat**
  - **Immutable provider inspection:** List every CivitAI version file from AIR, exact AIR, SHA-256, model-version URL, or download URL; suggest identity-verified author filenames with precision/file-ID collision fallbacks while preserving REST filenames; and page immutable Hugging Face repository/file results resolved to full commits with LFS/Xet SHA-256 or regular Git blob identity.
  - **Live destination policy:** Populate categories and roots from ComfyUI's registered model folders; collapse identical root registrations; distinguish same-named roots without exposing absolute paths; provide metadata and filename suggestions with ambiguity confirmation; and support search, sorting, filtered select-all, supported-file toggles, selected-rows-only bulk assignment with accessible flow guidance, and per-row overrides.
  - **Provider-neutral verified acquisition:** Extend the hardened transactional transfer primitive to verify regular Hugging Face Git blob identities without changing existing CivitAI SHA-256 behavior, cancellation timing, containment, resume validation, destination locking, or atomic promotion.
  - **Download Manager trust boundary:** Reject arbitrary roots, absolute paths, traversal, symlinks, incompatible extensions, unverifiable files, oversized requests, cross-origin mutations, and remote multi-user global access; exclude credentials, headers, and signed URLs from queue and bundle persistence.

- **Docs**
  - **Download Manager guide and research:** Document launch surfaces, provider locators, immutable identity, format exceptions, queue recovery, cancellation, bundles, and the validation boundary.

**Changed files:**
- `core/civitai_client.py`
- `core/download_manager/endpoints.py` (new)
- `core/download_manager/manager.py` (new)
- `core/download_manager/providers.py` (new)
- `core/model_loader/acquisition.py`
- `js/eclipse-download-manager.js` (new)
- `pyproject.toml`
- `README.md`
- `Readme/Download_Manager.md` (new)
- `Readme/README.md`

---

## 2026-08-11

### Version: 4.3.1

- **Feat**
  - **Secure diffusion model loading:** Resolve checkpoints, UNets, GGUF models, CLIP, VAE, audio VAE, and LoRAs through their declared ComfyUI folder roles; allow configured role roots to resolve through symlinks while rejecting traversal, symlinked files or subfolders beneath those roots, unsupported files, malformed workflow values, and unsafe component settings before cleanup or deserialization while retaining baked CLIP/VAE/audio-VAE compatibility for supported UNet files.
  - **Administrator-local legacy-format policy:** Deny pickle-capable .ckpt, .pt, .pth, and .bin artifacts by default and add Eclipse.AllowLegacyModelFormats as an immediate local-only override that is never stored in workflows or templates.
  - **Model-aware download precision:** Expose the complete CivitAI precision and GGUF quantization superset while dynamically showing only standard precisions or GGUF Q/IQ/TQ choices for the selected model type.
  - **Transfer-only download abort:** Reuse the CivitAI download button as a percentage-preserving abort control during network transfer, then close cancellation before hashing, verification, locking, and promotion while showing live in-place transfer, hashing, and verification percentages plus phase status in the node and console.

- **Fix**
  - **Verified integrity and CivitAI acquisition:** Replace timestamp trust with stable versioned SHA-256 metadata, serialize concurrent transfers per canonical destination, compare the primary selected model against trusted hashes when available while allowing metadata-free local models to load after recording a baseline, retain unconditional auxiliary-component path and format validation, validate AIR model/version/file identities, constrain DNS and redirects to public addresses, enforce strict resume and resource limits, and atomically promote only verified downloads.
  - **Transactional persistence and maintenance:** Group persistent JSON coordination anchors under private per-directory `.locks` folders; lock, flush, fsync, and atomically replace template and integrity JSON while preserving malformed files; hold ComfyUI's queue mutex during destructive maintenance, apply model-format validation to exact template-selected deletion targets, remove canonical and legacy sidecars, and retain tombstone rollback.
  - **Hardened model-loader endpoints:** Keep existing URLs while bounding JSON-object requests, strictly validating destructive booleans, rejecting cross-origin mutations, limiting multi-user global changes to loopback clients, offloading blocking model I/O, sanitizing failures, and reconciling audio_vae across frontend and backend feature definitions.
  - **Role-correct CivitAI file selection:** Select by target role before exact identity, precision, or primary status; support checkpoint-classified standalone diffusion variants such as Z-Image while model roles still exclude auxiliary files, component roles remain isolated, canonical AIR types override stale input tokens, `default` retains the primary artifact while an explicit precision selects a unique largest match, exact `+fileId` AIR identities survive persistence, and same-precision filename collisions gain a stable CivitAI file-ID suffix without changing hash verification.
  - **Filename-free template metadata:** Rebuild locator and expected-hash metadata from the visible AIR/SHA editor when saving a template under a new name, remove cleared inherited metadata, and consume only the locator used by a verified download.
  - **General settings category:** Rename the visible `Eclipse → Generic` subsection to `Eclipse → General` across general controls and Groups Panel sorting while preserving all setting IDs and stored values.

- **Refactor**
  - **Shared model-loader core:** Consolidate validation, loading, pipes, BlockSwap, templates, integrity, acquisition, lifecycle, and endpoint helpers under core/model_loader while retaining compatibility re-exports and unchanged node IDs, sockets, pipe keys, templates, and workflow serialization.

- **Docs**
  - **Diffusion loader security guide:** Document safe formats, integrity provenance, CivitAI and endpoint boundaries, the threat model, and a clearly opt-in real-model compatibility ledger.

**Changed files:**
- `.defaults/config.json.example`
- `core/civitai_client.py`
- `core/config_store.py`
- `core/json_store.py` (new)
- `core/loader_templates.py`
- `core/model_integrity.py`
- `core/model_loader/blockswap.py` (new)
- `core/model_loader/acquisition.py` (new)
- `core/model_loader/endpoints.py` (new)
- `core/model_loader/integrity.py` (new)
- `core/model_loader/lifecycle.py` (new)
- `core/model_loader/loading.py` (new)
- `core/model_loader/pipes.py` (new)
- `core/model_loader/progress.py` (new)
- `core/model_loader/smart.py` (new)
- `core/model_loader/templates.py` (new)
- `core/model_loader/validation.py` (new)
- `core/model_loader_common.py`
- `core/server_endpoints.py`
- `core/sml/json_store.py`
- `js/eclipse-groups-panel.js`
- `js/eclipse-smart-model-loader.js`
- `js/eclipse-smart-model-loader-options.js` (new)
- `js/eclipse-ui-enhancements.js`
- `py/RvLoader_ModelLoader.py`
- `py/RvLoader_ModelLoaderPipe.py`
- `py/RvLoader_SmartModelLoader.py`
- `py/RvLoader_VaeLoader.py`
- `py/RvLoader_VaeLoaderVideoAudio.py`
- `py/RvTools_BlockSwap.py`
- `pyproject.toml`
- `README.md`
- `Readme/Model_Loader_Security.md` (new)

---

### Version: 4.3.0

- **Feat (New)**
  - **Smart LM Registry Editor (Beta):** Add a guided interface for inspecting, adding, editing, downloading, verifying, and removing registry entries or local model files across all supported Smart LM backends.

- **Feat**
  - **Expanded Smart LM backend support:** Add explicit llama.cpp registry routing, native vLLM entries, corrected Linux Docker vLLM routing, and new curated llama.cpp/GGUF models with verified metadata.

- **Fix**
  - **Verified model acquisition and integrity:** Pin supported artifacts to immutable upstream revisions and exact SHA-256 hashes; verify downloads, repairs, and existing local files; recover interrupted repositories; preserve resumable progress; and prevent incomplete or corrupted models from being treated as usable.
  - **Safer checkpoint loading:** Restrict YOLO and legacy Florence checkpoints to reviewed weights-only loading, validate Florence shard completeness and model state, and require immutable pins before registry-provided remote code can run.
  - **Hardened Docker backends:** Pin managed images by release tag and digest, recreate containers when effective settings change, use read-only model mounts and stronger isolation, and remove shell command construction from Ollama imports.
  - **More reliable backend execution:** Correct vLLM, SGLang, llama.cpp, and Ollama routing, startup, reuse, quantization, parallelism, compile-mode, NVIDIA-driver, transport-error, and diagnostic handling.
  - **Accurate device and load planning:** Make Transformers, Florence, and WD14 honor effective device, precision, attention, memory, fallback, and cache identity instead of silently reusing incompatible state.
  - **Exception-safe lifecycle and maintenance:** Guarantee cleanup after successful or failed execution, preserve the original error when cleanup also fails, and prevent verification or deletion from racing active model use.
  - **Secure endpoints and persistence:** Reject cross-origin mutations, restrict global multi-user changes to loopback clients, bound request bodies, protect credentials, and atomically persist configuration, registry, provenance, and Docker state without losing concurrent updates.
  - **Transactional model preparation:** Make Mistral conversion explicit and recoverable, with resource preflight, validated temporary outputs, atomic completion, and interrupted-output quarantine.
  - **Output cleanup and privacy:** Apply one generated-text cleanup contract across backends and keep prompts, responses, backend bodies, images, and credentials out of normal logs.

- **Docs**
  - **Smart LM security and setup guides:** Document the registry editor, verified downloads, Docker isolation, credential handling, backend requirements, and remaining trust boundaries.

**Changed files:**
- `.defaults/docker_config.json.example`
- `.defaults/registry/llamacpp_models.json.example` (new)
- `.defaults/registry/sglang_models.json.example`
- `.defaults/registry/transformers_models.json.example`
- `.defaults/registry/vllm_models.json.example`
- `.defaults/registry/vllm_native_models.json.example` (new)
- `.defaults/registry/yolo_models.json.example`
- `core/config_store.py` (new)
- `core/common.py`
- `core/logger.py`
- `core/migration.py`
- `core/sml/backend_gguf.py`
- `core/sml/backend_llamacpp_docker.py`
- `core/sml/backend_ollama_docker.py`
- `core/sml/backend_sglang_docker.py`
- `core/sml/backend_transformers.py`
- `core/sml/backend_vllm_docker.py`
- `core/sml/backend_vllm_native.py`
- `core/sml/backend_wd14.py`
- `core/sml/backend_yolo.py`
- `core/sml/common.py`
- `core/sml/config_templates.py`
- `core/sml/container_spec.py` (new)
- `core/sml/credentials.py` (new)
- `core/sml/device.py`
- `core/sml/docker_error_handler.py`
- `core/sml/docker_image_policy.py` (new)
- `core/sml/florence2_wrapper.py`
- `core/sml/json_store.py` (new)
- `core/sml/lifecycle.py` (new)
- `core/sml/loader_base.py`
- `core/sml/mistral_weight_converter.py`
- `core/sml/model_acquisition.py` (new)
- `core/sml/model_cache.py`
- `core/sml/model_files.py`
- `core/sml/model_registry.py`
- `core/sml/model_types.py`
- `core/sml/server_endpoints.py`
- `core/sml/vlm_loader.py`
- `js/eclipse-sml-detection.js`
- `js/eclipse-sml-loader.js`
- `js/eclipse-sml-registry-events.js` (new)
- `js/eclipse-sml-registry-manager.js` (new)
- `py/RvLoader_SmartDetection.py`
- `py/RvLoader_SmartModelLoader_LM.py`
- `pyproject.toml`
- `Readme/Docker_Installation_Guide.md`
- `Readme/Docker_Installation_Guide_Linux.md`
- `Readme/LLM_Security_Warning.md`
- `Readme/Smart_LM_Loader_Guide.md`
- `scripts/manage-docker-images.sh`
---

### Version: 4.2.9

- **Fix**
  - **Compact workflow IDs:** Preserve every serialized node width and height across ID compaction and rollback in classic canvas, Nodes 2.0, and nested subgraphs, including batched frontend layout writebacks that continue after large workflows finish reconfiguring.

**Changed files:**
- `js/eclipse-workflow-compact-ids.js`
- `pyproject.toml`

---

## 2026-08-02

### Version: 4.2.8

- **Refactor**
  - **Nodes 2.0 viewport virtualization:** Remove the Eclipse-side render-control implementation and defer virtualization to the direct frontend implementation.

- **Fix**
  - **Nodes 2.0 compact collapsed nodes:** Reapply fallback compact geometry, expanded-width caps, resize observation, logical bounds, and paint tiers whenever viewport virtualization remounts a node, while keeping expanded z-index values stable and limiting remount work to the replacement element.

**Changed files:**
- `js/eclipse-node-size-fix.js`
- `js/eclipse-vue-node-settings.js`
- `js/eclipse-vue-node-viewport-virtualization.js` (removed)
- `pyproject.toml`

---

### Version: 4.2.7

- **Feat**
  - **Fast Mode Toggle:** Use native boolean switches in classic and Nodes 2.0, normalize legacy workflow values synchronously against connected target modes, and preserve boolean state through restrictions, off-mode changes, reconnection, synchronization, and serialization.

**Changed files:**
- `js/eclipse-mode-nodes.js`
- `pyproject.toml`

---

## 2026-08-01

### Version: 4.2.6

- **Feat**
  - **Nodes 2.0 viewport virtualization:** Add opt-in, exact settled-viewport component suppression through the public frontend rendering API, with two-frame hydration, interaction-safe retention, immediate disable restoration, native-setting precedence, update deduplication, and fail-open compatibility.

- **Fix**
  - **Classic Nodes 2.0 context menus:** Preserve Eclipse-owned preview menus, browser text editing, and native audio/video menus while routing ordinary images to image-aware LiteGraph actions and retaining complete classic widget and node menus.

**Changed files:**
- `js/eclipse-context-menu-ownership.js` (new)
- `js/eclipse-load-audio.js`
- `js/eclipse-load-image.js`
- `js/eclipse-video-preview-common.js`
- `js/eclipse-vue-classic-node-context-menu.js`
- `js/eclipse-vue-node-settings.js`
- `js/eclipse-vue-node-viewport-virtualization.js` (new)
- `pyproject.toml`

---

## 2026-07-30

### Version: 4.2.5

- **Fix**
  - **Nodes 2.0 collapsed display:** Share canonical setting metadata and migration precedence, always preserve expanded dimensions on vanilla frontends, and limit compact mode to natural collapsed width, header spacing, and price/status suppression while retaining the pin.
  - **Nodes 2.0 stacking:** Match the native unselected-collapsed, expanded, and selected-collapsed paint tiers on vanilla frontends while preserving native order and pointer ownership across selection, remount, and graph-navigation changes.
  - **Nodes 2.0 badges and low detail:** Hide only Muted/Bypassed status badges, cover all native detail surfaces with layout-preserving low-detail visibility, use the strict full-detail threshold, and cleanly rebind or remove fallbacks across canvas and renderer changes.
  - **Native capability boundary:** Bypass Eclipse display fallbacks when the frontend implements them and keep viewport virtualization absent on vanilla frontends.

**Changed files:**
- `.defaults/config.json.example`
- `core/server_endpoints.py`
- `js/eclipse-node-size-fix.js`
- `js/eclipse-ui-enhancements.js`
- `js/eclipse-vue-node-settings.js` (new)
- `pyproject.toml`

---

### Version: 4.2.4

- **Fix**
  - **Eclipse KSampler previews:** Preserve authoritative live, final, and hidden phases when viewport virtualization remounts or replaces Nodes 2.0 node elements, even when raw and reactive preview caches diverge, and clear transient previews with the correct root or subgraph `NodeLocatorId` key.
  - **Dynamic widget sizing:** Reapply the current visibility-driven geometry when viewport virtualization remounts or replaces Nodes 2.0 node elements, so Smart Model Loader and other conditional-widget nodes return at the correct height after panning or offscreen changes while classic canvas behavior remains unchanged.

**Changed files:**
- `js/eclipse-sampler-tiled-decode.js`
- `js/eclipse-widget-performance-utils.js`
- `pyproject.toml`

---

## 2026-07-25

### Version: 4.2.3

- **Feat**
  - **Eclipse settings organization:** Organize all thirteen controls under `Eclipse → Generic`, `Eclipse → Nodes 2.0`, and `Eclipse → Smart LM Loader`, with simplified labels, deterministic ordering, and Groups Panel access guidance while preserving stored IDs, callbacks, and write-only token handling.
  - **Classic Nodes 2.0 context menu:** Add an opt-in, immediately applied compatibility setting that replaces the PrimeVue node and widget menu with the complete classic LiteGraph menu, including Eclipse and third-party nested entries, callbacks, separators, and disabled states.

- **Fix**
  - **Eclipse KSampler previews:** In Nodes 2.0, preserve explicit live, final, and hidden phases across root and nested-subgraph navigation, so Vue remounts and reruns cannot reveal a stale preview; successful runs show only the new final, while errors or interruptions restore the last successful output and classic canvas behavior remains unchanged.

- **Perf**
  - **Nodes 2.0 interaction:** Limit collapsed-bound correction to collapsed nodes, batch graph-navigation size synchronization, suspend classic preview-culling scans and timers in the Vue renderer, clear stale promoted-widget culling state on renderer changes, replace relational badge selectors, and skip identical native size-style writes while preserving classic culling and dynamic-widget resizing.

**Changed files:**
- `js/eclipse-groups-panel.js`
- `js/eclipse-node-size-fix.js`
- `js/eclipse-preview-culling.js`
- `js/eclipse-sampler-tiled-decode.js`
- `js/eclipse-ui-enhancements.js`
- `js/eclipse-widget-performance-utils.js`
- `pyproject.toml`

---

## 2026-07-24

### Version: 4.2.2

- **Feat**
  - **Detailer live preview:** Add an enabled-by-default switch to Detailer (SEGS/pipe) for suppressing intermediate latent images while preserving progress updates and final denoised output.

**Changed files:**
- `extern/impact/core.py`
- `extern/impact/impact_pack.py`
- `extern/impact/impact_sampling.py`
- `py/RvSampler_DetailerForEach.py`
- `pyproject.toml`

---

### Version: 4.2.1

- **Feat (New)**
  - **Compact workflow IDs:** Add an action-bar command that safely renumbers active-workflow nodes in saved canvas order, places each subgraph definition's internal nodes directly after its first visual instance with recursive depth-first numbering, compacts link IDs, repairs duplicate IDs, removes orphaned links and stale slot references, preserves valid topology and special IDs, validates changes atomically, and participates in normal undo and Save handling.

- **Fix**
  - **Text DOM previews:** Restore hover-focused wheel scrolling in Nodes 2.0 for Show Text and Show Any variants, hand wheel input back to canvas zoom at text boundaries, and preserve classic-renderer behavior.

**Changed files:**
- `js/eclipse-show-any.js`
- `js/eclipse-show-text.js`
- `js/eclipse-widget-performance-utils.js`
- `js/eclipse-workflow-compact-ids.js` (new)
- `js/eclipse-workflow-id-utils.js` (new)

---

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
