# Unified Transformers Loading Module
#
# This module consolidates all HuggingFace Transformers model loading functions
# for vision-language and text-only models. It provides a single entry point
# (load_transformers_model) that routes to family-specific loaders.
#
# Supported model families:
# - Mistral3: Ministral 3B/8B/14B, Mistral Small 24B (vision-language)
# - QwenVL: Qwen2.5-VL, Qwen3-VL (vision-language)
# - Florence2: Florence-2 base/large (vision-language, requires transformers < 5.0)
# - LLM: Text-only models (Mistral, Llama, Qwen, etc.)
#
# For GGUF models, see backend_gguf.py (to be created)
# For vLLM/Docker inference, see backend_vllm_docker.py

from pathlib import Path
from typing import Any, Optional

import torch  # type: ignore

from .logger import log

_LOG_PREFIX = "Transformers"


# ==============================================================================
# TRANSFORMERS VERSION DETECTION
# ==============================================================================

import transformers  # type: ignore

transformers_version = (
    tuple(map(int, transformers.__version__.split(".")[:2]))
    if transformers.__version__[0].isdigit()
    else (4, 0)
)
# Handle RC versions like "5.0.0rc1"
if "rc" in transformers.__version__.lower():
    transformers_version = (5, 0)


# ==============================================================================
# NOTE: v1 loading functions (load_transformers_model, _load_mistral3, _load_qwenvl,
# _load_florence2, _load_llm) have been removed as they are no longer used.
# v2 uses loader_base.py for model loading which delegates to Docker backends.
# The generation functions below are still used by v2.
# ==============================================================================


# ==============================================================================
# FLORENCE-2 TASK CONFIGURATIONS
# ==============================================================================

# Florence tasks are defined in core/tasks.py. The legacy JSON config
# (smartlm_prompt_defaults.json) and MODEL_CONFIGS path are removed.


def get_florence_tasks():
    # Return Florence tasks as dict mapping florence_id -> {"prompt": token}.
    #
    # Used by generate_transformers to resolve florence_id → prompt token.
    from .tasks import FLORENCE_ID_TO_TASK

    return {fid: {"prompt": t.florence_token} for fid, t in FLORENCE_ID_TO_TASK.items()}


# ==============================================================================
# DETECTION UTILITIES (imported from vlm_detection.py)
# ==============================================================================

import re
from typing import Tuple, List
from PIL import Image  # type: ignore

from .vlm_detection import (
    tensor_to_pil,
    smart_resize_for_vlm,
    VLM_MAX_PIXELS_TRANSFORMERS,
    VLM_MAX_PIXELS_GENERIC,
    get_max_pixels_for_model_type,
    nms_filter,
    parse_florence_location_tokens,
    draw_bboxes,
    parse_qwen_detection_json,
    _parse_qwen_detection_json,
)

# ==============================================================================
# UNIFIED GENERATION ENTRY POINT
# ==============================================================================


def _build_few_shot_prompt(llm_mode: str, user_content: str) -> str:
    # Build a prompt with few-shot examples prepended for vision models doing text tasks.
    #
    # Vision models can't use _generate_llm because their processors expect images.
    # Instead, we prepend few-shot examples as text context in the prompt itself.
    from .config_templates import get_llm_few_shot_examples
    from .tasks import get_system_prompt

    LLM_FEW_SHOT_EXAMPLES = get_llm_few_shot_examples()

    # Normalize display name to key format: "Ultra Detailed Description" -> "ultra_detailed_description"
    mode_key = (
        llm_mode.lower().replace(" ", "_").replace("&", "&") if llm_mode else llm_mode
    )
    config = LLM_FEW_SHOT_EXAMPLES.get(mode_key, {})

    if not config:
        return user_content

    examples = config.get("examples", [])
    instruction_template = config.get("instruction_template", "")

    if not examples:
        # No few-shot examples, just apply instruction template
        if instruction_template and "{prompt}" in instruction_template:
            return instruction_template.replace("{prompt}", user_content)
        return user_content

    # Build few-shot context as text
    few_shot_text = ""
    for ex in examples:
        role = ex.get("role", "")
        content = ex.get("content", "")
        if role == "user":
            few_shot_text += f"Example Input: {content}\n"
        elif role == "assistant":
            few_shot_text += f"Example Output: {content}\n\n"

    # Apply instruction template to user content
    if instruction_template and "{prompt}" in instruction_template:
        formatted_user = instruction_template.replace("{prompt}", user_content)
    else:
        formatted_user = user_content

    # Combine: few-shot examples + actual request
    result = f"{few_shot_text}Now process this:\n{formatted_user}"
    log.debug(_LOG_PREFIX, f"  Built few-shot prompt with {len(examples)} examples")
    return result


# ==============================================================================
# SHARED VLM GENERATION HELPERS
# ==============================================================================


def _is_accelerate_dispatched(model) -> bool:
    # Check if a model has been dispatched by accelerate (device_map="auto").
    # Dispatched models have hf_device_map set and cannot be moved with .to().
    return hasattr(model, "hf_device_map") and model.hf_device_map is not None


def _set_vlm_seed(seed: Optional[int]) -> None:
    # Set random seed for reproducible generation.
    if seed is not None:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)


def _clear_kv_cache(model) -> None:
    # Clear KV cache from model to avoid stale state between generations.
    # Handles both top-level and nested language_model caches (e.g., Mllama).
    for attr in ("_past_key_values", "past_key_values"):
        if hasattr(model, attr):
            setattr(model, attr, None)
    if hasattr(model, "language_model"):
        for attr in ("_past_key_values", "past_key_values"):
            if hasattr(model.language_model, attr):
                setattr(model.language_model, attr, None)


def _prepare_vlm_image(
    image: Any, max_pixels: int, frame_count: int = 8
) -> Tuple[
    Optional[Image.Image], Optional[List[Image.Image]], Optional[Tuple[int, int]]
]:
    # Convert image tensor to PIL, handle video frames, and resize.
    #
    # Returns:
    #     (image_pil, frames, original_size) - frames is None for single images
    frames = None
    original_size = None

    # Handle video frames (multiple images in batch dimension)
    if (
        image is not None
        and hasattr(image, "shape")
        and len(image.shape) == 4
        and image.shape[0] > 1
    ):
        total_frames = image.shape[0]
        actual_count = min(frame_count, total_frames)
        # Keep the LAST actual_count frames — preserves recent context for
        # chained / video workflows; for single-image tasks N==total==1.
        start = total_frames - actual_count
        frames = []
        for i in range(start, total_frames):
            f = tensor_to_pil(image[i])
            if f is None:
                continue
            if original_size is None:
                original_size = (f.width, f.height)
            f_resized, _ = smart_resize_for_vlm(f, max_pixels=max_pixels)
            frames.append(f_resized)

    # Single image
    image_pil = tensor_to_pil(image) if image is not None else None
    if image_pil is not None:
        if original_size is None:
            original_size = (image_pil.width, image_pil.height)
        image_pil, _ = smart_resize_for_vlm(image_pil, max_pixels=max_pixels, factor=1)

    return image_pil, frames, original_size


def _parse_vlm_prompt(prompt: str) -> Tuple[Optional[str], str]:
    # Split prompt into system instruction and user message.
    #
    # Format: "System instruction\n\nuser message" or just "user message"
    # Handles "Additional context:" prefix in user message.
    #
    # Returns:
    #     (system_prompt, user_message) - system_prompt is None if not present
    system_prompt = None
    user_message = prompt

    if "\n\n" in prompt:
        parts = prompt.split("\n\n", 1)
        system_prompt = parts[0].strip()
        remaining = parts[1].strip() if parts[1].strip() else None

        if remaining and remaining.startswith("Additional context:"):
            user_message = remaining.replace("Additional context:", "").strip()
        elif remaining:
            user_message = remaining
        else:
            user_message = ""

    return system_prompt, user_message


