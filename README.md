# ComfyUI_Eclipse

ComfyUI_Eclipse is a collection of custom nodes, helpers and utilities for ComfyUI designed to make workflow building easier and more reliable. It includes type conversions, folder and filename helpers, image/video utilities, logic and flow helpers, and a broad pipe ecosystem.

> #### ⚠️ Warning
> - Workflows created with RvTools_v2 are NOT compatible with this version. This release contains a substantial cleanup and many improvements.
> - <b>Version 4.0.0: all legacy/deprecated nodes have been completely removed from the codebase.</b>
>   - *Note on upgrading:* If your existing workflows fail to load due to the version tag removals, you can automatically migrate them (with backups) by using the built-in **[Workflow Migration Tool](Readme/workflow_migration.md)** node, running the command-line script `python tools/migrate_workflow.py <path_to_workflow_or_directory>`, or following the manual search-and-replace mapping guide in [migration_mapping.txt](tools/migration_mapping.txt).

## Documentation

- **[Documentation Index](Readme/README.md)** — Full index with descriptions
- [ComfyUI Smart Model Loader](https://github.com/r-vage/ComfyUI_SmartModelLoader) — External provider for Eclipse diffusion loader, LoRA Stack, and Nunchaku PuLID node IDs, templates, and Download Manager
- [ComfyUI SmartLLM](https://github.com/r-vage/ComfyUI_SmartLLM) — External provider for Smart LM Loader, Smart Detection, Detection to Bboxes, Registry Manager, and `/smartlml` APIs
- [Smart Sampler Settings](Readme/Smart_Sampler_Settings.md) — Sampler config with seed modes
- [Smart Folder](Readme/Smart_Folder.md) — Output folder with image/video modes
- [Save Images](Readme/Save_Images.md) — Image saving with metadata and placeholders
- [Save Video with Generation Data](Readme/Save_Video_Data.md) — MP4 saving with workflow/generation metadata and placeholders
- [Replace String Advanced](Readme/Replace_String_Advanced.md) — Pattern-based LLM text processing with Florence-2-tuned terms
- [Smart Prompt v2](Readme/Smart_Prompt.md) — Multi-folder prompt building
- [Prompt Styler](Readme/Prompt_Styler.md) — 100+ visual styles for prompts
- [Prompt Styler v2](Readme/Prompt_Styler_v2.md) — The same styling and index behavior with compact feature chips
- [Danbooru Prompt Forge](Readme/Danbooru_Prompt_Forge.md) — Seeded taglist selection and unified post-to-catalog-to-SmartLLM corpus maintenance
- [Wildcard Processor](Readme/Wildcard_Processor.md) — Impact-derived prompt expansion with live preview and Eclipse seed controls
- [Read Prompt Files](Readme/ReadPromptFiles.md) — Load and navigate prompts from multiple text files
- [Save Prompt](Readme/Save_Prompt.md) — Caption/prompt saving
- [Load Image From Folder](Readme/Load_Image_From_Folder.md) — Batch image loading
- [Batch Selection, Slice & Dice, and Review](Readme/Batch_Selection_Slice_Dice.md) — Ordered visual batch curation while keeping images and filenames aligned
- [Set/Get & Mode Bridge](Readme/Set_Get_Bridge.md) — Named data channels and wireless mute/bypass control
- [Get First / Get All Active](Readme/GetFirst_GetAllActive.md) — Priority-based virtual variable routing
- [Utility Nodes](Readme/Utility_Nodes.md) — Switches, joiners, cleanup, helpers
- [Nunchaku Installation](Readme/Nunchaku_Installation.md) — Quantized Flux model setup
- [Workflow Migration Tool](Readme/workflow_migration.md) — How to automatically upgrade saved workflows from inside ComfyUI
- [Hugging Face Dataset Downloader](Readme/HuggingFace_Dataset_Downloader.md) — Linux and Windows snapshot utilities for repositories containing loose dataset files

> **Danbooru maintenance model:** Prefer a Qwen 3.x instruct model in the 8B/9B
> class or larger for the two-pass categorization workflow. Qwen 3.8 27B is the
> strongest tested model, while Qwen 3.5 9B is the smaller successful baseline;
> smaller or unqualified models should be trialed on a small batch before
> processing the full backlog.

## Contents

- `py/` — All custom node implementations (checkpoint loaders, conversion nodes, folder utilities, image helpers, logic nodes, passers, pipes, etc.).
- `core/` — Shared code: categories, logging helpers (`cstr`), VRAM purge helper, configuration, keys, and text processing engines.
- `js/` — Frontend JavaScript extensions for dynamic widget behavior in ComfyUI's LiteGraph canvas.
- `patterns/` — SmartTextProcessor JSON pattern files for content detection and removal.
- `prompts/` — Smart Prompt text files organized by category (subjects, settings, environments).
- `styles/` — Prompt style CSV/JSON files for the Prompt Styler node.
- `wildcards/` — Example wildcard text files for the Wildcard Processor.
- `scripts/` — Linux and Windows maintenance and dataset-download utilities.
- `.defaults/` — Git-tracked `.example` files extracted and hash-aware updated in repository folders while preserving user edits.
- `requirements.txt` / `pyproject.toml` — Declared dependencies and packaging metadata.

## License

This project is licensed under the Apache License 2.0 (see `LICENSE`). Check the license before embedding parts of this project in other software.

## Beginner-friendly installation

The easiest way to install ComfyUI_Eclipse is to place it in ComfyUI's `custom_nodes` folder so ComfyUI will discover the nodes automatically.

1. Locate your ComfyUI installation folder.
2. Inside ComfyUI, find (or create) the `custom_nodes` folder.
3. Copy the entire `ComfyUI_Eclipse` folder into `custom_nodes` so the tree looks like:

```
ComfyUI/
  custom_nodes/
    ComfyUI_Eclipse/
      py/
      core/
      README.md
      ...
```

Or, clone directly into `custom_nodes`:

```powershell
# from your ComfyUI directory (PowerShell)
git clone https://github.com/r-vage/ComfyUI_Eclipse custom_nodes/ComfyUI_Eclipse
```

4. Install any optional Python dependencies required by specific nodes. From the repository root (or your ComfyUI root), run:

```powershell
# optional - only if your ComfyUI environment is missing packages from requirements.txt
pip install -r custom_nodes/ComfyUI_Eclipse/requirements.txt

# For ComfyUI portable installations:
python_embeded\python.exe -m pip install -r custom_nodes/ComfyUI_Eclipse/requirements.txt
```

Common dependencies referenced by nodes include: torch, numpy, Pillow, opencv-python, piexif and others. ComfyUI itself usually provides the main ML stack (torch, torchvision, safetensors), but if you see errors you may need to install missing packages.

5. Restart ComfyUI. The new nodes should appear in the node list under categories provided by the package.

### Eclipse Folder Structure and Default Extraction

On startup, ComfyUI_Eclipse extracts default files from the `.defaults/` folder
directly into the repository's own folders. This seeds first installs and also
introduces newly packaged files after an update. All user-editable files live
inside the repo itself:

```
custom_nodes/
  ComfyUI_Eclipse/
    prompts/                # Smart Prompt text files
      tag_lists/            # Ready-to-use Prompt Forge category/rating corpora
      environment/          # Environment descriptions
      settings/             # Style and quality settings
      subjects/             # Subject categories
    styles/                 # Prompt Styler style files (CSV/JSON)
    patterns/               # SmartTextProcessor pattern files
    wildcards/              # Example wildcard files
    config.json             # Private Eclipse settings
    .defaults/              # Git-tracked defaults (*.example files)
```

**Important Notes:**
- **Edit files directly in the repo folders** (for example, `ComfyUI_Eclipse/prompts/`).
- **Git updates won't overwrite your edits** — new defaults are extracted automatically; an updated default replaces its runtime copy only when that copy still matches the previously packaged hash. User-modified files and intentionally deleted known files remain untouched.
- **No `models/Eclipse` links or migration** — Eclipse reads prompts, styles, and patterns directly from the repository and leaves former `models/Eclipse/*` entries untouched.
- **Wildcard integration** — Eclipse creates `models/wildcards/smart_prompt` as a junction or symlink to its `prompts/` directory so the Wildcard node can use the same files without duplication. Startup keeps a correct link, repairs a wrong or dangling target on Windows and Linux, and never replaces a real directory.

### Opening a console / terminal in the ComfyUI folder (beginner)

If you're new to command lines, here's a very short guide to open a terminal (console) already located in your ComfyUI folder so you can run commands there.

Windows (PowerShell / Windows Terminal):

- Option A — From File Explorer:
  1. Open File Explorer and navigate to the ComfyUI installation folder (the folder that contains `run_nvidia_gpu.bat`, `webui.bat`, `main.py` or similar files).
  2. Hold Shift, right-click on an empty area in the folder and choose "Open PowerShell window here" or "Open in Windows Terminal".

- Option B — From any PowerShell window:
  1. Open PowerShell or Windows Terminal.
  2. Change directory to the ComfyUI folder, for example:

```powershell
# replace the path below with your actual ComfyUI path
cd 'D:\path\to\ComfyUI'
# or using Set-Location
Set-Location 'D:\path\to\ComfyUI'
```

Notes for Windows:
- If your path contains spaces, wrap it in single or double quotes.
- Your default shell may be PowerShell (`pwsh.exe`) or Command Prompt (`cmd.exe`); PowerShell and Windows Terminal are recommended.

macOS / Linux (Terminal):

1. Open Terminal (Spotlight → "Terminal" on macOS, or your terminal emulator on Linux).
2. Change directory to the ComfyUI folder, for example:

```bash
# replace the path below with your actual ComfyUI path
cd /home/you/ComfyUI
```

Tips:
- Use Tab to autocomplete long folder names.
- If you use a Python virtual environment, activate it from the same console before running ComfyUI.

## External model providers

The former Eclipse diffusion loaders, LoRA Stack, Nunchaku PuLID nodes, and the Download Manager are provided by [ComfyUI Smart Model Loader](https://github.com/r-vage/ComfyUI_SmartModelLoader). Smart LM Loader, Smart Detection, Detection to Bboxes, the Registry Manager, and the `/smartlml` API are provided by [ComfyUI SmartLLM](https://github.com/r-vage/ComfyUI_SmartLLM). Install the corresponding pack to keep existing workflows resolving the unchanged `[Eclipse]` node IDs. Eclipse integrations such as Get First/Last Image, preview culling, and seed interoperability remain compatible with the external providers.

The three packs have independent settings and private configuration ownership. Eclipse controls remain under **Eclipse → General** and **Eclipse → Nodes 2.0**; the diffusion pack uses **Smart Model Loader → General**; and `ComfyUI_SmartLLM` uses **Smart LM Loader → Configuration**. Each category talks only to its own REST namespace and config file, so installation or extension load order does not merge log levels, retry policies, credentials, sliders, or pack-specific options.

## Tips & troubleshooting

- If a node raises an import error for a package, install the missing package into the same Python environment that runs ComfyUI.
- If you place the folder under `custom_nodes` but the nodes don't show up, restart ComfyUI and check the server logs for import errors.

## Contributing

Contributions, bug reports, and PRs are welcome. Please fork the repository, make changes in a feature branch, and open a PR with a short description of the change.

If opening issues, include the ComfyUI version, Python version, torch/CUDA details (if relevant), and error tracebacks.

## Node categories overview

This project groups nodes into categories to make them easier to find in ComfyUI. Below is a short summary of the categories provided by ComfyUI_Eclipse:

- **Eclipse (Main)** — Top-level group for general Eclipse nodes and primary entry points. Contains high-level helpers and commonly used nodes.
- **Loader** — Load Audio and related pipeline integration nodes. Diffusion model loaders and Smart LM nodes are supplied by the external provider packs linked above.
- **Conversion** — Type conversion helpers (Any → Float/Integer/String/Combo, lists ↔ batches, image/mask conversions, string merging, pipe concatenation, etc.).
- **Folder** — Nodes for creating and managing project folders, filename prefixing, and smart folder utilities with placeholder support to organize outputs.
- **Image** — Image utilities for loading from various sources, previewing, saving with advanced metadata, and manipulating images in workflows.
- **Router** — Routing and control nodes for conditional execution, switches, multi-switches, and any-type data passing through workflows.
- **Pipe** — Pipeline and composition helpers (12-channel pipes, context managers for image/video workflows, generation data, sampler settings, and pipe extraction nodes).
- **Primitives** — Small building-block nodes for basic values (Boolean, Integer, Float, String) used in control flow and logic operations.
- **Settings** — Nodes for sampler configurations, resolution presets, directory settings, ControlNet union types, and video name generators used to tune pipelines.
- **Text** — String and text-processing helpers (multiline input, smart prompts, wildcard processing, regex replacement, dual text inputs).
- **Video** — Video workflow utilities (loop/keep calculators, video clip combination, seamless joining, frame helpers for professional video generation).
- **Utilities** — General utility nodes (Show Any for debugging, workflow control with Stop, RAM/VRAM cleanup). LoRA Stack and Nunchaku PuLID are supplied by Smart Model Loader.

If you open ComfyUI after installing the package you'll find these categories in the node chooser; categories are intended to be concise and practical so you can quickly locate the right node for your workflow.

## Files by category

### Conversion
Convenience nodes for type conversion, list/batch transforms, string merging, and context/pipe manipulation.
- Concat Multi - Concatenate/merge multiple pipes or contexts.
- Convert Primitive - Convert Any value to String, Integer, Float, or Combo.
- Convert To Batch - Convert lists of images or masks to a batch tensor.
- Convert to List - Convert image/mask batches to lists.
- Image Convert - Convert images between color spaces/modes.
- Join - Concat strings, lists, or pipes with separator/merge option.
- Merge Strings - Merge multiple strings together.
- RIFE Multiplier - Multiplies/interpolates frames for high framerate video generation.
- String from List - Retrieve string from list at index.
- Widget to String - Read widget values from any node as string.

### Folder
Nodes for creating and managing project folders, filename prefixing, and smart folder utilities to organize outputs.
- Add Folder - Prefix a path with a folder name.
- Filename Prefix - Add a timestamped/formatted suffix or prefix to filenames.
- Folder Path - Configure and resolve custom folder paths.
- Smart Folder - Setup structured image/video output folder hierarchies.

### Image
Image utilities for loading, previewing, saving, and manipulating images in workflows and output nodes.
- Add Watermark Image - Overlay watermark images with custom alignment/scale.
- Image Comparer - Visual comparison tool for two images.
- Image Color Match - Match the color palette of one image to another.
- Image Crop By Mask - Crop an image using a mask boundary.
- Image Get First / Last - Retrieve the first or last image from a batch.
- Image Inset Crop - Perform inset crop on images.
- Image Soften - Apply blurring/softening filters.
- Image Filter Adjustments - Apply visual adjustments (contrast, brightness, saturation).
- Image Selector - Pick specific images from a batch by indices.
- Load Image - Load single image with metadata extraction.
- Load Image (Pipe) - Load image and output a unified pipe dictionary.
- Load Image From Folder - Read images from directory with batch/index options.
- Load Image From Folder (Pipe) - Read images from folder with unified pipe output.
- Load Batch From Folder - Load batch of images from folder.
- Load Batch From Folder Advanced - Advanced batch image loader.
- Save Images - Advanced image saving with placeholder metadata injection.
- Preview Image - Display images in the canvas.
- Preview Image (DOM) - HTML-based preview of images.
- SEGS Preview - Visualize Detailer SEGS segmentations.
- SEGS Preview Simple - Simple SEGS preview node.
- Tile Split / Assembly - Split images into tiles and assemble them.
- Tile Decode Assembly - Decode and assemble tiles.
- Text/Image With FX - Advanced typography and image FX shaders.
- Align Size - Align image dimensions to custom step/multiplier.
- Batch Slice / Interleave / Strip / Extend - Advanced image batch manipulation.
- Rescale / Upscale With Model / Upscale With Model v2 - Image scaling and super-resolution.

### Mask
Mask processing and type conversion utilities.
- Preview Mask - Display mask in the canvas.
- Mask to SEGS - Convert masks to Detailer SEGS format.

### Loader
Eclipse retains its audio loader. Diffusion loading is provided by [ComfyUI Smart Model Loader](https://github.com/r-vage/ComfyUI_SmartModelLoader), while language-model and detection loading is provided by [ComfyUI SmartLLM](https://github.com/r-vage/ComfyUI_SmartLLM).
- Load Audio - Load audio tracks for video generation.

### Logic & Primitives
Small building-block nodes for booleans, numbers, and strings, used in control flow and logic operations.
- Boolean - Toggle switch boolean value.
- Float - Decimal/floating-point number value.
- Integer - Whole number value.
- Integer (Gen) - Incrementing integer generator.
- None - Output Python `None` value.
- String - Text input field.
- Seed - Control and randomize seeds for generation.

### Sampler
Sampling processors and execution controllers.
- KSampler (Pipe) - Provided by [ComfyUI Smart Model Loader](https://github.com/r-vage/ComfyUI_SmartModelLoader).
- KSampler Kargim - Advanced sampler with custom scheduler tuning.

### Pipe
Pipeline and composition helpers: context managers, multi-channel pipes, generation data, and out nodes for assembling or emitting pipeline data.
- Pipe 12CH / 24CH / 36CH Any - Multi-channel any-type pipelines.
- Context (Image) - Image generation context configuration.
- Context (Video) - Video generation context configuration.
- Context (WanVideo) - WanVideo wrapper context pipeline.
- Generation Data - Store metadata settings in a pipe.
- Generation Data Gated - Store conditional metadata settings in a pipe.
- Pipe IO Sampler Settings - Sampler setting inspector/editor for pipes.
- Pipe IO Checkpoint Loader - Provided by [ComfyUI Smart Model Loader](https://github.com/r-vage/ComfyUI_SmartModelLoader).
- Pipe IO Load Image - Load Image settings inspector/editor for pipes.
- Pipe Out Smart Folder - Extract folder configurations from pipes.
- Pipe Out WanVideo Setup - Extract WanVideo setup parameters from pipes.

### Router
Routing and control nodes for conditional execution, switches, and data passing.
- Passer (Any / Boolean / Float / Int / String / Model / Clip / Vae / Segs / Audio / BasicPipe / Conditioning / ControlNet / DetailerPipe / Image / Latent / Mask / WanVideoModel / Pipe) - Fast routing/passthrough nodes for specific types.
- Switch (Any / Purge / Lazy / Lazy Purge) - Multi-input selectors with optional VRAM memory purges.
- If Execute - Route outputs based on boolean conditions.

### Settings
Nodes that expose or compose small settings objects (sampler presets, resolution helpers, directory settings) used to tune pipelines.
- ControlNet Union Type - ControlNet union type selection helper.
- Image Resolutions - Aspect ratio and resolution selectors for images.
- Video Resolution - Aspect ratio and resolution selectors for video.
- Smart Sampler Settings - Sampler preset configuration.
- WanVideo Setup - Configuration parameters for WanVideo.

### Text
Nodes for prompt construction, text processing, and string manipulation with advanced placeholder and wildcard support.
- CLIP Text Encode / Advanced and Conditioning Zero Out - Provided by [ComfyUI Smart Model Loader](https://github.com/r-vage/ComfyUI_SmartModelLoader).
- DeDuplicate - Remove duplicate words or tags from prompts.
- Dual Text - Join two prompt strings.
- Multiline Text / Multiline Text List - Paragraph text inputs.
- Prompt Styler - Apply styled tags to prompts.
- Prompt Styler v2 - Apply the same styles with compact feature chips.
- Read Prompt Files - Load and navigate prompts from multiple text files.
- Replace String - Replace substrings in prompts.
- Replace String Advanced - Pattern-based LLM text processing with Florence-2-tuned terms.
- Save Prompt - Save prompts/metadata to disk.
- Smart Prompt / Smart Prompt v2 - Structured prompt building.
- Wildcard Processor - Text processing with dynamic wildcards.

### Video & Audio
Nodes for video clip composition, frame utilities, and loop/frame calculations for video-friendly pipelines.
- Loop Calculator - Compute frame rates and loop timings.
- Keep Calculator - Trim frames to fit loop lengths.
- Audio Loop Calculator - Calculate audio sync timings.
- Audio Loop Align Silence - Fill silence to match duration.
- Trim to Shortest - Match video/audio lengths.
- Preview Video - Play video clips directly in canvas.
- Save Video - Compile frames and audio to video formats.
- Save Video with Generation Data - Save MP4 video with feature chips, A1111 generation metadata, workflow JSON, and filename placeholders.
- Video Frame Consistency - Smooth transitions across generated video frames.

### Utilities & Tools
General utility nodes for debugging, resource management, and workflow control.
- Resolution Scale - Scale coordinates and resolution ratios.
- Show Any - Render values, tensors, or images for debugging.
- Show Text - Preview text strings in ComfyUI DOM.
- Stop - Stop execution immediately.
- Block Swap - Memory optimizer for model block swapping.
- Mode Toggle / Switcher / Repeater / Relay / Bridge - ECLIPSE UI control tools for workflow states.
- Node Collector - Collect references to multiple nodes.
- LoRA Stack / LoRA Stack Apply and Nunchaku PuLID Loader / Apply - Provided by [ComfyUI Smart Model Loader](https://github.com/r-vage/ComfyUI_SmartModelLoader) under their unchanged workflow IDs.
- Workflow Migration Tool - Scan and automatically upgrade saved workflows from legacy Eclipse node versions to v4.0.0.

## Smart LM and detection integration

[ComfyUI SmartLLM](https://github.com/r-vage/ComfyUI_SmartLLM) now owns Smart LM Loader, Smart Detection, Detection to Bboxes, all native and container backends, the Registry Manager, installation guides, and security documentation. The node IDs and display names still carry their `[Eclipse]` suffix so saved workflows remain compatible. Eclipse continues to consume their outputs through its remaining image-selection, preview, and seed-integration nodes.

## The Pipe Ecosystem of [Eclipse]

The pipe ecosystem in ComfyUI_Eclipse is a sophisticated data interchange system designed to standardize and simplify the flow of complex data structures through ComfyUI workflows. Pipes act as containers that bundle related parameters, models, and settings into single, manageable objects, eliminating the need for dozens of individual node connections.

### Core Concept

A pipe is fundamentally a Python dictionary that encapsulates multiple related pieces of data. Instead of connecting separate wires for model, CLIP, VAE, latent tensor, dimensions, sampler settings, and metadata, all of this information can be passed through a single pipe connection. This approach dramatically reduces workflow complexity and improves maintainability.

### Pipe Types and Variants

#### Context Pipes
Context pipes are the foundation of the ecosystem, holding the core components of a generation pipeline:

- **Context (Image) (`Context (Image) [Eclipse]`):** Standard image generation context containing model, CLIP, VAE, conditioning (positive/negative), latent, sampler/scheduler, generation parameters (steps, cfg, seed, dimensions), and text prompts. Ideal for standard image generation workflows.
- **Context (Video) (`Context (Video) [Eclipse]`):** Extended context for video workflows, adding video-specific parameters like frame rate, frame load cap, skip frames, select every nth frame, and audio/image inputs/outputs. Designed for video generation pipelines.
- **Context (WanVideo) (`Context (WanVideo) [Eclipse]`):** Specialized wrapper for WAN Video Workflows, supporting WANVIDEOMODEL and WANTEXTENCODER types with additional video processing parameters for WAN-based video generation.

#### Generation Data Pipes
These pipes focus on sampler and generation settings:

- **Generation Data (`Generation Data [Eclipse]`):** Contains sampler/scheduler names, steps, cfg, seed, dimensions, text prompts, model/VAE names, LoRA names, denoise strength, and CLIP skip settings. Perfect for metadata tracking and parameter preservation.

#### Sampler Settings Pipes
Specialized pipes for different sampling configurations:

- **Sampler Settings (`Sampler Settings [Eclipse]`):** Comprehensive sampler configuration with sampler/scheduler, steps, CFG, seed, and denoise parameters.
- **Sampler Settings (Small) (`Sampler Settings (Small) [Eclipse]`):** Minimal sampler configuration with basic sampler/scheduler, steps, and CFG.
- **Sampler Settings (Small+Seed) (`Sampler Settings (Small+Seed) [Eclipse]`):** Minimal configuration with added seed control.
- **Sampler Settings (Seed) (`Sampler Settings (Seed) [Eclipse]`):** Full sampler settings with integrated seed management.
- **Sampler Settings (NI) (`Sampler Settings (NI) [Eclipse]`):** Noise Injection Parameters with generation settings (no seed).
- **Sampler Settings (NI+Seed) (`Sampler Settings (NI+Seed) [Eclipse]`):** Noise Injection Parameters with seed and generation settings.

#### Multi-Channel Pipes
Flexible any-type data pipes for custom workflows:

- **Pipe 12CH Any (`Pipe 12CH Any [Eclipse]`):** 12-channel any-type pipe for complex custom workflows requiring multiple arbitrary data streams.

### Key Abilities

#### 1. Standardized Data Interchange
- **Dict-Style Format:** All pipes use consistent dictionary structures with canonical key names.
- **Type Safety:** Each pipe component has defined types (MODEL, CLIP, VAE, LATENT, INT, FLOAT, STRING, etc.).
- **Extensibility:** New fields can be added without breaking existing workflows.

#### 2. Workflow Simplification
- **Reduced Connections:** Bundle 10+ parameters into single connections.
- **Cleaner Layouts:** Workflows become more readable and easier to debug.
- **Modular Design:** Components can be mixed and matched across different pipeline types.

#### 3. Data Manipulation Capabilities
- **Pipe Concatenation:** Merge multiple pipes using the Concat Multi node with strategies (overwrite, preserve, merge).
- **Component Extraction:** Extract individual elements (model, CLIP, VAE, latent) from pipes using Pipe Out nodes.
- **Context Building:** Construct pipes from scratch or modify existing ones.

#### 4. Advanced Features
- **Latent Generation:** Automatic latent tensor creation based on dimensions and batch size.
- **Metadata Preservation:** Maintain model names, VAE names, LoRA lists for reference.
- **Error Handling:** Graceful fallbacks and validation for missing or invalid data.
- **Memory Optimization:** Support for different weight dtypes and CLIP trimming.

### Pipe Output Nodes

Specialized nodes extract specific data from pipes:

- **Pipe Out Smart Folder (`Pipe Out Smart Folder [Eclipse]`):** Extracts smart folder configuration including paths, dimensions, and placeholder data.
- **Pipe Out WanVideo Setup (`Pipe Out WanVideo Setup [Eclipse]`):** Extracts WanVideo workflow setup parameters.

### Practical Applications

#### Complex Workflows
Pipes excel in workflows requiring multiple model components, ensemble CLIP setups, or video processing pipelines where managing dozens of individual connections becomes impractical.

#### Batch Processing
When processing multiple images or videos with consistent settings, pipes allow settings to be defined once and reused across batch operations.

#### Modular Pipeline Construction
Build reusable pipeline segments that can be connected together, with pipes handling the data flow between modules.

#### Memory Management
Pipes support efficient memory usage through dtype control and component lazy loading.

### Best Practices

- **Use Dict Pipes:** Prefer dict-style pipes over legacy tuple formats for maximum compatibility.
- **Validate Components:** Use pipe output nodes to ensure all required components are present.
- **Merge Strategically:** When concatenating pipes, choose appropriate merge strategies (merge for combining, overwrite for replacement).
- **Type Consistency:** Ensure pipe components match expected types for downstream nodes.
- **Documentation:** Include pipe metadata (model names, settings) for workflow reproducibility.

The pipe ecosystem transforms ComfyUI workflow construction from a web of individual connections into a streamlined, professional data flow system capable of handling the most complex AI generation pipelines.

to be continued...

---

Enjoy — and happy workflow-building!
