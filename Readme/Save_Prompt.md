# Save Prompt

Save text, prompts, or captions to files in txt, csv, or json format. Perfect for batch captioning workflows with automatic filename matching.

## Overview

The **Save Prompt** node saves text content to files with flexible naming and output options. Designed for:

- **Batch Captioning**: Save captions alongside source images
- **Training Data Preparation**: Export prompts in various formats
- **Prompt Logging**: Keep records of generated prompts
- **Dataset Annotation**: Create tag files for training

## Features

- 📝 **Multiple formats**: TXT, CSV, JSON
- 📂 **Source folder integration**: Save alongside source images
- 🏷️ **Placeholder system**: Dynamic filenames with `%source_filename`, `%date`, etc.
- 🔢 **Auto-numbering**: Sequential file naming with padding
- ➕ **Append mode**: Add to existing files
- 🔞 **NSFW detection**: Auto-detect and tag content levels (JSON)

---

## Inputs

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | STRING | (required) | The text/prompt to save. Connected from upstream node. |
| `output_path` | STRING | "" | Output folder. Empty + `use_source_folder` = save with source. Supports placeholders: `%source_folder`, `%source_base_folder`, `%date`, `%time`, `%counter`. |
| `use_source_folder` | BOOLEAN | True | Save in same folder as source image (from pipe or filename_opt). |
| `filename_prefix` | STRING | "%source_filename" | Filename prefix. Supports placeholders: `%source_filename`, `%source_folder`, `%source_base_folder`, `%date`, `%time`, `%counter`. |
| `filename_delimiter` | STRING | "_" | Delimiter between prefix and counter (new mode only). |
| `filename_number_padding` | INT | 4 | Counter digits (e.g., 4 = 0001). Only used in 'new' mode. |
| `extension` | COMBO | "txt" | File format: `txt`, `csv`, `json` |
| `write_mode` | COMBO | "new" | `new`: numbered files, `overwrite`: replace each time, `append`: add to file, `append_batch`: fast batch append (keeps file handle open, auto-closes after 10s idle), `keep`: skip if exists |
| `csv_positive_name` | STRING | "✅Style" | [CSV] Name/label for the style entry. |
| `csv_negative_prompt` | STRING | "ugly, deformed..." | [CSV] Negative prompt text for the style. |
| `nsfw_level` | COMBO | "disabled" | [JSON only] NSFW tagging: `disabled`, `auto`, `None`, `Mature`, `X` |
| `filename_opt` | STRING | (optional) | Full filepath to source file. Enables placeholders without needing a pipe. |
| `pipe_opt` | PIPE | (optional) | Pipe from Load Image From Folder. Overrides filename_opt if both connected. |
| `log_prompt` | BOOLEAN | False | When True, logs the `Filepath`, `Prompt` (cleaned), and `Negative prompt` (if provided) to the console. Logging occurs after a successful save and when a save is skipped in `keep` mode. |

---

## Logging

When `log_prompt` is enabled the node will emit console messages (via the node logger) showing:

- **Filepath**: full path to the saved file (or the path checked when skipping in `keep` mode)
- **Prompt**: cleaned prompt text (line breaks removed and spaces collapsed)
- **Negative prompt**: when `csv_negative_prompt` is non-empty (CSV saves)

Logging occurs immediately after a successful save or when a save is skipped due to `keep` mode.

## Outputs

| Output | Type | Description |
|--------|------|-------------|
| `text` | STRING | The original input text (passthrough for chaining) |

---

## Write Modes

### New Mode (`new`)

Creates the file if it doesn't exist, or adds numbered versions if it does:

- **First file**: `{prefix}.{ext}` (no counter)
- **If file exists**: `{prefix}{delimiter}{counter}.{ext}` (e.g., `cat_0001.txt`)
- Uses `filename_delimiter` between prefix and counter (default: `_`)
- Uses `filename_number_padding` for counter width (default: 4 → `0001`)

**Example Scenarios:**

| Settings | Image | Existing Files | File Created |
|----------|-------|----------------|--------------|
| `%source_filename` | `cat.png` | (none) | `cat.txt` ✅ |
| `%source_filename` | `cat.png` | `cat.txt` | `cat_0001.txt` |
| `%source_filename` | `cat.png` | `cat.txt`, `cat_0001.txt` | `cat_0002.txt` |
| `%source_filename` | `dog.png` | (none) | `dog.txt` ✅ |
| Static `"prompt"` | `cat.png` | (none) | `prompt.txt` |
| Static `"prompt"` | `dog.png` | `prompt.txt` | `prompt_0001.txt` |