def _get_vlm_device(smart_lm_instance) -> torch.device:
    # Get the target device for VLM inputs.
    #
    # Deterministically planned VLMs record their authoritative placement.
    effective_device = getattr(
        smart_lm_instance.model, "_sml_effective_device", None
    )
    if effective_device:
        return torch.device(effective_device)

    # Legacy quantized models use cuda:0 when CUDA is available.
    if hasattr(smart_lm_instance, "is_quantized") and smart_lm_instance.is_quantized:
        return (
            torch.device("cuda:0") if torch.cuda.is_available() else torch.device("cpu")
        )

    # Non-quantized: try ComfyUI device management for optimal GPU memory handling
    # Skip .to() for accelerate-dispatched models — they manage their own device placement
    if _is_accelerate_dispatched(smart_lm_instance.model):
        return next(smart_lm_instance.model.parameters()).device
    try:
        import comfy.model_management as mm  # type: ignore

        device = mm.get_torch_device()
        smart_lm_instance.model.to(device)
        return device
    except Exception:
        return next(smart_lm_instance.model.parameters()).device


def _get_model_dtype(model) -> torch.dtype:
    # Detect model dtype from the first floating-point parameter.
    for param in model.parameters():
        if param.dtype in (torch.float16, torch.bfloat16, torch.float32):
            return param.dtype
    return torch.float16


def _move_inputs_to_device(
    inputs: dict, device: torch.device, model_dtype: torch.dtype
) -> dict:
    # Move all tensor inputs to device with correct dtype.
    #
    # Float tensors (pixel_values, etc.) are cast to model_dtype.
    # Integer tensors (input_ids, attention_mask) stay as-is.
    processed = {}
    for key, value in inputs.items():
        if not isinstance(value, torch.Tensor):
            processed[key] = value
        elif value.dtype in (
            torch.float32,
            torch.float64,
            torch.float16,
            torch.bfloat16,
        ):
            if value.dtype != model_dtype:
                processed[key] = value.to(dtype=model_dtype, device=device)
            else:
                processed[key] = value.to(device=device)
        else:
            processed[key] = value.to(device=device)
    return processed


def _apply_vlm_chat_template(processor, messages: list) -> str:
    # Apply chat template to messages, with fallback to tokenizer.
    #
    # Modern transformers processors have apply_chat_template directly.
    # Older ones may only have it on the tokenizer. Tries both.
    if hasattr(processor, "chat_template") and processor.chat_template:
        return processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
    if (
        hasattr(processor, "tokenizer")
        and hasattr(processor.tokenizer, "chat_template")
        and processor.tokenizer.chat_template
    ):
        log.debug(_LOG_PREFIX, "  Using tokenizer's chat_template (processor has none)")
        return processor.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
    # Last resort: call it anyway (will raise AttributeError if not available)
    return processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )


def _maybe_offload_vlm(smart_lm_instance, device: torch.device) -> None:
    # Offload non-quantized VLM to CPU after generation if not keeping loaded.
    # Saves VRAM for other ComfyUI models.
    keep_loaded = getattr(smart_lm_instance, "keep_model_loaded", False)
    is_quantized = getattr(smart_lm_instance, "is_quantized", True)
    if keep_loaded or is_quantized:
        return
    # Skip offload for accelerate-dispatched models — they manage their own device placement
    if _is_accelerate_dispatched(smart_lm_instance.model):
        return
    try:
        import comfy.model_management as mm  # type: ignore

        offload_device = mm.unet_offload_device()
        if offload_device != device:
            smart_lm_instance.model.to(offload_device)
            mm.soft_empty_cache()
    except Exception:
        pass


# ==============================================================================
# PER-FAMILY POST-PROCESSING
# ==============================================================================

def _post_process_mistral(text: str) -> str:
    # Clean up Mistral-specific BPE artifacts and mojibake encoding issues.
    return text.replace("Ġ", " ").replace("Ċ", "\n").strip()


# Thinking detection patterns for models that output reasoning without <think> tags
_THINKING_START_PATTERNS = [
    r"^(?:Okay|Alright|Let me|Let\'s|First|I\'ll|I need to|I should|I will|I want to|Hmm|So|Now|Got it|Start with|Wait|Check)[,\s]",
]

_THINKING_REJECT_PATTERNS = (
    r"^(?:Okay|Alright|Let me|Let\'s|First|I\'ll|I need to|I should|I will|I want to|"
    r"Hmm|So|Now|Got it|Start with|Wait|Check|Yes|Yep|Now,)[,\.\s]"
)

_THINKING_DRAFT_PATTERNS = (
    r"^(?:Let me draft|Let me check|Wait,|Check if|So:|Yes\.|Yep\.)"
)

_THINKING_TRANSITION_PATTERNS = [
    r"(?:Final version[:\s]*|Alright[,\s]+actual[^:]*:|Proceed\.\s*|[Bb]egin fresh[^:]*:|Starting[:\s]+|Here(?:\'s| is) (?:the|my) (?:refined|expanded|improved|final)[^:]*:)",
    r"(?:^|\n)(?:The |A |An )(?=[A-Z]?[a-z]{2,})",
]


def _strip_untagged_thinking(text: str) -> str:
    # Remove untagged thinking/reasoning from model output.
    #
    # Models like Qwen3-VL-Thinking output reasoning text (starting with
    # "Okay, let me think..." or similar) before the actual output, without
    # wrapping it in <think> tags. This heuristic identifies and removes it.
    is_thinking = any(
        re.match(p, text, re.IGNORECASE) for p in _THINKING_START_PATTERNS
    )
    if not is_thinking:
        return text

    # Method 1: Find last substantial paragraph that looks like actual output
    paragraphs = re.split(r"\n\n+|\n(?=[A-Z][a-z])", text)
    for para in reversed(paragraphs):
        para = para.strip()
        if len(para) > 80 and not re.match(
            _THINKING_REJECT_PATTERNS, para, re.IGNORECASE
        ):
            if not re.search(_THINKING_DRAFT_PATTERNS, para, re.IGNORECASE):
                text = para
                break

    # Method 2: If still has thinking patterns, try transition markers
    if re.match(r"^(?:Okay|Alright|Let me|Let\'s|First)[,\s]", text, re.IGNORECASE):
        for pattern in _THINKING_TRANSITION_PATTERNS:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
            if match:
                potential = text[match.end() :].strip()
                if "The |A |An " in pattern:
                    potential = text[match.start() :].strip()
                    potential = (
                        re.split(r"\n\n+", potential)[0]
                        if "\n\n" in potential
                        else potential
                    )
                if len(potential) > 50:
                    text = potential
                    break

    return text


# ==============================================================================
# VLM MODEL TYPE RESOLUTION
# ==============================================================================


def _resolve_vlm_model_type(smart_lm_instance, model_family: str):
    # Convert model_family string to ModelType enum for VLM routing.
    from .model_types import ModelType

    if model_family == "Mistral3":
        return ModelType.MISTRAL3
    elif model_family in ("QwenVL", "Qwen"):
        return ModelType.QWENVL
    elif model_family == "LLaVA":
        # Check if the actual model_type is Mllama (Llama 3.2 Vision)
        actual = getattr(smart_lm_instance, "model_type", None)
        is_mllama = actual == ModelType.MLLAMA or str(actual).lower() in (
            "mllama",
            "modeltype.mllama",
        )
        if is_mllama:
            log.debug(_LOG_PREFIX, "  LLaVA family: detected Mllama (Llama 3.2 Vision)")
            return ModelType.MLLAMA
        return ModelType.LLAVA
    elif model_family in ("Mllama", "Llama-Vision"):
        return ModelType.MLLAMA
    else:
        raise ValueError(f"Unknown VLM model family: {model_family}")


# ==============================================================================
# GENERATION ROUTER
# ==============================================================================


