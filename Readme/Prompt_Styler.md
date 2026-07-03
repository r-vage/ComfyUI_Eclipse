# Prompt Styler Guide

The **Prompt Styler** node applies predefined visual styles to your prompts. It takes your base prompt and wraps it with style-specific prefixes, suffixes, and negative prompts - transforming a simple description into a fully styled prompt ready for image generation.

## Table of Contents

- [Overview](#overview)
- [Quick Start](#quick-start)
- [Node Inputs](#node-inputs)
- [Style Modes](#style-modes)
- [Included Styles](#included-styles)
- [Using the Index Feature](#using-the-index-feature)
- [Creating Custom Styles](#creating-custom-styles)
- [Style File Format](#style-file-format)
- [File Locations](#file-locations)
- [Examples](#examples)
- [Tips & Best Practices](#tips--best-practices)
- [Troubleshooting](#troubleshooting)

---

## Overview

The Prompt Styler node:

1. **Wraps your prompt** with style-specific text (prefix/suffix)
2. **Adds negative prompts** automatically based on the style
3. **Supports multiple style formats** (tag-based, natural language, custom)
4. **Enables batch processing** with index-based style selection

### Before & After Example

**Your input:**
```
a woman sitting on a wooden floor
```

**After applying "sai-cinematic" style (tag_based mode):**
```
cinematic film still a woman sitting on a wooden floor, shallow depth of field, vignette, highly detailed, high budget, bokeh, cinemascope, moody, epic, gorgeous, film grain, grainy
```

**Negative prompt automatically added:**
```
anime, cartoon, graphic, text, painting, crayon, graphite, abstract, glitch, deformed, mutated, ugly, disfigured
```

---

## Quick Start

1. **Add the node:** Search for "Prompt Styler" in the node menu
2. **Connect your prompt:** Link your text_positive input (from a text node or other source)
3. **Select a style mode:** Choose tag_based, natural_language, or custom
4. **Pick a style:** Select from the dropdown or use index
5. **Connect outputs:** Link text_positive and text_negative to your conditioning nodes

---

## Node Inputs

### Required Inputs

| Input | Type | Description |
|-------|------|-------------|
| `text_positive` | STRING | Your base prompt text (must be connected) |
| `style_mode` | COMBO | Style format: `tag_based`, `natural_language`, or `custom` |
| `style` | COMBO | The specific style to apply |
| `index` | INT | Style selection by index (0-based, with control_after_generate) |
| `apply_to_positive` | BOOLEAN | Enable/disable style application to positive prompt |
| `apply_to_negative` | BOOLEAN | Enable/disable style application to negative prompt |
| `log_prompt` | BOOLEAN | Log styled prompts to console for debugging |

### Optional Inputs

| Input | Type | Description |
|-------|------|-------------|
| `text_negative` | STRING | Your base negative prompt (combined with style's negative) |

### Outputs

| Output | Type | Description |
|--------|------|-------------|
| `text_positive` | STRING | Your prompt with style applied |
| `text_negative` | STRING | Combined negative prompt (style + your input) |

---

## Style Modes

### tag_based

Styles optimized for **SD15 - SDXL, Pony SDXL, Illustious** models. Uses comma-separated tags and descriptors.

**Format:** `style prefix {prompt}. style suffix tags, more tags`

**Example (sai-cinematic):**
- Prefix: `cinematic film still`
- Suffix: `shallow depth of field, vignette, highly detailed, high budget, bokeh...`

**Best for:** SD15 - SDXL, Pony SDXL, Illustious, NoobAI models

### natural_language

Styles written as **flowing sentences**. Better for models trained on natural language captions.

**Format:** `A [style description] of {prompt}, with [style qualities]`

**Example (sai-cinematic):**
```
A cinematic film still of {prompt}, with shallow depth of field, vignette, highly detailed high budget production values, bokeh, cinemascope framing, moody and epic atmosphere, gorgeous film grain
```

**Best for:** Flux, Qwen, Mistral, llava, llama with natural language training

### custom

Shows styles from **user-added style files**. Custom files are auto-detected and categorized:
- Tag-based custom styles also appear in `tag_based` mode
- Natural language custom styles also appear in `natural_language` mode
- All custom styles appear in `custom` mode

A `base` style (pass-through) is automatically added if not present — and is available in **all modes** (`tag_based`, `natural_language`, and `custom`). Selecting `base` simply passes your `text_positive` through unchanged. Note: explicit `base` entries (`{prompt}`) are ignored during automatic style auto-detection.

---

## Included Styles

Eclipse includes **107 pre-built style templates** (including the `base` placeholder) organized by category:

### Categories & Examples

| Category | Count | Example Styles |
|----------|-------|----------------|
| **SAI (Stability AI)** | 18 | 3d-model, analog film, anime, cinematic, comic book, digital art, enhance, fantasy art, photographic, pixel art |
| **Advertising** | 9 | advertising, automotive, corporate, fashion editorial, food photography, gourmet food, luxury, real estate, retail |
| **Art Styles** | 17 | abstract, art deco, art nouveau, cubist, expressionist, graffiti, hyperrealism, impressionist, pop art, renaissance, steampunk, surrealist, watercolor |
| **Futuristic** | 10 | biomechanical, cybernetic, cyberpunk cityscape, retro cyberpunk, sci-fi, vaporwave |
| **Game Styles** | 12 | Bubble Bobble, cyberpunk game, fighting game, GTA, Mario, Minecraft, Pokemon, retro arcade, RPG fantasy, Street Fighter, Zelda |
| **Miscellaneous** | 26 | architectural, disco, dreamscape, dystopian, fairy tale, gothic, grunge, horror, kawaii, lovecraftian, manga, metropolis, minimalist, monochrome, space, tribal |
| **Papercraft** | 9 | collage, flat papercut, kirigami, paper mache, paper quilling, papercut shadow box |
| **Photography** | 7 | alien, HDR, long exposure, polaroid, silhouette, tilt-shift |

### Popular Styles Quick Reference

| Style | Effect | Good For |
|-------|--------|----------|
| `sai-cinematic` | Film-like quality with bokeh and grain | Portraits, scenes |
| `sai-photographic` | 35mm film aesthetic | Realistic photos |
| `sai-anime` | Studio anime style | Anime characters |
| `sai-enhance` | General quality boost | Any subject |
| `sai-fantasy art` | Ethereal, magical look | Fantasy scenes |
| `ads-gourmet food photography` | Professional food styling | Food images |
| `artstyle-watercolor` | Painterly watercolor effect | Artistic renders |
| `misc-kawaii` | Cute, colorful anime style | Cute characters |
| `game-pixel art` | Retro 8-bit aesthetic | Game assets |

---

## Using the Index Feature

The `index` input enables **batch processing and automated style iteration**:

### Control After Generate

The index has `control_after_generate` enabled, giving you these options:

| Setting | Behavior |
|---------|----------|
| **fixed** | Keep the same index |
| **increment** | Move to next style after each generation |
| **decrement** | Move to previous style |
| **randomize** | Pick a random style each time |

### Index Wrapping

The index automatically wraps around:
- If you have 108 styles and set index to 110, it wraps to style 2 (110 % 108 = 2)
- This allows continuous iteration without errors

### Batch Style Testing

1. Set `index` to 0
2. Set control_after_generate to **increment**
3. Enable queue **batch count** (e.g., 10)
4. Generate - each image uses the next style

### JavaScript Sync

The node includes JavaScript that syncs the `index` and `style` dropdown:
- Changing the dropdown updates the index
- The index drives style selection (index takes priority during generation)

---

## Creating Custom Styles

### Step 1: Create Your Style File

Create a CSV or JSON file with your styles:

**CSV Format (recommended):**
```csv
name,prompt,negative_prompt
"my-portrait","professional portrait photograph of {prompt}, studio lighting, sharp focus, 85mm lens","blurry, amateur, bad lighting, noisy"
"my-landscape","breathtaking landscape photograph of {prompt}, golden hour, dramatic sky, 4k","overexposed, flat, dull colors"
```

**JSON Format:**
```json
[
  {
    "name": "my-portrait",
    "prompt": "professional portrait photograph of {prompt}, studio lighting, sharp focus, 85mm lens",
    "negative_prompt": "blurry, amateur, bad lighting, noisy"
  },
  {
    "name": "my-landscape",
    "prompt": "breathtaking landscape photograph of {prompt}, golden hour, dramatic sky, 4k",
    "negative_prompt": "overexposed, flat, dull colors"
  }
]
```

### Step 2: Save to the Styles Directory

**Primary location (also accessible via `models/Eclipse/styles/` junction):**
```
ComfyUI_Eclipse/styles/
```

Default styles are extracted from `.defaults/styles/` on first run and never overwritten.

### Step 3: Restart ComfyUI

Styles are loaded at startup. After adding new style files, restart ComfyUI to see them.

### Naming Convention

| Filename Prefix | Auto-categorized To |
|-----------------|---------------------|
| `tag_based_*.csv` | tag_based mode |
| `natural_lang_*.csv` | natural_language mode |
| Any other name | custom mode (auto-detected) |

---

## Style File Format

### The {prompt} Placeholder

The `{prompt}` placeholder is replaced with your input text:

```
Template: "anime artwork {prompt}, anime style, vibrant"
Input: "a girl with blue hair"
Result: "anime artwork a girl with blue hair, anime style, vibrant"
```

### Required Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique style identifier (shown in dropdown) |
| `prompt` | Yes | Style template containing `{prompt}` placeholder. The placeholder is replaced with `text_positive` input at runtime. |
| `negative_prompt` | No | Negative prompt added automatically (can be empty string) |

### Auto-Detection Rules

Custom styles are auto-detected using a **suffix-first classifier** (only the part *after* `{prompt}` is inspected). Key details:

- If the template is exactly `{prompt}`, it is **ignored** by auto-detection (it's the `base` pass-through style).
- If there is no suffix (nothing after `{prompt}`), the style is classified as **`tag_based`**.
- If the suffix contains sentence punctuation (`.`, `!`, `?`), it's a **`natural_language`** signal.
- Presence of **strong NL markers** in the suffix such as `with`, `featuring`, `depicting`, `showing` → **`natural_language`**.
- Otherwise the suffix is split on commas into segments; segments with **1–4 tokens** are considered "short". If **≥50%** of segments are short → **`tag_based`**, otherwise **`natural_language`**.
- Fallback: if the entire suffix has ≤4 tokens → **`tag_based`**, else **`natural_language`**.

Notes for developers:
- Weak markers (e.g., `and`, `in`, `by`, `for`) are treated specially and do not automatically force NL classification unless the segment is long.
- The classifier is intentionally **suffix-only** to avoid false positives from prefixes; this helps keep tag-based lists separated from flowing NL descriptions.
---

## File Locations

| Location | Purpose | Persists? |
|----------|---------|-----------|
| `ComfyUI_Eclipse/styles/` | Style files (primary) | ✅ Yes |
| `models/Eclipse/styles/` | Junction to above | ✅ Yes |

### Bundled Style Files

| File | Mode | Styles |
|------|------|--------|
| `tag_based_styles.csv` | tag_based | 107 styles (including `base`) |
| `natural_lang_styles.csv` | natural_language | 107 styles (including `base`, same templates rephrased) |

---

## Examples

### Example 1: Basic Portrait

**Input:**
- text_positive: `a young woman with flowing red hair`
- style_mode: `tag_based`
- style: `sai-photographic`

**Output:**
- text_positive: `cinematic photo a young woman with flowing red hair, 35mm photograph, film, bokeh, professional, 4k, highly detailed`
- text_negative: `drawing, painting, crayon, sketch, graphite, impressionist, noisy, blurry, soft, deformed, ugly`

### Example 2: Fantasy Scene

**Input:**
- text_positive: `a mystical forest with glowing mushrooms`
- style_mode: `natural_language`
- style: `sai-fantasy art`

**Output:**
- text_positive: `Ethereal fantasy concept art of a mystical forest with glowing mushrooms, with magnificent celestial and ethereal qualities, painterly epic and majestic magical fantasy art style, suitable for cover art with a dreamy atmosphere`
- text_negative: `Avoid photographic, realistic, realism, 35mm film, dslr, cropped, frame, text, deformed, glitch...`

### Example 3: Combining with Your Negative

**Input:**
- text_positive: `a robot in a cyberpunk city`
- text_negative: `humans, crowds` (your additional negative)
- style: `futuristic-cyberpunk cityscape`

**Output:**
- text_negative: `natural, rural, deformed, low contrast, black and white, sketch, watercolor, humans, crowds`

Your negative prompt is appended to the style's negative prompt.

### Example 4: Style Disabled

**Input:**
- text_positive: `a cat sleeping`
- apply_to_positive: `no`
- apply_to_negative: `yes`
- style: `misc-kawaii`

**Output:**
- text_positive: `a cat sleeping` (unchanged)
- text_negative: `dark, scary, realistic, monochrome, abstract` (style negative only)

---

## Tips & Best Practices

### Choosing the Right Mode

| Use Case | Recommended Mode |
|----------|------------------|
| SD 1.5, SDXL, Pony etc. | tag_based |
| Flux (general) | Either works |
| Flux (natural prompts) | natural_language |
| Pony models | tag_based |
| Testing custom styles | custom |

### Prompt Writing Tips

1. **Keep base prompts simple** - The style adds the artistic direction
2. **Avoid redundancy** - Don't include "cinematic" in your prompt if using a cinematic style
3. **Subject focus** - Your prompt should describe WHAT, the style describes HOW

### Performance Tips

1. **Use index for batches** - Faster than changing dropdown manually
2. **Enable log_prompt** - Debug style application issues
3. **test with "base" style** - See your raw prompt without styling
---

## Troubleshooting

### Style Not Appearing

**Problem:** New custom style not in dropdown

**Solutions:**
1. Restart ComfyUI (styles load at startup)
2. Check file location (must be in styles directory)
3. Verify CSV format (name,prompt,negative_prompt header)
4. Check for syntax errors in JSON files

### Style Not Applied

**Problem:** Output matches input exactly

**Solutions:**
1. Check `apply_to_positive` is enabled (yes)
2. Verify style exists in selected mode
3. Enable `log_prompt` to see what's happening
4. Check style template contains `{prompt}` placeholder

### Index Not Changing Style

**Problem:** Different index values show same style

**Solutions:**
1. Check `control_after_generate` setting
2. Verify style_mode has multiple styles loaded
3. Index wraps: index 0 and index 108 are the same with 108 styles

### Custom Style in Wrong Mode

**Problem:** Custom style appears in tag_based but should be natural_language

**Solution:** Adjust your prompt template:
- Start prefix with "A ", "An ", or "The " for natural_language
- Start suffix with "." or "," for tag_based

Or explicitly name your file:
- `natural_lang_mystyles.csv` → natural_language mode
- `tag_based_mystyles.csv` → tag_based mode

---

**Need more help?** Check the [main documentation index](README.md) or [report an issue](https://github.com/r-vage/ComfyUI_Eclipse/issues).