**Typical Use Case:** Batch captioning where you want matching filenames, with version history if re-run:
```
use_source_folder: True
filename_prefix: %source_filename
write_mode: new
```
→ First run: `cat.png` → `cat.txt`, `dog.png` → `dog.txt`
→ Re-run: `cat.png` → `cat_0001.txt` (original `cat.txt` preserved)

### Overwrite Mode (`overwrite`)

Overwrites the file if it already exists (no counter added):

- No counter in filename
- If file exists: content is replaced
- If file doesn't exist: created normally
- With `%source_filename`: each image gets its own file, re-running replaces existing captions

**Example Scenarios:**

| Settings | Image | File Written | Behavior |
|----------|-------|--------------|----------|
| `%source_filename` | `cat.png` | `cat.txt` | ✅ Per-image - each image gets its own file |
| `%source_filename` | `dog.png` | `dog.txt` | ✅ Per-image - each image gets its own file |
| Static `"captions"` | `cat.png` | `captions.txt` | ⚠️ All images write to same file! |
| Static `"captions"` | `dog.png` | `captions.txt` | ⚠️ Overwrites cat's caption! |

**Typical Use Case:** Batch captioning where each image needs its own caption file:
```
use_source_folder: True
filename_prefix: %source_filename
write_mode: overwrite
```
→ Each image gets a matching `.txt` file (e.g., `cat.png` → `cat.txt`)

> **⚠️ Warning:** Using a static `filename_prefix` (no `%source_filename`) with `overwrite` mode will result in only the LAST image's caption being saved, since each one overwrites the previous!

### Append Mode (`append`)

Single file, content added each execution:
```
my_prompt.txt  (grows with each run)
```

- No counter in filename
- Each run adds to the file
- TXT: New line between entries
- CSV: New row per entry
- JSON: Added to array/dict

### Append Batch Mode (`append_batch`)

Same as `append` but optimized for batch processing:
- Keeps file handle open between writes for faster I/O
- Auto-closes after 10 seconds of inactivity
- Best for high-volume batch captioning workflows

### Keep Mode (`keep`)

Skip saving if file already exists:
```
my_prompt.txt  (unchanged if it exists)
```

- No counter in filename
- If file exists: skipped entirely (no modification)
- If file doesn't exist: created normally
- Perfect for re-running batch processing without overwriting manual edits
- Logs "File already exists, skipping (keep mode)" when skipped

**Example Scenarios:**

| Settings | Image | File Checked | Behavior |
|----------|-------|--------------|----------|
| `%source_filename` | `cat.png` | `cat.txt` | ✅ Per-image - skips only if `cat.txt` exists |
| `%source_filename` | `dog.png` | `dog.txt` | ✅ Per-image - skips only if `dog.txt` exists |
| `%date_%source_filename` | `cat.png` | `2025-12-27_cat.txt` | ✅ Date-prefixed per-image |
| Static `"captions"` | `cat.png` | `captions.txt` | ⚠️ All images skip after first! |

**Typical Use Case:** Re-running batch captioning on a folder where you've manually edited some captions:
```
use_source_folder: True
filename_prefix: %source_filename
write_mode: keep
```
→ Images with existing `.txt` files are skipped, new images get captions generated.

> **⚠️ Warning:** Using a static `filename_prefix` (no `%source_filename`) with `keep` mode will skip ALL images after the first one, since they all check the same file!

---

### Write Modes Comparison

| Mode | File doesn't exist | File exists |
|------|-------------------|-------------|
| **new** | Creates `cat.txt` | Creates `cat_0001.txt` (preserves original) |
| **overwrite** | Creates `cat.txt` | Overwrites `cat.txt` (replaces content) |
| **keep** | Creates `cat.txt` | Skips entirely (preserves original) |
| **append** | Creates `cat.txt` | Adds to `cat.txt` (grows file) |
| **append_batch** | Creates `cat.txt` | Adds to `cat.txt` (fast, keeps file handle open) |

**Quick Guide:**
- **new** → Best for batch captioning with version history
- **overwrite** → Best for regenerating all captions fresh
- **keep** → Best for re-running without losing manual edits
- **append** → Best for collecting prompts into a single file like a random prompt bank
- **append_batch** → Same as append but faster for batch processing (keeps file handle open, auto-closes after 10s idle)

---

## File Formats

### TXT Format

Plain text, one prompt per file (new/overwrite) or multiple lines (append):

```
a beautiful landscape, mountains, sunset, golden hour
```

**Append behavior**: Each entry on a new line.

### CSV Format

Comma-separated values with 3 columns for style/training data:

