# Model Registry Reference

Copy-paste JSON blocks for adding models to the registry. Each section shows the exact format for its backend.

**Preferred:** Add custom models to `registry/user_models.json` under the matching backend key (e.g. `"transformers"`, `"ollama"`). This file survives updates and keeps your additions separate from built-in models. See the [user_models.json template](#adding-custom-models-via-user_modelsjson) at the bottom.

The per-backend files (`transformers_models.json`, `ollama_models.json`, etc.) contain the built-in models. You can add entries there too, but they may be overwritten on update.

---

## Transformers → `transformers_models.json`

**Required fields:** `repo_id`, `family`, `has_vision`

**Family values:** `Qwen`, `Mistral`, `Florence`, `LLaVA`, `Mllama`, `LLM_TEXT`, `VLM`

### Qwen Vision-Language Models

```json
"Qwen3-VL-2B-Instruct": {
    "repo_id": "Qwen/Qwen3-VL-2B-Instruct",
    "family": "Qwen",
    "has_vision": true
},
"Qwen3-VL-2B-Thinking": {
    "repo_id": "Qwen/Qwen3-VL-2B-Thinking",
    "family": "Qwen",
    "has_vision": true
},
"Qwen3-VL-4B-Instruct": {
    "repo_id": "Qwen/Qwen3-VL-4B-Instruct",
    "family": "Qwen",
    "has_vision": true
},
"Qwen3-VL-4B-Thinking": {
    "repo_id": "Qwen/Qwen3-VL-4B-Thinking",
    "family": "Qwen",
    "has_vision": true
},
"Qwen3-VL-8B-Instruct": {
    "repo_id": "Qwen/Qwen3-VL-8B-Instruct",
    "family": "Qwen",
    "has_vision": true
},
"Qwen3-VL-8B-Thinking": {
    "repo_id": "Qwen/Qwen3-VL-8B-Thinking",
    "family": "Qwen",
    "has_vision": true
},
"Qwen3-VL-32B-Instruct": {
    "repo_id": "Qwen/Qwen3-VL-32B-Instruct",
    "family": "Qwen",
    "has_vision": true
},
"Qwen3-VL-32B-Thinking": {
    "repo_id": "Qwen/Qwen3-VL-32B-Thinking",
    "family": "Qwen",
    "has_vision": true
},
"Qwen2.5-VL-3B-Instruct": {
    "repo_id": "Qwen/Qwen2.5-VL-3B-Instruct",
    "family": "Qwen",
    "has_vision": true
},
"Qwen2.5-VL-7B-Instruct": {
    "repo_id": "Qwen/Qwen2.5-VL-7B-Instruct",
    "family": "Qwen",
    "has_vision": true
}
```

#### Qwen Abliterated (Uncensored)

```json
"Huihui-Qwen3-VL-2B-Instruct-abliterated": {
    "repo_id": "huihui-ai/Huihui-Qwen3-VL-2B-Instruct-abliterated",
    "family": "Qwen",
    "has_vision": true
},
"Huihui-Qwen3-VL-2B-Thinking-abliterated": {
    "repo_id": "huihui-ai/Huihui-Qwen3-VL-2B-Thinking-abliterated",
    "family": "Qwen",
    "has_vision": true
},
"Huihui-Qwen3-VL-4B-Instruct-abliterated": {
    "repo_id": "huihui-ai/Huihui-Qwen3-VL-4B-Instruct-abliterated",
    "family": "Qwen",
    "has_vision": true
},
"Huihui-Qwen3-VL-4B-Thinking-abliterated": {
    "repo_id": "huihui-ai/Huihui-Qwen3-VL-4B-Thinking-abliterated",
    "family": "Qwen",
    "has_vision": true
},
"Huihui-Qwen3-VL-8B-Instruct-abliterated": {
    "repo_id": "huihui-ai/Huihui-Qwen3-VL-8B-Instruct-abliterated",
    "family": "Qwen",
    "has_vision": true
},
"Huihui-Qwen3-VL-8B-Thinking-abliterated": {
    "repo_id": "huihui-ai/Huihui-Qwen3-VL-8B-Thinking-abliterated",
    "family": "Qwen",
    "has_vision": true
}
```

### Mistral / Ministral Vision Models

```json
"Ministral-3-3B-Instruct-2512": {
    "repo_id": "mistralai/Ministral-3-3B-Instruct-2512",
    "family": "Mistral",
    "has_vision": true
},
"Ministral-3-8B-Instruct-2512": {
    "repo_id": "mistralai/Ministral-3-8B-Instruct-2512",
    "family": "Mistral",
    "has_vision": true
},
"Ministral-3-14B-Instruct-2512": {
    "repo_id": "mistralai/Ministral-3-14B-Instruct-2512",
    "family": "Mistral",
    "has_vision": true
}
```

#### Ministral Abliterated (Uncensored)

```json
"Huihui-Ministral-3-3B-Instruct-2512-abliterated": {
    "repo_id": "huihui-ai/Huihui-Ministral-3-3B-Instruct-2512-abliterated",
    "family": "Mistral",
    "has_vision": true
},
"Huihui-Ministral-3-3B-Reasoning-2512-abliterated": {
    "repo_id": "huihui-ai/Huihui-Ministral-3-3B-Reasoning-2512-abliterated",
    "family": "Mistral",
    "has_vision": true
},
"Huihui-Ministral-3-8B-Reasoning-2512-abliterated": {
    "repo_id": "huihui-ai/Huihui-Ministral-3-8B-Reasoning-2512-abliterated",
    "family": "Mistral",
    "has_vision": true
}
```

### LLaVA / Mllama Vision Models

```json
"llava-1.5-7b-hf": {
    "repo_id": "llava-hf/llava-1.5-7b-hf",
    "family": "LLaVA",
    "has_vision": true
},
"llava-1.5-13b-hf": {
    "repo_id": "llava-hf/llava-1.5-13b-hf",
    "family": "LLaVA",
    "has_vision": true
},
"llava-v1.6-mistral-7b-hf": {
    "repo_id": "llava-hf/llava-v1.6-mistral-7b-hf",
    "family": "LLaVA",
    "has_vision": true
},
"llava-v1.6-vicuna-7b-hf": {
    "repo_id": "llava-hf/llava-v1.6-vicuna-7b-hf",
    "family": "LLaVA",
    "has_vision": true
},
"llava-v1.6-vicuna-13b-hf": {
    "repo_id": "llava-hf/llava-v1.6-vicuna-13b-hf",
    "family": "LLaVA",
    "has_vision": true
},
"Llama-3.2-11B-Vision-Instruct": {
    "repo_id": "meta-llama/Llama-3.2-11B-Vision-Instruct",
    "family": "Mllama",
    "has_vision": true
},
"Llama-3.2-11B-Vision-Instruct-Swallow-8B-Merge": {
    "repo_id": "Kendamarron/Llama-3.2-11B-Vision-Instruct-Swallow-8B-Merge",
    "family": "Mllama",
    "has_vision": true
},
"Llama-3.2-90B-Vision-Instruct": {
    "repo_id": "meta-llama/Llama-3.2-90B-Vision-Instruct",
    "family": "Mllama",
    "has_vision": true
}
```

### Florence-2 Models

```json
"Florence-2-base": {
    "repo_id": "microsoft/Florence-2-base",
    "family": "Florence",
    "has_vision": true
},
"Florence-2-base-ft": {
    "repo_id": "microsoft/Florence-2-base-ft",
    "family": "Florence",
    "has_vision": true
},
"Florence-2-large": {
    "repo_id": "microsoft/Florence-2-large",
    "family": "Florence",
    "has_vision": true
},
"Florence-2-large-ft": {
    "repo_id": "microsoft/Florence-2-large-ft",
    "family": "Florence",
    "has_vision": true
},
"Florence-2-DocVQA": {
    "repo_id": "HuggingFaceM4/Florence-2-DocVQA",
    "family": "Florence",
    "has_vision": true
},
"Florence-2-Flux-Large": {
    "repo_id": "gokaygokay/Florence-2-Flux-Large",
    "family": "Florence",
    "has_vision": true
},
"Florence-2-base-PromptGen-v1.5": {
    "repo_id": "MiaoshouAI/Florence-2-base-PromptGen-v1.5",
    "family": "Florence",
    "has_vision": true
},
"Florence-2-large-PromptGen-v1.5": {
    "repo_id": "MiaoshouAI/Florence-2-large-PromptGen-v1.5",
    "family": "Florence",
    "has_vision": true
},
"Florence-2-base-PromptGen-v2.0": {
    "repo_id": "MiaoshouAI/Florence-2-base-PromptGen-v2.0",
    "family": "Florence",
    "has_vision": true
},
"Florence-2-large-PromptGen-v2.0": {
    "repo_id": "MiaoshouAI/Florence-2-large-PromptGen-v2.0",
    "family": "Florence",
    "has_vision": true
},
"Florence-2-SD3-Captioner": {
    "repo_id": "gokaygokay/Florence-2-SD3-Captioner",
    "family": "Florence",
    "has_vision": true
},
"CogFlorence-2.1-Large": {
    "repo_id": "thwri/CogFlorence-2.1-Large",
    "family": "Florence",
    "has_vision": true
},
"CogFlorence-2.2-Large": {
    "repo_id": "thwri/CogFlorence-2.2-Large",
    "family": "Florence",
    "has_vision": true
},
"Florence-2-base-Castollux-v0.5": {
    "repo_id": "PJMixers-Images/Florence-2-base-Castollux-v0.5",
    "family": "Florence",
    "has_vision": true
}
```

### Qwen3-VL FP8 Pre-quantized

```json
"Qwen3-VL-2B-Instruct-FP8": {
    "repo_id": "Qwen/Qwen3-VL-2B-Instruct-FP8",
    "family": "Qwen",
    "has_vision": true
},
"Qwen3-VL-2B-Thinking-FP8": {
    "repo_id": "Qwen/Qwen3-VL-2B-Thinking-FP8",
    "family": "Qwen",
    "has_vision": true
},
"Qwen3-VL-4B-Instruct-FP8": {
    "repo_id": "Qwen/Qwen3-VL-4B-Instruct-FP8",
    "family": "Qwen",
    "has_vision": true
},
"Qwen3-VL-4B-Thinking-FP8": {
    "repo_id": "Qwen/Qwen3-VL-4B-Thinking-FP8",
    "family": "Qwen",
    "has_vision": true
},
"Qwen3-VL-8B-Instruct-FP8": {
    "repo_id": "Qwen/Qwen3-VL-8B-Instruct-FP8",
    "family": "Qwen",
    "has_vision": true
},
"Qwen3-VL-8B-Thinking-FP8": {
    "repo_id": "Qwen/Qwen3-VL-8B-Thinking-FP8",
    "family": "Qwen",
    "has_vision": true
},
"Qwen3-VL-32B-Instruct-FP8": {
    "repo_id": "Qwen/Qwen3-VL-32B-Instruct-FP8",
    "family": "Qwen",
    "has_vision": true
},
"Qwen3-VL-32B-Thinking-FP8": {
    "repo_id": "Qwen/Qwen3-VL-32B-Thinking-FP8",
    "family": "Qwen",
    "has_vision": true
}
```

### Text-Only LLMs

```json
"Mistral-7B-Instruct-v0.3": {
    "repo_id": "mistralai/Mistral-7B-Instruct-v0.3",
    "family": "LLM_TEXT",
    "has_vision": false
},
"Huihui-HY-MT1.5-1.8B-abliterated": {
    "repo_id": "huihui-ai/Huihui-HY-MT1.5-1.8B-abliterated",
    "family": "LLM_TEXT",
    "has_vision": false
},
"Huihui-LFM2.5-1.2B-Instruct-abliterated": {
    "repo_id": "huihui-ai/Huihui-LFM2.5-1.2B-Instruct-abliterated",
    "family": "LLM_TEXT",
    "has_vision": false
},
"Huihui-LFM2.5-1.2B-Thinking-abliterated": {
    "repo_id": "huihui-ai/Huihui-LFM2.5-1.2B-Thinking-abliterated",
    "family": "LLM_TEXT",
    "has_vision": false
},
"Huihui-MiroThinker-v1.0-8B-abliterated": {
    "repo_id": "huihui-ai/Huihui-MiroThinker-v1.0-8B-abliterated",
    "family": "LLM_TEXT",
    "has_vision": false
}
```

---

## GGUF → `gguf_models.json`

**Required fields:** `repo_id`, `family`, `has_vision`, `file_pattern`
**Optional fields:** `mmproj` (for vision models), `quantizations` (to restrict available quants)

**Family values:** `Qwen`, `LLaVA`, `LLM_TEXT`

The `{quant}` placeholder in `file_pattern` is replaced at runtime with the selected quantization (e.g. `Q4_K_M`, `Q8_0`).

```json
"Qwen2.5-VL-3B-Instruct": {
    "repo_id": "lmstudio-community/Qwen2.5-VL-3B-Instruct-GGUF",
    "family": "Qwen",
    "has_vision": true,
    "file_pattern": "Qwen2.5-VL-3B-Instruct-{quant}.gguf",
    "mmproj": "mmproj-model-f16.gguf"
},
"Qwen2.5-VL-7B-Abliterated-Caption-it": {
    "repo_id": "mradermacher/Qwen2.5-VL-7B-Abliterated-Caption-it-GGUF",
    "family": "Qwen",
    "has_vision": true,
    "file_pattern": "Qwen2.5-VL-7B-Abliterated-Caption-it.{quant}.gguf",
    "mmproj": "Qwen2.5-VL-7B-Abliterated-Caption-it.mmproj-Q8_0.gguf"
},
"llava-v1.6-mistral-7b": {
    "repo_id": "cjpais/llava-1.6-mistral-7b-gguf",
    "family": "LLaVA",
    "has_vision": true,
    "file_pattern": "llava-v1.6-mistral-7b.{quant}.gguf",
    "mmproj": "mmproj-model-f16.gguf"
},
"Lexi-Llama-3-8B-Uncensored": {
    "repo_id": "bartowski/Lexi-Llama-3-8B-Uncensored-GGUF",
    "family": "LLM_TEXT",
    "has_vision": false,
    "file_pattern": "Lexi-Llama-3-8B-Uncensored-{quant}.gguf"
},
"Mistral-7B-Instruct-v0.3": {
    "repo_id": "bartowski/Mistral-7B-Instruct-v0.3-GGUF",
    "family": "LLM_TEXT",
    "has_vision": false,
    "file_pattern": "Mistral-7B-Instruct-v0.3-{quant}.gguf"
}
```

---

## Ollama → `ollama_models.json`

**Required fields:** `repo_id` (Ollama model tag), `family`, `has_vision`

**Family values:** `Qwen`, `Mistral`, `LLaVA`, `LLM_TEXT`, `VLM`

Ollama auto-pulls models on first use. Browse models at [ollama.com/library](https://ollama.com/library).

### Qwen Vision Models

```json
"qwen3-vl-2b": {
    "repo_id": "qwen3-vl:2b",
    "family": "Qwen",
    "has_vision": true
},
"qwen3-vl-4b": {
    "repo_id": "qwen3-vl:4b",
    "family": "Qwen",
    "has_vision": true
},
"qwen3-vl-8b": {
    "repo_id": "qwen3-vl:8b",
    "family": "Qwen",
    "has_vision": true
},
"qwen3-vl-32b": {
    "repo_id": "qwen3-vl:32b",
    "family": "Qwen",
    "has_vision": true
},
"qwen2.5vl-3b": {
    "repo_id": "qwen2.5vl:3b",
    "family": "Qwen",
    "has_vision": true
},
"qwen2.5vl-7b": {
    "repo_id": "qwen2.5vl:7b",
    "family": "Qwen",
    "has_vision": true
},
"qwen2.5vl-32b": {
    "repo_id": "qwen2.5vl:32b",
    "family": "Qwen",
    "has_vision": true
},
"qwen2.5vl-72b": {
    "repo_id": "qwen2.5vl:72b",
    "family": "Qwen",
    "has_vision": true
}
```

#### Qwen Abliterated (Ollama)

```json
"huihui_ai-qwen3-vl-abliterated-2b-instruct": {
    "repo_id": "huihui_ai/qwen3-vl-abliterated:2b-instruct",
    "family": "Qwen",
    "has_vision": true
},
"huihui_ai-qwen3-vl-abliterated-2b-thinking": {
    "repo_id": "huihui_ai/qwen3-vl-abliterated:2b-thinking",
    "family": "Qwen",
    "has_vision": true
},
"huihui_ai-qwen3-vl-abliterated-4b-instruct": {
    "repo_id": "huihui_ai/qwen3-vl-abliterated:4b-instruct",
    "family": "Qwen",
    "has_vision": true
},
"huihui_ai-qwen3-vl-abliterated-4b-thinking": {
    "repo_id": "huihui_ai/qwen3-vl-abliterated:4b-thinking",
    "family": "Qwen",
    "has_vision": true
},
"huihui_ai-qwen3-vl-abliterated-8b-instruct": {
    "repo_id": "huihui_ai/qwen3-vl-abliterated:8b-instruct",
    "family": "Qwen",
    "has_vision": true
},
"huihui_ai-qwen3-vl-abliterated-8b-thinking": {
    "repo_id": "huihui_ai/qwen3-vl-abliterated:8b-thinking",
    "family": "Qwen",
    "has_vision": true
}
```

### Mistral / Ministral Models

```json
"ministral-3-3b": {
    "repo_id": "ministral-3:3b",
    "family": "Mistral",
    "has_vision": true
},
"ministral-3-8b": {
    "repo_id": "ministral-3:8b",
    "family": "Mistral",
    "has_vision": true
},
"ministral-3-14b": {
    "repo_id": "ministral-3:14b",
    "family": "Mistral",
    "has_vision": true
},
"mistral-small3.1-24b": {
    "repo_id": "mistral-small3.1:24b",
    "family": "Mistral",
    "has_vision": true
}
```

### LLaVA / Mllama Vision Models

```json
"llava-7b": {
    "repo_id": "llava:7b",
    "family": "LLaVA",
    "has_vision": true
},
"llava-13b": {
    "repo_id": "llava:13b",
    "family": "LLaVA",
    "has_vision": true
},
"bakllava-7b": {
    "repo_id": "bakllava:7b",
    "family": "LLaVA",
    "has_vision": true
},
"llava-llama3-8b": {
    "repo_id": "llava-llama3:8b",
    "family": "LLaVA",
    "has_vision": true
},
"llava-phi3-3.8b": {
    "repo_id": "llava-phi3:3.8b",
    "family": "LLaVA",
    "has_vision": true
},
"llama3.2-vision-11b": {
    "repo_id": "llama3.2-vision:11b",
    "family": "LLaVA",
    "has_vision": true
},
"llama3.2-vision-90b": {
    "repo_id": "llama3.2-vision:90b",
    "family": "LLaVA",
    "has_vision": true
},
"minicpm-v-8b": {
    "repo_id": "minicpm-v:8b",
    "family": "VLM",
    "has_vision": true
},
"moondream-1.8b": {
    "repo_id": "moondream:1.8b",
    "family": "LLaVA",
    "has_vision": true
}
```

### Other Vision Models

```json
"gemma3-12b": {
    "repo_id": "gemma3:12b",
    "family": "VLM",
    "has_vision": true
}
```

### Text-Only LLMs

```json
"qwen2.5-7b": {
    "repo_id": "qwen2.5:7b",
    "family": "LLM_TEXT",
    "has_vision": false
},
"qwen2.5-14b": {
    "repo_id": "qwen2.5:14b",
    "family": "LLM_TEXT",
    "has_vision": false
},
"mistral-7b": {
    "repo_id": "mistral:7b",
    "family": "LLM_TEXT",
    "has_vision": false
},
"mistral-small-24b": {
    "repo_id": "mistral-small:24b",
    "family": "LLM_TEXT",
    "has_vision": false
},
"llama3.1-8b": {
    "repo_id": "llama3.1:8b",
    "family": "LLM_TEXT",
    "has_vision": false
},
"llama3.2-3b": {
    "repo_id": "llama3.2:3b",
    "family": "LLM_TEXT",
    "has_vision": false
},
"deepseek-r1-7b": {
    "repo_id": "deepseek-r1:7b",
    "family": "LLM_TEXT",
    "has_vision": false
},
"gemma3-4b": {
    "repo_id": "gemma3:4b",
    "family": "LLM_TEXT",
    "has_vision": false
},
"phi4-14b": {
    "repo_id": "phi4:14b",
    "family": "LLM_TEXT",
    "has_vision": false
}
```

#### Text-Only Abliterated (Ollama)

```json
"huihui_ai-hy-mt1.5-abliterated-1.8b": {
    "repo_id": "huihui_ai/hy-mt1.5-abliterated:1.8b",
    "family": "LLM_TEXT",
    "has_vision": false
},
"huihui_ai-glm-4.7-abliterated-358b-q2_K": {
    "repo_id": "huihui_ai/glm-4.7-abliterated:358b-q2_K",
    "family": "LLM_TEXT",
    "has_vision": false
}
```

---

## vLLM → `vllm_models.json`

**Required fields:** `repo_id`, `family`, `has_vision`, `tensor_parallel`

```json
"Qwen2.5-VL-3B-Instruct": {
    "repo_id": "Qwen/Qwen2.5-VL-3B-Instruct",
    "family": "Qwen",
    "has_vision": true,
    "tensor_parallel": 1
},
"Qwen2.5-VL-7B-Instruct": {
    "repo_id": "Qwen/Qwen2.5-VL-7B-Instruct",
    "family": "Qwen",
    "has_vision": true,
    "tensor_parallel": 1
},
"Qwen3-VL-4B-Instruct": {
    "repo_id": "Qwen/Qwen3-VL-4B-Instruct",
    "family": "Qwen",
    "has_vision": true,
    "tensor_parallel": 1
},
"Mistral-Small-3.1-24B-Instruct-2503": {
    "repo_id": "mistralai/Mistral-Small-3.1-24B-Instruct-2503",
    "family": "Mistral",
    "has_vision": true,
    "tensor_parallel": 2
}
```

---

## SGLang → `sglang_models.json`

**Required fields:** `repo_id`, `family`, `has_vision`, `tensor_parallel`, `data_parallel`

```json
"Qwen2.5-VL-3B-Instruct": {
    "repo_id": "Qwen/Qwen2.5-VL-3B-Instruct",
    "family": "Qwen",
    "has_vision": true,
    "tensor_parallel": 1,
    "data_parallel": 1
},
"Qwen2.5-VL-7B-Instruct": {
    "repo_id": "Qwen/Qwen2.5-VL-7B-Instruct",
    "family": "Qwen",
    "has_vision": true,
    "tensor_parallel": 1,
    "data_parallel": 1
},
"Qwen3-VL-4B-Instruct": {
    "repo_id": "Qwen/Qwen3-VL-4B-Instruct",
    "family": "Qwen",
    "has_vision": true,
    "tensor_parallel": 1,
    "data_parallel": 1
}
```

---

## WD14 Tagger → `wd14_models.json`

**Required fields:** `repo_id`

ONNX-based image classifiers from [SmilingWolf](https://huggingface.co/SmilingWolf). Each model needs `model.onnx` + `selected_tags.csv` from the HF repo.

### v3 (Recommended)

```json
"WD14-convnext-v3": {
    "repo_id": "SmilingWolf/wd-convnext-tagger-v3"
},
"WD14-eva02-large-v3": {
    "repo_id": "SmilingWolf/wd-eva02-large-tagger-v3"
},
"WD14-swinv2-v3": {
    "repo_id": "SmilingWolf/wd-swinv2-tagger-v3"
},
"WD14-vit-v3": {
    "repo_id": "SmilingWolf/wd-vit-tagger-v3"
}
```

### v2

```json
"WD14-moat-v2": {
    "repo_id": "SmilingWolf/wd-v1-4-moat-tagger-v2"
},
"WD14-convnextv2-v2": {
    "repo_id": "SmilingWolf/wd-v1-4-convnextv2-tagger-v2"
},
"WD14-convnext-v2": {
    "repo_id": "SmilingWolf/wd-v1-4-convnext-tagger-v2"
},
"WD14-vit-v2": {
    "repo_id": "SmilingWolf/wd-v1-4-vit-tagger-v2"
},
"WD14-swinv2-v2": {
    "repo_id": "SmilingWolf/wd-v1-4-swinv2-tagger-v2"
}
```

### v1

```json
"WD14-convnext-v1": {
    "repo_id": "SmilingWolf/wd-v1-4-convnext-tagger"
},
"WD14-vit-v1": {
    "repo_id": "SmilingWolf/wd-v1-4-vit-tagger"
}
```

---

## YOLO → `yolo_models.json`

**Required fields:** `url`, `filename`, `family`, `detection_type`, `description`

**detection_type values:** `bbox` (bounding box only), `segm` (instance segmentation mask)

YOLO models are `.pt` files downloaded to the `ultralytics/` folder inside your LLM models directory. Any `.pt` file placed there is auto-discovered at startup and added to the registry as `local_only`.

### Curated Downloads (from HuggingFace)

```json
"face_yolov8m": {
    "url": "https://huggingface.co/Bingsu/adetailer/resolve/main/face_yolov8m.pt",
    "filename": "face_yolov8m.pt",
    "family": "YOLO",
    "detection_type": "bbox",
    "description": "Face detection (YOLOv8 medium)"
},
"hand_yolov8s": {
    "url": "https://huggingface.co/Bingsu/adetailer/resolve/main/hand_yolov8s.pt",
    "filename": "hand_yolov8s.pt",
    "family": "YOLO",
    "detection_type": "bbox",
    "description": "Hand detection (YOLOv8 small)"
},
"person_yolov8m-seg": {
    "url": "https://huggingface.co/Bingsu/adetailer/resolve/main/person_yolov8m-seg.pt",
    "filename": "person_yolov8m-seg.pt",
    "family": "YOLO",
    "detection_type": "segm",
    "description": "Person segmentation (YOLOv8 medium)"
},
"face_yolov8m-seg": {
    "url": "https://huggingface.co/Bingsu/adetailer/resolve/main/face_yolov8m-seg_60.pt",
    "filename": "face_yolov8m-seg_60.pt",
    "family": "YOLO",
    "detection_type": "segm",
    "description": "Face segmentation (YOLOv8 medium)"
}
```

### Adding Custom YOLO Models

Drop any `.pt` YOLO model into the `ultralytics/` subfolder of your LLM models directory. It will be auto-discovered on next startup and appear in the model dropdown. To add a downloadable model with a URL, add it to `yolo_models.json`:

```json
"my-custom-yolo-model": {
    "url": "https://huggingface.co/username/repo/resolve/main/model.pt",
    "filename": "model.pt",
    "family": "YOLO",
    "detection_type": "bbox",
    "description": "My custom YOLO model"
}
```

---

## Adding Custom Models via `user_models.json`

For user-added models that survive updates, add entries to `registry/user_models.json` under the backend key:

```json
{
  "transformers": {
    "My-Custom-VLM": {
      "repo_id": "username/My-Custom-VLM",
      "family": "VLM",
      "has_vision": true
    }
  },
  "gguf": {
    "My-Custom-GGUF": {
      "repo_id": "username/My-Custom-GGUF",
      "family": "LLM_TEXT",
      "has_vision": false,
      "file_pattern": "My-Custom-{quant}.gguf"
    }
  },
  "ollama": {
    "my-custom-ollama": {
      "repo_id": "username/model:tag",
      "family": "LLM_TEXT",
      "has_vision": false
    }
  },
  "vllm": {
    "My-Custom-vLLM": {
      "repo_id": "username/My-Custom-Model",
      "family": "VLM",
      "has_vision": true,
      "tensor_parallel": 1
    }
  },
  "sglang": {
    "My-Custom-SGLang": {
      "repo_id": "username/My-Custom-Model",
      "family": "VLM",
      "has_vision": true,
      "tensor_parallel": 2,
      "data_parallel": 1
    }
  },
  "wd14": {
    "WD14-my-custom": {
      "repo_id": "username/wd-custom-tagger"
    }
  }
}
```

---

## Notes

- **Transformers** — auto-selects quantization (fp16 / 8-bit / 4-bit) based on available VRAM
- **FP8 Models** — pre-quantized, loaded directly without runtime quantization
- **GGUF** — quantization selected from dropdown; `{quant}` in `file_pattern` is replaced at runtime
- **Ollama** — auto-pulls on first use, models stored in Docker volume
- **vLLM / SGLang** — `tensor_parallel` sets GPU count; SGLang also uses `data_parallel`
- **YOLO** — `.pt` files auto-discovered from the `ultralytics/` subfolder; curated models auto-download from HuggingFace
- **WD14** — ONNX classifiers, ~300–380MB each, CUDA or CPU
- **Vision** — Qwen supports image + video, Florence-2 image only, LLM_TEXT is text-only

*Last updated: April 2026*
