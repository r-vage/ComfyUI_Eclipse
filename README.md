# ComfyUI_Eclipse

ComfyUI_Eclipse is a collection of custom nodes, helpers and utilities for ComfyUI designed to make workflow building easier and more reliable. It includes convenience nodes for loading checkpoints and pipelines, type conversions, folder and filename helpers, simple image utilities, logic and flow helpers, and small toolkits for working with VAE/CLIP and latents.

> #### ⚠️ Warning
> - Workflows created with RvTools_v2 are NOT compatible with this version. This release contains a substantial cleanup and many improvements.
> - <b>All legacy/deprecated nodes will be removed at version 4.0.0 to clean up the repository. Please update your workflows to use the current node versions before then.</b>

## Documentation

- **[Documentation Index](Readme/README.md)** — Full index with descriptions
- [Smart Model Loader](Readme/Smart_Loaders.md) — Unified loader: checkpoints, UNet, Nunchaku, GGUF
- [Standalone Loaders](Readme/Checkpoint_Loaders.md) — Model, CLIP, VAE component loaders
- [Smart Sampler Settings v1 / v2](Readme/Smart_Sampler_Settings_v2.md) — Sampler config with seed modes
- [Smart Folder v2](Readme/Smart_Folder_v2.md) — Output folder with image/video modes
- [Save Images v2](Readme/Save_Images.md) — Image saving with metadata and placeholders
- [Replace String v3](Readme/Replace_String_v3.md) — Pattern-based text processing
- [Smart Prompt v2](Readme/Smart_Prompt.md) — Multi-folder prompt building
- [Prompt Styler](Readme/Prompt_Styler.md) — 100+ visual styles for prompts
- [Wildcard Processor](Readme/Wildcard_Processor.md) — Dynamic prompt expansion
- [ReadPromptFiles](Readme/ReadPromptFiles_Usage.md) — Load prompts from text files
- [Save Prompt](Readme/Save_Prompt.md) — Caption/prompt saving
- [Load Image From Folder](Readme/Load_Image_From_Folder.md) — Batch image loading
- [Set/Get & Mode Bridge](Readme/Set_Get_Bridge.md) — Named data channels and wireless mute/bypass control
- [Get First / Get All Active](Readme/GetFirst_GetAllActive.md) — Priority-based virtual variable routing
- [Utility Nodes](Readme/Utility_Nodes.md) — Switches, joiners, cleanup, helpers
- [Nunchaku Installation](Readme/Nunchaku_Installation.md) — Quantized Flux model setup
- [Smart LM Loader](Readme/Smart_LM_Loader_Guide.md) — Vision-language models, text LLMs, WD14 taggers (8 backends)
- [Smart Detection](Readme/Smart_Detection_Guide.md) — YOLO + VLM object detection and description
- [Docker Installation (Windows/WSL2)](Readme/Docker_Installation_Guide.md) — WSL2 + Docker + NVIDIA GPU setup
- [Docker Installation (Linux)](Readme/Docker_Installation_Guide_Linux.md) — Docker Engine + NVIDIA Container Toolkit
- [Model Repository Reference](Readme/Model_Repos_Reference_Links.md) — HuggingFace URLs for supported LLM/VLM models
- [⚠️ LLM Security Warning](Readme/LLM_Security_Warning.md) — **Read before running any LLM.** Why Docker + Ollama is the safe default, venv hygiene, documented HF/pickle attacks

## Contents

- `py/` — All custom node implementations (checkpoint loaders, conversion nodes, folder utilities, image helpers, logic nodes, passers, pipes, etc.).
- `core/` — Shared code: categories, logging helpers (`cstr`), VRAM purge helper, configuration, keys, and text processing engines.
- `js/` — Frontend JavaScript extensions for dynamic widget behavior in ComfyUI's LiteGraph canvas.
- `patterns/` — SmartTextProcessor JSON pattern files for content detection and removal.
- `prompts/` — Smart Prompt text files organized by category (subjects, settings, environments).
- `styles/` — Prompt style CSV/JSON files for the Prompt Styler node.
- `templates/` — Smart Loader template JSON files for saving/loading checkpoint configurations.
- `wildcards/` — Example wildcard text files for the Wildcard Processor.
- `core/sml/` — Smart LM subsystem: LLM/VLM backends, model registry, Docker integration, detection helpers.
- `config/` — LLM few-shot training and system prompt files.
- `registry/` — Model registry JSON files (per-backend + user models + defaults).
- `scripts/` — Docker helper scripts (Linux).
- `docker_config.json` — Docker backend settings (ports, timeouts, images).
- `.defaults/` — Git-tracked `.example` files extracted to repo folders on first run (never overwrites user edits).
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