```csv
name,prompt,negative_prompt
"✅Style","a beautiful landscape, mountains, sunset, golden hour","ugly, blurry"
"✅Style","portrait of a woman, professional lighting","ugly, deformed"
```

**Features**:
- Automatic header row: `name,prompt,negative_prompt`
- `csv_positive_name` sets the name column (default: "✅Style")
- `csv_negative_prompt` sets the negative_prompt column
- Proper CSV escaping for commas and quotes
- Each entry is a new row

### JSON Format

Structured data with filename as key:

**Without NSFW (nsfw_level = disabled)**:
```json
{
  "photo_001.png": {
    "prompt": "a beautiful landscape, mountains, sunset"
  },
  "photo_002.png": {
    "prompt": "portrait of a woman, professional lighting"
  }
}
```

**With NSFW (nsfw_level ≠ disabled)**:
```json
{
  "photo_001.png": {
    "prompt": "a beautiful landscape, mountains, sunset",
    "nsfwLevel": "None"
  },
  "photo_002.png": {
    "prompt": "portrait of a woman, professional lighting",
    "nsfwLevel": "Mature"
  }
}
```

Uses source filename as key for easy dataset management.

---

## Placeholder System

### Available Placeholders

| Placeholder | Description | Example |
|-------------|-------------|---------|
| `%source_filename` | Source filename without extension | `photo_001` |
| `%source_folder` | Immediate parent folder name | `portraits` |
| `%source_base_folder` | Root folder from input list | `datasets` |
| `%date` / `%today` | Current date (YYYY-MM-DD) | `2025-12-24` |
| `%time` | Current time (HHMMSS) | `143052` |
| `%Y` | Year (4 digits) | `2025` |
| `%m` / `%M` | Month (2 digits) | `12` |
| `%d` / `%D` | Day (2 digits) | `24` |
| `%H` | Hour (2 digits) | `14` |
| `%S` | Second (2 digits) | `52` |
| `%counter` | Execution counter | `1`, `2`, `3`... |

### Folder Placeholders Explained

When processing `D:/datasets/portraits/subfolder/img.png` from input folder `D:/datasets/portraits`:

| Placeholder | Value | Use Case |
|-------------|-------|----------|
| `%source_folder` | `subfolder` | Immediate parent of the file |
| `%source_base_folder` | `portraits` | The folder from the input list |

This is useful for multi-folder workflows:
```
filename_prefix: %source_base_folder_%source_filename
```
Result: `portraits_img.txt`, `landscapes_photo.txt`

### Placeholder Usage

**In filename_prefix**:
```
%source_filename          → photo_001.txt
%date_%source_filename    → 2025-12-24_photo_001.txt
caption_%counter          → caption_1.txt, caption_2.txt, ...
%source_base_folder_%source_filename → portraits_photo_001.txt
```

**In output_path**:
```
%date/captions        → 2025-12-24/captions/
tagged/%model         → tagged/sd_xl_base/
%source_base_folder   → portraits/ (organize by source folder)
```

---

## Source Folder Integration

The most powerful feature for batch captioning workflows.

### How It Works

When `use_source_folder` is enabled:

1. Node reads `filepath` from `pipe_opt` (from Load Image From Folder)
2. Extracts the folder containing the source image
3. Saves output in that folder (or relative to it)

### Examples

**Source**: `D:/datasets/images/photo_001.png`

| output_path | Result |
|-------------|--------|
| (empty) | `D:/datasets/images/photo_001.txt` |
| `captions` | `D:/datasets/captions/photo_001.txt` |
| `../captions` | `D:/datasets/captions/photo_001.txt` |
| `./captions` | Auto-corrected to `../captions` |

### Typical Batch Captioning Setup

```
Load Image From Folder
    ↓ (image)
Vision Model (generates caption)
    ↓ (text)
Save Prompt
    ├─ use_source_folder: True
    ├─ filename_prefix: %source_filename
    ├─ write_mode: overwrite
    └─ pipe_opt: connected from Load Image From Folder
```

Result: Each image gets a matching `.txt` file in the same folder.

---

## NSFW Detection (JSON Only)

Automatic content level detection for dataset annotation.

### Detection Levels

| Level | Keywords Detected |
|-------|------------------|
| **X** | sex, nude, nsfw, porn, hentai, explicit, penetration, genitals, etc. |
| **Mature** | sexy, seductive, lingerie, bikini, cleavage, suggestive, etc. |
| **None** | No NSFW keywords detected |

### Settings

| nsfw_level | Behavior |
|------------|----------|
| `disabled` | No NSFW tagging, simple array format |
| `auto` | Automatically detect from prompt text |
| `None` | Force "None" level for all entries |
| `Mature` | Force "Mature" level for all entries |
| `X` | Force "X" level for all entries |

