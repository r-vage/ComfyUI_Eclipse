# Wildcard Processor User Guide

This guide explains how to use the Wildcard Processor node - a powerful template-based prompt expansion system that enables dynamic, randomized prompt generation with infinite variations.

## Table of Contents
- [Overview](#overview)
- [What Wildcard Processor Does](#what-wildcard-processor-does)
- [Getting Started](#getting-started)
- [Wildcard Syntax](#wildcard-syntax)
- [Creating Wildcard Files](#creating-wildcard-files)
- [Using the Node](#using-the-node)
- [Processing Modes](#processing-modes)
- [Seed Control](#seed-control)
- [Advanced Syntax](#advanced-syntax)
- [Tips & Best Practices](#tips--best-practices)
- [Troubleshooting](#troubleshooting)

---

## Overview

**Wildcard Processor** transforms static prompt templates into dynamic prompts with infinite variations. Using special syntax, you can create prompts that randomly select from options, reference wildcard files, and generate thousands of unique combinations.

### Key Features

- **Wildcard Syntax** - `{option1|option2|option3}` for inline options
- **File-Based Wildcards** - Reference external wildcard files
- **Weighted Selection** - Control probability with weights
- **Nested Wildcards** - Wildcards within wildcards
- **Seed Control** - Reproducible random generation
- **Two Modes** - Populate (auto-process) or Fixed (manual edit)

### When to Use Wildcard Processor

Use Wildcard Processor when you need:
- Large-scale batch generation with variation
- Template-based prompt systems
- Dataset creation for training
- Creative exploration of prompt space
- Systematic testing of prompt combinations
- Reproducible random variation

---

## What Wildcard Processor Does

### The Concept

Instead of typing complete prompts, you create **templates** with wildcards that expand to different values:

**Template:**
```
A {red|blue|green} {car|truck|motorcycle} driving through {city|forest|desert}
```

**With Seed 1:**
```
A blue car driving through forest
```

**With Seed 2:**
```
A green motorcycle driving through city
```

**With Seed 3:**
```
A red truck driving through desert
```

Same template, infinite variations controlled by seed.

### How It Works

1. **You write a template** with wildcard syntax
2. **Node processes template** using seed for random selections
3. **Output is expanded prompt** ready for generation
4. **Change seed** for different expansions
5. **Fix seed** for reproducible results

### File Location

**Wildcard Files:** `ComfyUI_Eclipse/wildcards/`

Wildcard files are plain `.txt` files with one option per line.

---

## Getting Started

### Step 1: Add the Node

1. Right-click in ComfyUI workflow
2. Navigate to: `Eclipse > Text > Wildcard Processor`
3. Node appears with:
   - `wildcard_text` - Your template
   - `populated_text` - Processed output
   - `mode` - Processing mode (populate/fixed)
   - `seed` - Random control
   - `wildcards` - Quick insert dropdown

### Step 2: Basic Inline Wildcards

Try this simple example:

**In `wildcard_text`:**
```
A {beautiful|handsome|cute} {woman|man|child}
```

**Set:**
- `mode`: populate
- `seed`: 1

**Result in `populated_text`:**
```
A beautiful woman
```

Change seed to get different combinations!

### Step 3: Using Wildcard Files

1. **Navigate to:** `ComfyUI_Eclipse/wildcards/`

2. **Create file:** `colors.txt`
   ```
   red
   blue
   green
   yellow
   purple
   ```

3. **In wildcard_text:**
   ```
   A {colors} car
   ```

4. **Result:** Node randomly selects from colors.txt
   ```
   A blue car
   ```

### Step 4: Connect and Generate

1. Connect `processed_text` output to CLIP Text Encode
2. Queue prompt
3. Different seed = different expansion

---

## Wildcard Syntax

### Inline Options

**Basic Syntax:**
```
{option1|option2|option3}
```

Randomly selects one option.

**Examples:**
```
{red|blue|green} sky
{happy|sad|excited} person
{photorealistic|anime|oil painting} style
```

**Multiple in One Prompt:**
```
A {young|old} {man|woman} wearing {red|blue|green} clothes
```

### File-Based Wildcards

**Syntax:**
```
{wildcard_filename}
```

References `wildcards/wildcard_filename.txt`

**Example:**

File: `wildcards/animals.txt`
```
cat
dog
bird
fish
```

Template:
```
A cute {animals} in the garden
```

Result:
```
A cute bird in the garden
```

### Weighted Options

**Syntax:**
```
{option1:weight|option2:weight|option3:weight}
```

Higher weight = higher probability.

**Example:**
```
{red:5|blue:3|green:1} car
```

Red is 5x more likely than green, 5/3 times more likely than blue.

**In Files:**

`wildcards/quality.txt`:
```
masterpiece:10
best quality:8
high quality:5
normal quality:2
low quality:1
```

Template:
```
{quality}, portrait
```

Result: "masterpiece" appears 10x more often than "low quality"

### Nested Wildcards

**Syntax:**
Wildcards can contain other wildcards.

**Example:**

`wildcards/art_style.txt`:
```
{modern|classical} {painting|sketch}
{digital|traditional} art
photorealistic
```

`wildcards/subject.txt`:
```
{male|female} {warrior|mage}
{young|old} person
```

Template:
```
{art_style} of {subject}
```

Result: Multiple levels of expansion
```
classical painting of female warrior
```

### Optional Elements

**Syntax:**
```
{option:|} 
```

Empty option means it might not appear.

**Example:**
```
{masterpiece:5|} portrait
```

Result: Sometimes "masterpiece portrait", sometimes just "portrait"

**Weighted Optional:**
```
{very detailed:3|detailed:2|} background
```

Result: 3/5 chance "very detailed", 2/5 chance "detailed", might be empty

---

## Creating Wildcard Files

### File Location

**Primary:** `ComfyUI_Eclipse/wildcards/`

All `.txt` files in this directory become wildcards.

### File Naming

**Rules:**
- Use lowercase
- No spaces (use underscores: `art_styles.txt`)
- Descriptive names
- `.txt` extension

**Good Examples:**
```
colors.txt
emotions.txt
art_styles.txt
camera_angles.txt
lighting_types.txt
```

**Bad Examples:**
```
c.txt                    (not descriptive)
Art Styles.txt          (has spaces)
styles.doc              (wrong extension)
```

### File Content Format

**One option per line:**
```
option one
option two
option three
```

**No blank lines** (or they count as options)

**Can include wildcards:**
```
{bright|dark} red
{light|deep} blue
vivid green
```

### Simple Wildcard File Example

**File:** `wildcards/emotions.txt`
```
happy
sad
excited
calm
mysterious
cheerful
melancholic
```

**Usage:**
```
{emotions} expression
```

### Weighted Wildcard File Example

**File:** `wildcards/quality.txt`
```
masterpiece:10
best quality:8
high quality:5
normal quality:2
```

**Usage:**
```
{quality}, detailed portrait
```

### Nested Wildcard File Example

**File:** `wildcards/character.txt`
```
{male|female} {human|elf|dwarf}
{young|old} {warrior|mage|rogue}
mysterious {hero|villain}
```

**Usage:**
```
{character}, full body portrait
```

Result: Multiple expansions possible
```
young warrior, full body portrait
mysterious villain, full body portrait
```

### Organizing Wildcards

**By Category:**
```
wildcards/
├── subjects/
│   ├── characters.txt
│   ├── animals.txt
│   └── objects.txt
├── styles/
│   ├── art_styles.txt
│   ├── photography.txt
│   └── rendering.txt
└── modifiers/
    ├── quality.txt
    ├── lighting.txt
    └── mood.txt
```

**Reference subfolders:**
```
{subjects/characters}
{styles/art_styles}
{modifiers/quality}
```

---

## Using the Node

### Node Inputs

#### wildcard_text (Required)
- **What it is:** Your template with wildcard syntax
- **Format:** Multi-line text field
- **Contains:** Wildcards like `{options}`, `{file_references}`
- **Example:** "A {colors} {animals} in {location}"

#### populated_text (Required)
- **What it is:** The processed/expanded result
- **In populate mode:** Auto-updated from wildcard_text
- **In fixed mode:** You can edit directly
- **This is what gets output:** Final processed prompt

#### mode (Required)
- **What it is:** How the node processes text
- **Options:**
  - `populate` - Auto-process wildcard_text based on seed
  - `fixed` - Use populated_text as-is (you can edit it)
- **Default:** populate

#### seed (Required)
- **What it is:** Controls random selections
- **Range:** Any integer or special values
- **Special values:**
  - `-1` - Randomize each time
  - `-2` - Increment from last seed
  - `-3` - Decrement from last seed
- **Default:** 0

#### wildcards (Optional)
- **What it is:** Quick-insert dropdown
- **Content:** Lists available wildcard files
- **Use:** Select to insert `{wildcard_name}` into text
- **Default:** "Select a Wildcard"

#### negative_prompt (Optional, Force Input)
- **What it is:** Comma-separated tags to remove from output
- **When connected:** Filters matching tags from the final expanded text
- **Use case:** Remove unwanted tags after wildcard expansion (Raffle-style filtering)

### Node Outputs

#### processed_text (STRING)
- **What it is:** Final expanded prompt
- **Content:** All wildcards expanded, random selections made
- **Cleaning:** Removes unresolved wildcards, cleans punctuation
- **Connect to:** CLIP Text Encode or other text nodes

### Basic Usage Example

**Setup:**
```
wildcard_text: A {red|blue|green} {car|truck} in {city|desert}
populated_text: (auto-filled)
mode: populate
seed: 42
```

**Process:**
1. Enter template in wildcard_text
2. Set mode to "populate"
3. Enter seed (or use buttons)
4. populated_text shows expanded result
5. processed_text output has same content
6. Connect to CLIP Text Encode

**Result with seed 42:**
```
A green truck in city
```

**Change seed to 100:**
```
A blue car in desert
```

---

## Processing Modes

### Populate Mode

**What it does:**
- Automatically processes `wildcard_text` using current seed
- Updates `populated_text` with result
- Changes when seed or wildcard_text changes
- Seed controls randomization

**When to use:**
- Normal wildcard operation
- Want auto-processing
- Using seeds for variation
- Template-based generation

**Workflow:**
```
1. Write template in wildcard_text
2. Set seed
3. populated_text auto-updates
4. Output is processed result
5. Change seed for new expansion
```

**Example:**

wildcard_text:
```
{quality:10|}, {art_style}, portrait of {subject}
```

Seed 1 → populated_text:
```
masterpiece, photorealistic, portrait of young woman
```

Seed 2 → populated_text:
```
oil painting, portrait of elderly man
```

### Fixed Mode

**What it does:**
- Ignores `wildcard_text`
- Uses `populated_text` directly
- You can manually edit `populated_text`
- Seed is ignored

**When to use:**
- Want to manually edit the processed result
- Need to override auto-processing
- Testing specific prompt
- One-off custom prompt

**Workflow:**
```
1. Set mode to "fixed"
2. Edit populated_text directly
3. Output uses your manual edit
4. wildcard_text and seed ignored
```

**Example:**

Mode: fixed
populated_text (manually edited):
```
This exact text, with my specific prompt
```

Output:
```
This exact text, with my specific prompt
```

### Switching Modes

**Populate → Fixed:**
1. Template processed automatically
2. Switch to fixed
3. Now you can edit populated_text
4. Manual edits preserved

**Fixed → Populate:**
1. Manual edits in populated_text
2. Switch to populate
3. populated_text overwritten with processed template
4. Manual edits lost

**Pro Tip:** Use populate mode to generate base prompt, switch to fixed to tweak manually.

---

## Seed Control

### Understanding Seeds

**Seed** determines which random options are selected. Same seed = same selections.

### Seed Values

| Value | Behavior |
|-------|----------|
| 0+ | Fixed seed - reproducible results |
| -1 | Randomize each time - new seed per queue |
| -2 | Increment from last seed (last_seed + 1) |
| -3 | Decrement from last seed (last_seed - 1) |

### Seed Buttons

#### 🎲 Randomize Each Time
- Sets seed to `-1`
- New random seed every queue
- Different output each time
- Non-reproducible

**Use case:** Exploration, generating variety

#### 🎲 New Fixed Random
- Generates new random number
- Sets as fixed seed
- Result is reproducible
- Click again for different fixed seed

**Use case:** Found good result, want to iterate

#### ♻️ (Use Last Queued Seed)
- Copies seed from last execution
- Enabled after first queue
- Reproduces previous output

**Use case:** "That was perfect, use that seed again"

### Negative Prompt Filtering

**Optional `negative_prompt`:**
- Comma-separated tags to exclude from output
- Connect from another text node (force input)
- Filters tags after wildcard expansion
- Works like Raffle's negative_prompt behavior

**Example:**
```
Negative Prompt Node → negative_prompt (Wildcard Processor)
```

Filtered tags won't appear in processed output.

### Special Seed Behaviors

#### -1: Randomize Each Time

**Template:**
```
A {colors} {animals}
```

**With seed -1:**
- Queue 1: "A blue dog"
- Queue 2: "A red cat"
- Queue 3: "A green bird"

Each queue generates new seed automatically.

#### -2: Increment

**Last seed was 100**

Set seed to -2:
- Uses seed 101
- Next queue: seed 102
- Next queue: seed 103

**Use case:** Systematic variation, batch generation

#### -3: Decrement

**Last seed was 100**

Set seed to -3:
- Uses seed 99
- Next queue: seed 98
- Next queue: seed 97

**Use case:** Going back through variations

### Seed Workflow Examples

#### Fixed Seed for Reproducibility

**Setup:**
```
Template: A {quality}, {style} portrait
Seed: 12345 (specific number)
```

**Result:** Same expansion every time
```
A masterpiece, photorealistic portrait
```

Perfect for: Client work, tutorials, documentation

#### Exploration Mode

**Setup:**
```
Template: {subject} in {style}, {lighting}
Seed: -1 (Randomize)
```

**Result:** Different every queue
- Queue 1: "warrior in oil painting, dramatic lighting"
- Queue 2: "mage in watercolor, soft lighting"
- Queue 3: "rogue in digital art, moody lighting"

Perfect for: Discovery, generating reference sets

#### Systematic Variation

**Setup:**
```
Template: {character}, {pose}
Seed: 1000 (start)
```

**Process:**
1. Generate with 1000
2. Like it? Keep seed
3. Want variation? Manually set 1001, 1002...
4. Or use -2 (increment) for automatic iteration

Perfect for: Controlled exploration, batch sets

---

## Advanced Syntax

### Complex Weighted Options

**Multiple weights in one wildcard:**
```
{masterpiece:10|best quality:8|high quality:5|normal:2|low quality:1}
```

**Nested with weights:**
```
{very {bright:3|dark:1}:5|slightly {bright:1|dark:3}:3|neutral:2}
```

### Optional Elements with Weights

**Empty option for sometimes omitting:**
```
{very detailed:5|detailed:3|:2} background
```

Result:
- 50% "very detailed background"
- 30% "detailed background"
- 20% "background" (empty option)

### Combining Multiple Wildcards

**Template:**
```
{quality}, {art_style}, {subject} {pose}, {lighting}, {background}
```

**With files:**
- `quality.txt`: masterpiece, best quality, etc.
- `art_style.txt`: photorealistic, anime, oil painting
- `subject.txt`: warrior, mage, rogue
- `pose.txt`: standing, sitting, action pose
- `lighting.txt`: dramatic, soft, golden hour
- `background.txt`: forest, city, mountains

**Result:** Millions of possible combinations

### Recursive Wildcard References

**File:** `wildcards/full_character.txt`
```
{age} {gender} {race} {class}
```

**File:** `wildcards/age.txt`
```
young
middle-aged
elderly
```

**File:** `wildcards/gender.txt`, etc.

**Template:**
```
{full_character}, full body portrait
```

**Result:**
```
young female elf mage, full body portrait
```

Multiple levels of expansion!

### Wildcard File References in Options

**File:** `wildcards/enhanced_style.txt`
```
{photorealistic|artistic|abstract} {painting|drawing|render}
ultra detailed, {lighting}
```

**Template:**
```
{enhanced_style}, portrait
```

**Result:**
```
photorealistic painting ultra detailed, dramatic lighting, portrait
```

---

## Tips & Best Practices

### Writing Effective Templates

**Start Simple:**
```
A {color} {animal}
```

**Add Complexity Gradually:**
```
A {quality}, {color} {animal} in {location}
```

**Use Weights for Control:**
```
A {common_option:5|rare_option:1} result
```

### Organizing Wildcard Files

**By Function:**
```
wildcards/
├── base/
│   ├── quality.txt      (quality tags)
│   ├── subjects.txt     (main subjects)
├── modifiers/
│   ├── colors.txt       (color options)
│   ├── sizes.txt        (size descriptors)
├── styles/
│   ├── art_styles.txt   (art styles)
│   ├── photography.txt  (photo styles)
└── technical/
    ├── lighting.txt     (lighting setups)
    ├── camera.txt       (camera settings)
```

**By Model Type:**
```
wildcards/
├── flux/
│   ├── quality.txt      (Flux-specific)
│   ├── styles.txt
├── sdxl/
│   ├── quality.txt      (SDXL-specific)
│   ├── styles.txt
└── shared/
    ├── colors.txt       (works with all)
    ├── emotions.txt
```

### File Content Best Practices

**Keep Options Coherent:**

✅ Good (`emotions.txt`):
```
happy
sad
excited
calm
```

❌ Bad (`emotions.txt`):
```
happy
blue car
dramatic lighting
```

**Use Consistent Format:**

✅ Good:
```
dramatic lighting, high contrast
soft natural light, diffused
golden hour glow, warm tones
```

❌ Bad:
```
dramatic lighting, high contrast
soft light
golden hour
```

**Weight Important Options:**

✅ Good (`quality.txt`):
```
masterpiece:10
best quality:8
high quality:5
normal quality:1
```

Ensures better quality appears more often.

### Template Design Patterns

**The Layer Pattern:**
```
{quality}, {style}, {subject}, {details}, {lighting}, {background}
```

Each wildcard handles one aspect.

**The Modifier Pattern:**
```
{base_subject} {modifier1} {modifier2}
```

Example:
```
{animal} {size} {color}
→ cat small orange
```

**The Optional Enhancement Pattern:**
```
{subject}, {enhancement:3|}
```

Sometimes adds enhancement, sometimes plain.

### Performance Optimization

**File Size:**
- Keep files under 100 options for speed
- Split large lists into categories
- Use weights to reduce rare options

**Nesting Depth:**
- Limit to 3-4 levels deep
- Too much nesting = slow processing
- Balance complexity vs performance

**Template Complexity:**
- Test templates with small batches first
- Monitor processing time
- Simplify if too slow

### Quality Control

**Test Templates:**
1. Start with seed = 0
2. Generate 10-20 outputs with different seeds
3. Check for weird combinations
4. Adjust weights or options

**Avoid Conflicts:**

❌ Bad:
```
{red|blue|green} {red|blue|yellow} car
→ "red red car" (redundant)
```

✅ Good:
```
{primary_color} car with {secondary_color} trim
```

**Validate Wildcards:**
- Check files exist
- No typos in filenames
- Files have content
- No empty lines

### Batch Generation Strategy

**Systematic Coverage:**
```
Seed: 1000
Generate 1
Seed: 1001
Generate 1
...
Seed: 1100
```

Gets 100 different variations.

**Random Exploration:**
```
Seed: -1
Batch size: 20
```

Generates 20 random variations at once.

**Controlled Variation:**
```
Template: {character:fixed}, {background}
Seed: different each time
```

Same character, different backgrounds.

---

## Troubleshooting

### Wildcards Not Expanding

**Problem:** `{wildcard}` appears in output instead of expanding.

**Causes & Solutions:**

1. **File doesn't exist:**
   - Check `ComfyUI_Eclipse/wildcards/wildcard.txt` exists
   - Verify filename matches exactly (case-sensitive)
   - Restart ComfyUI after creating files

2. **Wrong syntax:**
   - Use `{wildcard}` or `__wildcard__` syntax (both supported)
   - No spaces in wildcard name
   - File extension is `.txt`

3. **Mode is "fixed":**
   - In fixed mode, wildcards aren't processed
   - Switch to "populate" mode
   - wildcards process automatically

### Unresolved Wildcards in Output

**Problem:** Output shows `__wildcard__` or `{wildcard}` text.

**Solution:**

1. **Missing wildcard file:**
   - Create the file in `wildcards/` directory
   - Add options to file
   - Restart node or ComfyUI

2. **Typo in wildcard name:**
   - Check spelling in template
   - Check spelling in filename
   - Match case exactly

3. **Empty wildcard file:**
   - Add content to file
   - At least one option per file
   - No blank-only files

### Same Output Every Time

**Problem:** Different seeds produce identical results.

**Check:**

1. **Mode is "populate"?**
   - Fixed mode ignores seed
   - Switch to populate

2. **Template has wildcards?**
   - Plain text without `{options}` is always same
   - Add wildcard syntax

3. **Seed is fixed?**
   - This is correct behavior!
   - Same seed = same output
   - Use different seeds or -1 for variety

### Random Selection Not Random Enough

**Problem:** Some options appear way more than others.

**Causes:**

1. **Weighted options:**
   - Check for `:weight` in wildcards
   - High weights appear more often
   - This is intentional

2. **Small sample size:**
   - Generate more outputs
   - Random distribution needs volume
   - 10 outputs might look uneven, 100 will be better

**Solutions:**

- Remove or adjust weights
- Use equal weights for equal distribution
- Generate larger batches

### Weird Combinations

**Problem:** Generated prompts don't make sense.

**Example:**
```
red red car with red interior
```

**Causes:**

1. **Redundant wildcards:**
   - Multiple color wildcards in one template
   - Similar options in different wildcard files

**Solutions:**

- Use specific wildcard names
- Separate primary/secondary options
- Review wildcard file contents

**Better Template:**
```
{primary_color} car with {accent_color} trim
```

Where `primary_color` and `accent_color` are different files.

### Wildcard Dropdown Empty

**Problem:** `wildcards` dropdown shows only "Select a Wildcard".

**Causes:**

1. **No wildcard files:**
   - Create `.txt` files in `wildcards/` folder
   - Restart ComfyUI

2. **Files in wrong location:**
   - Must be in `ComfyUI_Eclipse/wildcards/`
   - Not in subfolders (or use subfolder syntax)

3. **Need refresh:**
   - Node updates every 5 seconds
   - Or restart ComfyUI

### Populated Text Not Updating

**Problem:** Change wildcard_text but populated_text doesn't update.

**Check:**

1. **Mode is "populate"?**
   - Fixed mode doesn't auto-update
   - Switch to populate mode

2. **Seed changed?**
   - Try changing seed to force update
   - Or use seed buttons

3. **Valid syntax?**
   - Check for syntax errors
   - Ensure brackets match: `{}`

**Force Update:**
- Change seed
- Toggle mode (populate→fixed→populate)
- Delete and reconnect node

### Seed Buttons Not Working

**Problem:** Clicking buttons does nothing.

**Check:**

1. **Mode is "populate"?**
   - Seed only matters in populate mode
   - Buttons work in populate mode

2. **Node initialized?**
   - Delete and re-add node
   - Restart ComfyUI

### Processing Too Slow

**Problem:** Node takes long time to process.

**Causes:**

1. **Complex nested wildcards:**
   - Multiple levels of nesting
   - Many wildcards in template

2. **Large wildcard files:**
   - Files with 500+ options
   - Weighted calculations slow

**Solutions:**

- Simplify template
- Reduce nesting depth
- Split large files into smaller ones
- Use fewer wildcards per prompt

### File Changes Not Reflecting

**Problem:** Updated wildcard file but output doesn't change.

**Solutions:**

1. **Restart ComfyUI:**
   - Full server restart
   - Reload browser (Ctrl+F5)

2. **Check file saved:**
   - Verify edits were saved
   - Check file modification date

3. **Clear cache:**
   - Delete node and re-add
   - Use different seed

### Weights Not Working

**Problem:** Weighted options don't seem weighted.

**Check:**

1. **Correct syntax:**
   ```
   {option:10|other:1}
   ```
   Not: `{option|10|other|1}`

2. **In file correctly:**
   ```
   option:10
   other:1
   ```
   Each on separate line

3. **Large enough sample:**
   - Need many generations to see distribution
   - 10 outputs might look random
   - 100+ outputs show clear weighting

---

## Advanced Topics

### Building Wildcard Libraries

**Hierarchical Organization:**

```
wildcards/
├── character/
│   ├── appearance.txt
│   ├── clothing.txt
│   └── accessories.txt
├── scene/
│   ├── location.txt
│   ├── time_of_day.txt
│   └── weather.txt
└── technical/
    ├── quality.txt
    ├── camera.txt
    └── lighting.txt
```

**Master Templates:**

`wildcards/character_full.txt`:
```
{character/appearance}, {character/clothing}, {character/accessories}
```

Use: `{character_full}` expands to complete character.

### Dynamic Prompt Systems

**Conditional Wildcards:**

`wildcards/style_quality.txt`:
```
{photorealistic:5|anime:3|:2}, {quality}
```

Sometimes skips style, always includes quality.

**Layered Prompting:**

```
Base: {subject}
Enhancement: {subject}, {modifier}
Full: {subject}, {modifier}, {technical}
```

Three levels of detail.

### Integration Patterns

**With Smart Prompt:**
```
Smart Prompt → base structure
Wildcard Processor → add variations
Merge → combine both
```

**Multi-Stage Processing:**
```
Wildcard Processor 1 → character
Wildcard Processor 2 → scene
Merge Strings → final prompt
```

**Negative Prompt Filtering:**
```
Negative Prompt → negative_prompt (WP 1)
```

Unwanted tags filtered from WP 1's output.

### Prompt Engineering

**Quality Layers:**
```
{quality:10|}, {art_style}, {subject}, {details:3|}, {technical:2|}
```

Quality and subject most important (weighted higher).

**Negative Prompts:**

`wildcards/negative_common.txt`:
```
worst quality
low quality
blurry
jpeg artifacts
```

Template:
```
{negative_common}, {specific_negatives}
```

**Style Transfer:**

`wildcards/style_intense.txt`:
```
in the style of {famous_artist}
{art_movement} style
inspired by {reference}
```

---

## Quick Reference

### Basic Syntax
| Syntax | Function |
|--------|----------|
| `{opt1\|opt2}` | Random selection from options |
| `{wildcard}` | Reference to wildcard file |
| `{opt1:5\|opt2:1}` | Weighted selection |
| `{opt\|}` | Optional (might be empty) |

### File Location
- Wildcard files: `ComfyUI_Eclipse/wildcards/`
- Format: `.txt` files, one option per line

### Processing Modes
| Mode | Behavior |
|------|----------|
| populate | Auto-process template with seed |
| fixed | Use populated_text manually |

### Seed Values
| Value | Behavior |
|-------|----------|
| 0+ | Fixed (reproducible) |
| -1 | Randomize each time |
| -2 | Increment from last |
| -3 | Decrement from last |

### Node Flow
```
wildcard_text (template)
    ↓ (if mode=populate)
seed controls processing
    ↓
populated_text (expanded)
    ↓
processed_text (output)
```

---

## Related Documentation

- [Smart Prompt Guide](Smart_Prompt.md) - Dropdown-based prompt building
- [Main README](../README.md#node-spotlight-wildcard-processor-eclipse) - Wildcard Processor spotlight
- [Wildcard Syntax Examples](../readme_wildcardprocessor.md) - Additional syntax examples

---

**Need help?** Check the main [README](../README.md) or open an issue on the [GitHub repository](https://github.com/r-vage/ComfyUI_Eclipse).
