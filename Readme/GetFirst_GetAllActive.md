# Get First & Get All Active Guide

Virtual frontend nodes for priority-based variable resolution. Zero backend cost — link resolution happens entirely in the browser at graph serialization time.

## Table of Contents
- [Overview](#overview)
- [How Set/Get Works](#how-setget-works)
- [Get First](#get-first)
- [Get All Active](#get-all-active)
- [Widgets](#widgets)
- [Type Filtering & Auto-Color](#type-filtering--auto-color)
- [Context Menu](#context-menu)
- [Active Detection](#active-detection)
- [Subgraph Support](#subgraph-support)
- [Real-World Patterns](#real-world-patterns)
- [Tips & Best Practices](#tips--best-practices)
- [Troubleshooting](#troubleshooting)
- [Related Documentation](#related-documentation)

---

## Overview

| Node | Category | What It Does |
|------|----------|--------------|
| **Get First** | `Eclipse > Primitives` | Resolves to the **first active** SetNode from a prioritized list — a single output |
| **Get All Active** | `Eclipse > Primitives` | Resolves **all active** SetNodes simultaneously — one output per var |

Both nodes are **virtual** — they exist only in the frontend. ComfyUI's backend never sees them. At serialization time (when you click Queue), each node rewrites the graph links so downstream nodes connect directly to the active setter's source. This means:

- No Python execution, no VRAM cost, no added latency
- Eclipse nodes recognize both `SetNode [Eclipse]` and KJNodes `SetNode` (one-directional — KJNodes does not see Eclipse setters)
- Supports subgraph scoping — setters in parent graphs are visible to getters in child subgraphs

---

## How Set/Get Works

The **Set/Get pattern** replaces direct node-to-node wires with named value channels:

```
                    ┌─────────────┐
Model Loader ───>  │ Set "model"  │     (publishes the value under a name)
                    └─────────────┘

        ... anywhere else in the workflow ...

                    ┌─────────────┐
                    │ Get "model"  │ ───> KSampler
                    └─────────────┘     (retrieves the value by name)
```

**Why use Set/Get instead of wires?**
- Groups become self-contained — muting a group doesn't break connections
- No spaghetti wires across large workflows
- Multiple nodes can read the same value without forking wires
- Groups can be rearranged freely without rewiring

Eclipse includes its own **Set** and **Get** nodes (`SetNode [Eclipse]` / `GetNode [Eclipse]`). Eclipse nodes recognize both Eclipse and KJNodes setters — you can mix both in the same workflow. Note that KJNodes' own Get nodes only see KJNodes `SetNode`, not `SetNode [Eclipse]`, so the cross-compatibility is one-directional (Eclipse sees all, KJNodes sees its own).

**Get First** and **Get All Active** build on this foundation by solving a fundamental routing problem:

### The Problem Set/Get Can't Solve Alone

With basic Set/Get, you publish a named value and retrieve it. But what happens when a model passes through multiple processing stages — each of which may or may not be active?

```
Model Loader   → Set "model_init"
LoRA Stack     → Set "model_lora"      (optional — may be muted)
Model Patcher  → Set "model_patched"   (optional — may be muted)
```

Which model does the KSampler need? If you hardwire `Get "model_patched"` and the patcher group is muted, the Get returns nothing. Hardwire `Get "model_init"` and you lose the LoRA/patcher modifications when they're active.

You'd need a chain of switches or conditionals to resolve "give me the most processed model that's currently active." That's exactly what **Get First** and **Get All Active** do — they walk a priority list and resolve to whichever stage is actually live, no manual switching required.

---

## Get First

**Purpose:** Select ONE value from a prioritized list of SetNode variables. The first variable whose setter is **active** (not muted, has a connected input) wins.

### Single Output

Get First has exactly **one output slot**. It resolves to whichever setter is first active in the priority list.

### How Priority Works

```
Get First Model
├─ var_1: "model_detailer"    ← checked first (muted → skip)
├─ var_2: "model_patched"     ← checked second (active → USE THIS)
├─ var_3: "model_init"        ← not reached
└─ output ──────────────────> resolves to model_patched's source
```

The node walks the list top-to-bottom. The first setter that is:
1. Not muted (node mode ≠ 2)
2. Not bypassed without a passthrough chain
3. Has something connected to its input

...becomes the resolved link. All others are ignored.

### Limitations

**No runtime value check:** Get First resolves links **before execution** — it checks whether the setter's graph connection is alive (not muted, has input), but it cannot verify what value the source node will actually produce at runtime. If a setter is connected and not muted, but its source node produces `None` due to conditional logic, empty batches, or upstream failures, Get First will still resolve to that setter. The downstream node receives `None` — a false positive.

**No variable existence validation:** Neither node validates whether a configured variable name still corresponds to an existing SetNode. If a SetNode is renamed or deleted, the var slot that references it is silently skipped — no error, no warning. This is intentional: when you delete an entire group, all its SetNodes disappear, and any Get First / Get All Active referencing those variables gracefully falls through to the next available var (or returns nothing). No broken workflows, no error popups — the node adapts automatically. The tradeoff is that typos or stale var names can be harder to spot in large workflows since there's no feedback that a var resolved to nothing.

For workflows where active setters may legitimately produce `None`, or where variable availability is uncertain, use **Get All Active + Any Multi-Switch** instead. The Multi-Switch runs at execution time and filters out `None` values, guaranteeing the first real result.

### Use Case: Fallback Model Selection

You have multiple model loading groups (initial, detailer, upscaler). Each may or may not be active. You want downstream nodes to always get "the best available model":

```
Get First Model
├─ var_1: "model_upscaler"    ← highest priority (if upscaler group is active)
├─ var_2: "model_detailer"    ← fallback (if detailer group is active)
├─ var_3: "model_init"        ← lowest priority (always active as base)
└─ output → KSampler (model)
```

Mute the upscaler group → Get First automatically falls back to `model_detailer`. Mute that too → falls back to `model_init`. No switches, no rewiring.

---

## Get All Active

**Purpose:** Collect ALL active values from a list of SetNode variables. Each variable gets its own output slot — only active ones produce valid links.

### Multiple Outputs

Get All Active has **one output per var widget**. Each output independently resolves to its setter (or nothing if that setter is inactive).

```
Get All Active Image
├─ var_1: "img_faceswap"     ── out_1 → (muted, no link)
├─ var_2: "img_upscale"      ── out_2 → image from upscale group
├─ var_3: "img_detailer"     ── out_3 → image from detailer group
├─ var_4: "img_initial"      ── out_4 → image from initial render
```

### Use Case: Latest Image in Pipeline

Connect all outputs to an **Any Multi-Switch** ("ReturnFirstNonNone") to get the latest processed image from whichever stage was last active:

```
Get All Active Image                    Any Multi-Switch
├─ out_1: img_copyright_logo    ───>   ├─ input_1
├─ out_2: img_rescale           ───>   ├─ input_2
├─ out_3: img_svr2              ───>   ├─ input_3
├─ out_4: img_mouth             ───>   ├─ input_4
├─ out_5: img_eye               ───>   ├─ input_5
├─ out_6: img_face              ───>   ├─ input_6
├─ out_7: img_tile              ───>   ├─ input_7
├─ out_8: img_upscale           ───>   ├─ input_8
├─ out_9: img_faceswap          ───>   ├─ input_9
├─ out_10: img_initial          ───>   ├─ input_10
├─ out_11: ref_image            ───>   ├─ input_11
                                       └─ output → (first non-None)
```

The Multi-Switch returns the first non-None input, which is the output of the last active processing stage. Enable/disable any group and the chain automatically adjusts.

---

## Widgets

Both nodes share the same widget layout:

| Widget | Values | Description |
|--------|--------|-------------|
| `type_filter` | `*`, MODEL, CLIP, VAE, CONDITIONING, LATENT, IMAGE, MASK, FLOAT, INT, STRING, CONTROL_NET, NOISE, GUIDER, SAMPLER, SIGMAS, PIPE† | Filter which SetNode variables appear in dropdowns |
| `var_count` | 1–20 | Number of variable slots |
| `var_1`–`var_20` | (dropdown) | SetNode variable names from the current scope |

† PIPE is available in Get All Active only.

**var dropdowns** automatically populate with all visible SetNode names (filtered by type). Variables already assigned to other slots are excluded from the dropdown to prevent duplicates.

---

## Type Filtering & Auto-Color

### Type Filter

Set `type_filter` to restrict which SetNode variables appear in the dropdowns:

- `*` (default) — show all SetNode variables regardless of type
- `IMAGE` — only show setters whose input is connected to an IMAGE type
- `MODEL` — only show model-type setters
- etc.

The node title updates dynamically: `Get First` → `Get First Model`, `Get All Active` → `Get All Active Image`.

### Auto-Color

Enable **Eclipse Settings → Set/Get Auto Color** to automatically color nodes by their resolved data type:

| Type | Color |
|------|-------|
| MODEL | Blue |
| CLIP | Yellow |
| VAE | Red |
| CONDITIONING | Brown |
| LATENT | Purple |
| IMAGE | Pale Blue |
| FLOAT | Green |
| MASK | Dark Green |
| INT | Dark Blue |

This makes it easy to visually identify what type of data each Get First / Get All Active is resolving.

---

## Context Menu

Right-click either node for these options:

| Menu Item | Description |
|-----------|-------------|
| **Reorder Vars** | Submenu per var: Move to Top, Move Up, Move Down, Move to Bottom, Insert Above |
| **Setters** | Submenu listing all configured vars with ✓ (active) or ✗ (inactive) status — click to navigate to that setter |
| **Go to active setter** | Centers the canvas on the first active setter (Get First) |
| **Show/Hide connections** | Toggle virtual link lines drawn from active setters to this node |

### Reorder Vars

Priority order matters for **Get First** — var_1 is checked before var_2. Use the Reorder Vars submenu to adjust priority without manually re-selecting variables:

1. Right-click → Reorder Vars
2. Click the variable you want to move
3. Choose ↑ Move to Top / ↑ Move Up / ↓ Move Down / ↓ Move to Bottom
4. Use ＋ Insert Above to add an empty slot at a specific position

**Removing a var:** There is no direct remove option. To remove a variable, move it to the bottom of the list (Move to Bottom), then decrease `var_count` by one. The last slot is dropped.

**Connection stability (Get All Active):** When you reorder vars, only the widget values (variable names) and output slot labels swap — the output slots themselves and their wires stay in place. This means existing connections to downstream nodes are preserved after reordering. No need to reconnect anything. Get First has a single output, so reordering simply changes which setter resolves first.

---

## Active Detection

### What "Active" Means

A setter is considered **active** when all of these are true:
1. The SetNode is **not muted** (mode ≠ 2)
2. The SetNode is **not bypassed** (mode ≠ 4) — unless its source chain passes through
3. The SetNode has **something connected** to its input slot
4. The source node connected to the setter is reachable (not broken)

### Bypass Walk-Through

When a node in the source chain is **bypassed** (mode 4), Eclipse walks backwards past it by following the bypassed node's input link until it finds a non-bypassed source. This means a setter with a bypassed intermediate node is still considered active — as long as there's a valid source at the end of the chain.

```
Image Out → Stop Button (bypassed) → SetNode
                  ↑
          Eclipse walks past this to find Image Out
```

This is one of the key reasons Eclipse ported its own Set/Get nodes rather than relying on KJNodes. KJNodes checks only the node directly connected to the setter — if that node is bypassed, the setter appears inactive and returns nothing. Eclipse's backward walk resolves through the bypass chain, avoiding false negatives. The walk is limited to **4 bypassed nodes** to prevent performance overhead during serialization — if a chain has more than 4 consecutive bypassed nodes, the setter is treated as inactive.

### Green Dot Indicators

Active variables show a **green dot** (●) next to their widget:

- **Get First:** Only the first active var (the one being resolved) gets a green dot
- **Get All Active:** All active vars get green dots simultaneously

**Note:** Green dots are a snapshot — they reflect the state at the time the node was last drawn. If you mute or unmute a group, the dots won't update automatically since there's no polling mechanism to detect external state changes. Refreshing the page (F5) forces a re-evaluation and updates all indicators. The actual link resolution at queue time is always correct regardless of what the dots show.

---

## Subgraph Support

Both nodes are **subgraph-aware**:

- **Setters propagate downward:** A SetNode in a parent graph is visible to Get First / Get All Active in any child subgraph
- **Getters look upward:** The variable dropdown shows SetNodes from the current graph plus all ancestor graphs
- **Sibling isolation:** SetNodes in unrelated sibling subgraphs are not visible to each other

This means you can place SetNodes at the top level and reference them inside subgraphs without issues.

---

## Real-World Patterns

### Pattern 1: Fallback Model Chain

A model passes through multiple processing stages — each optional. The KSampler needs whichever version is most processed:

```
Model Loader   → Set "model_init"       (always active)
LoRA Stack     → Set "model_lora"       (optional — may be muted)
Model Patcher  → Set "model_patched"    (optional — may be muted)

Get First Model
├─ var_1: "model_patched"     ← highest priority (patched + LoRA)
├─ var_2: "model_lora"        ← fallback (LoRA only)
├─ var_3: "model_init"        ← base (raw checkpoint)
└─ output → KSampler
```

Muting Model Patcher → automatically uses `model_lora`. Muting both → uses `model_init`. No switches, no rewiring.

### Pattern 2: Progressive Image Pipeline

Each processing stage stores its result. The final save node needs the latest processed image:

```
Stage order:  crop → render → faceswap → upscale → tile → face → eye → mouth → rescale → watermark

Get All Active Image (in Save group)
├─ var_1:  "img_watermark"       ← last stage
├─ var_2:  "img_rescale"
├─ var_3:  "img_mouth"
├─ var_4:  "img_eye"
├─ var_5:  "img_face"
├─ var_6:  "img_tile"
├─ var_7:  "img_upscale"
├─ var_8:  "img_faceswap"
├─ var_9:  "img_initial"
├─ var_10: "ref_image"           ← first stage
└─ all outputs → Any Multi-Switch → Save Images v2
```

The Multi-Switch returns the first non-None, which is the output of the last active group in the pipeline. Enable any combination of stages and the chain adjusts automatically.

### Pattern 3: Metadata Collection

Collect model names, LoRA names, and settings from all active groups for metadata embedding:

```
Get All Active String (type_filter: STRING)
├─ var_1: "lora_names_detailer_face"
├─ var_2: "lora_names_detailer_tile"
├─ var_3: "lora_names_initial"
└─ all outputs → Join String → Save Images metadata
```

Only lora name strings from active groups are collected. Muted groups produce no output.

### Pattern 4: Sampler Settings Override

Use Get First to pick sampler settings from the most specific active source:

```
Get First (type_filter: PIPE)
├─ var_1: "sampler_detailer"     ← group-specific override
├─ var_2: "sampler_model"        ← from model loader template
├─ var_3: "sampler_default"      ← workflow defaults
└─ output → IO Sampler Settings → KSampler
```

### Pattern 5: Conditional Image Source

Four mutually exclusive image input methods — only one should be active:

```
Get All Active Image + Any Multi-Switch

Sources (highest → lowest priority):
1. "img_rembg"          ← background removed
2. "img_resize"         ← resized
3. "img_crop_preview"   ← cropped (preview)
4. "img_crop_custom"    ← custom crop
5. "img_crop_auto"      ← auto crop
6. "img_video_frame"    ← video frame
7. "img_folder"         ← from folder
8. "img_load"           ← single image load

Only the first non-None result is used as "ref_image" for the rest of the pipeline.
```

---

## Tips & Best Practices

### When to Use Which

| Scenario | Use | Why |
|----------|-----|-----|
| Muting groups is the only way to disable stages | **Get First** | Simple, single output — muted = inactive is reliable |
| Source nodes may produce None at runtime | **Get All Active + Multi-Switch** | Runtime filtering eliminates None values |
| Need one value from a priority list | **Get First** | Direct, no extra nodes needed |
| Need to collect all active values | **Get All Active** | One output per var |
| Pipeline with conditional/optional processing | **Get All Active + Multi-Switch** | Safest — guarantees a real value |

**Rule of thumb:** If you control activation purely by muting/unmuting groups, Get First is clean and simple. If there's any chance an active setter's source could produce `None` at runtime, prefer Get All Active + Any Multi-Switch for reliable filtering.

### General Tips

1. **Name SetNodes descriptively** — Use prefixes like `model_`, `img_`, `lora_`, `sampler_` to make variable lists scannable
2. **Use type_filter** — Filtering by type reduces clutter in the dropdown when you have many SetNodes
3. **Priority order matters for Get First** — Put the most specific/optional sources first, the always-available fallback last
4. **Get All Active + Multi-Switch = progressive pipeline** — The core pattern for modular workflows where groups can be enabled/disabled independently, with runtime None filtering
5. **Enable auto-color** — Visual type differentiation helps when you have many Get First / Get All Active nodes
6. **Show connections for debugging** — Right-click → Show connections to see virtual link lines to active setters
7. **Go to setter for navigation** — Right-click → Setters or Go to active setter to quickly navigate large workflows
8. **One Set per name per scope** — Avoid duplicate SetNode names in the same graph (sibling subgraphs can reuse names safely)

---

## Troubleshooting

### Output Shows `*` Instead of Correct Type

The node hasn't resolved the setter type yet. This can happen on workflow load before LiteGraph finishes link restoration. The node auto-retries after 200ms. If it persists:
- Check that the SetNode has something connected to its input
- Try changing the type_filter and changing it back

### Variable Not Appearing in Dropdown

- The SetNode may be in a sibling subgraph (not visible from current scope)
- The type_filter may be excluding it — try `*` to see all variables
- Restart ComfyUI if SetNodes were recently added/renamed

### "No Active Setter Found"

All configured setters are either muted, bypassed without passthrough, or have no connected input. Check:
- Is the group containing the SetNode muted?
- Is the SetNode itself muted?
- Is there something connected to the SetNode's input?

### Green Dot Not Showing

The setter exists but isn't considered active. Verify:
- The SetNode's source node is not muted
- If the source is bypassed (mode 4), there must be a valid passthrough chain
- The SetNode input must have an actual link (not just a dangling wire)

---

## Related Documentation

- [Smart Model Loader Guide](Smart_Loaders.md) — Primary model loader (outputs via Set/Get for downstream nodes)
- [Smart Sampler Settings v1 / v2](Smart_Sampler_Settings_v2.md) — Sampler configuration with pipe output
- [Save Images v2](Save_Images.md) — Metadata embedding from collected pipeline data
- [Standalone Loaders Guide](Checkpoint_Loaders.md) — Focused component loaders
