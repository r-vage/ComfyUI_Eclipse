# Utility Nodes Guide

Quick reference for Eclipse's helper nodes — routers, joiners, cleanup, and other small utilities that glue workflows together.

## Table of Contents
- [Utility Nodes Guide](#utility-nodes-guide)
  - [Table of Contents](#table-of-contents)
  - [Routers](#routers)
    - [Any Multi-Switch](#any-multi-switch)
    - [Any Multi-Switch Purge](#any-multi-switch-purge)
    - [Any Dual-Switch](#any-dual-switch)
    - [IF A Else B](#if-a-else-b)
    - [IF A Else B (Fallback)](#if-a-else-b-fallback)
    - [Any Passer / Passer Purge](#any-passer--passer-purge)
  - [Conversion \& Joining](#conversion--joining)
    - [Join](#join)
    - [Concat Pipe Multi](#concat-pipe-multi)
  - [Text](#text)
    - [String DeDuplicate](#string-deduplicate)
  - [Tools](#tools)
    - [Show Any](#show-any)
    - [Stop](#stop)
    - [VRAM Cleanup](#vram-cleanup)
    - [RAM Cleanup](#ram-cleanup)
    - [Fast Bypasser](#fast-bypasser)
    - [Loop Calculator / Keep Calculator](#loop-calculator--keep-calculator)

---

## Routers

Routing nodes live in `Eclipse > Router`. They accept **any type** — models, images, latents, strings, pipes — and forward based on conditions.

### Any Multi-Switch

The workhorse of fallback chains. Accepts up to 64 inputs and returns the **first non-None** value. Input slots **auto-grow** — connect to the last slot and a new one appears. Disconnect trailing empty slots and they shrink. The `inputcount` widget is hidden.

| Input | Type | Description |
|-------|------|-------------|
| `any_1`–`any_N` | Any (optional) | Inputs checked in order, highest priority first |

**Output:** Single `Any` — the first input that is not None, not empty string, not empty dict/tuple/list.

**Key behavior:**
- Inputs are checked top to bottom — `any_1` has highest priority
- None, `""`, empty containers are all skipped
- If all inputs were empty strings, returns `""` (not None) — preserves string type
- If all inputs are None, returns None

**Common pattern:** Pair with [Get All Active](GetFirst_GetAllActive.md) to build progressive pipelines where the Multi-Switch picks the latest active result.

### Any Multi-Switch Purge

Same as Any Multi-Switch but with an added `Purge_VRAM` toggle. When enabled, purges VRAM before returning the result. Useful when switching between model-heavy branches where you want to free the unused model's VRAM.

### Any Dual-Switch

Manual 1-of-2 selector. Set `Input` to 1 or 2 to choose which input passes through.

| Input | Type | Description |
|-------|------|-------------|
| `Input` | Int (1–2) | Which input to output |
| `input1` | Any (optional) | First option |
| `input2` | Any (optional) | Second option |

Also available as **Any Dual-Switch Purge** with VRAM cleanup.

### IF A Else B

Boolean-controlled router. Unlike Dual-Switch which uses an integer selector, this takes a boolean condition.

| Input | Type | Description |
|-------|------|-------------|
| `on_true` | Any | Value returned when boolean is True |
| `on_false` | Any | Value returned when boolean is False |
| `boolean` | Boolean (force_input) | The condition — must be wired from another node |
| `Purge_VRAM` | Boolean | Optional VRAM cleanup before switching |

### IF A Else B (Fallback)

Same as IF A Else B but with a safe default: the `boolean` input is **optional**. When unconnected or from a muted node, it defaults to False (on_false path). The `on_false` input is also optional — returns None if unconnected.

This is useful when the condition comes from an optional group that may be muted.

### Any Passer / Passer Purge

Passthrough nodes — input goes straight to output unchanged. Seems pointless, but useful for:
- **Execution ordering** — forces ComfyUI to execute the source before downstream nodes
- **VRAM Purge variant** — clears VRAM at a specific point in the pipeline without adding logic

---

## Conversion & Joining

### Join

Combines multiple inputs into one. Auto-detects the input type and handles each differently. Input slots **auto-grow** — the `inputcount` widget is hidden.

| Input | Type | Description |
|-------|------|-------------|
| `delimiter` | String | Separator for string joins (default: `", "`, use `\n` for newline) |
| `input_1`–`input_N` | Any (optional) | Values to join |

**Type-specific behavior:**

| Input Type | Join Method |
|-----------|-------------|
| **STRING** | Concatenated with delimiter |
| **IMAGE** | Tensors resized to match first image, then batched (cat along dim 0) |
| **MASK** | Tensors batched (cat along dim 0) |
| **INT / FLOAT** | Converted to strings and joined with delimiter |
| **LIST** | Flattened and joined with delimiter |

**Category:** `Eclipse > Conversion`

### Concat Pipe Multi

Merges 2–64 pipe/context dicts into a single pipe. Essential when combining outputs from multiple groups that each produce partial pipes. Input slots **auto-grow** — the `inputcount` widget is hidden.

| Input | Type | Description |
|-------|------|-------------|
| `pipe_1`–`pipe_N` | PIPE (optional) | Pipe inputs, processed in order |
| `merge_strategy` | Combo | How to handle key conflicts |

**Merge strategies:**

| Strategy | Behavior |
|----------|----------|
| `overwrite` | Later pipes replace earlier values for the same key |
| `preserve` | First valid (non-None) value wins — later pipes can't overwrite |
| `merge` | Combines lists, uses later values for scalar conflicts |

**Output:** Single `PIPE` — merged context dict.

See the "Connecting the Pipe" sections in [Smart Loaders](Smart_Loaders.md), [Sampler Settings](Smart_Sampler_Settings_v2.md), and [Smart Folder](Smart_Folder_v2.md) for usage examples.

**Category:** `Eclipse > Conversion`

---

## Text

### String DeDuplicate

Combines multiple string inputs and removes duplicate entries. Handles both tag format (comma-separated) and prose (line-based). Input slots **auto-grow** (up to 20) — the `inputcount` widget is hidden.

| Input | Type | Description |
|-------|------|-------------|
| `dedup_inputs` | Boolean | Also deduplicate within each input before combining |
| `weight_handling` | Combo | `None`, `Remove Weights`, or `Normalize` (caps at 1.4) |
| `string_1`–`string_N` | String (force_input) | Text inputs to merge |

**Key behavior:**
- Auto-detects tag format vs prose (same detection as Replace String v3)
- Case-insensitive deduplication (underscores normalized to spaces)
- Expands weighted groups like `((a:2, b))` before dedup
- `Remove Weights` strips all emphasis markers (`(( ))`, `:1.5`, etc.)
- `Normalize` caps weight values at 1.4 to prevent overemphasis
- Empty inputs are silently ignored

**Category:** `Eclipse > Text`

---

## Tools

### Show Any

Debug/preview node. Displays the value of any input in the node's UI area. Supports all types — strings show as text, images can optionally show as previews, dicts/lists show as formatted text.

| Input | Type | Description |
|-------|------|-------------|
| `show_images` | Combo (`hide`/`show`) | Whether to render IMAGE tensor previews |
| `anything` | Any (optional) | Value to inspect |

**Output:** Passes the input through unchanged — can be inserted anywhere without breaking the chain.

**Category:** `Eclipse > Tools`

### Stop

Halts workflow execution at this point. Connect it inline to create a breakpoint — everything before it executes, everything after it doesn't.

| Input | Type | Description |
|-------|------|-------------|
| `input` | Any | Value to pass through before stopping |

**Output:** The input value (downstream nodes won't execute due to interrupt).

**Tip:** Bypass the Stop node (mode 4) to let execution continue past it. Useful as a toggleable checkpoint.

**Category:** `Eclipse > Tools`

### VRAM Cleanup

Frees GPU memory at a specific point in the pipeline. Insert between heavy stages (e.g., after model loading, before video processing).

| Input | Type | Description |
|-------|------|-------------|
| `anything` | Any | Passthrough value |
| `offload_model` | Boolean | Unload models from VRAM via ComfyUI (default: True) |
| `offload_cache` | Boolean | Clear VRAM cache (default: True) |
| `aggressive_cleanup` | Boolean | Force PyTorch GPU cache clear + garbage collection (default: False) |

`aggressive_cleanup` supports CUDA, ROCm, MPS, and XPU — may cause a brief lag but reclaims maximum VRAM.

**Category:** `Eclipse > Tools`

### RAM Cleanup

Clears system RAM (Windows only). On non-Windows platforms, passes input through unchanged.

| Input | Type | Description |
|-------|------|-------------|
| `anything` | Any | Passthrough value |
| `clean_file_cache` | Boolean | Clear Windows filesystem cache (default: True) |
| `clean_processes` | Boolean | Clear working set of user processes (default: True) |
| `clean_dlls` | Boolean | Clear current process working set (default: True) |
| `retry_times` | Int (1–10) | Number of cleanup attempts (default: 3) |

**Category:** `Eclipse > Tools`

### Fast Bypasser

Toggle-bypass utility. Connect nodes to it and it creates toggle switches that let you bypass/un-bypass them directly from the Fast Bypasser node. No inputs — purely a control node.

**Output:** Optional connection to chain with other mode-control nodes.

**Category:** `Eclipse > Tools`

### Loop Calculator / Keep Calculator

Video processing helpers for calculating loop iterations and frame counts:

**Loop Calculator** — Given total frames, context length, and overlap, calculates how many loops are needed to process the entire video.

**Keep Calculator** — Given the same parameters plus the current loop count, calculates how many frames to keep from the current batch.

Both are used with video pipelines (e.g., SeedVR2, AnimateDiff) to handle frame batching correctly.

**Category:** `Eclipse > Tools`
