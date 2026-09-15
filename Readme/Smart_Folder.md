# Smart Folder [Eclipse]

A dual-mode output folder configuration node with combo-chip feature selection, supporting both Image and Video generation workflows via a single unified interface.

## Table of Contents
- [Overview](#overview)
- [Visual Tour](#visual-tour)
- [Combo-Chip Features](#combo-chip-features)
- [Path Construction](#path-construction)
- [Image Mode](#image-mode)
- [Video Mode](#video-mode)
- [Common Settings](#common-settings)
- [Pipe Output](#pipe-output)
- [Usage Examples](#usage-examples)
- [Tips & Best Practices](#tips--best-practices)

---

## Overview

Smart Folder configures output paths and generation parameters for downstream nodes via a pipe connection. It supports two modes — **Image** and **Video** — switchable via a combo-chip radio toggle. Each mode exposes only the relevant settings, keeping the node compact.

### Key Capabilities

- **Dual mode** — Image or Video, toggled via radio chips (mutually exclusive)
- **Dynamic path construction** — root folder → date/time subfolder → batch subfolder
- **Image settings** — resolution presets, custom dimensions, latent type selection
- **Video settings** — optional VHS, loop, context, and duration groups around an always-on frame rate
- **Aspect-locked custom sizes** — separate Image and Video ratios with bidirectional width/height editing and divisor alignment
- **Seed control** — optional seed with randomize/increment/decrement modes
- **Pipe output** — all settings passed as a single pipe to downstream nodes

---

## Visual Tour

### One mode-aware pipe

![Smart Folder overview with Pipe Out Smart Folder](assets/smart-folder-overview.png)

Smart Folder keeps the destination and generation-specific settings in one reusable `PIPE`. Image and video are mutually exclusive modes, while optional date/time, batch, Image Size, VHS, Loop, Context, Duration, and seed chips control which settings participate. **Pipe Out Smart Folder** can then expose individual values for branches that need regular sockets.

### Image paths, dimensions, and latent format

![Smart Folder image-mode controls](assets/smart-folder-image-mode.png)

In image mode, the root folder can be extended with date/time and numbered batch subfolders. Enabling `image_size` adds a preset or custom width and height plus the latent format. Custom values can remain free or use an Image-specific aspect lock, and always align to `divisible_by`. These values travel in the pipe for compatible downstream latent creation. The optional seed controls are included only when the `seed` chip is active.

### Video frame budgets and sequential skips

![Smart Folder video-mode controls](assets/smart-folder-video-mode.png)

Video mode always exposes frame cadence and can independently add resolution, VHS loading, loop, context, and duration metadata. Loop automatically enables and locks Context. When VHS, Loop, and Context are enabled, a positive `loop_count` makes `context_length × loop_count` the effective frame-load cap. Context-based skips likewise run only with VHS and Context enabled.

---

## Combo-Chip Features

The node uses a combo-chip widget to toggle feature groups. Default enabled: `image`, `date_time`, and `context`.

| Chip | Default | Controls |
|------|---------|----------|
| `image` | **on** | Image Mode — shows image-specific settings |
| `video` | off | Video Mode — shows video-specific settings |
| `date_time` | **on** | Date/time subfolder creation |
| `batch` | off | Batch subfolder configuration |
| `image_size` | off | Resolution in both modes; latent type in Image Mode |
| `vhs` | off | `frame_load_cap`, skip controls, and `select_every_nth` |
| `loop` | off | `loop_count` and `overlap`; automatically enables Context |
| `context` | **on** | `context_length`; cannot be disabled while Loop is on |
| `duration` | off | Optional duration metadata in seconds |
| `seed` | off | Seed widget with mode buttons |

`image` and `video` are **radio-exclusive** — selecting one deselects the other. This switches the node between Image Mode and Video Mode.

---

## Path Construction

The output path is built in layers:

```
<ComfyUI output directory> / <root_folder> / [date_time] / [batch_folder]
```

### Root Folder

| Mode | Input | Default |
|------|-------|---------|
| Image | `root_folder_image` | `images` |
| Video | `root_folder_video` | `videos` |

Root folders are relative to the ComfyUI output directory. Use either `/` or `\` as the separator for nested folders; for example, `images/dataset` and `images\dataset` resolve to the same path on the backend host.

### Date/Time Subfolder (`date_time` chip)

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `date_time_format` | STRING | `%Y-%m-%d` | strftime format string |
| `date_time_position` | COMBO | `postfix` | `prefix` or `postfix` relative to root folder |

**Position examples:**
- **prefix:** `2025-09-27/images/`
- **postfix:** `images/2025-09-27/`

### Batch Subfolder (`batch` chip)

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `batch_folder_name` | STRING | `batch_{}` | Folder name template (`{}` replaced by number) |
| `batch_number` | INT | 1 | Current batch number |
| `batch_number_control` | COMBO | `fixed` | `fixed` or `increment` (auto-increments each queue) |

**Example path with all layers:**
```
output/images/2025-09-27/batch_1/
```

---

## Image Mode

Enabled when the `image` chip is selected. Shows image-specific settings.

### Resolution (`image_size` chip required)

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `image_size` | COMBO | `832x1216 (2:3 Flux, SDXL)` | Resolution preset |
| `image_aspect_ratio` | COMBO | `Free` | Custom-size ratio; visible only when `image_size` is `Custom` |
| `width` | INT | 832 | Custom width (visible when preset is "Custom") |
| `height` | INT | 1216 | Custom height (visible when preset is "Custom") |
| `divisible_by` | INT | 8 | Custom-size multiple (1–512) |
| `latent_type` | COMBO | `SD3 / Flux / Wan 2.1 / HunyuanVideo` | Latent format (sets channels + spatial downscale) |

Aspect options are `Free`, `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `9:21`, and `21:9`. `Free` keeps width and height independent. With another ratio selected, editing width recalculates height, while editing height recalculates width. The most recently edited edge remains the driver when `divisible_by` changes or the workflow reloads.

The driver edge is aligned first, then the calculated edge is rounded and clamped with the same `divisible_by` rules. For example, width `512`, ratio `9:16`, and divisor `32` resolves to `512 × 896`. Named presets override custom dimensions and are not affected by the aspect selector. The divisor itself is not added to the pipe. The latent type determines the correct empty latent dimensions for the model architecture.

### Always Visible (Image Mode)

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `batch_size` | INT | 1 | Number of images per batch |

---

## Video Mode

Enabled when the `video` chip is selected. Shows video-specific settings.

### Resolution (`image_size` chip required)

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `video_size` | COMBO | *(presets)* | Video resolution preset |
| `video_aspect_ratio` | COMBO | `Free` | Custom-size ratio; visible only when `video_size` is `Custom` |
| `video_width` | INT | 576 | Custom width (visible when preset is "Custom") |
| `video_height` | INT | 1024 | Custom height (visible when preset is "Custom") |
| `divisible_by` | INT | 8 | Custom-size multiple (1–512) |

Video keeps its own aspect selection and last-edited driver, independently of Image Mode. The same bidirectional editing, divisor rounding, bounds, and named-preset override behavior described for Image Mode applies here.

### Frame Settings

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `frame_rate` | FLOAT | 30.0 | Frames per second (8–240) |
| `frame_load_cap` | INT | 162 | VHS chip: max frames to load per batch (0 = no limit) |
| `context_length` | INT | 81 | Context chip: context length for WAN/video models |
| `loop_count` | INT | 0 | Loop chip: derives `frame_load_cap` when VHS and Context are also enabled |
| `overlap` | INT | 0 | Loop chip: overlap frames between clips |
| `duration` | FLOAT | 15.0 | Duration chip: optional seconds metadata (0.5–180.0, step 0.5) |

### Skip Settings

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `skip_first_frames` | INT | 0 | VHS chip: number of initial frames to skip |
| `skip_calculation` | INT | 0 | VHS + Context: additional `context_length × value` skip |
| `skip_calculation_control` | COMBO | `fixed` | VHS + Context: `fixed` or per-queue `increment` |
| `select_every_nth` | INT | 1 | VHS chip: select every Nth frame from input |

### Loop Count Override

When VHS, Loop, and Context are enabled and `loop_count` > 0, the effective `frame_load_cap` becomes:
```
frame_load_cap = context_length × loop_count
```
This overrides the manual `frame_load_cap` value.

### Skip Calculation

The total frames skipped is:
```
total_skip = skip_first_frames + (context_length × skip_calculation)
```
With VHS and Context enabled and `skip_calculation_control` set to `increment`, the `skip_calculation` value increases by 1 after each queue — useful for processing sequential segments of a long video.

---

## Common Settings

### Seed (`seed` chip)

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `seed` | INT | 0 | Generation seed. Special values: -1=random, -2=increment, -3=decrement |

When the `seed` chip is enabled, three buttons appear:
- **🌑 Randomize Each Time** — sets seed to -1 (random each queue)
- **🌕 New Fixed Random** — generates a concrete random seed
- **🌘 (Use Last Queued Seed)** — restores the seed from the last execution

The seed is included in the pipe output only when the `seed` chip is enabled.

---

## Pipe Output

The node outputs a single **PIPE** containing all configured values. Downstream nodes (Smart Model Loader, Save Images, etc.) read from this pipe.

### Image Mode Pipe Keys

| Key | Condition | Description |
|-----|-----------|-------------|
| `path` | Always | Full output directory path |
| `batch_size` | Always | Batch size |
| `width` | `image_size` chip on | Image width |
| `height` | `image_size` chip on | Image height |
| `latent_channels` | `image_size` chip on | Latent channel count from preset |
| `latent_downscale` | `image_size` chip on | Spatial downscale ratio from preset |
| `seed` | `seed` chip on | Generation seed |

### Video Mode Pipe Keys

| Key | Condition | Description |
|-----|-----------|-------------|
| `path` | Always | Full output directory path |
| `width`, `height` | `image_size` chip on | Video resolution |
| `frame_rate` | Always | Frames per second |
| `frame_load_cap` | VHS chip on | Max frames, including the conditional loop override |
| `skip_first_frames`, `select_every_nth` | VHS chip on | Frame skip and selection settings |
| `context_length` | Context chip on | Context length |
| `loop_count`, `overlap` | Loop chip on | Loop metadata |
| `duration` | Duration chip on | Independent duration metadata in seconds |
| `batch_size` | Always | Batch size |
| `seed` | Seed chip on | Generation seed |

### Connecting the Pipe

Use these dedicated nodes to extract values from the Smart Folder pipe:

| Node | Type | Description |
|------|------|-------------|
| **Pipe Out Smart Folder** | Extract-only | Extracts path, width, height, batch_size, latent, frame_rate, frame_load_cap, context_length, overlap, skip_first_frames, select_every_nth, seed, loop_count, and duration as individual outputs |
| **Concat Pipe Multi** | Merge | Combine the folder pipe with other pipes (e.g., Smart Model Loader pipe + Smart Sampler Settings pipe) |

**Pipe Out Smart Folder** is extract-only — it outputs individual values but does not pass through a combined pipe. Use **Concat Pipe Multi** when you need to merge Smart Folder settings with other pipe sources into a single pipe.

The Smart Folder pipe can also be connected directly to the `pipe_opt` input on **Smart Model Loader** or **Save Images** to provide path and dimension settings.

---

## Usage Examples

### Image Workflow — Date-Organized Output

1. Select `image` + `date_time` chips
2. Set `root_folder_image` to `images`
3. Date/time format: `%Y-%m-%d`, position: `postfix`
4. Connect pipe → Smart Model Loader or Save Images

Output path: `output/images/2025-09-27/`

### Video Workflow — Batch Processing

1. Select `video` + `date_time` + `batch` + `image_size` + `vhs` + `loop` chips (Loop enables Context)
2. Set `root_folder_video` to `videos`
3. Enable batch: `batch_{}`, `batch_number_control` = `increment`
4. Set `context_length` = 81, `loop_count` = 2 → `frame_load_cap` = 162
5. Connect pipe → video processing nodes

Output path: `output/videos/2025-09-27/batch_1/` (auto-increments)

### Image with Custom Resolution

1. Select `image` + `image_size` chips
2. Choose a resolution preset or select "Custom"
3. For a locked custom size, choose an aspect ratio and edit either width or height; the other edge updates automatically
4. Select latent type matching your model (e.g., "SD3 / Flux / Wan 2.1 / HunyuanVideo")
5. Connect pipe → Smart Model Loader (reads width, height, latent_channels, latent_downscale)

---

## Tips & Best Practices

- **Image vs Video** chips are radio-exclusive — only one mode is active at a time
- **Date/time folders** prevent overwriting between sessions — enable `date_time` chip by default
- **Batch increment** auto-advances `batch_number` each queue — useful for multi-run experiments
- **Skip calculation increment** auto-advances only while VHS and Context are enabled
- **Duration is metadata** — it is independent of the integer Context value
- **Latent type** must match your model — wrong latent dimensions cause generation errors
- **Pipe output** is read by Smart Model Loader, Save Images, and other pipe-aware nodes

---

## Related Documentation

- [Smart Model Loader](https://github.com/r-vage/ComfyUI_SmartModelLoader) - Reads folder pipe for path, dimensions, and latent type
- [Save Images](Save_Images.md) - Reads folder pipe for output path
- [Smart Sampler Settings](Smart_Sampler_Settings.md) - Sampler configuration pipe

---

*Part of [ComfyUI Eclipse](README.md) - Advanced nodes for ComfyUI*