def generate_transformers(
    smart_lm_instance,
    model_family: str,
    image: Any,
    prompt: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
    seed: Optional[int],
    repetition_penalty: float,
    num_beams: int = 1,
    do_sample: bool = True,
    context_size: Optional[int] = None,
    **kwargs,
) -> Tuple[str, dict]:
    # Route to the appropriate generator based on model_family.
    #
    # This is the main entry point for generating with any transformers-based model.
    # VLM families (Mistral3, QwenVL, LLaVA, Mllama) are handled by the unified
    # _generate_vlm(). Florence2 and LLM have their own dedicated generators.
    #
    # Args:
    #     smart_lm_instance: The SmartLM instance with loaded model
    #     model_family: Model family string (Mistral3, QwenVL, Florence2, LLM, LLaVA, etc.)
    #     image: Input image (PIL or tensor) - can be None for text-only
    #     prompt: Text prompt
    #     max_tokens: Maximum tokens to generate
    #     temperature, top_p, top_k: Sampling parameters
    #     seed: Random seed for reproducibility
    #     repetition_penalty: Penalty for token repetition
    #     num_beams: Number of beams for beam search
    #     do_sample: Whether to use sampling
    #     context_size: Optional maximum context length
    #     **kwargs: Additional arguments (llm_mode, vision_task, frame_count, etc.)
    #
    # Returns:
    #     Tuple of (generated_text, parsed_data_dict)
    llm_mode = kwargs.get("llm_mode")
    vision_task = kwargs.get("vision_task")
    use_few_shot = kwargs.get("use_few_shot", True)

    # ── LLM text-only with llm_mode (tokenizer-based, no image processor) ──
    if image is None and llm_mode and model_family == "LLM":
        instruction_template = kwargs.get("instruction_template", "")
        log.debug(_LOG_PREFIX, f"  LLM text-only task with llm_mode '{llm_mode}'")
        return _generate_llm(
            smart_lm_instance,
            prompt,
            max_tokens,
            temperature,
            top_p,
            top_k,
            seed,
            repetition_penalty,
            llm_mode,
            instruction_template,
            context_size=context_size,
            use_few_shot=use_few_shot,
        )

    # ── Florence2 routing (task-based paradigm, not chat) ──
    if model_family == "Florence2":
        text_input = kwargs.get("text_input")
        convert_to_bboxes = kwargs.get("convert_to_bboxes", False)
        detection_filter_threshold = kwargs.get("detection_filter_threshold", 0.80)
        nms_iou_threshold = kwargs.get("nms_iou_threshold", 0.50)
        return _generate_florence2(
            smart_lm_instance,
            image,
            prompt,
            max_tokens,
            num_beams,
            do_sample,
            seed,
            repetition_penalty,
            text_input,
            convert_to_bboxes,
            detection_filter_threshold,
            nms_iou_threshold,
            context_size=context_size,
        )

    # ── LLM text-only routing (no llm_mode, fallback to direct_chat) ──
    if model_family == "LLM":
        llm_mode = kwargs.get("llm_mode", "direct_chat")
        instruction_template = kwargs.get("instruction_template", "")
        return _generate_llm(
            smart_lm_instance,
            prompt,
            max_tokens,
            temperature,
            top_p,
            top_k,
            seed,
            repetition_penalty,
            llm_mode,
            instruction_template,
            context_size=context_size,
            use_few_shot=use_few_shot,
        )

    # ── VLM routing (vision + text-only with llm_mode) ──
    # VLM processors (Pixtral, QwenVL) have `images` as first positional arg —
    # _generate_llm's processor(text, ...) call would feed text as images → crash.
    # Route ALL VLM tasks through _generate_vlm which handles the processor correctly.
    model_type = _resolve_vlm_model_type(smart_lm_instance, model_family)

    frame_count = kwargs.get("frame_count", 8)
    explicit_system_prompt = kwargs.get("system_prompt")
    return _generate_vlm(
        smart_lm_instance,
        image,
        prompt,
        max_tokens,
        temperature,
        top_p,
        top_k,
        num_beams,
        do_sample,
        seed,
        repetition_penalty,
        model_type,
        context_size=context_size,
        frame_count=frame_count,
        vision_task=vision_task,
        llm_mode=llm_mode,
        use_few_shot=use_few_shot,
        explicit_system_prompt=explicit_system_prompt,
    )


# ==============================================================================
# UNIFIED VLM GENERATION
# ==============================================================================


def _generation_length_kwargs(
    model: Any,
    *,
    prompt_length: int,
    max_new_tokens: int,
) -> dict[str, int]:
    # Transformers 5.3 warns when a repository supplies max_length while the
    # caller supplies max_new_tokens. It also deprecates mixing an explicit
    # GenerationConfig with generation keyword arguments. Preserve Eclipse's
    # new-token limit by translating it to one absolute limit for that case.
    generation_config = getattr(model, "generation_config", None)
    if generation_config is not None and getattr(
        generation_config, "max_length", None
    ) is not None:
        return {"max_length": prompt_length + max_new_tokens}
    return {"max_new_tokens": max_new_tokens}


