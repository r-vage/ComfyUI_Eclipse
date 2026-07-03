# Smart Detection Guide

A comprehensive guide to the **Smart Detection** node for ComfyUI Eclipse.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Node Overview](#node-overview)
  - [Inputs](#inputs)
  - [Outputs](#outputs)
- [Detection Backends](#detection-backends)
  - [Florence-2](#florence-2)
  - [Qwen VL](#qwen-vl)
  - [YOLO](#yolo)
- [Tasks Reference](#tasks-reference)
- [Detection Parameters](#detection-parameters)
- [SEGS Output (Impact Pack)](#segs-output-impact-pack)
- [Quick Start Examples](#quick-start-examples)
- [Troubleshooting](#troubleshooting)

---

## Overview

The **Smart Detection** node is a unified detection node with Florence-2, Qwen VL, and YOLO backends. It detects objects, generates bounding boxes, and outputs masks and SEGS (Impact Pack compatible). The UI is model-driven — backend and family are inferred from your model selection.

### What Can This Node Do?

- **Object detection** — locate objects with bounding boxes and labels
- **Phrase grounding** — find specific objects by text description
- **Region captioning** — captions for detected regions
- **OCR** — extract text with bounding boxes
- **Segmentation** — instance and referring expression segmentation
- **SEGS output** — direct pipeline into Impact Pack nodes

---

## Key Features

| Feature | Description |
|---------|-------------|
| **3 Detection Backends** | Florence-2 (Transformers), Qwen VL (all backends), YOLO (ultralytics) |
| **Model-Driven UI** | Backend/family auto-detected from model selection; task list filtered per family |
| **Impact Pack Compatible** | SEGS output with crop factor and mask dilation |
| **Preview Boxes** | Toggle bounding box overlay on image output |
| **NMS Filtering** | Non-maximum suppression to merge overlapping detections |
| **Post-Processing Pipeline** | Confidence filter → NMS → detection area filter → drop size filter |
| **Class Filtering (YOLO)** | Filter by class name via user_input (semicolon-separated) |
| **Select Index** | Output a single detection by index, or all (-1) |
| **Mode Bar** | Toggle chips: Cleanup, Keep Loaded, Preview Boxes, Adjust, Advanced |

---

## Node Overview

### Inputs

#### Mode Bar Chips (hidden backing widgets, synced by JS)

| Chip | Default | Description |
|------|---------|-------------|
| **Cleanup** | ON | Pre-load VRAM cleanup |
| **Keep Loaded** | OFF | Cache detection model between runs |
| **Preview Boxes** | ON | Draw detection bboxes on image output |
| **Adjust** | OFF | Show bbox adjustment params (drop_size, crop_factor, dilation) |
| **Advanced** | OFF | Show advanced generation parameters |

#### Main Widgets

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| **model_name** | Dropdown | — | Detection model. Suffix indicates backend |
| **quantization** | Dropdown | Q4_K_M | GGUF only — quantization variant |
| **task** | Dropdown | — | Detection task. Florence: 7 tasks, Qwen: 2 tasks, YOLO: hidden |
| **user_input** | String | — | Grounding query. Florence: phrase to locate. Qwen: natural language. YOLO: optional class filter (semicolon-separated, e.g. `face;person`). Leave empty to detect all |

#### Detection Parameters

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| **confidence** | Float | 0.5 | Detection confidence threshold (0.0–1.0) |
| **nms_iou_threshold** | Float | 0.5 | NMS overlap threshold. Higher = more permissive |
| **detection_filter** | Float | 0.8 | Max bbox-to-image area ratio. Removes full-image false positives |
| **drop_size** | Integer | 10 | Min bbox dimension in pixels. Smaller detections are dropped |
| **crop_factor** | Float | 3.0 | SEGS crop expansion factor (Impact Pack default: 3.0) |
| **dilation** | Integer | 0 | Mask dilation in pixels. Positive=expand, negative=shrink |
| **select_index** | Integer | -1 | -1=all detections, 0+=select single detection by index |

#### Advanced Widgets (hidden by default)

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| **device** | Dropdown | cuda | Compute device |
| **num_beams** | Integer | 1 | Beam search count |
| **do_sample** | Boolean | True | Enable sampling vs greedy decoding |
| **use_torch_compile** | Boolean | False | torch.compile for faster inference |
| **convert_to_bboxes** | Boolean | False | Florence-only: convert quads/polys to bboxes |
| **temperature** | Float | 0.7 | Sampling temperature |
| **top_p** | Float | 0.9 | Nucleus sampling |
| **top_k** | Integer | 50 | Top-k sampling |
| **repetition_penalty** | Float | 1.0 | Repeat penalty |
| **seed** | Integer | -1 | Random seed. -1=random, -2=increment, -3=decrement |

#### Connection Slots

| Input | Type | Description |
|-------|------|-------------|
| **image** | IMAGE | Image to detect objects in (required) |

### Outputs

| Output | Type | Description |
|--------|------|-------------|
| **image** | IMAGE | Preview with bounding boxes drawn (or passthrough if preview disabled) |
| **mask** | MASK | Binary mask of all detections combined |
| **segs** | SEGS | Impact Pack compatible SEGS tuple `((h, w), [SEG, ...])` |
| **data** | JSON | Detection data dict: `bboxes`, `labels`, `confidences`, `coord_range`, `backend`, `model`, `task` |

---

## Detection Backends

### Florence-2

**Microsoft Florence-2** — specialized detection model with structured prompt tokens.

| Property | Value |
|----------|-------|
| **Backend** | Transformers only |
| **Tasks** | 7 detection tasks + OCR/DocVQA text tasks |
| **Output** | Structured bboxes with labels |
| **VRAM** | ~4 GB |

**Registry Models:** Florence-2-base, Florence-2-large, base-ft, large-ft, PromptGen variants

**Strengths:**
- Most detection tasks (phrase grounding, region caption, dense caption, segmentation)
- Structured output — reliable bbox parsing
- Low VRAM

**Limitations:**
- No Docker backend support
- First frame only for multi-frame input

### Qwen VL

**Qwen2.5-VL / Qwen3-VL** — general-purpose VLM with detection capability.

| Property | Value |
|----------|-------|
| **Backends** | Transformers, GGUF, vLLM, SGLang, Ollama |
| **Tasks** | Caption to Phrase Grounding, Region Caption |
| **Output** | JSON with bboxes (parsed from free-text) |

**Strengths:**
- Multi-backend support
- Natural language queries
- Works with any Qwen VL model already loaded

**Limitations:**
- Detection output is parsed from free-text JSON — less reliable than Florence's structured output
- Fewer specialized detection tasks

> ⚠️ **WIP:** Qwen VL detection is under construction — results may be inconsistent. Florence-2 and YOLO are more reliable for detection.

### YOLO

**YOLO** (ultralytics) — fast single-shot object detection.

| Property | Value |
|----------|-------|
| **Backend** | ultralytics (local) |
| **Tasks** | No task dropdown — always detects all classes |
| **Output** | Bboxes, labels, confidences + instance segmentation masks (-seg models) |
| **Auto-Discovery** | Local YOLO models from Impact Pack directories |

**Registry Models:** face_yolov8m, hand_yolov8s, person_yolov8m-seg, face_yolov8m-seg

**Strengths:**
- Fastest detection backend
- Instance segmentation masks with -seg models
- Class filtering via user_input
- No VRAM-heavy LLM loading

**Limitations:**
- Fixed class vocabulary (no natural language queries)
- No region captioning

**Class Filtering:**
Set `user_input` to semicolon-separated class names to filter detections:
- `face` — only face detections
- `face;person` — faces and persons
- Empty — all detected classes

Matching is fuzzy: substring and plural/singular tolerance (`eye` matches `eyes`, `breast` matches `Breasts`).

---

## Tasks Reference

### Detection Tasks

| Task | Florence | Qwen | user_input |
|------|:--------:|:----:|------------|
| **Caption to Phrase Grounding** | ✅ | ✅ | Required — phrase to locate |
| **Region Caption** | ✅ | ✅ | Optional |
| **Dense Region Caption** | ✅ | ❌ | Optional |
| **Region Proposal** | ✅ | ❌ | Optional |
| **Referring Expression Segmentation** | ✅ | ❌ | Required — describe what to segment |
| **OCR With Region** | ✅ | ❌ | Optional |
| **DocVQA** | ✅ | ❌ | Required — question about document |

### Text-Mode Tasks (return text, no bboxes)

| Task | Description |
|------|-------------|
| **OCR** | Extract text from image (Florence) |
| **DocVQA** | Answer questions about documents (Florence) |

> YOLO does not use the task dropdown — it always detects all classes, with optional class filtering via user_input.

---

## Detection Parameters

### Processing Pipeline

Detections go through this pipeline in order:

1. **Backend generates** raw detections (bboxes, labels, confidences)
2. **YOLO class filter** — filter by user_input class names (YOLO only)
3. **Confidence filter** — remove detections below `confidence` threshold
4. **NMS** — merge overlapping bboxes using `nms_iou_threshold`
5. **Detection filter** — remove bboxes larger than `detection_filter` × image area
6. **Drop size** — remove bboxes with width or height ≤ `drop_size` pixels
7. **Build SEGS** — create Impact Pack SEGS with `crop_factor` and `dilation`
8. **Select index** — if `select_index` ≥ 0, output only that detection

### Parameter Guide

| Parameter | When to Adjust |
|-----------|----------------|
| **confidence** | Lower (0.3) for more detections, higher (0.7) for fewer false positives |
| **nms_iou_threshold** | Lower (0.3) if too many overlapping boxes, higher (0.7) if boxes are being merged incorrectly |
| **detection_filter** | Lower (0.5) if full-image false positives appear, higher (0.95) to keep large detections |
| **drop_size** | Increase to filter out tiny noise detections |
| **crop_factor** | Standard is 3.0. Increase for more context around detections |
| **dilation** | Positive values expand masks, negative values shrink. Use for inpainting margin |
| **select_index** | -1 for all detections, 0 for first/largest, 1+ for specific detection |

---

## SEGS Output (Impact Pack)

The `segs` output is a tuple `((height, width), [SEG, ...])` compatible with Impact Pack nodes like SEGS to Mask, SEGS Preview, Detailer, etc.

Each SEG contains:
- **cropped_image** — cropped region from the original image
- **cropped_mask** — binary mask for the detection within the crop
- **confidence** — detection confidence score
- **crop_region** — `(x1, y1, x2, y2)` expanded by `crop_factor`
- **bbox** — original `(x1, y1, x2, y2)` bounding box
- **label** — detection label string
- **control_net_wrapper** — None (reserved for Impact Pack)

### Connecting to Impact Pack

```
Smart Detection → segs → SEGS to Mask → mask
Smart Detection → segs → Detailer → detailed image
Smart Detection → segs → SEGS Preview → preview
```

---

## Quick Start Examples

### Face Detection (YOLO)

1. Add **Smart Detection** node
2. Select model: `face_yolov8m`
3. Connect an image
4. Set confidence: 0.5
5. Outputs: image with face boxes, mask, SEGS

### Object Grounding (Florence)

1. Select model: `Florence-2-base`
2. Set task: **Caption to Phrase Grounding**
3. Set user_input: `the red car`
4. Connect an image
5. Output: bounding box around the red car

### Person Segmentation (YOLO -seg)

1. Select model: `person_yolov8m-seg`
2. Connect an image
3. Output: instance segmentation masks for each person
4. Connect `segs` to Impact Pack Detailer for per-person processing

### YOLO Class Filtering

1. Select any YOLO model
2. Set user_input: `face;hand`
3. Only face and hand detections are returned
4. Leave user_input empty to detect all classes

### Region Captioning (Florence)

1. Select model: `Florence-2-large`
2. Set task: **Dense Region Caption**
3. Connect an image
4. Output: bounding boxes with descriptive captions for each region

---

## Troubleshooting

### No Detections

- Lower the `confidence` threshold (try 0.3)
- Check `detection_filter` — if too low, large objects are filtered out
- Check `drop_size` — if too high, small objects are filtered out
- For Florence phrase grounding: make sure `user_input` matches what's in the image
- For YOLO class filtering: check spelling in `user_input`

### Florence Not Loading

Check that the model path exists and files are complete. Florence-2 uses a custom loading path via `florence2_wrapper.py`.

### Empty Mask/SEGS

If detections exist but mask is empty:
- Increase `dilation` to expand masks
- Check `select_index` — -1 returns all, 0+ returns single detection
- For YOLO -seg models with instance masks, check model is a `-seg` variant

### YOLO Model Not Found

YOLO models are auto-discovered from:
1. Registry defaults (4 curated models with download URLs)
2. Impact Pack model directories
3. Select a registry model — it auto-downloads on first use

### Preview Boxes Not Showing

Toggle the **Preview Boxes** chip on the mode bar (default: ON). Preview always draws ALL bounding boxes regardless of `select_index`.

### Debug Logging

Enable in `config.json`:
```json
{ "log_level": "debug" }
```

---

*Guide for ComfyUI Eclipse v3.3.4*
