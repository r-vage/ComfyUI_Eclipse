# ComfyUI_Eclipse User Documentation

Welcome to the user documentation for ComfyUI_Eclipse! This guide is designed for artists, creators, and users who want to understand how to use the nodes effectively - not for developers.

## Documentation Index

### Model Loaders

**[ComfyUI Smart Model Loader](https://github.com/r-vage/ComfyUI_SmartModelLoader)** — External provider for the six former Eclipse diffusion loader node IDs, loader templates, verified CivitAI/Hugging Face acquisition, and the persistent Download Manager.

**[ComfyUI SmartLLM](https://github.com/r-vage/ComfyUI_SmartLLM)** — External provider for Smart LM Loader, Smart Detection, the Registry Manager, Docker backends, and `/smartlml` APIs.

Smart LM and Detection retain their serialized `[Eclipse]` node IDs in the external pack.

### Settings & Folders

**[Smart Sampler Settings Guide](Smart_Sampler_Settings_v2.md)**
- Single-seed with combo-chip feature selection
- Selective pipe output, noise injection, upscale parameter

**[Smart Folder Guide](Smart_Folder.md)**
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

**[Save Images Guide](Save_Images.md)**
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
- Show Any, Show Text, Stop, Block Swap, Resolution Scale, Loop Calculator

### Installation & Setup

**[Nunchaku Installation Guide](Nunchaku_Installation.md)**
- Installing Nunchaku for quantized Flux models
- Step-by-step installation for ComfyUI Portable
- GPU compatibility information
- Troubleshooting dependency issues
- Understanding performance on different GPU architectures

**[ComfyUI SmartLLM setup and security](https://github.com/r-vage/ComfyUI_SmartLLM)**
- Docker installation for Windows/WSL2 and Linux
- Model repository reference and backend requirements
- Registry, model-integrity, credential, and runtime trust guidance

### Getting Started

If you're new to ComfyUI_Eclipse:

1. **Install the diffusion loaders:** [ComfyUI Smart Model Loader](https://github.com/r-vage/ComfyUI_SmartModelLoader)
   - Preserves the six former Eclipse loader node IDs for existing workflows
   - Includes loader templates and the Download Manager

2. **Configure Settings:** [Smart Sampler Settings](Smart_Sampler_Settings_v2.md) & [Smart Folder](Smart_Folder.md)
   - Set up sampler, scheduler, steps, CFG, seed
   - Configure output folders with date/batch organization

3. **Text Processing:** [Smart Prompt v2](Smart_Prompt.md) & [Wildcard Processor](Wildcard_Processor.md)
   - Build prompts efficiently from organized text files
   - Create prompt templates and generate infinite variations
   - Control randomization with seeds

4. **Image Organization:** [Load Image From Folder](Load_Image_From_Folder.md) & [Save Images](Save_Images.md)
   - Batch image loading with shuffle and auto-stop
   - Save with CivitAI-compatible metadata and placeholders

### Quick Help

**I want to...**

- **Load or download diffusion models** → [ComfyUI Smart Model Loader](https://github.com/r-vage/ComfyUI_SmartModelLoader)
- **Configure sampler settings** → [Smart Sampler Settings](Smart_Sampler_Settings_v2.md)
- **Set up output folders** → [Smart Folder Guide](Smart_Folder.md)
- **Apply visual styles to prompts** → [Prompt Styler Guide](Prompt_Styler.md)
- **Build prompts from files** → [Smart Prompt v2 Guide](Smart_Prompt.md)
- **Create prompt templates** → [Wildcard Processor Guide](Wildcard_Processor.md)
- **Clean up LLM/caption output** → [Replace String v3 Guide](Replace_String_v3.md)
- **Use VLM/LLM for captioning or detection** → [ComfyUI SmartLLM](https://github.com/r-vage/ComfyUI_SmartLLM)
- **Set up Smart LM Docker backends** → [ComfyUI SmartLLM](https://github.com/r-vage/ComfyUI_SmartLLM)
- **Save images with metadata** → [Save Images Guide](Save_Images.md)
- **Organize outputs with placeholders** → [Save Images Guide](Save_Images.md#placeholder-system)
- **Batch load images from folders** → [Load Image From Folder Guide](Load_Image_From_Folder.md)
- **Automatically upgrade workflows** → [Workflow Migration Tool Guide](workflow_migration.md)
- **Install Nunchaku support** → [ComfyUI-nunchaku](https://github.com/nunchaku-tech/ComfyUI-nunchaku): clone into `custom_nodes/`

### Common Questions

**Q: Which loader should I use?**

A: Install **ComfyUI Smart Model Loader** alongside Eclipse. It owns the six unchanged `[Eclipse]` diffusion-loader node IDs and keeps existing workflows compatible.

**Q: What are combo-chips?**

A: Combo-chips are clickable toggle buttons used across major nodes (Smart Model Loader, Smart Sampler Settings, Save Images, Replace String v3, Smart Prompt v2, etc.). They let you enable/disable feature sections — only enabled sections appear in the UI, keeping the node compact.

**Q: How do I reduce VRAM usage?**

A: Use ComfyUI Smart Model Loader for loader-integrated block swapping, or Eclipse's Universal Block Swap node for an already loaded diffusion model.

**Q: What are templates?**

A: Loader templates now belong to ComfyUI Smart Model Loader and are migrated there without deleting the Eclipse originals.

**Q: How do I build prompts quickly?**

A: Use [Smart Prompt v2](Smart_Prompt.md) for combo-chip folder selection with dropdowns, [Wildcard Processor](Wildcard_Processor.md) for template-based generation, or [Prompt Styler](Prompt_Styler.md) to apply pre-built visual styles.

**Q: How do I clean up LLM/caption output?**

A: Use [Replace String v3](Replace_String_v3.md) with combo-chip feature toggles. Enable features like `instructions`, `image_style`, `background`, `mood` etc. to selectively remove unwanted content from LLM descriptions.

**Q: How do I install Nunchaku for quantized models?**

A: Follow the [Nunchaku installation guide](Nunchaku_Installation.md). ComfyUI Smart Model Loader detects the backend for diffusion loading, while Eclipse retains its independent Nunchaku integrations.

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
| Loader Templates | `ComfyUI_SmartModelLoader/templates/` |
| Smart Prompt Files | `ComfyUI_Eclipse/prompts/` (also via `models/Eclipse/prompts/` junction) |
| Wildcard Files | `ComfyUI_Eclipse/wildcards/` |
| Prompt Styler Styles | `ComfyUI_Eclipse/styles/` (also via `models/Eclipse/styles/` junction) |
| LLM/VLM Models | `ComfyUI/models/LLM/` by default (configurable in Smart LM Loader settings) |
| YOLO Models | `ComfyUI/models/ultralytics/bbox/` or `ComfyUI/models/ultralytics/segm/` |
| SmartLLM Registry | `ComfyUI_SmartLLM/registry/` |
| SmartLLM Config Files | `ComfyUI_SmartLLM/config/` |
| SmartLLM Docker Config | `ComfyUI_SmartLLM/docker_config.json` |

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
- **License:** Apache-2.0 - See [LICENSE](../LICENSE)

### What's Not Covered Here

This user documentation focuses on Eclipse text processing and image saving. Diffusion-loader documentation lives with ComfyUI Smart Model Loader; Smart LM and detection documentation lives with ComfyUI SmartLLM.

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
