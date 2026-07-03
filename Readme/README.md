# ComfyUI_Eclipse User Documentation

Welcome to the user documentation for ComfyUI_Eclipse! This guide is designed for artists, creators, and users who want to understand how to use the nodes effectively - not for developers.

## Documentation Index

### Model Loaders

**[Smart Model Loader Guide](Smart_Loaders.md)** — The primary model loader
- Unified loader replacing 8 deprecated loaders
- Combo-chip feature toggles (templates, CLIP, VAE, latent, sampler, LoRA, model sampling, block swap)
- Multi-format: Standard Checkpoints, UNet, Nunchaku Flux/Qwen/ZImage, GGUF
- CLIP ensemble (up to 4 modules, 27 architecture types)
- Template save/load system
- LoRA support (3 slots), model sampling (8 methods), block swap

**[Standalone Loaders Guide](Checkpoint_Loaders.md)** — Focused component loaders
- Model Loader (direct outputs) / Model Loader Pipe (pipe output)
- CLIP Loader (1–4 external modules, 23+ architecture types)
- VAE Loader (with Wan 2.1 support)
- All 6 model formats, LoRA, model sampling, block swap

**[Smart LM Loader Guide](Smart_LM_Loader_Guide.md)** — Vision-language models & text LLMs
- 8 backends: Transformers, GGUF, vLLM, SGLang, Ollama, llama.cpp, YOLO, WD14
- Qwen VL, Mistral, Florence-2, LLaVA, text-only LLMs
- Vision tasks (captioning, analysis, OCR) and text tasks (rewrite, expand, translate)
- Template save/load, multi-task chaining, Docker integration

**[Smart Detection Guide](Smart_Detection_Guide.md)** — Object detection & description
- Florence-2 and Qwen VL detection tasks (grounding, region caption, segmentation)
- YOLO object detection and instance segmentation
- Outputs bboxes, masks, and SEGS

### Settings & Folders

**[Smart Sampler Settings v1 / v2 Guide](Smart_Sampler_Settings_v2.md)**
- v1: single-seed with combo-chip feature selection — simpler for standard workflows
- v2: dual-seed (image_seed + prompt_seed) with per-seed mode chips
- Both: selective pipe output, noise injection, upscale parameters

**[Smart Folder v2 Guide](Smart_Folder_v2.md)**
- Dual Image/Video mode with path construction (root → date_time → batch)
- Image mode: resolution presets, latent type config
- Video mode: frame rate, context length, loop count, overlap, skip calculations

### Text Processing

**[Prompt Styler Guide](Prompt_Styler.md)**
- Apply 108+ pre-built visual styles to prompts
- Three modes: tag_based, natural_language, custom
- Index-based batch processing with control_after_generate
- Create custom style files (CSV/JSON)
- Automatic negative prompt generation

**[Smart Prompt v2 Guide](Smart_Prompt.md)**
- Multi-folder combo-chip selection — choose which prompt folders are active
- Dynamic dropdown widgets for each text file in selected folders
- Seed-controlled random selection for reproducible results
- Creating custom prompt libraries

**[Wildcard Processor Guide](Wildcard_Processor.md)**
- Template-based prompt expansion
- Wildcard syntax and patterns
- Weighted random selection
- Nested wildcards
- Creating wildcard files

**[ReadPromptFiles Guide](ReadPromptFiles_Usage.md)** ⭐ NEW
- Load prompts from multiple text files with index navigation
- Navigation modes: fixed index, random (-1), increment (-2), decrement (-3)
- JavaScript buttons for easy mode switching
- Bounds-safe architecture prevents index errors
- Multi-file support with quoted paths
- Auto file change detection

**[Save Prompt Guide](Save_Prompt.md)** ⭐ NEW
- Save captions/prompts to txt, csv, json
- Source folder integration for batch captioning
- Placeholder system (%source_filename, %date, etc.)
- Auto-numbering and append modes
- NSFW auto-detection for JSON