### Output Format

With `nsfw_level` enabled, JSON uses filename-keyed format:
```json
{
  "image_001.png": {
    "prompt": "...",
    "nsfwLevel": "None"
  }
}
```

---

## Usage Examples

### Basic Prompt Saving

Save prompts with date prefix:
```
output_path: prompts
filename_prefix: %date_prompt
write_mode: new
extension: txt
```
Result: `prompts/2025-12-24_prompt_0001.txt`

### Batch Captioning

Save captions alongside source images:
```
use_source_folder: True
filename_prefix: %source_filename
write_mode: overwrite
extension: txt
pipe_opt: connected from Load Image From Folder
```
Source: `images/cat_photo.png` → Output: `images/cat_photo.txt`

### Training Dataset (CSV)

Collect all prompts in a single CSV:
```
output_path: datasets
filename_prefix: training_prompts
write_mode: append
extension: csv
```
Result: `datasets/training_prompts.csv` with all prompts as rows

### Civitai-Style JSON

Create JSON with NSFW ratings:
```
output_path: metadata
filename_prefix: dataset
write_mode: append
extension: json
nsfw_level: auto
pipe_opt: connected
```
Result: `metadata/dataset.json` with filenames as keys and NSFW levels

### Separate Folders by Date

Organize captions by date:
```
output_path: %Y/%m/%d
filename_prefix: caption_%counter
write_mode: new
extension: txt
```
Result: `2025/12/24/caption_0001.txt`

---

## Path Handling

### Absolute Paths

Full paths are used directly (allows saving outside ComfyUI):
```
output_path: D:/my_datasets/captions
```

### Relative Paths (Without Source Folder)

Resolved inside ComfyUI's output directory:
```
output_path: captions/daily
→ ComfyUI/output/captions/daily/
```

### Relative Paths (With Source Folder)

Resolved relative to source image folder:
```
use_source_folder: True
output_path: captions
source: D:/images/photo.png
→ D:/images/captions/
```

### Auto-Correction

Single dot paths are auto-corrected to go up one level:
```
output_path: .\captions  →  ..\captions
output_path: ./captions  →  ../captions
```

This matches the common expectation that `.\folder` means "outside the current folder".

---

## Text Processing

The node automatically cleans input text:

1. **Removes line breaks**: `\r\n`, `\r`, `\n` → space
2. **Collapses spaces**: Multiple spaces → single space
3. **Trims whitespace**: Leading/trailing spaces removed

This ensures captions are single-line for compatibility with training tools.

---

## Pipe Integration

### From Load Image From Folder

```
Load Image From Folder (pipe) → Save Prompt (pipe_opt)
```

The pipe contains:
| Field | Example | Description |
|-------|---------|-------------|
| `path` | `D:/images` | Base folder from input |
| `filename` | `D:/images/subfolder/cat.png` | Full path to image |

Enables placeholders:
- `%source_filename` - filename without extension (e.g., `cat`)
- `%source_folder` - immediate parent folder (e.g., `subfolder`)
- `%source_base_folder` - root folder from input (e.g., `images`)

### Using filename_opt Instead of Pipe

If you don't have a pipe from Load Image From Folder, you can connect a **full filepath** string to `filename_opt`:

```
Any String Node (full path) → Save Prompt (filename_opt)
```

**Important:** The filepath must be complete, e.g., `D:/images/subfolder/cat.png`

From this, the node derives:
- `%source_filename` → `cat` (filename without extension)
- `%source_folder` → `subfolder` (parent folder name)

> **Note:** `%source_base_folder` is not available with `filename_opt` since there's no base folder reference. The pipe takes priority if both are connected.

---

## Troubleshooting

### File Not Saving

1. Check `output_path` is valid/writable
2. Verify folder exists (node creates it if needed)
3. Check for permission issues

### Wrong Filename

1. Verify `filename_prefix` with placeholders
2. Check `pipe_opt` is connected for source placeholders
3. Empty placeholders fall back to placeholder name without `%`

### Append Not Working

1. Ensure `write_mode` is set to "append"
2. Check file permissions
3. For JSON, verify existing file is valid JSON

### Source Folder Not Used

1. Enable `use_source_folder`
2. Connect `pipe_opt` from Load Image From Folder
3. Verify pipe contains `filepath` or `path`

---

## Related Nodes

- **[Load Image From Folder](Load_Image_From_Folder.md)** - Load images sequentially for batch processing
- **[Save Images](Save_Images.md)** - Save images with metadata
- **[Replace String v3](Replace_String_v3.md)** - Clean up captions before saving
