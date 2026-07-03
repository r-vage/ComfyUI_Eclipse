# Set/Get & Mode Bridge Guide

Replace spaghetti wires with clean named channels. **Set/Get** handles data routing, **Mode Bridge** handles mute/bypass propagation — both work wirelessly across your entire workflow including subgraphs.

## Table of Contents

- [Set/Get \& Mode Bridge Guide](#setget--mode-bridge-guide)
  - [Table of Contents](#table-of-contents)
  - [Set/Get — Data Routing](#setget--data-routing)
    - [Set Node](#set-node)
    - [Get Node](#get-node)
    - [Creating Set/Get Pairs](#creating-setget-pairs)
    - [Converting Wires to Set/Get](#converting-wires-to-setget)
    - [Converting Set/Get Back to Wires](#converting-setget-back-to-wires)
    - [Navigating Between Set and Get](#navigating-between-set-and-get)
    - [Show/Hide Virtual Connections](#showhide-virtual-connections)
  - [Mode Bridge — Wireless Mute/Bypass Control](#mode-bridge--wireless-mutebypass-control)
    - [Mode Bridge Set](#mode-bridge-set)
    - [Mode Bridge Get](#mode-bridge-get)
    - [How Bridge Pairing Works](#how-bridge-pairing-works)
    - [Bidirectional Mode Sync](#bidirectional-mode-sync)
    - [Creating Bridge Pairs](#creating-bridge-pairs)
  - [Advanced Getters](#advanced-getters)
    - [Get First — Priority-Based Selection](#get-first--priority-based-selection)
    - [Get All Active — Collect All Active Values](#get-all-active--collect-all-active-values)
    - [Nav Arrows \& Active Indicators](#nav-arrows--active-indicators)
  - [Subgraph Support](#subgraph-support)
  - [Copy-Paste Behavior](#copy-paste-behavior)
    - [Set/Get Paste](#setget-paste)
    - [Bridge Paste](#bridge-paste)
  - [Context Menu Reference](#context-menu-reference)
    - [Canvas Right-Click (Eclipse submenu)](#canvas-right-click-eclipse-submenu)
    - [Node Right-Click (Eclipse submenu)](#node-right-click-eclipse-submenu)
    - [Wire Right-Click](#wire-right-click)
  - [Real-World Examples](#real-world-examples)
    - [Example 1: Clean Model Loading](#example-1-clean-model-loading)
    - [Example 2: Mode Bridge for Group Control](#example-2-mode-bridge-for-group-control)
    - [Example 3: Fallback Model with Get First](#example-3-fallback-model-with-get-first)
    - [Example 4: Progressive Image Pipeline](#example-4-progressive-image-pipeline)
  - [Tips \& Best Practices](#tips--best-practices)
  - [Troubleshooting](#troubleshooting)
    - [Get dropdown is empty](#get-dropdown-is-empty)
    - [Bridge Get shows ⚠ next to the name](#bridge-get-shows--next-to-the-name)
    - [Pasted group has wrong Bridge names](#pasted-group-has-wrong-bridge-names)
    - [Get First resolves to the wrong setter](#get-first-resolves-to-the-wrong-setter)
    - [Green dots don't update after muting a group](#green-dots-dont-update-after-muting-a-group)
  - [Related Documentation](#related-documentation)

---

## Set/Get — Data Routing

Set/Get nodes replace direct wires with named value channels. A **Set** node publishes a value under a name, and any number of **Get** nodes can retrieve that value by name — no wire needed.

```
                    ┌─────────────┐
Model Loader ───>  │ Set "model"  │     (publishes the value)
                    └─────────────┘

        ... anywhere else in the workflow ...

                    ┌─────────────┐
                    │ Get "model"  │ ───> KSampler
                    └─────────────┘     (retrieves the value by name)
```

Both nodes are **virtual** — they only exist in the frontend. When you click Queue, ComfyUI rewrites the graph so downstream nodes connect directly to the setter's source. There is no Python execution cost, no VRAM overhead, and no added latency.

### Set Node

| | |
|---|---|
| **Node name** | `SetNode [Eclipse]` |
| **Category** | Eclipse > Set-Get |
| **Input** | Any type — auto-detects from connection |
| **Output** | Passthrough — same type as input |
| **Widget** | `Constant` — the variable name |

**Auto-naming:** Connect something to the Set's input and leave the name empty — it auto-fills with the connected type (e.g., `MODEL`) and sets the title to `Set_MODEL`.

**Name uniqueness:** Each Set name must be unique within its scope. If you create a duplicate, it's automatically renamed with a suffix (`_0`, `_1`, etc.).

### Get Node

| | |
|---|---|
| **Node name** | `GetNode [Eclipse]` |
| **Category** | Eclipse > Set-Get |
| **Output** | Matches the paired Set's type |
| **Widget** | `Constant` — dropdown of available Set names |

The dropdown lists all visible Set names. If the Get's output is already connected to something, the list filters to show only type-compatible variables.

**Scope labels:** Variables from parent graphs show `(parent)`, from child subgraphs show `(child)`. Local variables show the plain name.

**Double-click** a Get node to jump to its paired Set — works across subgraphs.

### Creating Set/Get Pairs

**From the canvas:**
1. Right-click empty canvas → **Eclipse** → **Add SetNode** or **Add GetNode**
2. Name the Set, then select that name in the Get's dropdown

**From a wire (fastest):**
1. Right-click any wire → **Eclipse: Convert to Set/Get**
2. The wire is replaced with a Set/Get pair, auto-named by type

**From a node:**
1. Right-click a node → **Eclipse** → **Convert all outputs to Set/Get**
2. Every output wire becomes a Set/Get pair

### Converting Wires to Set/Get

| Method | How |
|--------|-----|
| Single wire | Right-click a wire → **Eclipse: Convert to Set/Get** |
| All outputs of a node | Right-click node → Eclipse → **Convert all outputs to Set/Get** |
| All inputs of a node | Right-click node → Eclipse → **Convert all inputs to Set/Get** |
| Both directions | Right-click node → Eclipse → **Convert all to Set/Get** |
| Selected nodes | Select nodes → right-click canvas → Eclipse → **Convert selected outputs/inputs to Set/Get** |

### Converting Set/Get Back to Wires

To restore direct connections:

| Method | How |
|--------|-----|
| Single pair | Right-click the Set or Get → Eclipse → **Convert to links** |
| All connected to a node | Right-click the node → Eclipse → **Convert all to links** |
| Selected Set/Get nodes | Select them → right-click canvas → Eclipse → **Convert selected Set/Get to links** |

### Navigating Between Set and Get

- **Double-click** a Get node → centers the canvas on its paired Set
- **Right-click** a Set → **Getters** → submenu lists all paired Gets with navigation
- **Right-click** a Get → **Go to setter** → centers on the paired Set
- Works across subgraphs — navigates into the correct subgraph automatically

### Show/Hide Virtual Connections

Right-click a Set or Get → **Show/Hide connections** to toggle dotted lines drawn between paired nodes. Useful for debugging which Get is reading from which Set.

Right-click a Set → **Hide all connections** to hide virtual links for all Set/Get pairs in the graph.

---

## Mode Bridge — Wireless Mute/Bypass Control

Mode Bridge nodes control **mute/bypass/active state** wirelessly. A **Bridge Set** publishes its mode state, and all **Bridge Gets** with the same name receive it — no wires needed between them.

This is different from Set/Get: Set/Get routes **data**, Bridge routes **mode state** (mute, bypass, active).

### Mode Bridge Set

| | |
|---|---|
| **Node name** | `Mode Bridge Set [Eclipse]` |
| **Category** | Eclipse > Tools |
| **Output** | `oc` — connect to Switchers, Repeaters, or other mode nodes |
| **Widget** | `bridge name` — text field for the channel name |

The Bridge Set is the **publisher**. When its mode changes (muted, bypassed, or active), all Bridge Gets with the same name receive the change wirelessly.

It also has a wired output (`oc`) for connecting to mode propagation nodes like Fast Mode Switcher or Mode Repeater — combining wireless and wired control.

### Mode Bridge Get

| | |
|---|---|
| **Node name** | `Mode Bridge Get [Eclipse]` |
| **Category** | Eclipse > Tools |
| **Widget** | `bridge name` — dropdown listing all existing Bridge Set names |

The Bridge Get is the **subscriber**. It receives mode changes from its paired Bridge Set and propagates them to all nodes connected to its inputs.

The dropdown shows all available Bridge Set names. If a selected name no longer has a matching Set, it shows a **⚠** warning marker.

**Bridge Get has no output slots** — it controls connected nodes via mode propagation, not data flow.

### How Bridge Pairing Works

```
┌──────────────────────┐              ┌──────────────────────┐
│  Mode Bridge Set     │   wireless   │  Mode Bridge Get     │
│  "Model Loader"      │ ──────────>  │  "Model Loader"      │
│  [MUTED]             │              │  [MUTED]             │
└──────────────────────┘              └──────────────────────┘
                                             │
                                      connected nodes
                                      also get muted
```

1. Type a name in the Bridge Set's `bridge name` field (e.g., `"Model Loader"`)
2. On any Bridge Get, select that name from the dropdown
3. Now they're paired — muting the Set mutes the Get (and everything connected to the Get)

Multiple Bridge Gets can subscribe to the same Bridge Set name. One Set controls many Gets.

### Bidirectional Mode Sync

Mode changes flow **both directions**:

- **Set → Get:** Muting/unmuting a Bridge Set pushes the mode to all matching Gets
- **Get → Set:** If a Bridge Get's mode changes directly (e.g., its containing group is muted), it notifies the Bridge Set, which may sync all other Gets

This means you can mute a group containing a Bridge Get, and the Bridge Set plus all other Gets will follow.

### Creating Bridge Pairs

**From the canvas:**
1. Right-click empty canvas → **Eclipse** → **Add Bridge Set** / **Add Bridge Get**

**From a node:**
1. Right-click any node → **Eclipse** → **Add Bridge Set** / **Add Bridge Get**
2. The Bridge is placed next to the node

**Quick pair:** Create a Bridge Set, name it, then create a Bridge Get and select the same name from its dropdown.

---

## Advanced Getters

For detailed documentation on Get First and Get All Active, see the [Get First & Get All Active Guide](GetFirst_GetAllActive.md). Below is a summary.

### Get First — Priority-Based Selection

**Purpose:** Select ONE value from a prioritized list of Set variables. The first variable whose setter is active wins.

```
Get First Model
├─ var_1: "model_patched"     ← checked first (muted → skip)
├─ var_2: "model_lora"        ← checked second (active → USE THIS)
├─ var_3: "model_init"        ← not reached
└─ single output ────────────> resolves to model_lora's source
```

**Widgets:** `type_filter` (filter by data type), `var_count` (1–20 slots), `var_1`–`var_20` (Set variable names)

**Use case:** Fallback chains — "give me the most processed model that's currently active." Put the most specific source first, the always-available fallback last.

### Get All Active — Collect All Active Values

**Purpose:** Collect ALL active values simultaneously. Each variable gets its own output slot.

```
Get All Active Image
├─ var_1: "img_upscale"      ── out_1 → image from upscale group
├─ var_2: "img_detailer"     ── out_2 → image from detailer group
├─ var_3: "img_initial"      ── out_3 → image from initial render
```

**Use case:** Connect all outputs to an **Any Multi-Switch** to get the last processed result from a pipeline where any stage can be enabled/disabled.

### Nav Arrows & Active Indicators

Both Get First and Get All Active show visual indicators next to each variable:

| Indicator | Meaning |
|-----------|---------|
| **Green dot** (●) on the left | The setter for this variable is active |
| **Arrow** (►) on the right | Click to jump to that setter's node |

- **Get First:** Only the first active variable (the resolved one) gets a green dot
- **Get All Active:** All active variables show green dots

The nav arrows make it easy to quickly locate any setter in a large workflow — just click the arrow next to a variable name to center the canvas on that Set node. Works across subgraphs.

Toggle arrows on/off via the node's properties panel (`showNav`).

---

## Subgraph Support

All Set/Get and Bridge nodes are **subgraph-aware**:

| Behavior | Description |
|----------|-------------|
| **Setters propagate downward** | A Set in a parent graph is visible to Gets in any child subgraph |
| **Getters look upward** | Get dropdowns show variables from the current graph plus all ancestors |
| **Sibling isolation** | Sets in unrelated sibling subgraphs are not visible to each other |
| **Bridge spans all graphs** | Bridge Set/Get can pair across the root graph and any subgraph — they search all graphs |

This means you can place Set nodes at the top level and reference them from inside subgraphs. Bridge pairs work across any graph boundary.

---

## Copy-Paste Behavior

When you copy-paste nodes or groups containing Set/Get or Bridge nodes, names are automatically deduplicated:

### Set/Get Paste

- If a pasted Set's name conflicts with an existing one, it's renamed (e.g., `model` → `model_0`)
- All pasted Gets that referenced the original name are updated to the new name
- Gets inside pasted subgraphs are also updated

### Bridge Paste

- If a pasted Bridge Set's name conflicts, it's renamed (e.g., `Model Loader` → `Model Loader_1`)
- All pasted Bridge Gets with the old name are updated to match
- Bridge Gets inside subgraphs that arrive late (subgraph configured after the Set) also pick up the rename

**Example:** You have a group with `Bridge Set "Model Loader_0"` and `Bridge Get "Model Loader_0"`. Paste the group → both are renamed to `Model Loader_1` automatically. This works even when the Bridge Get is inside a subgraph within the pasted group.

---

## Context Menu Reference

### Canvas Right-Click (Eclipse submenu)

| Item | Description |
|------|-------------|
| Add SetNode | Create a new Set node at cursor |
| Add GetNode | Create a new Get node at cursor |
| Add Bridge Set | Create a new Bridge Set at cursor |
| Add Bridge Get | Create a new Bridge Get at cursor |
| Convert selected outputs to Set/Get | Replace output wires of selected nodes with Set/Get pairs |
| Convert selected inputs to Set/Get | Replace input wires of selected nodes with Set/Get pairs |
| Convert selected Set/Get to links | Remove selected pairs, restore direct wires |

### Node Right-Click (Eclipse submenu)

| Item | Available on | Description |
|------|-------------|-------------|
| Add SetNode | Any node | Place a Set beside the node |
| Add GetNode | Any node | Place a Get beside the node |
| Add Bridge Set | Any node | Place a Bridge Set beside the node |
| Add Bridge Get | Any node | Place a Bridge Get beside the node |
| Convert all outputs to Set/Get | Any node | All output wires → Set/Get pairs |
| Convert all inputs to Set/Get | Any node | All input wires → Set/Get pairs |
| Convert all to Set/Get | Any node | Both directions |
| Convert all to links | Any node | Remove all connected Set/Get, restore wires |
| Convert to links | Set or Get | Remove this pair, restore wire |
| Show/Hide connections | Set or Get | Toggle virtual link visualization |
| Getters | Set node | List all paired Gets with navigation |
| Hide all connections | Set node | Hide all virtual links |
| Go to setter | Get node | Navigate to paired Set |

### Wire Right-Click

| Item | Description |
|------|-------------|
| Eclipse: Convert to Set/Get | Replace this wire with a Set/Get pair |

---

## Real-World Examples

### Example 1: Clean Model Loading

Instead of running wires from the model loader to every node that needs MODEL/CLIP/VAE:

```
IO Checkpoint Loader
├─ model → Set "model"
├─ clip  → Set "clip"
└─ vae   → Set "vae"

KSampler:           Get "model" → model input
CLIP Text Encode:   Get "clip"  → clip input
VAE Decode:         Get "vae"   → vae input
```

No wires crossing the canvas. Add more nodes that need the model? Just add another Get.

### Example 2: Mode Bridge for Group Control

You have an "Upscaler" group and a "Detailer" group. You want to mute both from a single control point:

```
Bridge Set "Pipeline"  ←── connected to Fast Mode Switcher

   Inside Upscaler group:
   Bridge Get "Pipeline" → connected to upscaler nodes
   
   Inside Detailer subgraph:
   Bridge Get "Pipeline" → connected to detailer nodes
```

Mute the Bridge Set → both groups mute. Unmute → both activate. Works across subgraph boundaries.

### Example 3: Fallback Model with Get First

Multiple model loading options — use whichever is most processed:

```
Model Loader   → Set "model_init"        (always active)
LoRA Stack     → Set "model_lora"        (optional group)
Differential   → Set "model_differential" (optional group)

Get First Model
├─ var_1: "model_differential"  ← highest priority
├─ var_2: "model_lora"          ← fallback
├─ var_3: "model_init"          ← always available
└─ output → KSampler
```

Mute the Differential group → automatically uses `model_lora`. Mute both → uses `model_init`.

### Example 4: Progressive Image Pipeline

Each processing stage stores its output. Collect the latest result:

```
Get All Active Image
├─ var_1:  "img_watermark"      ← last stage
├─ var_2:  "img_rescale"
├─ var_3:  "img_mouth"
├─ var_4:  "img_eye"
├─ var_5:  "img_face"
├─ var_6:  "img_upscale"
├─ var_7:  "img_faceswap"
├─ var_8:  "img_initial"        ← first stage
└─ all outputs → Any Multi-Switch → Save Images v2
```

The Multi-Switch returns the first non-None result — the output of the last active stage. Enable or disable any combination of stages and the chain adjusts automatically.

---

## Tips & Best Practices

1. **Name Sets descriptively** — Use prefixes like `model_`, `img_`, `str_`, `lora_` for easy scanning
2. **Use type_filter** on Get First / Get All Active — reduces dropdown clutter
3. **Priority order matters** for Get First — most specific first, fallback last
4. **One Set per name per scope** — avoid duplicate names in the same graph
5. **Bridge Set = publisher, Bridge Get = subscriber** — one Set can control many Gets
6. **Bridge nodes carry mode, not data** — use Set/Get for data, Bridge for mute/bypass control
7. **Show connections for debugging** — right-click → Show connections to visualize links
8. **Nav arrows for navigation** — click the ► on Get First / Get All Active to jump to any setter
9. **Copy-paste is safe** — names are automatically deduplicated, even inside subgraphs

---

## Troubleshooting

### Get dropdown is empty

- No Set nodes exist in the current scope
- The `type_filter` is excluding all available Sets — try `*`
- The Set is in a sibling subgraph (not visible from current scope)

### Bridge Get shows ⚠ next to the name

The selected Bridge Set name no longer exists. Either:
- The Bridge Set was deleted or renamed
- Select a different name from the dropdown

### Pasted group has wrong Bridge names

This shouldn't happen after the paste dedup fix. If a Bridge Get inside a subgraph still shows the old name:
- Right-click the Bridge Get and reselect the correct name from the dropdown
- Save and reload the workflow

### Get First resolves to the wrong setter

Check priority order — var_1 is checked first. Use right-click → **Reorder Vars** to adjust.

### Green dots don't update after muting a group

Green dots are drawn when the node refreshes on canvas. Refresh the page (F5) to force a re-evaluation. The actual link resolution at queue time is always correct regardless of what the dots show.

---

## Related Documentation

- [Get First & Get All Active — Detailed Guide](GetFirst_GetAllActive.md) — In-depth coverage of priority resolution, active detection, bypass walk-through, reorder vars, and advanced patterns
- [Smart Model Loader](Smart_Loaders.md) — Outputs via Set/Get for downstream nodes
- [Utility Nodes](Utility_Nodes.md) — Any Multi-Switch, mode nodes, other helpers