def _generate_vlm(
    smart_lm_instance,
    image: Any,
    prompt: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
    num_beams: int,
    do_sample: bool,
    seed: Optional[int],
    repetition_penalty: float,
    model_type,
    context_size: Optional[int] = None,
    frame_count: int = 8,
    vision_task: Optional[str] = None,
    llm_mode: Optional[str] = None,
    use_few_shot: bool = True,
    explicit_system_prompt: Optional[str] = None,
) -> Tuple[str, dict]:
    # Unified generation for all VLM families (Mistral3, QwenVL, LLaVA, Mllama).
    #
    # This function handles the complete generation pipeline:
    # 1. Seed setting, KV cache clearing
    # 2. Image conversion and resize
    # 3. Per-family message building (vision or text-only with llm_mode few-shot)
    # 4. Chat template application and input processing
    # 5. Generation with model.generate()
    # 6. Decoding and per-family post-processing
    #
    # Florence2 and LLM are handled separately (different paradigms).
    #
    # Args:
    #     smart_lm_instance: The SmartLM instance with loaded model/processor
    #     image: Input image (PIL or tensor) - can be None for text-only VLM tasks
    #     prompt: Text prompt (may contain system instruction separated by \n\n)
    #     max_tokens: Maximum tokens to generate
    #     temperature, top_p, top_k: Sampling parameters
    #     num_beams: Number of beams for beam search
    #     do_sample: Whether to use sampling
    #     seed: Random seed for reproducibility
    #     repetition_penalty: Penalty for token repetition
    #     model_type: ModelType enum value (MISTRAL3, QWENVL, LLAVA, MLLAMA)
    #     context_size: Optional maximum context length
    #     frame_count: Max video frames to process (QwenVL only)
    #     vision_task: Vision task name for few-shot injection
    #     llm_mode: LLM mode key for text-only few-shot (multi-task chain)
    #
    # Returns:
    #     Tuple of (generated_text, parsed_data_dict)
    from .model_types import ModelType

    log.debug(
        _LOG_PREFIX,
        f"_generate_vlm: model_type={model_type}, prompt={prompt[:100] if prompt else 'None'}...",
    )

    # ── 1. SEED + KV CACHE ──────────────────────────────────────────────
    _set_vlm_seed(seed)
    _clear_kv_cache(smart_lm_instance.model)

    # ── 2. IMAGE PREPARATION ────────────────────────────────────────────
    # Per-model-type cap — processor will re-resize anyway, this is just an
    # upstream sanity guard. Capable models (Qwen-VL) get a higher cap so we
    # don't strip detail their dynamic patcher could have used.
    max_pixels = get_max_pixels_for_model_type(model_type)
    image_pil, frames, original_size = _prepare_vlm_image(
        image, max_pixels, frame_count
    )

    # ── 3. BUILD MESSAGES ───────────────────────────────────────────────
    # Text-only with llm_mode: build chat messages with system + few-shot + instruction_template
    # This path is used by VLM models in multi-task chain (can't use _generate_llm because
    # VLM processors have images as first positional arg → crash on text-only calls)
    if image_pil is None and llm_mode:
        log.debug(
            _LOG_PREFIX,
            f"  VLM text-only with llm_mode '{llm_mode}', building chat messages",
        )
        from .config_templates import get_llm_few_shot_examples
        from .tasks import get_system_prompt

        LLM_FEW_SHOT_EXAMPLES = get_llm_few_shot_examples()

        config = LLM_FEW_SHOT_EXAMPLES.get(llm_mode)
        if config:
            display_name = config.get("display_name", llm_mode)
        else:
            display_name = llm_mode.replace("_", " ").title()
            config = {
                "display_name": display_name,
                "instruction_template": "",
                "examples": [],
            }

        sys_prompt = get_system_prompt(display_name)
        if not sys_prompt:
            sys_prompt = "You are a helpful assistant."

        examples_val = config.get("examples", []) if use_few_shot else []
        examples = examples_val if isinstance(examples_val, list) else []
        template_val = config.get("instruction_template", "")
        if isinstance(template_val, list):
            template = "\n".join(str(item) for item in template_val)
        else:
            template = str(template_val or "")

        if isinstance(prompt, list):
            prompt = "\n".join(str(item) for item in prompt)
        elif not isinstance(prompt, str):
            prompt = str(prompt or "")

        messages = [{"role": "system", "content": sys_prompt}]
        if examples:
            messages.extend(examples)

        if llm_mode != "direct_chat" and template:
            req = (
                template.replace("{prompt}", prompt)
                if "{prompt}" in template
                else f"{template} {prompt}"
            )
            messages.append({"role": "user", "content": req})
        else:
            messages.append({"role": "user", "content": prompt})

        log.debug(
            _LOG_PREFIX,
            f"  VLM LLM-mode: {len(messages)} messages, {len(examples)} few-shot examples",
        )

    else:
        # Vision path (or text-only without llm_mode)
        if explicit_system_prompt is not None:
            # Eclipse 3.5+ — system + user passed separately, no parsing needed
            system_prompt = explicit_system_prompt or None
            user_message = prompt or ""
        elif model_type in (ModelType.MISTRAL3, ModelType.QWENVL):
            system_prompt, user_message = _parse_vlm_prompt(prompt)
        else:
            system_prompt, user_message = None, prompt

        if model_type == ModelType.MISTRAL3:
            # Mistral: system role + user content with image + optional few-shot examples
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})

            # Inject text-only few-shot examples for vision tasks (message-level)
            if image_pil and vision_task and use_few_shot:
                from .config_templates import get_vision_few_shot_messages

                few_shot = get_vision_few_shot_messages(vision_task)
                if few_shot:
                    messages.extend(few_shot)

            if image_pil is not None:
                user_content = [{"type": "image"}]
                if user_message:
                    user_content.append({"type": "text", "text": user_message})
            else:
                # Text-only mode for Mistral (e.g., chained multi-task)
                user_content = [{"type": "text", "text": user_message or ""}]
            messages.append({"role": "user", "content": user_content})

        elif model_type == ModelType.QWENVL:
            # Qwen: NO system role (FP8 models don't handle it well)
            # All content (image/video + combined system+user text) goes in user role
            messages = []

            # Inject text-only few-shot examples as chat messages before image
            if image_pil and vision_task and use_few_shot:
                from .config_templates import get_vision_few_shot_messages

                few_shot = get_vision_few_shot_messages(vision_task)
                if few_shot:
                    messages.extend(few_shot)

            user_content = []
            if image_pil and frames is None:
                user_content.append({"type": "image", "image": image_pil})
            if frames and len(frames) > 1:
                user_content.append({"type": "video", "video": frames})

            prompt_text = system_prompt or ""
            if user_message:
                prompt_text = (
                    f"{prompt_text}\n\n{user_message}" if prompt_text else user_message
                )
            if not prompt_text:
                prompt_text = "Describe this image in detail."
            user_content.append({"type": "text", "text": prompt_text})
            messages.append({"role": "user", "content": user_content})

        else:
            # LLaVA / Mllama: simple user-only format with image placeholder
            messages = []

            # Inject text-only few-shot examples as chat messages before image
            if image_pil and vision_task and use_few_shot:
                from .config_templates import get_vision_few_shot_messages

                few_shot = get_vision_few_shot_messages(vision_task)
                if few_shot:
                    messages.extend(few_shot)

            prompt_text = explicit_system_prompt or ""
            if prompt:
                prompt_text = f"{prompt_text}\n\n{prompt}" if prompt_text else prompt
            if not prompt_text:
                prompt_text = "Describe this image in detail."

            if image_pil is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "image"},
                            {"type": "text", "text": prompt_text},
                        ],
                    }
                )
            else:
                messages.append(
                    {"role": "user", "content": [{"type": "text", "text": prompt_text}]}
                )

    log.debug(
        _LOG_PREFIX,
        f"  Messages: {len(messages)} message(s), model_type={model_type.value}",
    )

    # ── 4. CHAT TEMPLATE + PROCESSOR ────────────────────────────────────
    # Qwen-VL family (Qwen2-VL / Qwen2.5-VL / Qwen3-VL / Qwen3.5-VL) requires the
    # all-in-one path: apply_chat_template must tokenize WITH the image so it can
    # expand the {"type": "image"} placeholder into the correct number of
    # <|image_pad|> tokens (count depends on image grid_thw). Doing the legacy
    # two-step (apply_chat_template tokenize=False → processor(text, images)) drops
    # the image info during template expansion, leading to:
    #   "Image features and image tokens do not match, tokens: 0, features: N"
    inputs: Optional[dict[str, Any]] = None

    use_unified_template = (
        model_type == ModelType.QWENVL
        and image_pil is not None
        and (frames is None or len(frames) <= 1)
    )

    if use_unified_template:
        # Always pass our bundled official Qwen-VL template explicitly. This
        # guarantees correct rendering of list-content (image+text) messages
        # regardless of what jinja the model ships, including abliterated /
        # merged variants (e.g. Huihui-Qwen3.5-*-abliterated) that bundle a
        # text-only Qwen template which silently drops image markers and
        # produces the cryptic `tokens: 0, features: N` mismatch downstream.
        # The official Qwen3.5-VL template handles both image-bearing and
        # text-only messages, so it works as a universal Qwen-VL template.
        fallback_tpl: Optional[str] = None
        try:
            tpl_path = (
                Path(__file__).parent / "templates" / "qwen_vl_chat_template.jinja"
            )
            if tpl_path.exists():
                fallback_tpl = tpl_path.read_text(encoding="utf-8")
            else:
                log.warning(
                    _LOG_PREFIX,
                    f"Bundled Qwen-VL template missing at {tpl_path}; using model's own template",
                )
        except Exception as e:
            log.warning(
                _LOG_PREFIX,
                f"Could not read bundled Qwen-VL template ({e}); using model's own template",
            )

        try:
            tpl_kwargs = {"chat_template": fallback_tpl} if fallback_tpl else {}
            inputs = smart_lm_instance.processor.apply_chat_template(
                messages,
                add_generation_prompt=True,
                tokenize=True,
                return_dict=True,
                return_tensors="pt",
                **tpl_kwargs,
            )
        except Exception as e:
            log.error(
                _LOG_PREFIX,
                f"Unified chat template failed ({e}), falling back to two-step path",
            )
            use_unified_template = False

    if not use_unified_template:
        try:
            formatted_text = _apply_vlm_chat_template(
                smart_lm_instance.processor, messages
            )
        except Exception as e:
            log.error(_LOG_PREFIX, f"Chat template error: {e}")
            raise

        # Build processor kwargs (differs for Qwen video support)
        processor_kwargs: dict[str, Any] = {
            "text": formatted_text,
            "return_tensors": "pt",
        }
        if image_pil is not None and frames is None:
            # Single image: Qwen expects list, others expect single PIL
            processor_kwargs["images"] = (
                [image_pil] if model_type == ModelType.QWENVL else image_pil
            )
        if frames and len(frames) > 1 and model_type == ModelType.QWENVL:
            processor_kwargs["videos"] = [frames]

        inputs = smart_lm_instance.processor(**processor_kwargs)

    if inputs is None:
        raise ValueError("Failed to initialize inputs from processor")

    # ── 5. MOVE TO DEVICE ──────────────────────────────────────────────
    device = _get_vlm_device(smart_lm_instance)
    model_dtype = _get_model_dtype(smart_lm_instance.model)
    inputs = _move_inputs_to_device(inputs, device, model_dtype)

    log.debug(_LOG_PREFIX, f"  Device: {device}, dtype: {model_dtype}")
    for k, v in inputs.items():
        if isinstance(v, torch.Tensor):
            log.debug(_LOG_PREFIX, f"    {k}: shape={v.shape}, dtype={v.dtype}")

    # ── 6. BUILD GEN KWARGS ─────────────────────────────────────────────
    has_video = frames is not None and len(frames) > 1
    effective_beams = 1 if has_video and num_beams > 1 else num_beams
    if has_video and num_beams > 1:
        log.msg(
            _LOG_PREFIX, "Note: Beam search disabled for video (using sampling instead)"
        )

    effective_do_sample = do_sample and temperature > 0

    effective_max_new_tokens = max_tokens
    if context_size and context_size > 0:
        if "input_ids" in inputs:
            prompt_len = inputs["input_ids"].shape[1]
            remaining = context_size - prompt_len
            effective_max_new_tokens = min(max_tokens, max(1, remaining))

    prompt_length = (
        inputs["input_ids"].shape[1] if "input_ids" in inputs else 0
    )
    gen_kwargs: dict[str, Any] = _generation_length_kwargs(
        smart_lm_instance.model,
        prompt_length=prompt_length,
        max_new_tokens=effective_max_new_tokens,
    )

    if effective_beams > 1:
        gen_kwargs["num_beams"] = effective_beams
        gen_kwargs["do_sample"] = False
    else:
        gen_kwargs["do_sample"] = effective_do_sample
        if effective_do_sample:
            gen_kwargs["temperature"] = max(temperature, 0.01)
            gen_kwargs["top_p"] = top_p
            if top_k > 0:
                gen_kwargs["top_k"] = top_k

    if repetition_penalty > 0 and repetition_penalty != 1.0:
        gen_kwargs["repetition_penalty"] = repetition_penalty

    # Model-specific gen kwargs
    if model_type == ModelType.QWENVL:
        # Qwen needs explicit stop tokens and pad token
        stop_tokens: list[int] = [smart_lm_instance.tokenizer.eos_token_id]
        if (
            hasattr(smart_lm_instance.tokenizer, "eot_id")
            and smart_lm_instance.tokenizer.eot_id is not None
        ):
            stop_tokens.append(smart_lm_instance.tokenizer.eot_id)
        gen_kwargs["eos_token_id"] = stop_tokens
        gen_kwargs["pad_token_id"] = smart_lm_instance.tokenizer.pad_token_id
        # KV/state cache for fast autoregressive generation. Critical for Qwen3.5 hybrid
        # (linear_attention + full_attention/Mamba SSM): without explicit use_cache=True,
        # generation can fall back to O(n²) per-step recomputation and hang for many minutes.
        gen_kwargs["use_cache"] = True
    elif model_type in (ModelType.LLAVA, ModelType.MLLAMA):
        # LLaVA/Mllama need pad token and KV caching for fast autoregressive generation
        tokenizer = None
        if getattr(smart_lm_instance, "processor", None) is not None:
            tokenizer = getattr(smart_lm_instance.processor, "tokenizer", None)
            if tokenizer is None:
                tokenizer = smart_lm_instance.processor
        if tokenizer is None:
            tokenizer = getattr(smart_lm_instance, "tokenizer", None)

        gen_kwargs["pad_token_id"] = getattr(
            tokenizer, "pad_token_id", None
        ) or getattr(tokenizer, "eos_token_id", None)
        gen_kwargs["use_cache"] = True

    # ── 7. GENERATE ─────────────────────────────────────────────────────
    try:
        import logging

        # Temporarily suppress accelerate dispatch warnings during generation —
        # these are informational and expected when device_map="auto" offloads params
        _accel_logger = logging.getLogger("accelerate.big_modeling")
        _prev_level = _accel_logger.level
        _accel_logger.setLevel(logging.ERROR)
        try:
            with torch.inference_mode():
                output_ids = smart_lm_instance.model.generate(**inputs, **gen_kwargs)
            if torch.cuda.is_available():
                torch.cuda.synchronize()
        finally:
            _accel_logger.setLevel(_prev_level)
    except Exception as e:
        log.error(
            _LOG_PREFIX,
            f"Generation failed ({model_type.value}, {type(e).__name__})",
        )
        log.debug(_LOG_PREFIX, f"Generation error: {e}")
        raise

    # ── 8. DECODE ────────────────────────────────────────────────────────
    input_len = inputs["input_ids"].shape[-1]
    generated_ids = output_ids[0][input_len:]

    # Use tokenizer for decoding (processor.decode delegates to tokenizer.decode)
    decoder = None
    if getattr(smart_lm_instance, "processor", None) is not None:
        decoder = getattr(smart_lm_instance.processor, "tokenizer", None)
        if decoder is None:
            decoder = smart_lm_instance.processor
    if decoder is None:
        decoder = getattr(smart_lm_instance, "tokenizer", None)

    if decoder is not None:
        raw_text = decoder.decode(
            generated_ids, skip_special_tokens=True, clean_up_tokenization_spaces=True
        ).strip()
    else:
        raw_text = ""
        log.warning(
            _LOG_PREFIX,
            "No tokenizer/decoder found for decoding; output IDs might be lost",
        )

    # ── 9. POST-PROCESS (per-family) ─────────────────────────────────────
    if model_type == ModelType.MISTRAL3:
        # Mistral: BPE artifact cleanup + mojibake encoding fixes
        from .common import clean_model_output

        cleaned_text, _ = clean_model_output(_post_process_mistral(raw_text))
        parsed_data = {}

    elif model_type == ModelType.QWENVL:
        # Qwen: strip thinking tags + untagged thinking heuristics + detection parsing
        from .common import clean_model_output

        cleaned_text, _ = clean_model_output(raw_text)
        cleaned_text = _strip_untagged_thinking(cleaned_text)

        # Parse detection JSON from output (use original image size for coordinate system)
        img_size = original_size or (
            (image_pil.width, image_pil.height) if image_pil else None
        )
        parsed_data, cleaned_text = _parse_qwen_detection_json(
            cleaned_text, image_size=img_size
        )
        if not parsed_data:
            parsed_data = {"raw_output": raw_text}

    else:
        # LLaVA / Mllama: strip thinking tags only
        from .common import clean_model_output

        cleaned_text, _ = clean_model_output(raw_text)
        parsed_data = {}

    # ── 10. OFFLOAD ─────────────────────────────────────────────────────
    _maybe_offload_vlm(smart_lm_instance, device)

    log.debug(_LOG_PREFIX, f"  Generated: {cleaned_text[:200]}...")
    return cleaned_text, parsed_data