### Eclipse Folder Structure (First Launch)

On first launch, ComfyUI_Eclipse extracts default files from the `.defaults/` folder directly into the repository's own folders. All user-editable files live inside the repo itself:

```
custom_nodes/
  ComfyUI_Eclipse/
    templates/              # Smart Loader templates (checkpoint configurations)
    prompts/                # Smart Prompt text files
      environment/          # Environment descriptions
      settings/             # Style and quality settings
      subjects/             # Subject categories
    styles/                 # Prompt Styler style files (CSV/JSON)
    patterns/               # SmartTextProcessor pattern files
    wildcards/              # Example wildcard files
    config/                 # LLM few-shot training, system prompts
    registry/               # Model registry JSON files
    scripts/                # Docker helper scripts (Linux)
    docker_config.json      # Docker backend config
    .defaults/              # Git-tracked defaults (*.example files)
```

For convenience, junctions (Windows) or symlinks (Linux/macOS) are created so files are also accessible from within the `models/` directory:

```
ComfyUI/
  models/
    Eclipse/
      templates  →  ComfyUI_Eclipse/templates/
      prompts    →  ComfyUI_Eclipse/prompts/
      styles     →  ComfyUI_Eclipse/styles/
      patterns   →  ComfyUI_Eclipse/patterns/
    wildcards/
      smart_prompt  →  ComfyUI_Eclipse/prompts/
```

**Important Notes:**
- **Edit files directly in the repo folders** (e.g., `ComfyUI_Eclipse/templates/`, `ComfyUI_Eclipse/prompts/`) or via the `models/Eclipse/` junctions — they point to the same locations.
- **Git updates won't overwrite your edits** — the `.defaults/` extraction only copies files that don't already exist.
- **Wildcard integration** — `models/wildcards/smart_prompt/` is a junction/symlink pointing to the repo's `prompts/` folder for seamless wildcard processor integration.
- **Automatic migration** — If upgrading from a version that used `models/Eclipse/` as a separate folder, your existing files are automatically migrated into the repo and the old folder is renamed to `Eclipse_backup/`.

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

## Quick start — using the Smart Model Loader

The **Smart Model Loader** is the primary model loader, replacing the older Smart Loader Plus/Smart Loader/Smart Loader Basic variants. It uses combo-chip feature toggles to show only the settings you need.

### Smart Model Loader [Eclipse]

- **Multi-Format Support:** Standard Checkpoints, UNet models, Nunchaku quantized Flux/Qwen/ZImage (SVDQuant INT4/FP4/FP8), and GGUF quantized models.
- **Combo-Chip Features:** Toggle visibility of sections (templates, CLIP, VAE, latent, sampler, LoRA, model sampling, block swap, memory cleanup, seed) using clickable chips — disabled sections are hidden from the UI.
- **Template System:** Save and load complete configurations including model selections, CLIP/VAE settings, and sampler parameters.
- **CLIP Ensemble:** Support for up to 4 CLIP modules with 27 architecture types (Flux, Flux2, SD3, SDXL, Qwen, HiDream, Hunyuan, WAN, etc.).
- **LoRA Support:** Up to 3 LoRA slots with per-slot weight control and on/off switches.
- **Model Sampling:** 8 sampling methods (SD3, AuraFlow, Flux, Stable Cascade, LCM, ContinuousEDM, ContinuousV, LTXV) with method-specific parameters.
- **Block Swap:** GPU↔CPU block swapping for large models that don't fit in VRAM.
- **Quantization Options:**
  - Nunchaku Flux: Data type, cache threshold, attention mode, CPU offload
  - Nunchaku Qwen/ZImage: GPU block allocation, pinned memory, CPU offload
  - GGUF: Dequantization dtype, patch dtype, device placement