**[Replace String v3 Guide](Replace_String_v3.md)**
- 12 combo-chip feature toggles for selective text processing
- SmartTextProcessor JSON pattern-based content detection and removal
- Auto-detects tags vs prose format
- NSFW content handling (none/soften/remove)
- Age adjustment and LLM list processing

### Image Processing

**[Load Image From Folder Guide](Load_Image_From_Folder.md)**
- Batch image loading with 4 index modes (random, increment, decrement, shuffle)
- Combo-chip mode selection
- Multi-folder cumulative indexing with per-folder caching
- Seed_input freezing for consistent iteration
- Auto-stop at end of folder
- Metadata extraction (ComfyUI, Auto1111, NovelAI)

**[Save Images v2 Guide](Save_Images.md)**
- Combo-chip feature toggles for flexible configuration
- CivitAI-compatible A1111 metadata embedding
- 7 output formats (PNG, JPG, JPEG, GIF, TIFF, WebP, BMP)
- Placeholder system (%today, %seed, %model, %sampler_name, etc.)
- Preview-only mode (skips disk save and metadata processing)
- LoRA/embedding hashing for Civitai compatibility
- Pipe integration for automatic metadata extraction

### Routing & Variables

**[Get First & Get All Active Guide](GetFirst_GetAllActive.md)**
- Virtual frontend nodes — zero backend cost, resolved at graph serialization
- Get First: resolves the first active SetNode from a prioritized fallback list (single output)
- Get All Active: resolves all active SetNodes simultaneously (one output per var)
- Type filtering, auto-color, green dot indicators, subgraph-aware scoping
- Real-world patterns: fallback model chains, progressive image pipelines, metadata collection
- Cross-compatible with KJNodes SetNode and Eclipse SetNode

**[Utility Nodes Guide](Utility_Nodes.md)** — Routers, joiners, cleanup & helpers
- Any Multi-Switch (first non-None), Dual-Switch, IF A Else B, passers
- Join (string/image/mask concatenation), Concat Pipe Multi (merge pipes)
- String DeDuplicate (case-insensitive, weight handling)
- Show Any, Stop, VRAM Cleanup, RAM Cleanup, Fast Bypasser, Loop Calculator

### Installation & Setup

**[Nunchaku Installation Guide](Nunchaku_Installation.md)**
- Installing Nunchaku for quantized Flux models
- Step-by-step installation for ComfyUI Portable
- GPU compatibility information
- Troubleshooting dependency issues
- Understanding performance on different GPU architectures

**[Docker Installation Guide (Windows/WSL2)](Docker_Installation_Guide.md)**
- WSL2 + Docker Desktop + NVIDIA GPU passthrough setup
- Required for vLLM, SGLang, Ollama, llama.cpp Docker backends

**[Docker Installation Guide (Linux)](Docker_Installation_Guide_Linux.md)**
- Docker Engine + NVIDIA Container Toolkit setup
- Multi-distro support (Ubuntu, Fedora, Arch, etc.)

**[Model Repository Reference](Model_Repos_Reference_Links.md)**
- HuggingFace URLs for all supported LLM/VLM models
- Organized by model family (Qwen, Mistral, Florence-2, LLaVA, etc.)

**[⚠️ LLM Security Warning](LLM_Security_Warning.md)** — **read before running any LLM**
- Documented attacks against Hugging Face / pickle / `trust_remote_code`
- Three-tier security ladder: in-process `transformers` vs. Docker vs. Ollama
- Why venv is hygiene (not a sandbox) and never use system Python
- Why Ollama + Docker is the recommended safe default

### Getting Started

If you're new to ComfyUI_Eclipse:

1. **Start Here:** [Smart Model Loader Guide](Smart_Loaders.md)
   - The primary model loader for all workflows
   - Supports all model formats (Standard, UNet, Nunchaku, GGUF)
   - Use combo-chip toggles to show only what you need
   - Save/load configurations with templates

2. **Configure Settings:** [Smart Sampler Settings v2](Smart_Sampler_Settings_v2.md) & [Smart Folder v2](Smart_Folder_v2.md)
   - Set up sampler, scheduler, steps, CFG, seed
   - Configure output folders with date/batch organization