# ==============================================================================
# FLORENCE2 GENERATION
# ==============================================================================


def _is_degenerate_florence_output(text: str, task_id: str) -> bool:
    # Detect garbage output from Florence when a task token is unsupported.
    # Unsupported tasks produce echoed token fragments like "ANALYZE>ANALYZE>ANALYZE>"
    # or garbled versions of the task token like "MIXED_CAPTION_CAPTION_PLUS>".
    if len(text) < 5:
        return False
    upper = text.upper()
    # Check if output contains the task token name or significant fragments of it
    # e.g. task_id="prompt_gen_analyze" → token parts: ANALYZE, GENERATE_TAGS, MIXED_CAPTION
    from .tasks import FLORENCE_ID_TO_TASK

    task_obj = FLORENCE_ID_TO_TASK.get(task_id)
    if task_obj and task_obj.florence_token:
        token_core = task_obj.florence_token.strip(
            "<>"
        ).upper()  # e.g. "ANALYZE", "MIXED_CAPTION_PLUS"
        # Check if output contains the token name (or major fragments)
        if token_core in upper:
            return True
        # Check fragments: split by _ and see if multiple parts appear
        parts = [p for p in token_core.split("_") if len(p) >= 4]
        if parts:
            matches = sum(1 for p in parts if p in upper)
            if matches >= len(parts) * 0.5 and matches >= 2:
                return True
    # Check for repeated '>' which indicates echoed tokens
    if text.count(">") >= 3:
        return True
    # Check if output is mostly uppercase token-like text (garbled echo)
    stripped = text.strip()
    if stripped and ">" in stripped:
        alpha_upper = sum(1 for c in stripped if c.isupper() or c in "_>")
        if alpha_upper > len(stripped) * 0.7:
            return True
    # Check for excessive repetition of same word
    words = re.findall(r"[A-Za-z_]{3,}", text)
    if len(words) >= 4:
        from collections import Counter

        counts = Counter(w.upper() for w in words)
        _, most_common_count = counts.most_common(1)[0]
        if most_common_count >= len(words) * 0.5 and most_common_count >= 3:
            return True
    return False