- **Output:** Single PIPE containing model, CLIP, VAE, latent, dimensions, batch size, sampler settings, and metadata.

### Required Extensions for Quantized Models

To use Nunchaku or GGUF quantized models with the Smart Loaders, you need to install the following ComfyUI extensions:

**For Nunchaku Support (SVDQuant INT4/FP4/FP8):**
- Repository: [ComfyUI-Nunchaku](https://github.com/nunchaku-tech/ComfyUI-nunchaku)
- Installation: Clone into your `custom_nodes` folder
- Supports: Nunchaku Flux, Nunchaku Qwen, and Nunchaku ZImage quantized models

**For GGUF Support:**
- Repository: [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)
- Installation: Clone into your `custom_nodes` folder
- Supports: GGUF quantized model formats

```powershell
# Navigate to your ComfyUI custom_nodes directory
cd ComfyUI/custom_nodes

# Install Nunchaku support
git clone https://github.com/nunchaku-tech/ComfyUI-nunchaku

# Install GGUF support
git clone https://github.com/city96/ComfyUI-GGUF
```

**Note:** The Smart Model Loader works without these extensions installed, but quantized model options will be disabled. Standard Checkpoints and UNet models work without additional dependencies.

Basic usage:

1. Add **Smart Model Loader** to your workflow.
2. Use the combo-chip to enable the feature sections you need (e.g., clip, vae, latent, sampler, lora).
3. Select model type (Standard Checkpoint, UNet, Nunchaku Flux, Nunchaku Qwen, Nunchaku ZImage, or GGUF).
4. Choose the appropriate model file from the dropdown.
5. Configure CLIP (baked or external) and VAE (baked or external).
6. Optionally enable model sampling and select appropriate method (SD3, Flux, etc.) for your model architecture.
7. Enable the **templates** chip to save/load configurations for quick workflow iteration.
8. Connect the pipe output to downstream nodes or use Pipe Out nodes to extract components.

The Smart Model Loader includes comprehensive error handling, automatic VRAM cleanup, and graceful fallbacks when optional extensions (Nunchaku, GGUF) are not installed.

## Tips & troubleshooting

- If a node raises an import error for a package, install the missing package into the same Python environment that runs ComfyUI.
- If you place the folder under `custom_nodes` but the nodes don't show up, restart ComfyUI and check the server logs for import errors.

## Contributing

Contributions, bug reports, and PRs are welcome. Please fork the repository, make changes in a feature branch, and open a PR with a short description of the change.

If opening issues, include the ComfyUI version, Python version, torch/CUDA details (if relevant), and error tracebacks.

## Node categories overview

This project groups nodes into categories to make them easier to find in ComfyUI. Below is a short summary of the categories provided by ComfyUI_Eclipse:

- **Eclipse (Main)** — Top-level group for general Eclipse nodes and primary entry points. Contains high-level helpers and commonly used nodes.
- **Loader** — Smart loaders and checkpoint loaders (model / VAE / CLIP / latent). Advanced loaders with multi-format support including Standard Checkpoints, UNet, Nunchaku quantized models, and GGUF formats.
- **Conversion** — Type conversion helpers (Any → Float/Integer/String/Combo, lists ↔ batches, image/mask conversions, string merging, pipe concatenation, etc.).
- **Folder** — Nodes for creating and managing project folders, filename prefixing, and smart folder utilities with placeholder support to organize outputs.
- **Image** — Image utilities for loading from various sources, previewing, saving with advanced metadata, and manipulating images in workflows.
- **Router** — Routing and control nodes for conditional execution, switches, multi-switches, and any-type data passing through workflows.
- **Pipe** — Pipeline and composition helpers (12-channel pipes, context managers for image/video workflows, generation data, sampler settings, and pipe extraction nodes).
- **Primitives** — Small building-block nodes for basic values (Boolean, Integer, Float, String) used in control flow and logic operations.
- **Settings** — Nodes for sampler configurations, resolution presets, directory settings, ControlNet union types, and video name generators used to tune pipelines.
- **Text** — String and text-processing helpers (multiline input, smart prompts, wildcard processing, regex replacement, dual text inputs).
- **Video** — Video workflow utilities (loop/keep calculators, video clip combination, seamless joining, frame helpers for professional video generation).
- **Utilities** — General utility nodes (LoRA stack management, Show Any for debugging, workflow control with Stop, RAM/VRAM cleanup).

If you open ComfyUI after installing the package you'll find these categories in the node chooser; categories are intended to be concise and practical so you can quickly locate the right node for your workflow.

## Files by category

### Conversion
Convenience nodes for type conversion, list/batch transforms, string merging, and context/pipe manipulation.
- Concat Multi - Concatenate multiple pipes
- Convert Primitive - Convert Any to String/Integer/Float/Combo
- Convert To Batch - Convert image/mask lists to batches
- Convert to List - Convert image/mask batches to lists
- Detection to Bboxes - Convert Florence-2 detection data to masks and standardized bboxes
- Image Convert - Convert images to RGB format
- Join - Join strings, lists, and pipes
- Lora Stack to String - Convert LoRA stack to formatted string
- Merge Strings - Merge multiple strings
- String from List - Extract string from list by index
- Widget to String - Convert widget values to strings

### Folder
Nodes for creating and managing project folders, filename prefixing, and smart folder utilities to organize outputs.
- Add Folder - Add folder prefix to paths
- Filename Prefix - Add customizable filename prefix
- Smart Folder v2 - Dual Image/Video mode folder with date/batch subfolders, resolution presets, and latent type config

### Image
Image utilities for loading, previewing, saving, and manipulating images in workflows and output nodes.
- Add Watermark Image - Add watermark to images with positioning and scaling options
- Load Image - Load single image with metadata
- Load Image From Folder - Batch image loading from folders with shuffle mode, combo-chip index modes, and multi-folder support
- Load Image Path - Load image from custom path
- Load Image Path (Pipe) - Load image from path with pipe output
- Preview Image - Preview images in workflow
- Preview Mask - Preview masks in workflow
- Save Images v2 - Feature-chip image saving with CivitAI metadata, placeholder system, and 7 output formats

### Loader
Nodes for loading model checkpoints with support for Standard, UNet, Nunchaku quantized, and GGUF formats.
- Smart Model Loader - Unified loader with combo-chip feature toggles, templates, CLIP ensemble, LoRA, model sampling, block swap
- Smart LM Loader - VLM/LLM generation, WD14 tagging, multi-task chaining across 8 backends (Transformers, GGUF, vLLM, SGLang, Ollama, llama.cpp)
- Smart Detection - Object detection with Florence-2, Qwen VL, and YOLO; outputs bboxes, masks, and SEGS
- Checkpoint Loader Small - Basic checkpoint loader (legacy)
- Checkpoint Loader Small (Pipe) - Basic checkpoint loader with pipe output (legacy)

### Primitives (Logic / Basic values)
Small building-block nodes for booleans, numbers, and strings, used in control flow and logic operations.
- Boolean - Boolean value input
- Float - Float value input
- Integer - Integer value input
- Integer (Gen) - Integer with generate_after widget to increment number after each generation
- String - String value input

### Router
Routing and control nodes for conditional execution, switches, and data passing.
- Any Passer - Pass any data type through workflow
- Any Passer Purge - Pass any data type with VRAM purge on switch
- Any Dual-Switch - Switch between two any-type inputs
- Any Dual-Switch Purge - Switch between two inputs with VRAM purge on switch
- Any Multi-Switch - Switch between multiple any-type inputs
- Any Multi-Switch Purge - Switch between multiple inputs with VRAM purge on switch
- If Execute - Conditional execution control

### Pipe
Pipeline and composition helpers: context managers, multi-channel pipes, generation data, and out nodes for assembling or emitting pipeline data.
- Pipe 12CH Any - 12-channel any-type pipe
- Context (Image) - Image generation context pipe
- Context (Video) - Video generation context pipe
- Context (WanVideo) - WanVideo wrapper context pipe
- Generation Data - Generation metadata pipe
- Pipe IO Sampler Settings - Input/output node for sampler settings with pipe passthrough
- Pipe Out Checkpoint Loader - Extract checkpoint loader data from pipe
- Pipe Out Load Directory Settings - Extract directory settings from pipe
- Pipe Out Load Image - Extract image data from pipe
- Pipe Out Sampler Settings - Extract sampler settings from pipe
- Pipe Out Smart Folder - Extract smart folder data from pipe
- Pipe Out VCNameGen - Extract video name generator data from pipe
- Pipe Out WanVideo Setup - Extract WanVideo setup from pipe

### Settings
Nodes that expose or compose small settings objects (sampler presets, resolution helpers, directory settings) used to tune pipelines.
- ControlNet Union Type - ControlNet union type selector for Flux
- Custom Size - Custom resolution input
- Image Resolutions - Resolution presets for images
- Video Resolution - Resolution presets for video
- Load Directory Settings - Directory configuration for outputs
- Sampler Selection - Sampler and scheduler selector
- Smart Sampler Settings v1 / v2 - Combo-chip sampler configuration (v1: single seed, v2: dual seed) with noise injection and upscale parameters
- VCNameGen v1 - Video/checkpoint name generator v1
- VCNameGen v2 - Video/checkpoint name generator v2
- WanVideo Setup - WanVideo configuration

### Text
Nodes for prompt construction, text processing, and string manipulation with advanced placeholder and wildcard support.
- Dual Text - Two independent text inputs
- Multiline Text - Multiline string input that also outputs the string as list
- Prompt Styler - Apply pre-built visual styles to prompts with tag_based, natural_language, and custom modes
- Read Prompt Files - Load prompts from multiple text files with index-based navigation
- Replace String - Simple string replacement
- Replace String v2 - Advanced regex string replacement
- Replace String v3 - Combo-chip text processing with 12 feature toggles, SmartTextProcessor pattern-based removal, NSFW handling
- Save Prompt - Save captions/prompts to text, CSV, or JSON with source folder integration
- Smart Prompt v2 - Multi-folder combo-chip prompt building with dynamic dropdown widgets, seed control
- Wildcard Processor - Process wildcards in prompts with weighted options, nested wildcards, and seed control

### Video
Nodes for video clip composition, frame utilities, and loop/frame calculations for video-friendly pipelines.
- Loop Calculator - Calculate loop frame counts for video generation
- Keep Calculator - Calculate frame keep/trim values for video processing
- Combine Video Clips - Concatenate video clips
- Seamless Join Video Clips - Join video clips with seamless transitions

### Utilities
General utility nodes for LoRA management, debugging, resource management, and workflow control.
- Fast Muter - Quick mute toggle for nodes
- Fast Bypasser - Quick bypass toggle for nodes
- Fast Groups Muter - Quick mute toggle for node groups
- Fast Groups Bypasser - Quick bypass toggle for node groups
- Mute / Bypass Repeater - Propagate mute/bypass state to connected nodes
- Node Collector - Collect multiple node references for batch operations
- LoRA Stack - Build LoRA stack configuration
- LoRA Stack Apply - Apply LoRA stack to model/CLIP (supports nunchaku quantized models)
- Show Any - Display any data type for debugging, tensor to image conversion for images and masks
- Stop - Stop workflow execution
- RAM Cleanup - Manual RAM cleanup
- VRAM Cleanup - Manual VRAM cleanup

## Smart LM Subsystem

The Smart LM subsystem integrates large language models (LLMs), vision-language models (VLMs), and detection models directly into Eclipse. Install optional dependencies with `pip install ComfyUI_Eclipse[sml]`. Docker backends require Docker installed separately — see the [Docker guides](Readme/Docker_Installation_Guide.md).

### Supported Model Families

| Family | Vision | Backends | Examples |
|--------|:------:|----------|----------|
| **Qwen** (Qwen2.5-VL, Qwen3-VL) | ✅ | Transformers, GGUF, vLLM, SGLang, Ollama | Qwen2.5-VL-3B/7B, Qwen3-VL-8B |
| **Mistral** (Mistral3, Ministral3) | ✅ | Transformers, vLLM, SGLang, Ollama | Ministral-3-3B/8B-Instruct |
| **Florence-2** | ✅ | Transformers | base, large, base-ft, large-ft, PromptGen |
| **LLaVA** | ✅ | Transformers, GGUF, Ollama | v1.6-vicuna-7b, mistral-7b |
| **LLM** (text-only) | ❌ | GGUF, Ollama | Lexi-Llama-3-8B, Mistral-7B-Instruct |
| **YOLO** | ✅ | Ultralytics (local) | face_yolov8m, hand_yolov8s, person_yolov8m-seg |
| **WD14 Tagger** | ✅ | ONNX (local) | convnext-v3, eva02-large-v3, swinv2-v3 |

### Backends

| Backend | Type | Notes |
|---------|------|-------|
| **Transformers** | Local | HuggingFace, full precision or FP8, Flash Attention 2 / SDPA |
| **GGUF** | Local | llama-cpp-python, 10+ quantization levels (Q3–Q8, IQ3–IQ4) |
| **Ollama** | Docker | Recommended for easy setup, auto-pulls models, NVIDIA + AMD/ROCm |
| **vLLM** | Docker | Multi-GPU batched inference, NVIDIA + AMD/ROCm |
| **SGLang** | Docker | Multi-GPU with RadixAttention, NVIDIA + AMD/ROCm |
| **llama.cpp** | Docker | Lightweight GGUF inference, CPU/GPU hybrid |
| **YOLO** | Local | Ultralytics object detection & segmentation |
| **WD14** | Local | ONNX-based image tagging (SmilingWolf models) |

### Available Tasks

**Vision** — Simple / Detailed / Ultra Detailed / Cinematic Description, Image Analysis, Detailed Analysis, Tags, Video Summary, OCR

**Text** — Expand Text, Refine & Expand Prompt, Rewrite Style, Tags ↔ Natural Language, Translate to English, Short Story, Summarize, Prompt Variations

**Custom** — Direct Chat, Question Answering, Custom Instruction, Wan 2.2 Scene / Timeline (5s / 20s, plus 2s and 3s slow-pacing variants)

**Florence-only** — PromptGen Analyse / Mixed Caption / Mixed Caption Plus

**Detection** (Smart Detection node) — Caption to Phrase Grounding, Region Caption, Dense Region Caption, Region Proposal, Referring Expression Segmentation, OCR With Region, DocVQA

**YOLO** (Smart Detection node) — all-class detection with optional class filtering, instance segmentation (-seg models)

### Docker Helper Scripts (Linux)

Located in `scripts/` — interactive shell scripts for Docker setup and management:

| Script | Description |
|--------|-------------|
| `install-docker-engine.sh` | Install Docker Engine + NVIDIA Container Toolkit (multi-distro) |
| `remove-docker-nvidia.sh` | Safely remove Docker and NVIDIA toolkit, with optional data purge |
| `manage-docker-images.sh` | Pull, update, list, inspect, and clean backend Docker images |

### Backward Compatibility

If upgrading from standalone **ComfyUI_SmartLML**, existing workflows using `[SML]` node IDs will continue to work — deprecated wrapper nodes forward to the new `[Eclipse]` implementations transparently. Remove the standalone SmartLML pack after upgrading to avoid conflicts.

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

- **Pipe Out Checkpoint Loader (`Pipe Out Checkpoint Loader [Eclipse]`):** Extracts model, CLIP, VAE, latent, dimensions, batch size, and model/VAE names from checkpoint loader pipes.
- **Pipe Out Smart Folder (`Pipe Out Smart Folder [Eclipse]`):** Extracts smart folder configuration including paths, dimensions, and placeholder data.
- **Pipe Out Sampler Settings (`Pipe Out Sampler Settings [Eclipse]`):** Extracts all sampler and generation parameters (sampler, scheduler, steps, CFG, seed, denoise, etc.).
- **Pipe Out Load Directory Settings (`Pipe Out Load Directory Settings [Eclipse]`):** Extracts directory settings for output path management.
- **Pipe Out Load Image (`Pipe Out Load Image [Eclipse]`):** Extracts image data and associated metadata from image loading pipes.
- **Pipe Out VCNameGen (`Pipe Out VCNameGen [Eclipse]`):** Extracts video/checkpoint name generator configuration.
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