3. **Text Processing:** [Smart Prompt v2](Smart_Prompt.md) & [Wildcard Processor](Wildcard_Processor.md)
   - Build prompts efficiently from organized text files
   - Create prompt templates and generate infinite variations
   - Control randomization with seeds

4. **Image Organization:** [Load Image From Folder](Load_Image_From_Folder.md) & [Save Images v2](Save_Images.md)
   - Batch image loading with shuffle and auto-stop
   - Save with CivitAI-compatible metadata and placeholders

### Quick Help

**I want to...**

- **Load a model** → [Smart Model Loader Guide](Smart_Loaders.md)
- **Save/load configurations** → [Template System](Smart_Loaders.md#template-system)
- **Use quantized models** → [Model Types & Formats](Smart_Loaders.md#model-types--formats)
- **Reduce VRAM usage** → [Quantization Configuration](Smart_Loaders.md#quantization-configuration)
- **Build CLIP ensembles** → [CLIP Configuration](Smart_Loaders.md#clip-configuration)
- **Configure sampler settings** → [Smart Sampler Settings v2](Smart_Sampler_Settings_v2.md)
- **Set up output folders** → [Smart Folder v2 Guide](Smart_Folder_v2.md)
- **Apply visual styles to prompts** → [Prompt Styler Guide](Prompt_Styler.md)
- **Build prompts from files** → [Smart Prompt v2 Guide](Smart_Prompt.md)
- **Create prompt templates** → [Wildcard Processor Guide](Wildcard_Processor.md)
- **Clean up LLM/caption output** → [Replace String v3 Guide](Replace_String_v3.md)
- **Use VLM/LLM for captioning** → [Smart LM Loader Guide](Smart_LM_Loader_Guide.md)
- **Detect objects with YOLO/VLM** → [Smart Detection Guide](Smart_Detection_Guide.md)
- **Set up Docker backends** → [Docker Installation (Windows)](Docker_Installation_Guide.md) or [Linux](Docker_Installation_Guide_Linux.md)
- **Save images with metadata** → [Save Images v2 Guide](Save_Images.md)
- **Organize outputs with placeholders** → [Save Images v2 Guide](Save_Images.md#placeholder-system)
- **Batch load images from folders** → [Load Image From Folder Guide](Load_Image_From_Folder.md)
- **Install Nunchaku support** → [ComfyUI-nunchaku](https://github.com/nunchaku-tech/ComfyUI-nunchaku): clone into `custom_nodes/`

### Common Questions

**Q: Which loader should I use?**

A: Use the **Smart Model Loader** — it's the single unified loader that replaces 8 older variants (Smart Loader Plus, Smart Loader, Smart Loader Basic, etc.). Use combo-chip feature toggles to enable only the sections you need.

**Q: What are combo-chips?**

A: Combo-chips are clickable toggle buttons used across major nodes (Smart Model Loader, Smart Sampler Settings v2, Save Images v2, Replace String v3, Smart Prompt v2, etc.). They let you enable/disable feature sections — only enabled sections appear in the UI, keeping the node compact.

**Q: How do I reduce VRAM usage?**

A: Use quantized models (Nunchaku or GGUF) with the Smart Model Loader. Enable the **block_swap** chip to offload model blocks to CPU. See [Quantization Configuration](Smart_Loaders.md#quantization-configuration) for details.

**Q: What are templates?**

A: Templates save your complete loader configuration (model, CLIP, VAE, sampler, etc.) so you can restore it instantly later. Enable the **templates** chip in Smart Model Loader to access Save/Load. See [Template System](Smart_Loaders.md#template-system).

**Q: How do I build prompts quickly?**

A: Use [Smart Prompt v2](Smart_Prompt.md) for combo-chip folder selection with dropdowns, [Wildcard Processor](Wildcard_Processor.md) for template-based generation, or [Prompt Styler](Prompt_Styler.md) to apply pre-built visual styles.

**Q: How do I clean up LLM/caption output?**

A: Use [Replace String v3](Replace_String_v3.md) with combo-chip feature toggles. Enable features like `instructions`, `image_style`, `background`, `mood` etc. to selectively remove unwanted content from LLM descriptions.

**Q: How do I install Nunchaku for quantized models?**

A: Clone the [ComfyUI-nunchaku](https://github.com/nunchaku-tech/ComfyUI-nunchaku) repository into your `custom_nodes/` folder and restart ComfyUI. The Smart Model Loader will automatically detect the extension and enable Nunchaku model options.

**Q: What GPU do I need for Nunchaku/quantized models?**

A: RTX 30 and 40 series GPUs work well with the primary benefit being lower VRAM usage. RTX 50 series (Blackwell) will add native FP4 acceleration for additional speed.

### File Locations Reference

| Item | Location |
|------|----------|
| Standard Checkpoints | `ComfyUI/models/checkpoints/` |
| UNet Models | `ComfyUI/models/diffusion_models/` |
| Nunchaku Models | `ComfyUI/models/diffusion_models/` |
| Qwen Models | `ComfyUI/models/diffusion_models/` |
| GGUF Models | `ComfyUI/models/diffusion_models/` |
| CLIP Files | `ComfyUI/models/clip/`<br>`ComfyUI/models/text_encoders/` |
| VAE Files | `ComfyUI/models/vae/` |
| Templates | `ComfyUI_Eclipse/templates/` (also via `models/Eclipse/templates/` junction) |
| Smart Prompt Files | `ComfyUI_Eclipse/prompts/` (also via `models/Eclipse/prompts/` junction) |
| Wildcard Files | `ComfyUI_Eclipse/wildcards/` |
| Prompt Styler Styles | `ComfyUI_Eclipse/styles/` (also via `models/Eclipse/styles/` junction) |
| LLM/VLM Models | `ComfyUI/models/LLM/` (configurable in Eclipse settings) |
| YOLO Models | `ComfyUI/models/yolo/` |
| LLM Registry | `ComfyUI_Eclipse/registry/` |
| LLM Config | `ComfyUI_Eclipse/config/` |
| Docker Config | `ComfyUI_Eclipse/docker_config.json` |

### Required Extensions

Some features require additional extensions:

**For Nunchaku Models (Quantized Flux/Qwen):**
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/nunchaku-tech/ComfyUI-nunchaku
```

**For GGUF Models:**
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/city96/ComfyUI-GGUF
```

### Recommended File Formats

| Format | Status | Notes |
|--------|--------|-------|
| `.safetensors` | ✅ Recommended | Safe, fast, modern |
| `.sft` | ✅ Recommended | Safetensors alternative |
| `.ckpt` | ⚠️ Legacy | Works but shows warning |
| `.pt` | ⚠️ Legacy | Works but shows warning |
| `.pth` | ⚠️ Legacy | Works but shows warning |
| `.bin` | ⚠️ Risky | PyTorch binary - can execute code |

Always prefer `.safetensors` when available for safety and speed. Avoid `.bin`, `.ckpt`, `.pt`, and `.pth` from untrusted sources as they can contain malicious code.

### Support & Help

- **Main README:** [../README.md](../README.md) - Overview and feature highlights
- **GitHub Issues:** [Report bugs or request features](https://github.com/r-vage/ComfyUI_Eclipse/issues)
- **License:** GPL-3.0 - See [LICENSE](../LICENSE)

### What's Not Covered Here

This user documentation focuses on model loaders, text processing, and image saving. For other Eclipse features:

- **Pipe System** - See main [README](../README.md#the-pipe-ecosystem-of-eclipse)
- **Other Nodes** - See [Files by Category](../README.md#files-by-category)

---

## Contributing to Documentation

Found an error or want to improve these guides?

1. Documentation lives in `Readme/` folder
2. Written in Markdown for easy editing
3. Focus on user-friendly language (not developer jargon)
4. Include examples and step-by-step instructions
5. Submit PRs with improvements

---

**Happy creating!** If these guides helped you, consider starring the [repository](https://github.com/r-vage/ComfyUI_Eclipse) ⭐