def _generate_florence2(
    base_instance,
    image: Any,
    task_or_prompt: str,
    max_tokens: int,
    num_beams: int,
    do_sample: bool,
    seed: Optional[int],
    repetition_penalty: float = 1.0,
    text_input: Optional[str] = None,
    convert_to_bboxes: bool = True,
    detection_filter_threshold: float = 0.80,
    nms_iou_threshold: float = 0.50,
    context_size: Optional[int] = None,
) -> Tuple[str, dict]:
    # Generate with Florence-2 - returns (text, parsed_data).
    #
    # Args:
    #     base_instance: SmartLM instance with loaded model
    #     image: Input image tensor
    #     task_or_prompt: Task name or custom prompt
    #     max_tokens: Maximum tokens to generate
    #     num_beams: Number of beams for beam search
    #     do_sample: Whether to use sampling
    #     seed: Random seed
    #     repetition_penalty: Penalty for repetition
    #     text_input: Additional text input for specific tasks
    #     convert_to_bboxes: Convert detections to bboxes format
    #     detection_filter_threshold: Filter threshold for oversized detections
    #     nms_iou_threshold: IoU threshold for NMS filtering (0.0-1.0)
    #
    # Returns:
    #     Tuple of (generated_text, parsed_data_dict)
    log.debug(_LOG_PREFIX, f"_generate_florence2: task={task_or_prompt}")
    log.debug(
        _LOG_PREFIX,
        f"  max_tokens={max_tokens}, num_beams={num_beams}, do_sample={do_sample}",
    )

    if seed is not None:
        # Use hash for better randomization (Florence-2 seeds can be full uint32)
        import hashlib

        seed_bytes = str(seed).encode("utf-8")
        hash_seed = int(hashlib.sha256(seed_bytes).hexdigest()[:8], 16)
        torch.manual_seed(hash_seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(hash_seed)

    # Handle image tensor - Florence-2 expects PIL image
    if image is None:
        raise ValueError("Florence-2 requires an image input")

    # Convert tensor to PIL and cap at 2MP to avoid wasting memory
    image_pil = tensor_to_pil(image) if isinstance(image, torch.Tensor) else image
    if not image_pil:
        raise ValueError("Failed to convert image to PIL format")
    image_pil, original_size = smart_resize_for_vlm(
        image_pil, max_pixels=VLM_MAX_PIXELS_GENERIC, factor=1
    )
    resized_size = (image_pil.width, image_pil.height)

    # Get task prompt token - load from configured Florence tasks when possible
    florence_tasks = get_florence_tasks()
    task_prompt = florence_tasks.get(task_or_prompt, {}).get("prompt", task_or_prompt)
    if task_prompt is None:
        task_prompt = task_or_prompt or ""

    # Tasks that require text input after the task token
    tasks_requiring_text = [
        "caption_to_phrase_grounding",
        "referring_expression_segmentation",
        "docvqa",
    ]

    # Build full prompt for generation
    # CRITICAL: Florence-2 native tasks expect ONLY the task token (e.g., "<MORE_DETAILED_CAPTION>")
    # with NO additional text. The system_prompt in config is for LLM backends, NOT Florence native.
    # For tasks requiring text input, the text is appended directly to the task token.

    if task_or_prompt in tasks_requiring_text and text_input and text_input.strip():
        # Task token + text input (e.g., "<CAPTION_TO_PHRASE_GROUNDING>person")
        prompt = task_prompt + text_input
        log.debug(
            _LOG_PREFIX,
            f"Task: {task_or_prompt} | Text input: {text_input[:50]}... | Prompt: {prompt}",
        )
    else:
        # Just task token (e.g., "<CAPTION>", "<MORE_DETAILED_CAPTION>")
        # DO NOT add any system_prompt - Florence expects ONLY the task token
        prompt = task_prompt
        log.debug(_LOG_PREFIX, f"Task: {task_or_prompt} | Prompt: {prompt}")

    # Follow the device/dtype selected during Florence construction. Generation
    # must not relocate an explicitly CPU-loaded model merely because CUDA exists.
    device = next(base_instance.model.parameters()).device
    dtype = _get_model_dtype(base_instance.model)

    # Process image with do_rescale=False (ComfyUI images are already 0-1)
    inputs = base_instance.processor(
        text=prompt, images=image_pil, return_tensors="pt", do_rescale=False
    )
    inputs = {
        k: (
            v.to(dtype).to(device)
            if torch.is_tensor(v) and v.dtype.is_floating_point
            else v.to(device)
        )
        for k, v in inputs.items()
    }

    # Mutual exclusion: num_beams > 1 + do_sample=True triggers beam sampling
    # which produces empty/degenerate output on Florence2. Force do_sample=False for beam search.
    effective_do_sample = False if num_beams > 1 else do_sample

    # Clamp max_tokens to the model's max_position_embeddings (BART offset of 2).
    # Widget default (4096) is intentionally high so users don't have to adjust per model.
    # base/PromptGen models: max_pos=1024 → capped at 1022
    # large models: max_pos=4096 → capped at 4094
    max_pos = getattr(
        getattr(base_instance.model.config, "text_config", None),
        "max_position_embeddings",
        None,
    )
    effective_max_tokens = max_tokens
    effective_context_size = context_size
    if max_pos and max_pos > 0:
        safe_max = max_pos - 2  # BART position offset
        effective_max_tokens = min(max_tokens, safe_max)
        if context_size and context_size > max_pos:
            effective_context_size = max_pos

    if effective_context_size and effective_context_size > 0:
        if "input_ids" in inputs:
            prompt_len = inputs["input_ids"].shape[1]
            remaining = effective_context_size - prompt_len
            effective_max_tokens = min(effective_max_tokens, max(1, remaining))

    if effective_max_tokens != max_tokens:
        log.debug(
            _LOG_PREFIX, f"Florence: max_tokens {max_tokens} → {effective_max_tokens}"
        )

    florence_gen_kwargs = {
        "input_ids": inputs["input_ids"],
        "pixel_values": inputs["pixel_values"],
        "max_new_tokens": effective_max_tokens,
        "do_sample": effective_do_sample,
        "num_beams": num_beams,
        "repetition_penalty": (
            repetition_penalty if repetition_penalty and repetition_penalty > 0 else 1.0
        ),
        # Note: use_cache=False for Florence2 because comfyui-florence2's modeling code
        # has a bug in prepare_inputs_for_generation that doesn't handle None past_key_values
        "use_cache": False,
    }
    generated_ids = base_instance.model.generate(**florence_gen_kwargs)

    # Decode with skip_special_tokens=True to get clean text first
    results_clean = base_instance.processor.batch_decode(
        generated_ids, skip_special_tokens=True
    )[0]
    # Also decode with skip_special_tokens=False for spatial tasks that need location tokens
    results = base_instance.processor.batch_decode(
        generated_ids, skip_special_tokens=False
    )[0]

    # Define tasks that produce spatial data (bboxes, polygons, etc.)
    spatial_tasks = [
        "region_caption",  # Object detection with captions
        "dense_region_caption",
        "region_proposal",
        "caption_to_phrase_grounding",
        "ocr_with_region",
        "referring_expression_segmentation",
    ]

    # Parse structured output (bounding boxes, labels, etc.) ONLY for spatial tasks
    parsed_data = {}
    is_spatial_task = task_or_prompt in spatial_tasks

    if is_spatial_task:
        # Try to parse with processor's post_process method
        try:
            parsed_answer = base_instance.processor.post_process_generation(
                results,
                task=task_prompt,
                image_size=(image_pil.width, image_pil.height),
            )

            # Extract bboxes and labels
            if task_prompt in parsed_answer:
                task_result = parsed_answer[task_prompt]

                # Handle different result formats
                if "bboxes" in task_result and "labels" in task_result:
                    parsed_data["bboxes"] = task_result["bboxes"]
                    parsed_data["labels"] = task_result["labels"]
                elif "quad_boxes" in task_result and "labels" in task_result:
                    # OCR with region returns quad boxes (4 corners)
                    parsed_data["quad_boxes"] = task_result["quad_boxes"]
                    parsed_data["labels"] = task_result["labels"]
                elif "polygons" in task_result and "labels" in task_result:
                    # Segmentation returns polygons
                    parsed_data["polygons"] = task_result["polygons"]
                    parsed_data["labels"] = task_result["labels"]

        except Exception as e:
            # Fallback: Manual parsing if processor fails
            log.warning(
                _LOG_PREFIX, f"Processor parsing failed, using manual parser: {e}"
            )
            parsed_data = parse_florence_location_tokens(
                results, image_pil.width, image_pil.height
            )

        # Scale detection coordinates from resized image space back to original
        if resized_size != original_size:
            orig_w, orig_h = original_size
            res_w, res_h = resized_size
            sx = orig_w / res_w
            sy = orig_h / res_h
            if "bboxes" in parsed_data:
                parsed_data["bboxes"] = [
                    [b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy]
                    for b in parsed_data["bboxes"]
                ]
            if "quad_boxes" in parsed_data:
                parsed_data["quad_boxes"] = [
                    (
                        [[pt[0] * sx, pt[1] * sy] for pt in qb]
                        if isinstance(qb[0], (list, tuple))
                        else [
                            qb[j] * (sx if j % 2 == 0 else sy) for j in range(len(qb))
                        ]
                    )
                    for qb in parsed_data["quad_boxes"]
                ]
            if "polygons" in parsed_data:
                parsed_data["polygons"] = [
                    (
                        [[pt[0] * sx, pt[1] * sy] for pt in poly]
                        if isinstance(poly[0], (list, tuple))
                        else [
                            poly[j] * (sx if j % 2 == 0 else sy)
                            for j in range(len(poly))
                        ]
                    )
                    for poly in parsed_data["polygons"]
                ]
            log.debug(
                _LOG_PREFIX,
                f"  Scaled Florence2 coords: {res_w}x{res_h} → {orig_w}x{orig_h} (scale {sx:.3f}, {sy:.3f})",
            )

        # Convert quad boxes to regular bboxes if needed
        if "quad_boxes" in parsed_data and convert_to_bboxes:
            quad_boxes = parsed_data["quad_boxes"]
            bboxes = []
            for quad in quad_boxes:
                # Quad format: [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
                # Convert to bbox: [min_x, min_y, max_x, max_y]
                if isinstance(quad[0], (list, tuple)):
                    # Nested list format
                    xs = [pt[0] for pt in quad]
                    ys = [pt[1] for pt in quad]
                else:
                    # Flat list format [x1,y1,x2,y2,...]
                    xs = [quad[i] for i in range(0, len(quad), 2)]
                    ys = [quad[i] for i in range(1, len(quad), 2)]

                bbox = [min(xs), min(ys), max(xs), max(ys)]
                bboxes.append(bbox)

            parsed_data["bboxes"] = bboxes
            del parsed_data["quad_boxes"]

        # Convert polygons to bboxes if needed
        if "polygons" in parsed_data and convert_to_bboxes:
            polygons = parsed_data["polygons"]
            bboxes = []
            for poly in polygons:
                # Polygon format: [[x1,y1], [x2,y2], ...] or [x1,y1,x2,y2,...]
                if isinstance(poly[0], (list, tuple)):
                    xs = [pt[0] for pt in poly]
                    ys = [pt[1] for pt in poly]
                else:
                    xs = [poly[i] for i in range(0, len(poly), 2)]
                    ys = [poly[i] for i in range(1, len(poly), 2)]

                bbox = [min(xs), min(ys), max(xs), max(ys)]
                bboxes.append(bbox)

            parsed_data["bboxes"] = bboxes
            del parsed_data["polygons"]

        # Filter out oversized detections (likely errors)
        # Use original image area since coords have been scaled back
        if "bboxes" in parsed_data and parsed_data["bboxes"]:
            img_area = original_size[0] * original_size[1]
            filtered_bboxes = []
            filtered_labels = []

            for i, bbox in enumerate(parsed_data["bboxes"]):
                x1, y1, x2, y2 = bbox
                bbox_area = (x2 - x1) * (y2 - y1)
                bbox_ratio = bbox_area / img_area if img_area > 0 else 0

                # Filter out detections that cover > threshold of image (likely errors)
                if bbox_ratio <= detection_filter_threshold:
                    filtered_bboxes.append(bbox)
                    if "labels" in parsed_data and i < len(parsed_data["labels"]):
                        filtered_labels.append(parsed_data["labels"][i])

            parsed_data["bboxes"] = filtered_bboxes
            if filtered_labels:
                parsed_data["labels"] = filtered_labels

            # Apply NMS to remove overlapping detections (skip if threshold is 1.0)
            if len(parsed_data["bboxes"]) > 1 and nms_iou_threshold < 1.0:
                nms_bboxes, nms_labels, _ = nms_filter(
                    parsed_data["bboxes"],
                    parsed_data.get("labels", []),
                    iou_threshold=nms_iou_threshold,
                )
                if nms_bboxes:
                    parsed_data["bboxes"] = nms_bboxes
                    if nms_labels:
                        parsed_data["labels"] = nms_labels

    # Clean up special tokens for text output
    # Special handling for ocr_with_region to preserve line breaks
    if task_or_prompt == "ocr_with_region":
        # For OCR with region, clean text but preserve structure
        clean_results = results_clean.strip()
    elif is_spatial_task:
        # For other spatial tasks, use clean version (skip_special_tokens=True already removed tokens)
        clean_results = results_clean.strip()
    else:
        # For non-spatial tasks, just use clean version
        clean_results = results_clean.strip()
        # Remove any remaining special tokens manually
        for token in ["<s>", "</s>", "<pad>", "<|endoftext|>"]:
            clean_results = clean_results.replace(token, "")
        # Remove task token echoes from the output.
        # Florence models often echo back the task token (e.g., '<GENERATE_TAGS>') but since
        # these aren't registered special tokens, skip_special_tokens=True doesn't strip them.
        # The token may appear as exact match, or as subword fragments (e.g., 'ERATE_TAGS>')
        # at the start of the output. We strip:
        #   1. Exact full token (e.g., '<GENERATE_TAGS>')
        #   2. Any leading fragment that is a suffix of the task token (subword echo artifacts)
        try:
            if isinstance(task_prompt, str) and task_prompt:
                # Strip exact full token anywhere in text
                clean_results = clean_results.replace(task_prompt, "")
                # Strip leading fragments: if output starts with a suffix of the token
                # (e.g., token='<GENERATE_TAGS>', output starts with 'ERATE_TAGS>...')
                # check progressively shorter suffixes of the task token
                token_no_brackets = task_prompt.strip("<>")
                if token_no_brackets and clean_results:
                    stripped = clean_results.lstrip()
                    # Check if output starts with '>' (closing bracket of echoed token)
                    if stripped.startswith(">"):
                        stripped = stripped[1:].lstrip()
                    # Check if output starts with a suffix of the token text
                    # e.g., 'ERATE_TAGS>' from '<GENERATE_TAGS>'
                    for i in range(len(token_no_brackets)):
                        suffix = token_no_brackets[i:]
                        if stripped.upper().startswith(suffix.upper()):
                            # Found a suffix match — remove it plus any trailing '>'
                            after = stripped[len(suffix) :]
                            if after.startswith(">"):
                                after = after[1:]
                            clean_results = after.lstrip()
                            break
        except Exception:
            pass
        clean_results = clean_results.strip()

    # Detect degenerate output (unsupported task on this model)
    # Patterns: token echoes ("ANALYZE>ANALYZE>ANALYZE>"), excessive repetition,
    # or output that is mostly uppercase token fragments
    if clean_results and _is_degenerate_florence_output(clean_results, task_or_prompt):
        log.warning(
            _LOG_PREFIX,
            f"Florence: degenerate output detected for task '{task_or_prompt}' "
            f"— task likely not supported by this model",
        )
        clean_results = ""

    # Debug: Log parsed data before returning
    if parsed_data:
        log.debug(_LOG_PREFIX, f"Parsed data keys: {list(parsed_data.keys())}")
        if "bboxes" in parsed_data:
            log.debug(_LOG_PREFIX, f"  bboxes count: {len(parsed_data['bboxes'])}")
            if parsed_data["bboxes"]:
                log.debug(_LOG_PREFIX, f"  first bbox: {parsed_data['bboxes'][0]}")
        if "labels" in parsed_data:
            log.debug(
                _LOG_PREFIX, f"  labels: {parsed_data['labels'][:5]}..."
            )  # First 5 labels
    else:
        log.debug(_LOG_PREFIX, "No parsed data returned")

    return (clean_results, parsed_data)


# ==============================================================================
# LLM (TEXT-ONLY) GENERATION
# ==============================================================================


def _generate_llm(
    smart_lm_instance,
    prompt: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
    seed: Optional[int],
    repetition_penalty: float,
    llm_mode: str,
    instruction_template: str,
    context_size: Optional[int] = None,
    use_few_shot: bool = True,
) -> Tuple[str, dict]:
    # Generate text-only completion with LLM (no images) - transformers path only.
    #
    # For GGUF models, use smartlm_llm.generate_llm() which handles both.
    #
    # Args:
    #     smart_lm_instance: The SmartLM instance
    #     prompt: Text prompt
    #     max_tokens: Maximum tokens to generate
    #     temperature: Sampling temperature
    #     top_p: Nucleus sampling parameter
    #     top_k: Top-k sampling parameter
    #     seed: Random seed for reproducibility
    #     repetition_penalty: Penalty for token repetition
    #     llm_mode: Few-shot mode selection
    #     instruction_template: Custom instruction template
    #
    # Returns:
    #     Tuple of (cleaned_text, data_dict_with_raw_output)
    log.debug(_LOG_PREFIX, f"_generate_llm: llm_mode={llm_mode}")

    # Use getter function to get the latest loaded config (not stale import reference)
    from .config_templates import get_llm_few_shot_examples
    from .tasks import get_system_prompt

    LLM_FEW_SHOT_EXAMPLES = get_llm_few_shot_examples()

    log.debug(_LOG_PREFIX, f"  Available modes: {list(LLM_FEW_SHOT_EXAMPLES.keys())}")

    # Set seed if provided
    if seed is not None:
        from transformers import set_seed  # type: ignore
        import hashlib

        seed_bytes = str(seed).encode("utf-8")
        hash_object = hashlib.sha256(seed_bytes)
        hashed_seed = int(hash_object.hexdigest(), 16) % (2**32)
        set_seed(hashed_seed)

    # Load configuration for the selected mode (get examples and instruction_template)
    config = LLM_FEW_SHOT_EXAMPLES.get(llm_mode)
    if config:
        display_name = config.get("display_name", llm_mode)
    else:
        # No few-shot entry for this mode — derive display name from mode key
        # for correct system prompt lookup (e.g., "detailed_description" -> "Detailed Description")
        display_name = llm_mode.replace("_", " ").title()
        config = {
            "display_name": display_name,
            "instruction_template": "",
            "examples": [],
        }
        log.debug(
            _LOG_PREFIX,
            f"No few-shot config for '{llm_mode}', using task system prompt for '{display_name}'",
        )

    # Get system_prompt from prompt_defaults (authoritative source)
    # display_name is used for case-insensitive lookup in task dict
    system_prompt = get_system_prompt(display_name)
    if not system_prompt:
        system_prompt = "You are a helpful assistant."

    examples_val = config.get("examples", []) if use_few_shot else []
    examples = examples_val if isinstance(examples_val, list) else []
    log.debug(
        _LOG_PREFIX,
        f"  LLM mode: display_name={display_name}, {len(examples)} examples (use_few_shot={use_few_shot})",
    )

    # Get instruction template (custom or from config)
    template_val = (
        instruction_template
        if instruction_template
        else config.get("instruction_template", "")
    )
    if isinstance(template_val, list):
        template = "\n".join(str(item) for item in template_val)
    else:
        template = str(template_val or "")

    if isinstance(prompt, list):
        prompt = "\n".join(str(item) for item in prompt)
    elif not isinstance(prompt, str):
        prompt = str(prompt or "")

    # Build messages: system + (optional examples) + user request
    messages = [{"role": "system", "content": system_prompt}]

    # Add few-shot examples only if available for this task
    if examples:
        messages.extend(examples)

    # Build user request
    if llm_mode != "direct_chat" and template:
        # Apply instruction template
        req = (
            template.replace("{prompt}", prompt)
            if "{prompt}" in template
            else f"{template} {prompt}"
        )
        messages.append({"role": "user", "content": req})
    else:
        # Direct chat mode - just use prompt directly
        messages.append({"role": "user", "content": prompt})

    # Generate response with transformers
    try:
        # Apply chat template to convert messages to text
        input_text = smart_lm_instance.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

        # Tokenize and generate
        inputs = smart_lm_instance.processor(input_text, return_tensors="pt").to(
            smart_lm_instance.model.device
        )

        effective_max_tokens = max_tokens
        if context_size and context_size > 0:
            if "input_ids" in inputs:
                prompt_len = inputs["input_ids"].shape[1]
                remaining = context_size - prompt_len
                effective_max_tokens = min(max_tokens, max(1, remaining))

        llm_gen_kwargs = {
            "max_new_tokens": effective_max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
            "repetition_penalty": repetition_penalty,
            "do_sample": temperature > 0,
            "pad_token_id": smart_lm_instance.processor.eos_token_id,
        }
        outputs = smart_lm_instance.model.generate(**inputs, **llm_gen_kwargs)

        # Decode only the generated tokens (skip input)
        generated_tokens = outputs[0][inputs["input_ids"].shape[1] :]
        text = smart_lm_instance.processor.decode(
            generated_tokens, skip_special_tokens=True
        )

        # Keep raw output for data (includes thinking tags)
        raw_text = text.strip()

        # Strip reasoning/thinking tags from "Thinker" models (e.g., MiroThinker, DeepSeek-R1)
        # These models output <think>...</think> before the actual response
        cleaned_text = re.sub(
            r"<think>.*?</think>\s*", "", text, flags=re.DOTALL
        ).strip()

        # Handle models that output untagged thinking (no <think> tags)
        # MiroThinker and similar models output reasoning like "Okay, let's tackle this..."
        # then have the actual output after double newlines

        # Method 1: Look for common thinking start patterns and find the actual output after
        thinking_start_patterns = [
            r"^(?:Okay|Alright|Let me|Let\'s|First|I\'ll|I need to|I should|I will|I want to|Hmm|So|Now)[,\s]",
        ]
        is_thinking_output = any(
            re.match(p, cleaned_text, re.IGNORECASE) for p in thinking_start_patterns
        )

        if is_thinking_output:
            # Look for double newline followed by substantial content
            # The actual output often starts with "A/An/The" or a descriptive phrase
            double_newline_match = re.search(r"\n\n+([A-Z][^\n]{50,})", cleaned_text)
            if double_newline_match:
                potential_output = double_newline_match.group(1)
                # Get the rest of the text after this point
                rest_start = double_newline_match.start(1)
                potential_output = cleaned_text[rest_start:].strip()
                # Verify it's substantial and looks like actual output (not more thinking)
                if len(potential_output) > 100 and not re.match(
                    r"^(?:Okay|Alright|Let me|Let\'s|First|I\'ll|I need to|I should)[,\s]",
                    potential_output,
                    re.IGNORECASE,
                ):
                    cleaned_text = potential_output

        # Method 2: Look for explicit transition markers
        thinking_end_patterns = [
            r"(?:Final version[:\s]*|Alright[,\s]+actual[^:]*:|Proceed\.\s*|[Bb]egin fresh[^:]*:|Starting[:\s]+|Here(?:\'s| is) (?:the|my) (?:refined|expanded|improved)[^:]*:)",
        ]
        for pattern in thinking_end_patterns:
            match = re.search(pattern, cleaned_text, flags=re.IGNORECASE)
            if match:
                # Keep only content after the thinking marker
                potential_output = cleaned_text[match.end() :].strip()
                # Only use if substantial content remains (>50 chars)
                if len(potential_output) > 50:
                    cleaned_text = potential_output
                    break

        # Return tuple: (cleaned_text, data_dict) - data dict includes raw output for data output
        return cleaned_text, {"raw_output": raw_text}

    except Exception as e:
        log.error(_LOG_PREFIX, f"LLM generation failed ({type(e).__name__})")
        log.debug(_LOG_PREFIX, f"LLM generation error: {e}")
        raise


# ==============================================================================
# EXPORTS
# ==============================================================================

__all__ = [
    # Generation entry point
    "generate_transformers",
    # Unified VLM generation
    "_generate_vlm",
    # Florence2 generation (separate paradigm)
    "_generate_florence2",
    # LLM generation (text-only)
    "_generate_llm",
    # Florence2 utilities
    "get_florence_tasks",
    # Detection utilities (re-exported from vlm_detection)
    "nms_filter",
    "parse_florence_location_tokens",
    "draw_bboxes",
    "parse_qwen_detection_json",
    "_parse_qwen_detection_json",
]
