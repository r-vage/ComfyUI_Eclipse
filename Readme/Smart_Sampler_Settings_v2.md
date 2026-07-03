# Smart Sampler Settings v2 [Eclipse]

A combo-chip-driven sampler configuration node with dual seeds (image + prompt), selective pipe output, noise injection, and upscale parameters.

> **Also available:** **Smart Sampler Settings v1** — a simpler single-seed version with the same combo-chip system. Use v1 when you only need one seed and don't need per-seed mode chips. See [v1 vs v2 comparison](#v1-vs-v2) below.

## Table of Contents
- [Overview](#overview)
- [v1 vs v2](#v1-vs-v2)
- [Combo-Chip Features](#combo-chip-features)
- [Inputs](#inputs)
- [Dual Seed System](#dual-seed-system)
- [Noise Injection](#noise-injection)
- [Upscale Parameters](#upscale-parameters)
- [Allow Overwrite](#allow-overwrite)
- [Pipe Output](#pipe-output)
- [Usage Examples](#usage-examples)
- [Tips & Best Practices](#tips--best-practices)

---

## Overview

Smart Sampler Settings v2 replaces the legacy Sampler Settings nodes (6 deprecated variants) with a single combo-chip interface. Enable only the settings you need — unused parameters are hidden and excluded from the pipe output entirely. This means downstream nodes only receive the values you explicitly configure.

### Key Capabilities

- **Selective output** — only enabled chips add values to the pipe
- **Dual seed system** — separate image seed and prompt seed with independent mode chips
- **Noise injection** — sigmas denoise + noise strength for advanced sampling
- **Upscale parameters** — steps, denoise, and scale value for upscale workflows
- **Allow overwrite** — optional flag to let IO nodes override these values

---

## v1 vs v2

Both versions replace the 6 legacy Sampler Settings nodes. Choose based on your seed needs:

| Feature | Smart Sampler Settings (v1) | Smart Sampler Settings v2 |
|---------|----------------------------|---------------------------|
| **Seeds** | Single `seed` | Dual `image_seed` + `prompt_seed` |
| **Seed control** | 3 buttons (Randomize, New Fixed, Last Seed) | Emoji mode chips per seed (🎲 random, ⏫ increment, ⏬ decrement) |
| **Feature chips** | 10 chips | 17 chips (includes per-seed mode chips) |
| **Default features** | sampler, scheduler, steps, cfg, denoise | sampler, scheduler, steps, cfg, denoise, image_seed, 🎲 img random |
| **Core settings** | Same (sampler, scheduler, steps, cfg, guidance, denoise) | Same |
| **Noise injection** | Same (sigmas_denoise, noise_strength) | Same |
| **Upscale** | Same (upscale_steps, upscale_denoise, upscale_value) | Same |
| **Allow overwrite** | Same | Same |
| **Pipe output** | Same selective behavior | Same + backward compat `seed` alias for `image_seed` |

### v1 Combo-Chip Features

| Chip | Controls |
|------|----------|
| `allow_overwrite` | Allow overwrite flag |
| `sampler` | Sampler algorithm |
| `scheduler` | Scheduler algorithm |
| `steps` | Step count |
| `cfg` | CFG scale |
| `guidance` | Guidance scale |
| `denoise` | Denoise strength |
| `seed` | Single seed + 3 control buttons |
| `noise_injection` | Sigmas denoise + noise strength |
| `upscale` | Upscale steps, denoise, scale |

### When to Use v1

- You only need one seed (most standard workflows)
- You prefer button-based seed control over mode chips
- You want a simpler, more compact node

### When to Use v2

- You need separate image and prompt seeds (prompt variation workflows)
- You want inline mode chips (random/increment/decrement) instead of buttons
- You run dual-seed experiments (fixed image seed + varying prompt seed)

---

## Combo-Chip Features

Each chip controls a parameter group. Only enabled chips include their values in the pipe output.

| Chip | Default | Controls |
|------|---------|----------|
| `allow_overwrite` | off | Allow overwrite flag in pipe |
| `sampler` | **on** | Sampler algorithm selection |
| `scheduler` | **on** | Scheduler algorithm selection |
| `steps` | **on** | Step count slider |
| `cfg` | **on** | CFG scale slider |
| `guidance` | off | Guidance scale slider |
| `denoise` | **on** | Denoise strength slider |
| `noise_injection` | off | Sigmas denoise + noise strength |
| `upscale` | off | Upscale steps, denoise, and scale value |
| `image_seed` | **on** | Image generation seed + mode chips |
| `🎲 img random` | **on** | Set image seed to random each queue |
| `⏫ img increment` | off | Increment image seed each queue |
| `⏬ img decrement` | off | Decrement image seed each queue |
| `prompt_seed` | off | Prompt variation seed + mode chips |
| `🎲 prm random` | off | Set prompt seed to random each queue |
| `⏫ prm increment` | off | Increment prompt seed each queue |
| `⏬ prm decrement` | off | Decrement prompt seed each queue |

Default enabled: `sampler`, `scheduler`, `steps`, `cfg`, `denoise`, `image_seed`, `🎲 img random`.

---

## Inputs

### Core Sampler Settings

| Input | Type | Default | Range | Chip |
|-------|------|---------|-------|------|
| `sampler_name` | COMBO | *(ComfyUI samplers)* | — | `sampler` |
| `scheduler` | COMBO | *(ComfyUI schedulers)* | — | `scheduler` |
| `steps` | INT | 30 | 1–150 | `steps` |
| `cfg` | FLOAT | 5.0 | 1.0–30.0 | `cfg` |
| `guidance` | FLOAT | 3.5 | 0–10.0 | `guidance` |
| `denoise` | FLOAT | 1.0 | 0–1.0 | `denoise` |

### Noise Injection (`noise_injection` chip)

| Input | Type | Default | Range |
|-------|------|---------|-------|
| `sigmas_denoise` | FLOAT | 0.45 | 0–1.0 |
| `noise_strength` | FLOAT | 0.50 | 0–1.0 |

### Upscale (`upscale` chip)

| Input | Type | Default | Range |
|-------|------|---------|-------|
| `upscale_steps` | INT | 15 | 1–150 |
| `upscale_denoise` | FLOAT | 0.5 | 0–1.0 |
| `upscale_value` | FLOAT | 1.5 | 0.1–10.0 |

### Seeds

| Input | Type | Default | Chip |
|-------|------|---------|------|
| `image_seed` | INT | 0 | `image_seed` |
| `prompt_seed` | INT | 0 | `prompt_seed` |

### Overwrite Flag

| Input | Type | Default | Chip |
|-------|------|---------|------|
| `allow_overwrite` | BOOLEAN | False | `allow_overwrite` |

---

## Dual Seed System

Smart Sampler Settings v2 provides two independent seeds, each with its own mode chips:

### Image Seed

Controls the primary generation seed. Enable the `image_seed` chip to include it in the pipe.

**Mode chips** (mutually exclusive with concrete values):
- **🎲 img random** — seed resolved to a random value each queue
- **⏫ img increment** — seed incremented from last used value
- **⏬ img decrement** — seed decremented from last used value

When a mode chip is active, the seed widget shows the special value (-1, -2, or -3). The actual resolved seed is computed at queue time and stored for the next increment/decrement.

### Prompt Seed

A secondary seed for prompt variation workflows. Enable the `prompt_seed` chip to include it in the pipe.

**Mode chips** work identically to image seed:
- **🎲 prm random** — random each queue
- **⏫ prm increment** — increment from last value
- **⏬ prm decrement** — decrement from last value

### Seed Buttons

When a seed chip is enabled, three buttons appear below the seed widget:
- **🎲 Randomize Each Time** — sets seed to -1 (random mode)
- **🎲 New Fixed Random** — generates a concrete random seed value
- **♻️ Use Last Queued Seed** — restores the seed from the last execution

### Workflow Save Behavior

When saving a workflow, mode chips are automatically deselected if the seed has been resolved to a concrete value. This prevents stale modes: dragging an output image back into ComfyUI shows the actual seed that was used, not a mode chip that would generate a different seed.

---

## Noise Injection

Enable the `noise_injection` chip to expose:

- **`sigmas_denoise`** — controls the sigma denoising schedule strength
- **`noise_strength`** — controls how much noise is injected

Both values are passed through the pipe as `sigmas_denoise` and `noise_strength` keys. Downstream sampler nodes that support noise injection can read these values.

---

## Upscale Parameters

Enable the `upscale` chip to expose:

- **`upscale_steps`** — number of sampling steps for the upscale pass
- **`upscale_denoise`** — denoise strength for the upscale pass
- **`upscale_value`** — scale factor (e.g., 1.5 = 150% of original size)

These are passed through the pipe for downstream upscale workflows.

---

## Allow Overwrite

Enable the `allow_overwrite` chip to include a `_allow_overwrite` flag in the pipe. When set to `True`, IO pipe nodes can override values from this node with their own direct inputs. When `False` (or absent), this node's values take priority.

---

## Pipe Output

The node outputs a single **PIPE** containing only the values for enabled chips.

| Key | Chip Required | Description |
|-----|---------------|-------------|
| `sampler_name` | `sampler` | Sampler algorithm name |
| `scheduler` | `scheduler` | Scheduler algorithm name |
| `steps` | `steps` | Step count |
| `cfg` | `cfg` | CFG scale |
| `guidance` | `guidance` | Guidance scale |
| `denoise` | `denoise` | Denoise strength |
| `sigmas_denoise` | `noise_injection` | Sigma denoise value |
| `noise_strength` | `noise_injection` | Noise injection strength |
| `upscale_steps` | `upscale` | Upscale step count |
| `upscale_denoise` | `upscale` | Upscale denoise strength |
| `upscale_value` | `upscale` | Upscale scale factor |
| `image_seed` | `image_seed` | Image generation seed |
| `seed` | `image_seed` | Same as image_seed (backward compat alias) |
| `prompt_seed` | `prompt_seed` | Prompt variation seed |
| `_allow_overwrite` | `allow_overwrite` | Overwrite permission flag |

**Key behavior:** Disabled chips produce **no pipe keys** for their parameters. This lets downstream nodes distinguish "not configured" from "configured as 0".

### Connecting the Pipe

Use these dedicated nodes to extract, override, or merge sampler settings from the pipe:

| Node | Type | Description |
|------|------|-------------|
| **IO Sampler Settings v2.1** | IO (bidirectional) | For v1 (single seed) pipes — extract/override sampler, scheduler, steps, cfg, guidance, denoise, seed |
| **IO Sampler Settings v2.2** | IO (bidirectional) | For v2 (dual seed) pipes — same as v2.1 plus image_seed and prompt_seed |
| **IO Context Image** | IO (bidirectional) | Merge sampler pipe into a full context pipe alongside model, clip, vae, latent, images, prompts |
| **Concat Pipe Multi** | Merge | Combine sampler pipe with other pipes (e.g., Smart Model Loader pipe + Smart Folder v2 pipe) |

**IO nodes** are bidirectional: pipe in + optional direct inputs → merged pipe out + individual value outputs. Connect the sampler pipe to an IO node to extract individual values or override specific settings before passing the pipe downstream.

---

## Usage Examples

### Standard Image Generation

1. Enable chips: `sampler`, `scheduler`, `steps`, `cfg`, `denoise`, `image_seed`
2. Select sampler (e.g., `euler`), scheduler (e.g., `karras`)
3. Set steps=30, cfg=5, denoise=1.0
4. Enable `🎲 img random` for a new seed each queue
5. Connect pipe → Smart Model Loader

### img2img with Fixed Seed

1. Enable chips: `sampler`, `scheduler`, `steps`, `cfg`, `denoise`, `image_seed`
2. Set denoise=0.5 for partial denoising
3. Set a concrete image_seed (no mode chip)
4. Connect pipe → Smart Model Loader

### Upscale Workflow

1. Enable standard chips + `upscale`
2. Set upscale_steps=15, upscale_denoise=0.5, upscale_value=1.5
3. Downstream upscale nodes read upscale parameters from pipe

### Dual Seed for Prompt Variations

1. Enable `image_seed` with a fixed concrete seed
2. Enable `prompt_seed` with `🎲 prm random`
3. Each queue uses the same image seed but a different prompt seed
4. Useful for testing prompt variations with consistent composition

---

## Tips & Best Practices

- **Enable only what you need** — fewer chips = smaller pipe, cleaner workflow
- **Seed mode chips** auto-deselect on workflow save to prevent stale modes
- **Image seed** also writes a `seed` key for backward compatibility with older nodes
- **Guidance** is separate from CFG — useful for models that distinguish the two (e.g., Flux)
- **Noise injection** is for advanced workflows — most users only need the core settings
- **Chain with Smart Folder v2** — folder pipe provides path/dimensions, sampler pipe provides generation settings

---

## Related Documentation

- [Smart Model Loader](Smart_Loaders.md) - Reads sampler pipe for generation settings
- [Smart Folder v2](Smart_Folder_v2.md) - Output folder and dimension configuration
- [Save Images v2](Save_Images.md) - Reads pipe for metadata embedding

---

*Part of [ComfyUI Eclipse](README.md) - Advanced nodes for ComfyUI*
