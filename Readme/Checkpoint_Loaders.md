# Standalone Loaders Guide

These are lightweight, focused loaders for users who want direct control over individual components without the all-in-one Smart Model Loader.

## Table of Contents
- [Overview](#overview)
- [Model Loader](#model-loader)
- [Model Loader Pipe](#model-loader-pipe)
- [CLIP Loader](#clip-loader)
- [VAE Loader](#vae-loader)
- [Combo-Chip Features](#combo-chip-features)
- [Model Types](#model-types)
- [LoRA Support](#lora-support)
- [Model Sampling](#model-sampling)
- [Block Swap](#block-swap)
- [Connecting the Pipe](#connecting-the-pipe)
- [Smart Model Loader vs Standalone](#smart-model-loader-vs-standalone)
- [Troubleshooting](#troubleshooting)

---

## Overview

Eclipse provides four standalone loader nodes for users who prefer direct, focused control over individual components:

| Node | Output | Use Case |
|------|--------|----------|
| **Model Loader** | model, clip, vae, model_name | Direct outputs — wire straight to KSampler, CLIP Encode, VAE Decode |
| **Model Loader Pipe** | pipe | Single pipe output — cleaner wiring with IO/Pipe Out extraction |
| **CLIP Loader** | clip | External CLIP loading (1–4 modules, 23+ architecture types) |
| **VAE Loader** | vae, vae_name | External VAE loading with enhanced Wan 2.1 support |

All four loaders use the same combo-chip feature system as the Smart Model Loader but without latent, sampler, seed, or template features — they focus purely on model loading.

### When to Use Standalone Loaders

- You want **separate nodes** for model, CLIP, and VAE instead of one monolithic loader
- You're loading **UNet-only models** that have no baked CLIP/VAE and need external CLIP + VAE loaders
- You prefer **direct outputs** (model → KSampler) over pipe extraction
- You want the **simplest possible setup** — no latent, sampler, or template configuration

For the all-in-one experience with templates, latent generation, sampler settings, and seed control, use the [Smart Model Loader](Smart_Loaders.md).

---

## Model Loader

**Node:** `Model Loader [Eclipse]` — Category: `Eclipse > Loader`

Loads a model and outputs individual components directly. Supports all model formats with optional LoRA, model sampling, and block swap via combo-chips.

### Outputs

| Output | Type | Description |
|--------|------|-------------|
| `model` | MODEL | Diffusion model for image generation |
| `clip` | CLIP | Text encoder (baked from checkpoint, None for UNet/Nunchaku/GGUF) |
| `vae` | VAE | Image encoder/decoder (baked from checkpoint, None for UNet/Nunchaku/GGUF) |
| `model_name` | STRING | Checkpoint filename |

### Example Wiring

```
Model Loader
├─ model ────────> KSampler (model)
├─ clip ─────────> CLIP Text Encode (Positive)
├─ clip ─────────> CLIP Text Encode (Negative)
├─ vae ──────────> VAE Decode
└─ model_name ───> (metadata / display)
```

**UNet/Nunchaku/GGUF models** don't include CLIP or VAE — pair with **CLIP Loader** and **VAE Loader** for those formats.

---

## Model Loader Pipe

**Node:** `Model Loader Pipe [Eclipse]` — Category: `Eclipse > Loader`

Same model loading as Model Loader but outputs a single PIPE dict instead of separate connections.

### Pipe Keys

| Key | Condition | Description |
|-----|-----------|-------------|
| `model` | Always | Diffusion model |
| `clip` | If checkpoint has baked CLIP | Text encoder |
| `vae` | If checkpoint has baked VAE | Image encoder/decoder |
| `model_name` | Always | Checkpoint filename |
| `is_nunchaku` | Always | Whether model is Nunchaku format |
| `lora_names` | If LoRA chip enabled | Comma-separated LoRA filenames |
| `clip_skip` | If standard checkpoint with CLIP layer trimming | CLIP layer value |

### Example Wiring

```
Model Loader Pipe
└─ pipe ──────────> IO Checkpoint Loader
                    ├─ model ──────> KSampler
                    ├─ clip ───────> CLIP Text Encode
                    └─ vae ────────> VAE Decode
```

---

## CLIP Loader

**Node:** `CLIP Loader [Eclipse]` — Category: `Eclipse > Loader`

Loads 1–4 external CLIP text encoder modules. Required for UNet, Nunchaku, and GGUF models that don't include a baked CLIP.

### Inputs

| Input | Description |
|-------|-------------|
| `clip_count` | Number of CLIP modules to load (1–4) |
| `clip_name1`–`clip_name4` | CLIP model files (from `models/clip/` and `models/text_encoders/`) |
| `clip_type` | Architecture type — determines how modules are combined |

### Supported CLIP Types

`flux`, `flux2`, `sd3`, `sdxl`, `stable_cascade`, `stable_audio`, `hunyuan_dit`, `mochi`, `ltxv`, `hunyuan_video`, `pixart`, `cosmos`, `lumina2`, `wan`, `hidream`, `chroma`, `ace`, `omnigen2`, `qwen_image`, `hunyuan_image`, `hunyuan_video_15`, `ovis`, `kandinsky5`, `kandinsky5_image`, `newbie`

### Output

| Output | Type | Description |
|--------|------|-------------|
| `clip` | CLIP | Loaded text encoder(s) |

### Example: Flux Model Setup

```
Model Loader (UNet)                CLIP Loader
├─ model ──> KSampler              ├─ clip ──> CLIP Text Encode (Pos)
                                   └─ clip ──> CLIP Text Encode (Neg)

Settings: clip_count=2, clip_type="flux"
  clip_name1 = clip_l.safetensors
  clip_name2 = t5xxl_fp16.safetensors
```

---

## VAE Loader

**Node:** `VAE Loader [Eclipse]` — Category: `Eclipse > Loader`

Loads an external VAE model with enhanced architecture detection (including Wan 2.1 support). Use this when your model format doesn't include a baked VAE, or when you want to override the baked VAE with a different one.

### Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `vae_name` | — | VAE model file from `models/vae/` |
| `disable_offload` | `true` | Keep VAE on GPU for faster decode (disable = allow CPU offload) |

### Outputs

| Output | Type | Description |
|--------|------|-------------|
| `vae` | VAE | Loaded VAE model |
| `vae_name` | STRING | VAE filename |

---

## Combo-Chip Features

Both Model Loader and Model Loader Pipe use a combo-chip bar with 4 optional features:

| Chip | Default | What It Enables |
|------|---------|-----------------|
| `lora` | Off | LoRA loading (1–3 slots with individual weights) |
| `model_sampling` | Off | Advanced sampling method override |
| `block_swap` | Off | Transformer block offloading for VRAM savings |
| `memory_cleanup` | **On** | Purge VRAM before loading |

Click a chip to toggle it. Enabled chips reveal their associated widgets; disabled chips hide them for a cleaner UI.

### Always-Visible Inputs

These inputs are always shown regardless of chip state:

| Input | Description |
|-------|-------------|
| `model_type` | Select format: Standard Checkpoint, UNet, Nunchaku Flux/Qwen/ZImage, GGUF |
| `ckpt_name` / `unet_name` / etc. | Model file selector (shown based on `model_type`) |
| `enable_clip_layer` | Enable baked CLIP layer trimming (Standard Checkpoint only) |
| `stop_at_clip_layer` | CLIP layer to stop at (default: `-2`) |

---

## Model Types

| Type | File Location | CLIP | VAE | Notes |
|------|---------------|------|-----|-------|
| Standard Checkpoint | `models/checkpoints/` | Baked | Baked | Full model with embedded CLIP + VAE |
| UNet Model | `models/diffusion_models/` | None | None | Diffusion model only — needs external CLIP + VAE loaders |
| Nunchaku Flux | `models/diffusion_models/` | None | None | Quantized Flux — needs external CLIP + VAE |
| Nunchaku Qwen | `models/diffusion_models/` | None | None | Quantized Qwen — needs external CLIP + VAE |
| Nunchaku ZImage | `models/diffusion_models/` | None | None | Quantized ZImage — needs external CLIP + VAE |
| GGUF Model | `models/diffusion_models_gguf/` | None | None | GGUF quantized — needs external CLIP + VAE |

**Standard Checkpoint** is the only type that includes baked CLIP and VAE. All other types require separate **CLIP Loader** and **VAE Loader** nodes.

### Nunchaku-Specific Settings

Visible only when `model_type` is Nunchaku Flux/Qwen/ZImage:

| Input | Default | Description |
|-------|---------|-------------|
| `data_type` | bfloat16 | Model precision |
| `cache_threshold` | 0.0 | Cache threshold (0 = disabled) |
| `attention` | flash-attention2 | Attention implementation |
| `i2f_mode` | enabled | GEMM implementation |
| `cpu_offload` | auto | CPU offload mode |
| `num_blocks_on_gpu` | 30 | GPU blocks (Qwen/ZImage only) |
| `use_pin_memory` | enable | Pinned memory for faster transfers |

### GGUF-Specific Settings

Visible only when `model_type` is GGUF:

| Input | Default | Description |
|-------|---------|-------------|
| `gguf_dequant_dtype` | default | Dequantization dtype |
| `gguf_patch_dtype` | default | LoRA patch dtype |
| `gguf_patch_on_device` | no | Apply patches on GPU |

---

## LoRA Support

Enable the `lora` chip to reveal 1–3 LoRA slots.

| Input | Description |
|-------|-------------|
| `lora_count` | Number of active LoRA slots (1–3) |
| `lora_name_1`–`lora_name_3` | LoRA files from `models/loras/` |
| `lora_weight_1`–`lora_weight_3` | Model weight per LoRA (–10 to 10, default 1.0) |

LoRAs are applied in order. Set a LoRA to "None" to skip that slot.

---

## Model Sampling

Enable the `model_sampling` chip to override the model's default sampling behavior.

| Method | Default Shift | Use Case |
|--------|--------------|----------|
| None | — | Use model's built-in sampling |
| SD3 | 3.0 | Stable Diffusion 3 |
| AuraFlow | 1.73 | AuraFlow models |
| Flux | max_shift=1.15, base_shift=0.5 | Flux models (shift scales with resolution) |
| Stable Cascade | 2.0 | Stable Cascade |
| LCM | — | Latent Consistency Models (distilled) |
| ContinuousEDM | — | Continuous EDM sampling (with subtype selection) |
| ContinuousV | — | Continuous V-prediction sampling |
| LTXV | — | LTX Video models |

---

## Block Swap

Enable the `block_swap` chip to offload transformer blocks from GPU to CPU, trading speed for VRAM savings.

| Input | Default | Description |
|-------|---------|-------------|
| `blocks_to_swap` | 5 | Number of blocks to offload (0 = disabled) |
| `offload_embeddings` | no | Also offload embedding/projection layers |

Suggested values by architecture: Flux/Chroma ~10 (max 57), SD3 ~8 (max 24–38), Wan ~10 (max 30–40), HunyuanVideo ~10 (max 60).

> **Note:** On ComfyUI 0.18.0+ with native dynamic VRAM management, the block_swap chip is automatically disabled — ComfyUI handles offloading natively.

---

## Connecting the Pipe

Model Loader Pipe outputs a single PIPE — use these dedicated nodes to extract, override, or merge its values:

| Node | Type | Description |
|------|------|-------------|
| **IO Checkpoint Loader** | IO (bidirectional) | Pipe in + direct inputs → merged pipe out + individual outputs (model, clip, vae, latent, sampler, dimensions, model_name) |
| **IO Context Image** | IO (bidirectional) | Full context merge — model, clip, vae, latent, conditioning, images, sampler, prompts, path |
| **IO Generation Data** | IO (bidirectional) | Extract/override generation metadata — steps, cfg, sampler, scheduler, seed, prompts, model/vae/lora names |
| **IO Pipe 12/24/36CH Any** | IO (bidirectional) | Generic any-type channel extraction |
| **Concat Pipe Multi** | Merge | Combine multiple pipes into one (2–64 inputs, overwrite/preserve/merge strategy) |

**IO nodes** are bidirectional: they accept a pipe input plus optional direct inputs, merge them, and output both a combined pipe and individual value outputs. This lets you override specific pipe values mid-chain.

---

## Smart Model Loader vs Standalone

| Feature | Smart Model Loader | Standalone Loaders |
|---------|-------------------|--------------------|
| Model formats | All 6 types | All 6 types |
| LoRA (3 slots) | Yes | Yes |
| Model sampling | Yes | Yes |
| Block swap | Yes | Yes |
| Baked CLIP/VAE | Yes + external CLIP ensemble | Baked only (use CLIP/VAE Loader for external) |
| Templates | Yes | No |
| Latent generation | Yes | No |
| Sampler settings | Yes | No |
| Seed control | Yes | No |
| Output | Pipe only | Direct outputs or Pipe |

**Choose Smart Model Loader** when you want everything in one node with templates and latent/sampler configuration.

**Choose Standalone Loaders** when you want focused nodes with direct outputs, or when pairing UNet/Nunchaku/GGUF models with separate CLIP and VAE loaders.

---

## Troubleshooting

### Model Not Found

- Verify the file is in the correct folder for its type (see [Model Types](#model-types))
- Restart ComfyUI to refresh file lists
- Check file permissions

### CLIP or VAE is None

If Model Loader outputs None for clip or vae, the selected model type doesn't include them. Add a **CLIP Loader** and/or **VAE Loader** node.

### Legacy Format Warning

`.ckpt`, `.pt`, `.pth` files trigger a security warning. Convert to `.safetensors` or ignore if you trust the source.

### Out of Memory

1. Enable `block_swap` chip and increase `blocks_to_swap`
2. Enable `memory_cleanup` chip (purges VRAM before loading)
3. Use quantized models (Nunchaku or GGUF) for lower VRAM usage
4. Close other GPU applications

---

## Related Documentation

- [Smart Model Loader Guide](Smart_Loaders.md) — All-in-one loader with templates, latent, sampler
- [Smart Sampler Settings v1 / v2](Smart_Sampler_Settings_v2.md) — Sampler configuration nodes
- [Save Images v2](Save_Images.md) — Save with metadata from pipe

### CLIP Layer Setting Not Working

**Problem:** Changing `stop_at_clip_layer` doesn't seem to affect output.

**Solutions:**
1. Ensure value is between `-24` and `-1`
2. Try more extreme values (like `-1` vs `-4`) to see difference
3. Clear ComfyUI cache and regenerate
4. Check that CLIP is actually connected to your text encoding nodes

### Pipe Output Not Connecting

**Problem:** Using Pipe version, but connections fail.

**Solutions:**
1. Verify you're connecting to pipe-compatible nodes
2. Use "Pipe Out Checkpoint Loader" node to extract components
3. Check that downstream node expects pipe input
4. For standard nodes, use regular Checkpoint Loader Small instead

---

## Related Documentation

- [Smart Loader User Guide](Smart_Loaders.md) - Advanced multi-format loaders with templates and quantization

---

## Quick Reference

### Checkpoint Loader Small

| Setting | Default | Purpose |
|---------|---------|---------|
| ckpt_name | (required) | Which model to load |
| vae_name | Baked VAE | Which VAE to use |
| stop_at_clip_layer | -2 | CLIP layer trimming |

**Outputs:** model, clip, vae, model_name

### Checkpoint Loader Small (Pipe)

| Setting | Default | Purpose |
|---------|---------|---------|
| ckpt_name | (required) | Which model to load |
| vae_name | Baked VAE | Which VAE to use |
| stop_at_clip_layer | -2 | CLIP layer trimming |

**Outputs:** pipe (containing model, clip, vae, metadata)

---

**Need help?** Check the main [README](../README.md) or open an issue on the [GitHub repository](https://github.com/r-vage/ComfyUI_Eclipse).
