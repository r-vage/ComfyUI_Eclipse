# Smart Language Model Loader Guide

A comprehensive guide to the **Smart Language Model Loader** node for ComfyUI Eclipse.

---

## Table of Contents

- [Smart Language Model Loader Guide](#smart-language-model-loader-guide)
  - [Table of Contents](#table-of-contents)
  - [Overview](#overview)
    - [What Can This Node Do?](#what-can-this-node-do)
  - [Key Features](#key-features)
  - [Node Overview](#node-overview)
    - [Inputs](#inputs)
      - [Core Widgets](#core-widgets)
      - [Mode Bar Chips (hidden backing widgets, synced by JS)](#mode-bar-chips-hidden-backing-widgets-synced-by-js)
      - [Advanced Widgets (hidden by default)](#advanced-widgets-hidden-by-default)
      - [WD14 Widgets (hidden unless WD14 model selected)](#wd14-widgets-hidden-unless-wd14-model-selected)
      - [Connection Slots](#connection-slots)
    - [Outputs](#outputs)
  - [Model Families](#model-families)
    - [Qwen](#qwen)
    - [Mistral](#mistral)
    - [Florence](#florence)
    - [LLaVA](#llava)
    - [VLM (Generic)](#vlm-generic)
    - [LLM (Text-Only)](#llm-text-only)
    - [WD14 Tagger](#wd14-tagger)
  - [Backends](#backends)
    - [Transformers](#transformers)
    - [GGUF (llama-cpp-python)](#gguf-llama-cpp-python)
    - [vLLM (Docker)](#vllm-docker)
    - [SGLang (Docker)](#sglang-docker)
    - [Ollama (Docker)](#ollama-docker)
    - [llama.cpp (Docker)](#llamacpp-docker)
    - [WD14 (ONNX)](#wd14-onnx)
  - [Compatibility Matrix](#compatibility-matrix)
  - [Tasks Reference](#tasks-reference)
    - [Custom Tasks](#custom-tasks)
    - [Vision Tasks (all VLM families)](#vision-tasks-all-vlm-families)
    - [Vision Tasks (Florence-only)](#vision-tasks-florence-only)
    - [Text Tasks (all families)](#text-tasks-all-families)
  - [Multi-Task Mode](#multi-task-mode)
    - [How It Works](#how-it-works)
    - [Example: Image → Tags → Natural Language → Expanded Prompt](#example-image--tags--natural-language--expanded-prompt)
    - [Notes](#notes)
  - [Quantization](#quantization)
    - [Transformers](#transformers-1)
    - [GGUF](#gguf)
    - [Docker (vLLM / SGLang / Ollama / llama.cpp)](#docker-vllm--sglang--ollama--llamacpp)
  - [Docker Configuration](#docker-configuration)
  - [Quick Start Examples](#quick-start-examples)
    - [Image Description (Registry Model)](#image-description-registry-model)
    - [WD14 Tagging](#wd14-tagging)
    - [Text Expansion (No Image)](#text-expansion-no-image)
    - [Docker Backend (Ollama)](#docker-backend-ollama)
  - [Troubleshooting](#troubleshooting)
    - [Model Not in Dropdown](#model-not-in-dropdown)
    - [Florence-2 Not Loading](#florence-2-not-loading)
    - [Mistral3 Not Loading](#mistral3-not-loading)
    - [Docker Container Won't Start](#docker-container-wont-start)
    - [Out of Memory (OOM)](#out-of-memory-oom)
    - [GGUF Vision Not Working](#gguf-vision-not-working)
    - [Debug Logging](#debug-logging)
  - [Configuration Files](#configuration-files)

---

## Overview

The **Smart Language Model Loader** is a unified node for loading and running vision-language models, text-only LLMs, and WD14 taggers in ComfyUI. It uses a **registry-based workflow** — pick a model from the unified dropdown, choose a task, and generate. No templates, no manual path resolution.

### What Can This Node Do?

- **Image Analysis** — describe, analyze, and extract information from images
- **Text Generation** — chat, expand prompts, translate, summarize
- **Tag Generation** — WD14 tagger with booru-style output
- **Video Analysis** — summarize video sequences (Qwen)
- **Prompt Pipelines** — chain up to 4 tasks sequentially via multi-task mode
- **OCR** — extract text from images

> **Note:** For object detection with bounding boxes, masks, and SEGS output, use the [Smart Detection](Smart_Detection_Guide.md) node instead.

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Registry-Based Model Selection** | Unified dropdown with 50+ models grouped by backend — no templates, no manual paths |
| **Auto-Download** | Models download from HuggingFace/ModelScope on first use with integrity verification |
| **8 Backends** | Transformers, GGUF, vLLM, SGLang, Ollama, llama.cpp, YOLO, WD14 |
| **Multi-Task Chaining** | Chain 2–4 sequential tasks with output→input flow |
| **Few-Shot Training** | Per-task example pairs (user-editable in `config/`), toggleable via **Training** chip |
| **Editable System Prompts** | Customize per-task instructions in `config/system_prompts.json` |
| **Mode Bar** | Toggle chips: Cleanup, Keep Loaded, Multi-Task, Training, Advanced |
| **Persist-on-Execute** | Advanced parameters (temperature, top_p, etc.) saved to defaults on each run |
| **Docker Lifecycle** | Auto-start/stop containers, stale image detection |
| **Image Passthrough** | Input images flow through to the output for downstream nodes |
| **WD14 Tagger** | ONNX-based image tagging with configurable thresholds |

---

## Node Overview

### Inputs

#### Core Widgets

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| **model** | Dropdown | — | Model from registry. Suffix indicates backend (no suffix=Transformers, -GGUF, -vLLM, -SGLang, -Ollama) |
| **quantization** | Dropdown | Q4_K_M | GGUF only — quantization variant |
| **task** | Dropdown | Detailed Description | Task to perform. Vision tasks require an image |
| **task_2** | Dropdown | None | Optional 2nd task (multi-task mode) |
| **task_3** | Dropdown | None | Optional 3rd task (multi-task mode) |
| **task_4** | Dropdown | None | Optional 4th task (multi-task mode) |
| **user_prompt** | String | — | Custom instructions or additional context |
| **context_size** | Integer | 8192 | Model context window (512–131072). Persisted on execute |
| **max_tokens** | Integer | 2048 | Maximum tokens to generate (1–32768) |
| **attention_mode** | Dropdown | auto | Transformers only — auto, flash_attention_2, sdpa, eager |
| **auto_stop_container** | Boolean | True | Docker backends — stop container after generation |
| **seed** | Integer | -1 | Random seed. -1=random, -2=increment, -3=decrement |

#### Mode Bar Chips (hidden backing widgets, synced by JS)

| Chip | Default | Description |
|------|---------|-------------|
| **Cleanup** | ON | Pre-load VRAM cleanup — free memory before loading |
| **Keep Loaded** | OFF | Cache model in VRAM between runs |
| **Multi-Task** | OFF | Enable sequential task chaining (shows task_2/3/4) |
| **Training** | ON | Include few-shot training examples in the prompt. Disable to reduce context size (saves ~1–3 KB per prompt; system prompts and task instructions still load) |
| **Advanced** | OFF | Show advanced generation parameters |

#### Advanced Widgets (hidden by default)

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| **device** | Dropdown | cuda | Compute device |
| **temperature** | Float | 0.7 | Sampling temperature (0.1–2.0) |
| **top_p** | Float | 0.9 | Nucleus sampling (0.1–1.0) |
| **top_k** | Integer | 50 | Top-k sampling, 0=disabled |
| **num_beams** | Integer | 1 | Beam search count. 1=greedy |
| **do_sample** | Boolean | True | Enable sampling vs greedy decoding |
| **repetition_penalty** | Float | 1.0 | Repeat penalty (1.0–2.0) |
| **frame_count** | Integer | 8 | Qwen VL — video frames to analyze |
| **use_torch_compile** | Boolean | False | torch.compile for faster inference |

All advanced widgets are persisted to defaults on execute.

#### WD14 Widgets (hidden unless WD14 model selected)

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| **threshold** | Float | 0.35 | General tag confidence threshold |
| **char_threshold** | Float | 0.85 | Character tag confidence threshold |
| **exclude_tags** | String | — | Comma-separated tags to exclude |
| **replace_underscore** | Boolean | True | Replace underscores with spaces |

#### Connection Slots

| Input | Type | Description |
|-------|------|-------------|
| **images** | IMAGE | Optional. Image input for vision tasks and WD14 |
| **text** | STRING | Optional. Text input for text processing tasks (overrides user_prompt) |

### Outputs

| Output | Type | Description |
|--------|------|-------------|
| **image** | IMAGE | Passthrough of input images (or 64×64 placeholder if no image) |
| **text** | STRING | Generated text or tags |

---

## Model Families

The model family is **auto-detected from the registry entry**. Each model in the registry has a `family` field that determines which generation path is used.

### Qwen

**Vision-language models from Alibaba** — Qwen2.5-VL and Qwen3-VL series.

| Property | Value |
|----------|-------|
| **Vision** | ✅ Yes |
| **Video** | ✅ Yes (multi-frame) |
| **Backends** | Transformers, GGUF, vLLM, SGLang, Ollama |
| **Best For** | Versatile — descriptions, analysis, detection, video |
| **VRAM (3B)** | ~7 GB (FP16), ~4 GB (4-bit) |

**Registry Models:** Qwen2.5-VL-3B/7B, Qwen3-VL-8B

### Mistral

**Vision-language models from Mistral AI** — Ministral-3 (3B, 8B) and Mistral Small 3.

| Property | Value |
|----------|-------|
| **Vision** | ✅ Yes |
| **Video** | ❌ No |
| **Backends** | Transformers, vLLM, SGLang, Ollama |
| **Best For** | High-quality image descriptions |
| **Requires** | transformers v5.0+ |

**Registry Models:** Ministral-3-3B/8B-Instruct

### Florence

**Microsoft Florence-2** specialized vision models with 15+ task types.

| Property | Value |
|----------|-------|
| **Vision** | ✅ Yes |
| **Video** | ❌ No |
| **Backends** | Transformers only |
| **Best For** | OCR, prompt generation (PromptGen models) |
| **VRAM** | ~4 GB |

**Registry Models:** Florence-2-base/large, base-ft/large-ft, PromptGen v1/v2/v1.5/v2.0

### LLaVA

**Large Language and Vision Assistant** — LLaVA 1.5/1.6 and Llama 3.2 Vision.

| Property | Value |
|----------|-------|
| **Vision** | ✅ Yes |
| **Video** | ❌ No |
| **Backends** | Transformers, GGUF, Ollama |
| **Best For** | General vision tasks |

### VLM (Generic)

**Catch-all** for vision-language models not in a specific family (Gemma 3, Phi-3/4 Vision, MiniCPM-V, Moondream, etc.). Auto-detected from model config — uses the LLaVA execution path for Docker backends.

### LLM (Text-Only)

**Text-only language models** without vision.

| Property | Value |
|----------|-------|
| **Vision** | ❌ No |
| **Backends** | GGUF, Ollama |
| **Best For** | Text expansion, translation, tag conversion |

### WD14 Tagger

**ONNX-based image tagger** using SmilingWolf's WaifuDiffusion models. Outputs booru-style comma-separated tags — completely separate from the VLM/LLM pipeline.

| Property | Value |
|----------|-------|
| **Runtime** | ONNX (CUDA or CPU) |
| **Best For** | Fast tag generation for Stable Diffusion prompts |
| **Speed** | ~1–2s per image |

**Registry Models:** wd-eva02-large-tagger-v3, wd-vit-large-tagger-v3, wd-swinv2-tagger-v3, wd-convnext-tagger-v3, wd-vit-tagger-v3

**Output format:** `1girl, solo, long hair, looking at viewer, smile, blue eyes, ...`

---

## Backends

### Transformers

HuggingFace Transformers — direct Python loading.

| Property | Value |
|----------|-------|
| **Docker** | ❌ No |
| **Quantization** | BitsAndBytes (4-bit, 8-bit) |
| **Families** | All (Qwen, Mistral, Florence, LLaVA, VLM, LLM) |
| **Best For** | Simplest setup |

### GGUF (llama-cpp-python)

llama-cpp-python for GGUF format models.

| Property | Value |
|----------|-------|
| **Docker** | ❌ No |
| **Quantization** | Built into GGUF file (Q3–Q8, IQ3–IQ4) |
| **Families** | Qwen, LLaVA, LLM |
| **Best For** | Pre-quantized models, lower VRAM |

> GGUF vision requires an mmproj file (auto-downloaded from registry). Mistral3 architecture is not supported.

### vLLM (Docker)

High-performance inference server via Docker.

| Property | Value |
|----------|-------|
| **Docker** | ✅ Yes |
| **Quantization** | FP8, AWQ, GPTQ (auto-detected) |
| **Families** | Qwen, Mistral, LLM |
| **Best For** | Pre-quantized FP8 models, continuous batching |

### SGLang (Docker)

Alternative to vLLM with RadixAttention for KV cache reuse.

| Property | Value |
|----------|-------|
| **Docker** | ✅ Yes |
| **Quantization** | FP8, AWQ, GPTQ (pre-quantized only) |
| **Families** | Qwen, Mistral, LLM |
| **Best For** | Better throughput for repeated requests |

### Ollama (Docker)

Easy model management with auto-pull from Ollama registry.

| Property | Value |
|----------|-------|
| **Docker** | ✅ Yes |
| **Quantization** | Pre-quantized from Ollama registry |
| **Families** | Qwen, Mistral, LLaVA, LLM |
| **Best For** | Easiest setup, fast batch processing |

### llama.cpp (Docker)

Reference GGUF engine via Docker with vision support.

| Property | Value |
|----------|-------|
| **Docker** | ✅ Yes |
| **Quantization** | Built into GGUF file |
| **Families** | Qwen, Mistral, LLaVA, LLM |
| **Best For** | GGUF models with vision, GPU layer offloading |

### WD14 (ONNX)

ONNX Runtime for SmilingWolf WD14 tagger models.

| Property | Value |
|----------|-------|
| **Docker** | ❌ No |
| **Best For** | Fast booru-style tag generation |

---

## Compatibility Matrix

| Backend | Qwen | Mistral | Florence | LLaVA | LLM | WD14 |
|---------|:----:|:-------:|:--------:|:-----:|:---:|:----:|
| **Transformers** | ✅ | ✅¹ | ✅ | ✅ | ✅ | — |
| **GGUF** | ✅ | ❌² | ❌ | ✅³ | ✅ | — |
| **vLLM (Docker)** | ✅ | ✅ | ❌ | ❌ | ✅ | — |
| **SGLang (Docker)** | ✅ | ✅ | ❌ | ❌ | ✅ | — |
| **Ollama (Docker)** | ✅ | ✅ | ❌ | ✅ | ✅ | — |
| **llama.cpp (Docker)** | ✅ | ✅ | ❌ | ✅ | ✅ | — |
| **WD14 (ONNX)** | — | — | — | — | — | ✅ |

1. ¹ Mistral requires transformers v5.0+
2. ² Mistral3 architecture not supported by llama-cpp-python
3. ³ LLaVA GGUF requires mmproj file

---

## Tasks Reference

### Custom Tasks

| Task | Image | Description |
|------|:-----:|-------------|
| **Direct Chat** | Optional | Interactive conversation |
| **Question Answering** | Optional | Answer questions about image or text |
| **Custom Instruction** | Optional | Use your own prompt in user_prompt |
| **Wan 2.2 Scene 5s** | Optional | One cinematic paragraph for a 5-second Wan 2.2 video (no timeline markers) |
| **Wan 2.2 Timeline 5s** | Optional | One paragraph using `(At 0 seconds: ...) ... (At 5 seconds: ...)` markers (per-second beats) |
| **Wan 2.2 Timeline 5s 2s** | Optional | Slower 3-beat timeline `(At 0s)(At 2s)(At 4s)` — each beat unfolds before the next |
| **Wan 2.2 Timeline 5s 3s** | Optional | Slowest 2-beat timeline `(At 0s)(At 3s)` — long, unhurried action arcs |
| **Wan 2.2 Scene 20s** | Optional | Four cinematic paragraphs (5s each) with maintained character / scene continuity |
| **Wan 2.2 Timeline 20s** | Optional | Four timeline paragraphs (5s each), each with per-second markers |

### Vision Tasks (all VLM families)

| Task | Description |
|------|-------------|
| **Simple Description** | Brief one-sentence description |
| **Detailed Description** | Paragraph-length description |
| **Ultra Detailed Description** | Comprehensive description |
| **Cinematic Description** | Film/cinematography style description |
| **Image Analysis** | Technical analysis |
| **Detailed Analysis** | Analytical breakdown |
| **Tags** | Generate comma-separated tags |
| **Video Summary** | Summarize video frames (Qwen only) |
| **OCR** | Extract text from image |

### Vision Tasks (Florence-only)

| Task | Florence Token | Description |
|------|---------------|-------------|
| **PromptGen Analyse** | `<ANALYZE>` | Analytical description |
| **PromptGen Mixed Caption** | `<MIXED_CAPTION>` | Mixed-style caption |
| **PromptGen Mixed Caption Plus** | `<MIXED_CAPTION_PLUS>` | Enhanced mixed caption |

### Text Tasks (all families)

| Task | Description |
|------|-------------|
| **Expand Text** | Expand input text into detailed form |
| **Refine & Expand Prompt** | Improve and expand prompts |
| **Rewrite Style** | Rewrite in different style |
| **Tags to Natural Language** | Convert tags to sentences |
| **Natural Language to Tags** | Convert sentences to tags |
| **Translate to English** | Translate to English |
| **Short Story** | Generate a short story |
| **Summarize** | Summarize text |
| **Prompt Variations** | Generate 5 variations of the same action with different manner / speed / emotion, separated by `---` |

> **Note:** Text tasks work on VLM or LLM models. When a VLM has no image connected, text tasks run in text-only mode automatically.

---

## Multi-Task Mode

Chain 2–4 sequential tasks where each task's output becomes the input for the next.

### How It Works

1. Enable the **Multi-Task** chip on the mode bar
2. Set **task_2** (and optionally task_3, task_4)
3. **Task 1** runs with your original input (image + user_prompt or text)
4. **Task 2** receives the text output from Task 1
5. **Task 3/4** continue the chain
6. Final output is returned

### Example: Image → Tags → Natural Language → Expanded Prompt

| Step | Task | Input | Output |
|------|------|-------|--------|
| 1 | Tags | Image | `1girl, long hair, blue eyes, dress...` |
| 2 | Tags to Natural Language | Tags from step 1 | `A girl with long hair and blue eyes wearing a dress...` |
| 3 | Expand Text | Text from step 2 | Detailed expanded description |
| 4 | Refine & Expand Prompt | Text from step 3 | Final polished prompt |

### Notes

- Only Task 1 uses the image input — subsequent tasks are text-only
- Model is loaded once and reused for all tasks
- KV cache is cleared between tasks to prevent VRAM accumulation
- Few-shot training is applied per task (disable with the **Training** chip to reduce context pressure on small-context models)
- Florence does not support multi-task chaining

---

## Quantization

Quantization is **auto-detected** — there is no manual quantization dropdown for Transformers or Docker backends.

### Transformers

BitsAndBytes precision is auto-selected based on available VRAM:
- **fp16** — full precision, used when enough VRAM is available
- **8-bit** — BitsAndBytes int8, ~50% VRAM reduction
- **4-bit** — BitsAndBytes NF4, ~75% VRAM reduction (fallback when VRAM is tight)

### GGUF

GGUF quantization is built into the file. Select the variant from the **quantization** dropdown:
- **Q4_K_M** — good balance of quality/size
- **Q5_K_M** — better quality, larger
- **Q6_K** — near-lossless
- **Q8_0** — highest quality GGUF

### Docker (vLLM / SGLang / Ollama / llama.cpp)

FP8, AWQ, and GPTQ models are auto-detected from registry metadata. No user configuration needed.

---

## Docker Configuration

Docker backends are configured in `docker_config.json`:

```json
{
  "vllm":     { "docker_image": "vllm/vllm-openai:latest",          "port": 8000  },
  "sglang":   { "docker_image": "lmsysorg/sglang:latest",           "port": 30000 },
  "ollama":   { "docker_image": "ollama/ollama",                     "port": 11434 },
  "llamacpp": { "docker_image": "ghcr.io/ggml-org/llama.cpp:server-cuda", "port": 8080 }
}
```

- Containers auto-start when needed
- Stop behavior controlled by `auto_stop_container` widget
- AMD/ROCm GPUs auto-detected — correct Docker images selected automatically
- Stale images detected on container creation — auto-recreates when image was updated

---

## Quick Start Examples

### Image Description (Registry Model)

1. Add **Smart Language Model Loader** node
2. Select model: `Qwen2.5-VL-3B-Instruct` (Transformers)
3. Set task: **Detailed Description**
4. Connect an image to `images`
5. Queue Prompt

### WD14 Tagging

1. Select model: `wd-eva02-large-tagger-v3`
2. Connect an image
3. Adjust threshold (default 0.35)
4. Output: comma-separated booru tags

### Text Expansion (No Image)

1. Select an LLM model (e.g. GGUF text-only)
2. Set task: **Expand Text**
3. Type prompt in `user_prompt` or connect via `text`
4. Queue Prompt

### Docker Backend (Ollama)

1. Select model: `Qwen2.5-VL-7B -Ollama`
2. Docker daemon starts automatically if installed
3. Model auto-pulls on first use
4. Set `auto_stop_container`: True to free VRAM after

---

## Troubleshooting

### Model Not in Dropdown

Models are loaded from JSON registry files in `registry/`. User models can be added to `registry/user_models.json`.

### Florence-2 Not Loading

Check that the model path exists and files are complete. Florence-2 uses a custom loading path via `florence2_wrapper.py`.

### Mistral3 Not Loading

Mistral3 requires transformers v5.0+:
```bash
pip install transformers>=5.0.0
```

### Docker Container Won't Start

1. Check Docker is running: `docker info`
2. Check GPU access: `docker run --gpus all nvidia/cuda:12.0-base nvidia-smi`
3. Check `docker_config.json` settings
4. Check container logs: `docker logs <container_name>`

### Out of Memory (OOM)

1. Enable **Cleanup** chip (mode bar)
2. Use GGUF quantization (Q4_K_M)
3. Enable `auto_stop_container`
4. Reduce `context_size`
5. Disable **Keep Loaded** chip
6. Use a smaller model

### GGUF Vision Not Working

Ensure mmproj file exists in the same folder as the GGUF model. Registry models auto-download mmproj files. For llama.cpp Docker, verify with: `curl http://localhost:8080/props` (should show `"vision": true`).

### Debug Logging

Enable in `config.json`:
```json
{ "log_level": "debug" }
```

---

## Configuration Files

| File | Purpose |
|------|---------|
| `config.json` | Main config: LLM folder path, log level, HF token |
| `docker_config.json` | Docker backend settings: ports, timeouts, images |
| `registry/*.json` | Model registry files (7 backend files + defaults + user_models) |
| `config/system_prompts.json` | Per-task system prompts (user-editable) |
| `config/llm_few_shot_training.json` | Few-shot training examples |
| `.defaults/` | Git-tracked defaults (`.example` suffix); extracted to repo on first run |

---

*Guide for ComfyUI Eclipse v3.3.4*
