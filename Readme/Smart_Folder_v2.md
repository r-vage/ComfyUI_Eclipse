# Smart Folder v2 [Eclipse]

A dual-mode output folder configuration node with combo-chip feature selection, supporting both Image and Video generation workflows via a single unified interface.

## Table of Contents
- [Overview](#overview)
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

Smart Folder v2 configures output paths and generation parameters for downstream nodes via a pipe connection. It supports two modes — **Image** and **Video** — switchable via a combo-chip radio toggle. Each mode exposes only the relevant settings, keeping the node compact.

### Key Capabilities

- **Dual mode** — Image or Video, toggled via radio chips (mutually exclusive)
- **Dynamic path construction** — root folder → date/time subfolder → batch subfolder
- **Image settings** — resolution presets, custom dimensions, latent type selection
- **Video settings** — frame rate, context length, loop count, overlap, skip calculations
- **Seed control** — optional seed with randomize/increment/decrement modes
- **Pipe output** — all settings passed as a single pipe to downstream nodes

---

## Combo-Chip Features

The node uses a combo-chip widget to toggle feature groups. Default enabled: `image`, `date_time`.

| Chip | Default | Controls |
|------|---------|----------|
| `image` | **on** | Image Mode — shows image-specific settings |
| `video` | off | Video Mode — shows video-specific settings |
| `date_time` | **on** | Date/time subfolder creation |
| `batch` | off | Batch subfolder configuration |
| `image_size` | off | Image resolution and latent type (Image Mode only) |
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
| `width` | INT | 832 | Custom width (visible when preset is "Custom") |
| `height` | INT | 1216 | Custom height (visible when preset is "Custom") |
| `latent_type` | COMBO | `SD3 / Flux / Wan 2.1 / HunyuanVideo` | Latent format (sets channels + spatial downscale) |

The latent type determines the correct empty latent dimensions for the model architecture. Each preset maps to specific channel count and downscale ratio values passed through the pipe.

### Always Visible (Image Mode)

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `batch_size` | INT | 1 | Number of images per batch |

---

## Video Mode

Enabled when the `video` chip is selected. Shows video-specific settings.

### Resolution

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `video_size` | COMBO | *(presets)* | Video resolution preset |
| `video_width` | INT | 576 | Custom width (visible when preset is "Custom") |
| `video_height` | INT | 1024 | Custom height (visible when preset is "Custom") |

### Frame Settings

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `frame_rate` | FLOAT | 30.0 | Frames per second (8–240) |
| `frame_load_cap` | INT | 162 | Max frames to load per batch (0 = no limit) |
| `context_length` | INT | 81 | Context length for WAN/video models |
| `loop_count` | INT | 0 | Overrides `frame_load_cap` with `context_length × loop_count` when > 0 |
| `overlap` | INT | 0 | Overlap frames between clips |

### Skip Settings

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `skip_first_frames` | INT | 0 | Number of initial frames to skip |
| `skip_calculation` | INT | 0 | Additional skip: `context_length × value` added to skip_first_frames |
| `skip_calculation_control` | COMBO | `fixed` | `fixed` or `increment` (auto-increments each queue) |
| `select_every_nth` | INT | 1 | Select every Nth frame from input |

### Loop Count Override

When `loop_count` > 0, the effective `frame_load_cap` becomes:
```
frame_load_cap = context_length × loop_count
```
This overrides the manual `frame_load_cap` value.

### Skip Calculation

The total frames skipped is:
```
total_skip = skip_first_frames + (context_length × skip_calculation)
```
With `skip_calculation_control` set to `increment`, the `skip_calculation` value increases by 1 after each queue — useful for processing sequential segments of a long video.

---

## Common Settings

### Seed (`seed` chip)

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `seed` | INT | 0 | Generation seed. Special values: -1=random, -2=increment, -3=decrement |

When the `seed` chip is enabled, three buttons appear:
- **🎲 Randomize Each Time** — sets seed to -1 (random each queue)
- **🎲 New Fixed Random** — generates a concrete random seed
- **♻️ Use Last Queued Seed** — restores the seed from the last execution

The seed is included in the pipe output only when the `seed` chip is enabled.

---

## Pipe Output

The node outputs a single **PIPE** containing all configured values. Downstream nodes (Smart Model Loader, Save Images v2, etc.) read from this pipe.

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

| Key | Always | Description |
|-----|--------|-------------|
| `path` | Yes | Full output directory path |
| `width` | Yes | Video width |
| `height` | Yes | Video height |
| `frame_rate` | Yes | Frames per second |
| `frame_load_cap` | Yes | Max frames (after loop_count override) |
| `context_length` | Yes | Context length |
| `overlap` | Yes | Overlap frames |
| `skip_first_frames` | Yes | Total frames to skip (after calculation) |
| `select_every_nth` | Yes | Frame selection interval |
| `batch_size` | Yes | Batch size |
| `seed` | If chip on | Generation seed |

### Connecting the Pipe

Use these dedicated nodes to extract values from the Smart Folder pipe:

| Node | Type | Description |
|------|------|-------------|
| **Pipe Out Smart Folder** | Extract-only | Extracts path, width, height, batch_size, latent, frame_rate, frame_load_cap, context_length, overlap, skip_first_frames, select_every_nth, seed as individual outputs |
| **Concat Pipe Multi** | Merge | Combine the folder pipe with other pipes (e.g., Smart Model Loader pipe + Smart Sampler Settings pipe) |

**Pipe Out Smart Folder** is extract-only — it outputs individual values but does not pass through a combined pipe. Use **Concat Pipe Multi** when you need to merge Smart Folder settings with other pipe sources into a single pipe.

The Smart Folder pipe can also be connected directly to the `pipe_opt` input on **Smart Model Loader** or **Save Images v2** to provide path and dimension settings.

---

## Usage Examples

### Image Workflow — Date-Organized Output

1. Select `image` + `date_time` chips
2. Set `root_folder_image` to `images`
3. Date/time format: `%Y-%m-%d`, position: `postfix`
4. Connect pipe → Smart Model Loader or Save Images v2

Output path: `output/images/2025-09-27/`

### Video Workflow — Batch Processing

1. Select `video` + `date_time` + `batch` chips
2. Set `root_folder_video` to `videos`
3. Enable batch: `batch_{}`, `batch_number_control` = `increment`
4. Set `context_length` = 81, `loop_count` = 2 → `frame_load_cap` = 162
5. Connect pipe → video processing nodes

Output path: `output/videos/2025-09-27/batch_1/` (auto-increments)

### Image with Custom Resolution

1. Select `image` + `image_size` chips
2. Choose a resolution preset or select "Custom" and set width/height manually
3. Select latent type matching your model (e.g., "SD3 / Flux / Wan 2.1 / HunyuanVideo")
4. Connect pipe → Smart Model Loader (reads width, height, latent_channels, latent_downscale)

---

## Tips & Best Practices

- **Image vs Video** chips are radio-exclusive — only one mode is active at a time
- **Date/time folders** prevent overwriting between sessions — enable `date_time` chip by default
- **Batch increment** auto-advances `batch_number` each queue — useful for multi-run experiments
- **Skip calculation increment** auto-advances for sequential video segment processing
- **Latent type** must match your model — wrong latent dimensions cause generation errors
- **Pipe output** is read by Smart Model Loader, Save Images v2, and other pipe-aware nodes

---

## Related Documentation

- [Smart Model Loader](Smart_Loaders.md) - Reads folder pipe for path, dimensions, and latent type
- [Save Images v2](Save_Images.md) - Reads folder pipe for output path
- [Smart Sampler Settings v2](Smart_Sampler_Settings_v2.md) - Sampler configuration pipe

---

*Part of [ComfyUI Eclipse](README.md) - Advanced nodes for ComfyUI*
