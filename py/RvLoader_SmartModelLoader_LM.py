# RvLoader_SmartModelLoader - Smart Model Loader
#
# Registry-based model loader — no templates, no model_source, no manual path resolution.
# The model registry (core/model_registry.py) provides the unified model list with backend
# suffixes, and this node dispatches to the correct loader and generator based on registry data.
#
# Replaces: Smart Language Model Loader v3 (template-based workflow)
# Node ID: "Smart LM Loader [Eclipse]"

import gc
import os
import random
import time
import uuid
from datetime import datetime

import torch  # type: ignore

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.logger import log
from ..core.image_helpers import unwrap_value as _unwrap_scalar
from ..core.common import make_comfy_tqdm_class
from ..core.sml.model_registry import (
    get_model_list,
    get_model_entry,
    is_model_separator,
    has_vision as registry_has_vision,
    is_wd14_model,
    get_default,
    load_defaults,
    save_defaults,
    is_trust_remote_code_allowed,
    FAMILY_MAP,
)
from ..core.sml.tasks import (
    TASK_BY_NAME,
    get_task_names,
    get_system_prompt,
    is_florence_task,
    push_system_prompt_override,
    reset_system_prompt_override,
)
from ..core.sml.config_templates import (
    TemplateContext,
    get_config_value,
    get_llm_models_path,
)
from ..core.sml.loader_base import load_model_with_backend
from ..core.sml.model_files import (
    ensure_model_path,
    verify_model_integrity,
    check_model_completeness,
)

_LOG_PREFIX = "SmartML"

# Isolated random state for seed generation — avoids interference from other extensions.
_initial_random_state = random.getstate()
random.seed(datetime.now().timestamp())
_sml_seed_random_state = random.getstate()
random.setstate(_initial_random_state)


def _new_random_seed():
    global _sml_seed_random_state
    prev_state = random.getstate()
    random.setstate(_sml_seed_random_state)
    seed = random.randint(0, 2**64 - 1)
    _sml_seed_random_state = random.getstate()
    random.setstate(prev_state)
    return seed


# ============================================================================
# Backend → loading method mapping
# ============================================================================

_BACKEND_TO_METHOD = {
    "transformers": "Transformers",
    "gguf": "GGUF (llama-cpp-python)",
    "ollama": "Ollama (Docker)",
    "vllm": "vLLM (Docker)",
    "sglang": "SGLang (Docker)",
}

# Registry family string → model_family value for load_model_with_backend / generation
_FAMILY_TO_EXEC = {
    "Qwen": "Qwen",
    "Mistral": "Mistral",
    "Florence": "Florence",
    "LLaVA": "LLaVA",
    "VLM": "VLM",
    "LLM_TEXT": "LLM (Text-Only)",
}

# Transformers family → generate_transformers model_family param
_FAMILY_TF_MAP = {
    "Qwen": "QwenVL",
    "Mistral": "Mistral3",
    "LLM (Text-Only)": "LLM",
    "LLaVA": "LLaVA",
    "VLM": "LLaVA",
}

# Docker backends (need temp image files for vision)
_DOCKER_BACKENDS = {"vllm", "sglang", "ollama"}

# Tasks that should never pass images (text processing only)
_TEXT_ONLY_TASKS = {
    "Tags to Natural Language",
    "Natural Language to Tags",
    "Refine & Expand Prompt",
    "Expand Text",
    "Summarize",
    "Rewrite Style",
    "Translate to English",
    "Prompt Variations",
}

# Tasks that use images when connected, but also work text-only
_FLEXIBLE_TASKS = {
    "Direct Chat",
    "Custom Instruction",
    "Question Answering",
    "Wan 2.2 Scene 5s",
    "Wan 2.2 Timeline 5s",
    "Wan 2.2 Timeline 5s 2s",
    "Wan 2.2 Timeline 5s 3s",
    "Wan 2.2 Scene 10s",
    "Wan 2.2 Timeline 10s",
    "Wan 2.2 Scene 20s",
    "Wan 2.2 Timeline 20s",
    "Wan 2.2 CN Atomic",
    "LTX 2.3 I2V",
}


# ============================================================================
# Image Utilities
# ============================================================================


def _get_temp_image_path(suffix: str = ".jpg") -> str:
    # Temp file in ComfyUI/temp (cleaned on restart).
    import folder_paths  # type: ignore

    temp_dir = folder_paths.get_temp_directory()
    return os.path.join(temp_dir, f"sml_temp_{uuid.uuid4().hex}{suffix}")


def _tensor_to_temp_jpegs(input_image, max_pixels: int = 0, frame_count: int = 0):
    # Convert ComfyUI image tensor [B,H,W,C] or list of tensors to temp JPEG files.
    # When frame_count > 0 and the batch exceeds it, keep the LAST frame_count
    # frames (preserves the most recent context for chained / video workflows).
    # Returns (image_paths, original_size, resized_size).
    from ..core.sml.vlm_detection import tensor_to_pil, smart_resize_for_vlm

    image_paths = []
    original_size = None
    resized_size = None

    def _process(frame):
        nonlocal original_size, resized_size
        img = tensor_to_pil(frame)
        if img is None:
            raise ValueError("Failed to convert image tensor to PIL Image")
        if original_size is None:
            original_size = (img.width, img.height)
        if max_pixels > 0:
            img, _ = smart_resize_for_vlm(img, max_pixels=max_pixels)
        if resized_size is None:
            resized_size = (img.width, img.height)
        path = _get_temp_image_path()
        img.save(path, "JPEG", quality=95)
        image_paths.append(path)

    # Flatten any combination of list, tuple, or batched tensors to a list of individual 3D tensors
    flat_frames = []
    if isinstance(input_image, (list, tuple)):
        for item in input_image:
            if isinstance(item, torch.Tensor):
                if item.dim() == 3:
                    flat_frames.append(item)
                elif item.dim() == 4:
                    for j in range(item.shape[0]):
                        flat_frames.append(item[j])
    elif isinstance(input_image, torch.Tensor):
        if input_image.dim() == 3:
            flat_frames.append(input_image)
        elif input_image.dim() == 4:
            for j in range(input_image.shape[0]):
                flat_frames.append(input_image[j])

    if flat_frames:
        total = len(flat_frames)
        start = max(0, total - frame_count) if frame_count and frame_count > 0 else 0
        for i in range(start, total):
            _process(flat_frames[i])

    return (
        image_paths,
        original_size or (0, 0),
        resized_size or original_size or (0, 0),
    )


def _cleanup_temp_files(paths):
    if paths:
        for p in paths:
            try:
                os.remove(p)
            except Exception:
                pass


# ============================================================================
# Prompt Building
# ============================================================================


def _build_vlm_prompt(task_name, user_prompt, input_image, *, family="Qwen"):
    # Build VLM prompt as a (system, user, is_text_only) triple.
    # Backends now receive system + user separately — no more "\n\nAdditional context:"
    # marker hack and no parser ambiguity when the system text contains blank lines.
    has_text = bool(user_prompt and user_prompt.strip())
    has_image = input_image is not None
    is_text_only = (task_name in _TEXT_ONLY_TASKS and has_text) or (
        task_name in _FLEXIBLE_TASKS and has_text and not has_image
    )

    if is_text_only:
        # Backend supplies the system prompt via llm_mode (+ few-shot)
        return None, user_prompt, True

    if task_name in _FLEXIBLE_TASKS and has_image and has_text:
        # Direct Chat / Custom / QA with image+text.
        # Prefer the task's system prompt (JSON entry, or wired override via ContextVar)
        # so user_prompt flows as the actual user message and few-shot training applies.
        # Only when neither override nor JSON entry exists fall back to the legacy
        # behavior: user_prompt drives the system slot (preserves prior workflows
        # for tasks like Direct Chat that have no JSON system prompt defined).
        base = get_system_prompt(task_name)
        if base:
            return base, user_prompt.strip(), False
        return user_prompt.strip(), "", False

    if has_text:
        base = get_system_prompt(task_name)
        if family in ("LLaVA", "VLM") or not base:
            return None, user_prompt, False
        return base, user_prompt, False

    # Image only, no user text
    base = get_system_prompt(task_name) or task_name or "Describe this image in detail."
    return base, "", False


# ============================================================================
# Backend Generation Dispatch
# ============================================================================


def _dispatch_generate(
    instance,
    *,
    prompt,
    input_image=None,
    is_text_only_task=False,
    max_tokens,
    temperature,
    top_p,
    top_k,
    seed,
    repetition_penalty=1.0,
    num_beams=1,
    do_sample=True,
    model_family="",
    task_name="",
    context_size=8192,
    frame_count=1,
    llm_mode=None,
    use_few_shot=True,
    min_p=0.0,
    mirostat=0,
    mirostat_eta=0.1,
    mirostat_tau=5.0,
    repeat_last_n=64,
    stop_sequences=None,
    system_prompt=None,
):
    # Route generation to the correct backend.
    # Returns (result, raw_output, data, original_size, resized_size).
    is_vision = input_image is not None and not is_text_only_task
    vision_task = task_name if is_vision and not llm_mode else None
    image_paths = None
    original_size = resized_size = None
    raw_output = None
    data = {}

    try:
        # ── Docker / OpenAI-compatible backends ──
        if hasattr(instance, "is_vllm") and instance.is_vllm:
            native = hasattr(instance, "is_vllm_native") and instance.is_vllm_native
            if native:
                from ..core.sml.backend_vllm_native import generate_vllm
            else:
                from ..core.sml.backend_vllm_docker import generate_vllm
            if is_vision:
                from ..core.sml.vlm_detection import get_max_pixels_for_model_type

                image_paths, original_size, resized_size = _tensor_to_temp_jpegs(
                    input_image,
                    max_pixels=get_max_pixels_for_model_type(
                        getattr(instance, "model_type", None)
                    ),
                    frame_count=frame_count,
                )
            kw = dict(
                smart_lm_instance=instance,
                prompt=prompt,
                image_paths=image_paths,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                seed=seed,
                repetition_penalty=repetition_penalty,
                use_few_shot=use_few_shot,
                min_p=min_p,
                stop_sequences=stop_sequences,
                system_prompt=system_prompt,
            )
            if vision_task:
                kw["vision_task"] = vision_task
            if llm_mode:
                kw["llm_mode"] = llm_mode
            gen = generate_vllm(**kw)
            result, raw_output = gen if isinstance(gen, tuple) else (gen, None)

        elif hasattr(instance, "is_sglang") and instance.is_sglang:
            from ..core.sml.backend_sglang_docker import generate_sglang

            if is_vision:
                from ..core.sml.vlm_detection import get_max_pixels_for_model_type

                image_paths, original_size, resized_size = _tensor_to_temp_jpegs(
                    input_image,
                    max_pixels=get_max_pixels_for_model_type(
                        getattr(instance, "model_type", None)
                    ),
                    frame_count=frame_count,
                )
            kw = dict(
                smart_lm_instance=instance,
                prompt=prompt,
                image_paths=image_paths,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                seed=seed,
                repetition_penalty=repetition_penalty,
                use_few_shot=use_few_shot,
                min_p=min_p,
                stop_sequences=stop_sequences,
                system_prompt=system_prompt,
            )
            if vision_task:
                kw["vision_task"] = vision_task
            if llm_mode:
                kw["llm_mode"] = llm_mode
            gen = generate_sglang(**kw)
            result, raw_output = gen if isinstance(gen, tuple) else (gen, None)

        elif hasattr(instance, "is_ollama") and instance.is_ollama:
            from ..core.sml.backend_ollama_docker import generate_ollama

            if is_vision:
                from ..core.sml.vlm_detection import get_max_pixels_for_model_type

                image_paths, original_size, resized_size = _tensor_to_temp_jpegs(
                    input_image,
                    max_pixels=get_max_pixels_for_model_type(
                        getattr(instance, "model_type", None)
                    ),
                    frame_count=frame_count,
                )
            kw = dict(
                smart_lm_instance=instance,
                prompt=prompt,
                image_paths=image_paths,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                seed=seed,
                repetition_penalty=repetition_penalty,
                use_few_shot=use_few_shot,
                min_p=min_p,
                mirostat=mirostat,
                mirostat_eta=mirostat_eta,
                mirostat_tau=mirostat_tau,
                repeat_last_n=repeat_last_n,
                stop_sequences=stop_sequences,
                system_prompt=system_prompt,
            )
            if vision_task:
                kw["vision_task"] = vision_task
            if llm_mode:
                kw["llm_mode"] = llm_mode
            result, raw_output = generate_ollama(**kw)

        elif hasattr(instance, "is_llamacpp_docker") and instance.is_llamacpp_docker:
            from ..core.sml.backend_llamacpp_docker import generate_llamacpp

            if is_vision:
                from ..core.sml.vlm_detection import get_max_pixels_for_model_type

                image_paths, original_size, resized_size = _tensor_to_temp_jpegs(
                    input_image,
                    max_pixels=get_max_pixels_for_model_type(
                        getattr(instance, "model_type", None)
                    ),
                    frame_count=frame_count,
                )
            kw = dict(
                smart_lm_instance=instance,
                prompt=prompt,
                image_paths=image_paths,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                seed=seed,
                repetition_penalty=repetition_penalty,
                use_few_shot=use_few_shot,
                min_p=min_p,
                mirostat=mirostat,
                mirostat_eta=mirostat_eta,
                mirostat_tau=mirostat_tau,
                repeat_last_n=repeat_last_n,
                stop_sequences=stop_sequences,
                system_prompt=system_prompt,
            )
            if vision_task:
                kw["vision_task"] = vision_task
            if llm_mode:
                kw["llm_mode"] = llm_mode
            result, raw_output = generate_llamacpp(**kw)

        # ── Local backends ──
        elif instance.is_gguf:
            from ..core.sml.backend_gguf import generate_gguf

            effective_image = input_image if is_vision else None
            is_text_family = model_family in ("LLM (Text-Only)",)
            kw = dict(
                smart_lm_instance=instance,
                model_type="text" if is_text_family else "vision",
                image=effective_image,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                seed=seed,
                repetition_penalty=repetition_penalty,
                use_few_shot=use_few_shot,
                min_p=min_p,
                mirostat=mirostat,
                mirostat_eta=mirostat_eta,
                mirostat_tau=mirostat_tau,
                repeat_last_n=repeat_last_n,
                stop_sequences=stop_sequences,
                system_prompt=system_prompt,
            )
            if not is_text_family:
                kw["frame_count"] = frame_count
            if vision_task:
                kw["vision_task"] = vision_task
            if llm_mode:
                kw["llm_mode"] = llm_mode
            result = generate_gguf(**kw)

        else:
            # Transformers
            from ..core.sml.backend_transformers import generate_transformers

            effective_image = input_image if is_vision else None
            tf_family = _FAMILY_TF_MAP.get(model_family, "LLM")
            kw = dict(
                smart_lm_instance=instance,
                model_family=tf_family,
                image=effective_image,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                seed=seed,
                repetition_penalty=repetition_penalty,
                context_size=context_size,
                use_few_shot=use_few_shot,
                system_prompt=system_prompt,
            )
            if is_vision:
                kw["num_beams"] = num_beams
                kw["do_sample"] = do_sample
                kw["frame_count"] = frame_count
            if vision_task:
                kw["vision_task"] = vision_task
            if llm_mode:
                kw["llm_mode"] = llm_mode
                kw["instruction_template"] = ""
            result, data = generate_transformers(**kw)
            raw_output = data.get("raw_output", result) if data else result
    finally:
        _cleanup_temp_files(image_paths)

    return result, raw_output, data, original_size, resized_size


# ============================================================================
# Per-Family Generation Router
# ============================================================================


def _generate_for_family(
    *,
    model_family,
    instance,
    task_name,
    user_prompt,
    input_image,
    max_tokens,
    temperature,
    top_p,
    top_k,
    num_beams,
    do_sample,
    seed,
    repetition_penalty,
    context_size,
    frame_count,
    use_few_shot=True,
    min_p=0.0,
    mirostat=0,
    mirostat_eta=0.1,
    mirostat_tau=5.0,
    repeat_last_n=64,
    stop_sequences=None,
):
    # Dispatch to correct generation path based on model family.
    # Returns (result, data).
    result = ""
    data = {}

    if model_family == "Florence":
        from ..core.sml.backend_transformers import generate_transformers

        # Florence uses florence_id as prompt token
        task_obj = TASK_BY_NAME.get(task_name)
        if not task_obj or not task_obj.florence_id:
            raise ValueError(
                f"Task '{task_name}' is not supported by Florence-2 (no florence_id mapping)"
            )
        florence_prompt = task_obj.florence_id

        # Florence text input: use user_prompt for detection-like tasks
        florence_text = user_prompt or ""

        if (
            input_image is not None
            and input_image.dim() == 4
            and input_image.shape[0] > 1
        ):
            log.warning(
                "Florence-2",
                f"Video not supported ({input_image.shape[0]} frames), using first frame only",
            )

        result, data = generate_transformers(
            smart_lm_instance=instance,
            model_family="Florence2",
            image=input_image,
            prompt=florence_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            num_beams=num_beams,
            do_sample=do_sample,
            seed=seed,
            repetition_penalty=repetition_penalty,
            text_input=florence_text,
            context_size=context_size,
            convert_to_bboxes=False,
        )

    elif model_family in ("Qwen", "Mistral", "LLaVA", "VLM"):
        sys_prompt, user_msg, is_text_only = _build_vlm_prompt(
            task_name, user_prompt, input_image, family=model_family
        )

        # For text-only tasks, pass llm_mode for proper few-shot + system prompt handling
        result, raw, data, _, _ = _dispatch_generate(
            instance,
            prompt=user_msg,
            input_image=input_image,
            is_text_only_task=is_text_only,
            system_prompt=sys_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            seed=seed,
            repetition_penalty=repetition_penalty,
            num_beams=num_beams,
            do_sample=do_sample,
            model_family=model_family,
            task_name=task_name,
            context_size=context_size,
            frame_count=frame_count,
            llm_mode=task_name.lower().replace(" ", "_") if is_text_only else None,
            use_few_shot=use_few_shot,
            min_p=min_p,
            mirostat=mirostat,
            mirostat_eta=mirostat_eta,
            mirostat_tau=mirostat_tau,
            repeat_last_n=repeat_last_n,
            stop_sequences=stop_sequences,
        )

    elif model_family == "LLM (Text-Only)":
        text_content = user_prompt or ""
        # Allow empty user_prompt when a system prompt is available (wired override
        # via ContextVar OR JSON-defined task system prompt) — the model will
        # respond to the system instruction alone. Required for Direct Chat with
        # connected system_prompt and no user input.
        if not text_content.strip() and not get_system_prompt(task_name):
            raise ValueError(
                "LLM requires a prompt. Wire a string into 'user_prompt' or type one."
            )

        # Replace underscores for tag conversion tasks
        if task_name in ("Tags to Natural Language", "Natural Language to Tags"):
            text_content = text_content.replace("_", " ")

        llm_mode = task_name.lower().replace(" ", "_")
        # Don't prepend system — backend handles it via llm_mode
        prompt = text_content

        result, raw, data, _, _ = _dispatch_generate(
            instance,
            prompt=prompt,
            input_image=None,
            is_text_only_task=True,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            seed=seed,
            repetition_penalty=repetition_penalty,
            model_family="LLM (Text-Only)",
            task_name=task_name,
            context_size=context_size,
            llm_mode=llm_mode,
            use_few_shot=use_few_shot,
            min_p=min_p,
            mirostat=mirostat,
            mirostat_eta=mirostat_eta,
            mirostat_tau=mirostat_tau,
            repeat_last_n=repeat_last_n,
            stop_sequences=stop_sequences,
        )
        if raw is not None and raw != result:
            data = {"raw_output": raw}

    else:
        raise ValueError(f"Unknown model family: {model_family}")

    return result, data


# ============================================================================
# Multi-Task Chaining
# ============================================================================


def _run_multi_task_chain(
    *,
    tasks_to_run,
    first_result,
    first_data,
    instance,
    model_family,
    max_tokens,
    temperature,
    top_p,
    top_k,
    num_beams,
    do_sample,
    seed,
    repetition_penalty,
    context_size,
    frame_count,
    use_few_shot=True,
    min_p=0.0,
    mirostat=0,
    mirostat_eta=0.1,
    mirostat_tau=5.0,
    repeat_last_n=64,
    stop_sequences=None,
):
    # Run tasks 2..N sequentially, chaining output → input.
    # Returns (final_result, final_data).

    # Clear GGUF state after first task
    if hasattr(instance, "is_gguf") and instance.is_gguf:
        from ..core.sml.backend_gguf import clear_gguf_state_between_tasks

        clear_gguf_state_between_tasks(instance)

    all_results = [
        {
            "step": 1,
            "task": tasks_to_run[0],
            "result": first_result,
            "data": first_data or None,
        }
    ]
    current_text = first_result

    for idx in range(1, len(tasks_to_run)):
        task_name = tasks_to_run[idx]
        log.info(
            _LOG_PREFIX, f"Multi-task step {idx + 1}/{len(tasks_to_run)}: {task_name}"
        )

        if hasattr(instance, "is_gguf") and instance.is_gguf:
            from ..core.sml.backend_gguf import clear_gguf_state_between_tasks

            clear_gguf_state_between_tasks(instance)

        if not current_text or not current_text.strip():
            log.warning(_LOG_PREFIX, f"Task {idx} returned empty, stopping chain")
            break

        chained_llm_mode = task_name.lower().replace(" ", "_")

        # Don't prepend system — backend handles system + few-shot via llm_mode
        prompt = current_text

        task_result, _, task_data, _, _ = _dispatch_generate(
            instance,
            prompt=prompt,
            input_image=None,
            is_text_only_task=True,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            seed=seed,
            repetition_penalty=repetition_penalty,
            num_beams=num_beams,
            do_sample=do_sample,
            model_family=model_family,
            task_name=task_name,
            context_size=context_size,
            frame_count=frame_count,
            llm_mode=chained_llm_mode,
            use_few_shot=use_few_shot,
            min_p=min_p,
            mirostat=mirostat,
            mirostat_eta=mirostat_eta,
            mirostat_tau=mirostat_tau,
            repeat_last_n=repeat_last_n,
            stop_sequences=stop_sequences,
        )

        all_results.append(
            {
                "step": idx + 1,
                "task": task_name,
                "result": task_result,
                "data": task_data or None,
            }
        )
        current_text = task_result

    data = {
        "multi_task": True,
        "task_count": len(all_results),
        "tasks": all_results,
        "final_result": current_text,
    }
    log.info(_LOG_PREFIX, f"Multi-task complete: {len(all_results)} tasks")
    return current_text, data


# ============================================================================
# WD14 Tagger Fast-Path
# ============================================================================


def _execute_wd14(
    *,
    repo_id,
    images,
    threshold,
    char_threshold,
    replace_underscore,
    keep_model_loaded,
    node_id=None,
    initial_title=None,
):
    # WD14 tagger — completely separate from LLM pipeline.
    import numpy as np  # type: ignore
    from PIL import Image
    from ..core.sml.backend_wd14 import tag_image, load_wd14_model, unload_wd14_model
    import comfy.utils  # type: ignore

    if images is None:
        raise ValueError("WD14 Tagger requires an image input")

    # Model name = last part of repo_id (e.g. "SmilingWolf/wd-swinv2-tagger-v3" → "wd-swinv2-tagger-v3")
    wd14_model_name = repo_id.split("/")[-1] if "/" in repo_id else repo_id

    # Auto-download if not installed
    llm_base = get_llm_models_path()
    model_dir = llm_base / wd14_model_name
    onnx_flat = llm_base / f"{wd14_model_name}.onnx"
    if not (model_dir / "model.onnx").exists() and not onnx_flat.exists():
        log.msg(_LOG_PREFIX, f"Downloading WD14 model: {wd14_model_name}")
        temp_info = {
            "repo_id": repo_id,
            "local_path": "",
            "model_family": "WD14",
            "loading_method": "WD14 Tagger",
        }
        ensure_model_path(temp_info)

    session, tags_data = load_wd14_model(wd14_model_name)

    # Flatten/normalize images list for processing
    flat_list = []

    def _process(item):
        if isinstance(item, (list, tuple)):
            for sub in item:
                _process(sub)
        elif isinstance(item, torch.Tensor):
            if item.dim() == 4:
                for i in range(item.shape[0]):
                    flat_list.append(item[i : i + 1, ...])
            elif item.dim() == 3:
                flat_list.append(item.unsqueeze(0))
        elif item is not None:
            flat_list.append(item)

    _process(images)

    def update_title(sub_status: str):
        if node_id is not None:
            try:
                from server import PromptServer

                PromptServer.instance.send_sync(
                    "eclipse/update_node_title",
                    {"node_id": node_id, "title": sub_status},
                )
            except Exception:
                pass

    def restore_title():
        if node_id is not None:
            try:
                from server import PromptServer

                PromptServer.instance.send_sync(
                    "eclipse/update_node_title",
                    {"node_id": node_id, "title": initial_title},
                )
            except Exception:
                pass

    results = []
    pbar = comfy.utils.ProgressBar(len(flat_list))

    try:
        for batch_number, image in enumerate(flat_list):
            log.msg(
                _LOG_PREFIX,
                f"Tagging image {batch_number + 1}/{len(flat_list)}",
            )
            if node_id is not None:
                update_title(f"Tagging {batch_number + 1}/{len(flat_list)}")

            if hasattr(image, "shape") and image.ndim == 4 and image.shape[0] == 1:
                image = image.squeeze(0)
            img_np = (image.cpu().numpy() * 255).astype(np.uint8)
            pil_img = Image.fromarray(img_np)
            tags = tag_image(
                pil_img,
                session,
                tags_data,
                threshold=threshold,
                char_threshold=char_threshold,
                replace_underscore=replace_underscore,
                trailing_comma=False,
            )
            results.append(tags)
            pbar.update(1)
    finally:
        if initial_title and node_id is not None:
            restore_title()

    if not keep_model_loaded:
        unload_wd14_model()

    return io.NodeOutput(flat_list, results)


# ============================================================================
# Model Cleanup
# ============================================================================


def _cleanup_model(*, loading_method, keep_model_loaded, model_path, instance):
    # Handle Docker auto-stop and model VRAM cleanup.

    # Docker auto-stop: stop the backing container when Keep Loaded is OFF.
    if not keep_model_loaded:
        if loading_method == "vLLM (Docker)":
            from ..core.sml import backend_vllm_docker

            backend_vllm_docker.stop_vllm_container()
        elif hasattr(instance, "is_sglang") and instance.is_sglang:
            from ..core.sml import backend_sglang_docker

            backend_sglang_docker.stop_sglang_container()
        elif loading_method == "Ollama (Docker)":
            from ..core.sml import backend_ollama_docker

            backend_ollama_docker.stop_ollama_container()
        elif loading_method == "llama.cpp (Docker)":
            from ..core.sml import backend_llamacpp_docker

            backend_llamacpp_docker.stop_llamacpp_container()

    if keep_model_loaded:
        return

    is_gguf = loading_method == "GGUF (llama-cpp-python)"
    is_transformers = loading_method.lower() == "transformers"
    is_vllm_native = loading_method == "vLLM (Native)"

    if is_vllm_native:
        from ..core.sml import backend_vllm_native

        backend_vllm_native.unload_vllm(instance, model_path)

    if is_gguf:
        from ..core.sml.backend_gguf import cleanup_chat_handler_vision
        from ..core.sml.model_cache import clear_gguf_cache, is_gguf_cache_empty

        if not is_gguf_cache_empty():
            clear_gguf_cache()
        else:
            actual = instance.model if hasattr(instance, "model") else instance
            if actual is not None:
                for attr in ("_eclipse_chat_handler", "chat_handler"):
                    handler = getattr(actual, attr, None)
                    if handler is not None:
                        cleanup_chat_handler_vision(handler)
                        setattr(actual, attr, None)
                if hasattr(actual, "close") and callable(actual.close):
                    actual.close()
        if hasattr(instance, "model"):
            instance.model = None
        if hasattr(instance, "chat_handler_ref"):
            instance.chat_handler_ref = None

    if is_transformers:
        from ..core.sml.model_cache import (
            clear_transformers_cache,
            is_transformers_cache_empty,
        )

        if not is_transformers_cache_empty():
            clear_transformers_cache()
        else:
            actual = instance.model if hasattr(instance, "model") else instance
            if actual is not None:
                if hasattr(actual, "eval"):
                    actual.eval()
                if hasattr(actual, "zero_grad"):
                    try:
                        actual.zero_grad(set_to_none=True)
                    except Exception:
                        pass
        if hasattr(instance, "model"):
            instance.model = None
        if hasattr(instance, "processor"):
            instance.processor = None

    for _ in range(3):
        gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        try:
            torch.cuda.ipc_collect()
        except Exception:
            pass


# ============================================================================
# Model Path Resolution
# ============================================================================


def _resolve_model_path(entry, quantization=None):
    # Resolve local model path from a registry entry.
    # Returns (model_path, needs_download).
    from pathlib import Path
    import folder_paths  # type: ignore

    backend = entry["backend"]
    repo_id = entry.get("repo_id", "")
    name = entry["name"]

    # Docker / API backends — use model identifier, no local path
    if backend == "ollama":
        return repo_id, False
    if backend in ("vllm", "sglang"):
        return repo_id, False

    # Local backends — check LLM folder
    llm_base = get_llm_models_path()

    if backend == "gguf":
        file_pattern = entry.get("file_pattern", "")
        repo_folder = repo_id.split("/")[-1] if "/" in repo_id else name

        # Build candidate filenames for the selected quantization.
        # Multiple naming conventions exist across HF repos:
        #   file_pattern:  "Model-Name-{quant}.gguf"  (registry-defined, authoritative)
        #   dash variant:  "Model-Name-Q4_K_M.gguf"   (common convention)
        #   dot variant:   "Model-Name.Q4_K_M.gguf"   (mradermacher repos)
        gguf_filenames = []
        if file_pattern and quantization:
            gguf_filenames.append(file_pattern.replace("{quant}", quantization))
        if quantization:
            for variant in (
                f"{name}-{quantization}.gguf",
                f"{name}.{quantization}.gguf",
            ):
                if variant not in gguf_filenames:
                    gguf_filenames.append(variant)

        # Candidate folder names, in priority order:
        #   1. repo folder  (e.g. Lexi-Llama-3-8B-Uncensored-GGUF)
        #   2. model name   (e.g. Lexi-Llama-3-8B-Uncensored)
        #   3. name + quant (e.g. Lexi-Llama-3-8B-Uncensored-Q4_K_M) — old v3 layout
        seen = set()
        candidates = []
        for c in [repo_folder, name] + (
            [f"{name}-{quantization}"] if quantization else []
        ):
            if c not in seen:
                seen.add(c)
                candidates.append(c)

        # Pass 1: exact filename match across all candidate folders
        for folder_name in candidates:
            candidate_dir = llm_base / folder_name
            if not candidate_dir.exists():
                continue
            for fn in gguf_filenames:
                if (candidate_dir / fn).is_file():
                    return str(candidate_dir / fn), False

        # Pass 2: single .gguf in a candidate folder (unambiguous)
        for folder_name in candidates:
            candidate_dir = llm_base / folder_name
            if not candidate_dir.exists():
                continue
            gguf_files = list(candidate_dir.glob("*.gguf"))
            if len(gguf_files) == 1:
                return str(gguf_files[0]), False

        # Pass 3: flat file in LLM base directory
        for fn in gguf_filenames:
            if (llm_base / fn).is_file():
                return str(llm_base / fn), False

        # Not found — download needed
        return str(llm_base / repo_folder), True

    if backend == "wd14":
        wd14_name = repo_id.split("/")[-1] if "/" in repo_id else name
        model_dir = llm_base / wd14_name
        if (model_dir / "model.onnx").exists():
            return str(model_dir), False
        onnx_flat = llm_base / f"{wd14_name}.onnx"
        if onnx_flat.exists():
            return str(llm_base), False
        return str(model_dir), True

    # Transformers: check model folder
    model_dir = llm_base / name
    if model_dir.exists():
        is_complete, _ = check_model_completeness(model_dir, repo_id=repo_id)
        if is_complete:
            return str(model_dir), False
        else:
            log.warning(
                _LOG_PREFIX, f"Model folder '{model_dir}' exists but is incomplete."
            )

    # Check under repo_id last component
    if "/" in repo_id:
        alt_dir = llm_base / repo_id.split("/")[-1]
        if alt_dir.exists():
            is_complete, _ = check_model_completeness(alt_dir, repo_id=repo_id)
            if is_complete:
                return str(alt_dir), False
            else:
                log.warning(
                    _LOG_PREFIX, f"Model folder '{alt_dir}' exists but is incomplete."
                )

    # Also check models/florence2/ for Florence models
    if entry.get("family") == "Florence":
        models_dir = Path(folder_paths.models_dir)
        florence_dir = models_dir / "florence2" / name
        if florence_dir.exists():
            is_complete, _ = check_model_completeness(florence_dir, repo_id=repo_id)
            if is_complete:
                return str(florence_dir), False
            else:
                log.warning(
                    _LOG_PREFIX,
                    f"Model folder '{florence_dir}' exists but is incomplete.",
                )

    return str(model_dir), True


def _get_model_source(entry):
    # Determine download source: per-entry "source" override → global defaults → "huggingface".
    source = entry.get("source")
    if not source:
        source = load_defaults().get("model_source", "huggingface")
    return source.lower()


def _get_auth_token(source):
    # Get authentication token for the download source.
    import os

    if source == "modelscope":
        token = os.environ.get("MODELSCOPE_API_TOKEN")
        if not token:
            config_token = get_config_value("modelscope_token", "")
            if config_token and config_token.strip():
                token = config_token.strip()
        return token
    # HuggingFace
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if not token:
        config_token = get_config_value("hf_token", "")
        if config_token and config_token.strip():
            token = config_token.strip()
    return token


def _download_single_file(
    repo_id, filename, local_dir, source="huggingface", token=None
):
    # Download a single file from HuggingFace or ModelScope with ComfyUI progress bar.
    import os

    ComfyTqdm = make_comfy_tqdm_class(filename, log_prefix=_LOG_PREFIX)

    if source == "modelscope":
        try:
            from modelscope.hub.file_download import model_file_download  # type: ignore
        except ImportError:
            raise RuntimeError(
                "ModelScope support requires the 'modelscope' package. "
                "Install with: pip install modelscope"
            ) from None

        dl_kwargs = {
            "model_id": repo_id,
            "file_path": filename,
            "local_dir": local_dir,
        }
        if token:
            dl_kwargs["token"] = token
        model_file_download(**dl_kwargs)
    else:
        from huggingface_hub import hf_hub_download  # type: ignore

        if os.environ.get("HF_XET_HIGH_PERFORMANCE") == "1":
            log.msg(
                _LOG_PREFIX,
                "Using Xet high-performance transfer for accelerated download",
            )

        import inspect

        dl_kwargs = {
            "repo_id": repo_id,
            "filename": filename,
            "local_dir": local_dir,
            "tqdm_class": ComfyTqdm,
        }
        if "local_dir_use_symlinks" in inspect.signature(hf_hub_download).parameters:
            dl_kwargs["local_dir_use_symlinks"] = False
        if token:
            dl_kwargs["token"] = token
        hf_hub_download(**dl_kwargs)


def _ensure_downloaded(entry, quantization=None):
    # Download model if not locally available.
    # For GGUF: downloads only the single file matching the quantization.
    # For others: delegates to ensure_model_path (snapshot_download).
    # Returns the local model_path string.
    backend = entry["backend"]
    repo_id = entry.get("repo_id", "")

    # ── GGUF: single-file download ───────────────────────────
    if backend == "gguf" and quantization and repo_id:
        file_pattern = entry.get("file_pattern", "")
        if not file_pattern:
            log.warning(
                _LOG_PREFIX, "GGUF entry missing file_pattern, falling back to snapshot"
            )
        else:
            filename = file_pattern.replace("{quant}", quantization)
            repo_folder = repo_id.split("/")[-1] if "/" in repo_id else entry["name"]
            target_dir = get_llm_models_path() / repo_folder
            target_file = target_dir / filename

            if target_file.exists():
                return str(target_file)

            source = _get_model_source(entry)
            token = _get_auth_token(source)
            source_label = "ModelScope" if source == "modelscope" else "HuggingFace"
            log.msg(
                _LOG_PREFIX, f"Downloading GGUF file from {source_label}: {filename}"
            )
            if token:
                log.debug(
                    _LOG_PREFIX,
                    f"Using {source_label} token for authenticated download",
                )

            try:
                _download_single_file(repo_id, filename, str(target_dir), source, token)
            except Exception as e:
                err_name = type(e).__name__
                if (
                    "NotExist" in err_name
                    or "EntryNotFound" in err_name
                    or "404" in str(e)
                ):
                    raise RuntimeError(
                        f"File '{filename}' not found in repo '{repo_id}' ({source_label}). "
                        f"This quantization ({quantization}) may not be available for this model."
                    ) from None
                if "RepositoryNotFound" in err_name or "401" in str(e):
                    raise RuntimeError(
                        f"Repository '{repo_id}' not found or access denied ({source_label}). "
                        f"Check the repo_id or set the appropriate auth token."
                    ) from None
                raise RuntimeError(
                    f"Failed to download '{filename}' from '{repo_id}' ({source_label}): {err_name}: {e}"
                ) from None

            # Also download mmproj if the model has one
            mmproj = entry.get("mmproj")
            if mmproj and not (target_dir / mmproj).exists():
                log.msg(
                    _LOG_PREFIX, f"Downloading mmproj from {source_label}: {mmproj}"
                )
                try:
                    _download_single_file(
                        repo_id, mmproj, str(target_dir), source, token
                    )
                except Exception as e:
                    err_name = type(e).__name__
                    if (
                        "NotExist" in err_name
                        or "EntryNotFound" in err_name
                        or "404" in str(e)
                    ):
                        raise RuntimeError(
                            f"Vision projector '{mmproj}' not found in repo '{repo_id}' ({source_label}). "
                            f"Check the mmproj filename in the registry."
                        ) from None
                    raise RuntimeError(
                        f"Failed to download mmproj '{mmproj}' from '{repo_id}' ({source_label}): {err_name}: {e}"
                    ) from None

            log.msg(_LOG_PREFIX, f"Downloaded to {target_dir}")

            # Verify integrity of downloaded files
            from pathlib import Path

            retries_val = get_config_value("retry_download_attempts", 2)
            max_retries = int(retries_val) if retries_val is not None else 2

            for file_to_verify, hf_name, label in [
                (target_file, filename, "GGUF model"),
                (target_dir / mmproj if mmproj else None, mmproj, "mmproj"),
            ]:
                if file_to_verify is None or not Path(file_to_verify).exists():
                    continue
                for attempt in range(max_retries + 1):
                    result = verify_model_integrity(
                        Path(file_to_verify),
                        repo_id,
                        hf_filename=hf_name,
                        return_details=True,
                    )
                    is_success = (
                        result.success if hasattr(result, "success") else result
                    )
                    if is_success:
                        break
                    if attempt < max_retries:
                        log.warning(
                            _LOG_PREFIX,
                            f"{label} verification failed, re-downloading (attempt {attempt + 2}/{max_retries + 1})",
                        )
                        try:
                            Path(file_to_verify).unlink(missing_ok=True)
                            sha_file = (
                                Path(file_to_verify).parent
                                / f"{Path(file_to_verify).name}.sha256"
                            )
                            sha_file.unlink(missing_ok=True)
                            _download_single_file(
                                repo_id, hf_name, str(target_dir), source, token
                            )
                        except Exception as e:
                            log.error(
                                _LOG_PREFIX, f"Re-download of {label} failed: {e}"
                            )
                            break
                    else:
                        log.error(
                            _LOG_PREFIX,
                            f"{label} integrity verification failed after {max_retries + 1} attempts",
                        )

            return str(target_file)

    # ── Non-GGUF: full snapshot via ensure_model_path ────────
    loading_method = _BACKEND_TO_METHOD.get(backend, "Transformers")
    family_str = entry.get("family", "")
    family_exec = _FAMILY_TO_EXEC.get(family_str, family_str)

    temp_info = {
        "repo_id": repo_id,
        "local_path": "",
        "model_family": family_exec,
        "loading_method": loading_method,
    }

    if backend == "gguf" and entry.get("mmproj"):
        temp_info["mmproj_url"] = ""
        temp_info["mmproj_path"] = ""

    model_path, _, _ = ensure_model_path(temp_info)
    return str(model_path)


# ============================================================================
# Node Class
# ============================================================================


class RvLoader_SmartModelLoader_LM(io.ComfyNode):

    @classmethod
    def define_schema(cls):
        models = get_model_list()
        first_model = next(
            (m for m in models if not is_model_separator(m)),
            models[0] if models else "",
        )
        defaults = load_defaults()

        # Task list — full superset for schema validation (JS filters at runtime)
        task_names = get_task_names(has_vision=True, include_all_families=True)
        task_names_none = ["None"] + task_names

        # GGUF quantization list from defaults (user-editable, JS refreshes on model change)
        quant_placeholders = defaults.get(
            "quantizations", ["Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"]
        )

        return io.Schema(
            node_id="Smart LM Loader [Eclipse]",
            display_name="Smart LM Loader",
            search_aliases=["Smart Model Loader", "SmartLML", "LLM", "VLM"],
            category=CATEGORY.MAIN.value + CATEGORY.LOADER.value,
            description="Registry-based model loader with unified model dropdown, "
            "multi-task chaining, and WD14 tagger support.",
            inputs=[
                # ── Model selection ───────────────────────────────────
                io.Combo.Input(
                    "model",
                    options=models,
                    default=first_model,
                    tooltip="Choose the language, vision, or tagging model to load.\n"
                    "Suffixes indicate the backend engine:\n"
                    "• (no suffix) - Hugging Face Transformers (local, PyTorch)\n"
                    "• -GGUF - llama.cpp / GGUF engine (high performance local CPU/GPU hybrid)\n"
                    "• -vLLM / -SGLang / -Ollama - Running via Docker containers or external servers\n"
                    "• -WD14 - WD14 tagger for anime-style image tagging.",
                ),
                io.Combo.Input(
                    "quantization",
                    options=quant_placeholders,
                    default="Q4_K_M",
                    tooltip="Choose GGUF quantization precision. Lower bits (e.g. Q4_K_M) use less VRAM but lose accuracy. "
                    "Higher bits (e.g. Q8_0) are more accurate but demand more VRAM. Only applies to GGUF models.",
                ),
                # Mode bar (JS DOM widget inserts here — non-serialized)
                # ── Tasks ─────────────────────────────────────────────
                io.Combo.Input(
                    "task",
                    options=task_names,
                    default="Detailed Description",
                    tooltip="The primary task to run. Determines the prompt template, system instructions, and training examples. "
                    "Note: Vision tasks (VLM) require feeding an image into the 'images' input slot.",
                ),
                io.Combo.Input(
                    "task_2",
                    options=task_names_none,
                    default="None",
                    tooltip="Optional second task in a multi-task chain. Runs in sequence using the output of the first task as context.",
                ),
                io.Combo.Input(
                    "task_3",
                    options=task_names_none,
                    default="None",
                    tooltip="Optional third task in a multi-task chain. Runs in sequence using the output of the second task as context.",
                ),
                io.Combo.Input(
                    "task_4",
                    options=task_names_none,
                    default="None",
                    tooltip="Optional fourth task in a multi-task chain. Runs in sequence using the output of the third task as context.",
                ),
                # ── Prompt / context ──────────────────────────────────
                io.String.Input(
                    "user_prompt",
                    default="",
                    multiline=True,
                    tooltip="Your custom instructions, question, or prompt for the model. "
                    "Type text directly or wire an upstream string. Combined with the selected task prompt.",
                ),
                io.Int.Input(
                    "context_size",
                    default=int(defaults.get("context_size", 8192)),
                    min=512,
                    max=131072,
                    step=512,
                    tooltip="Maximum context window (tokens) allocated for the model. "
                    "Determines how much history, text, and image tokens the model can process at once. Larger sizes use more VRAM.",
                ),
                io.Int.Input(
                    "max_tokens",
                    default=int(defaults.get("image_max_tokens", 2048)),
                    min=1,
                    max=32768,
                    step=1,
                    tooltip="The maximum number of tokens the model is allowed to generate in its response. "
                    "Lower limits prevent long-winded answers and save generation time.",
                ),
                io.Combo.Input(
                    "attention_mode",
                    options=["auto", "flash_attention_2", "sdpa", "eager"],
                    default="auto",
                    tooltip="Attention mechanism for Hugging Face Transformers:\n"
                    "• auto: Automatically selects the best available engine\n"
                    "• flash_attention_2: Fastest, uses the least VRAM (requires Ampere/Ada GPU & flash-attn installed)\n"
                    "• sdpa: PyTorch Scaled Dot-Product Attention (good default)\n"
                    "• eager: Traditional PyTorch attention (fallback, uses most VRAM)",
                ),
                # ── Advanced sampling (hidden by default) ─────────────
                io.Combo.Input(
                    "device",
                    options=["cuda", "cpu", "mps"],
                    default=str(defaults.get("device", "cuda")),
                    tooltip="Hardware device to load the model onto. "
                    "Use 'cuda' for NVIDIA GPUs, 'mps' for Apple Silicon, or 'cpu' (slow, fallback).",
                ),
                io.Float.Input(
                    "temperature",
                    default=float(defaults.get("temperature", 0.7)),
                    min=0.1,
                    max=2.0,
                    step=0.1,
                    tooltip="Controls randomness: higher values (e.g. 0.8+) make output more creative/diverse; "
                    "lower values (e.g. 0.2) make it more deterministic and focused.",
                ),
                io.Float.Input(
                    "top_p",
                    default=float(defaults.get("top_p", 0.9)),
                    min=0.1,
                    max=1.0,
                    step=0.05,
                    tooltip="Nucleus sampling: limits generation to the cumulative probability tokens (e.g. 0.9 keeps top 90% likely words). "
                    "Filters out low-probability gibberish.",
                ),
                io.Int.Input(
                    "top_k",
                    default=int(defaults.get("top_k", 50)),
                    min=0,
                    max=1000,
                    step=1,
                    tooltip="Limits generation to the top K most likely next words. "
                    "Lower values (e.g. 40) make output more focused; 0 disables it.",
                ),
                io.Int.Input(
                    "num_beams",
                    default=int(defaults.get("num_beams", 1)),
                    min=1,
                    max=10,
                    step=1,
                    tooltip="Number of parallel paths explored during beam search. "
                    "Values > 1 produce higher quality text but are significantly slower. Set to 1 for standard sampling.",
                ),
                io.Boolean.Input(
                    "do_sample",
                    default=bool(defaults.get("do_sample", True)),
                    tooltip="When enabled, uses probabilistic sampling (temperature, top_p, top_k). "
                    "When disabled, uses greedy decoding (always picking the most likely next word, ignoring temperature).",
                ),
                io.Float.Input(
                    "repetition_penalty",
                    default=float(defaults.get("repetition_penalty", 1.0)),
                    min=1.0,
                    max=2.0,
                    step=0.1,
                    tooltip="Penalizes repeating the same phrases or words. "
                    "Values > 1.0 (e.g. 1.1 or 1.2) help reduce loops and repetitive output.",
                ),
                io.Int.Input(
                    "frame_count",
                    default=int(defaults.get("frame_count", 8)),
                    min=1,
                    max=100,
                    step=1,
                    tooltip="QwenVL only — number of frames to extract and feed to QwenVL when processing video inputs. "
                    "More frames provide more temporal detail but require significantly more VRAM.",
                ),
                io.Boolean.Input(
                    "use_torch_compile",
                    default=bool(defaults.get("use_torch_compile", False)),
                    tooltip="JIT compiles the model using PyTorch 2.x compile. "
                    "Increases initial startup/load time (~1-3 minutes first run) but speeds up subsequent inference runs.",
                ),
                io.Float.Input(
                    "min_p",
                    default=float(defaults.get("min_p", 0.0)),
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Minimum probability threshold relative to the top token. "
                    "For example, min_p=0.05 filters out any token with less than 5% probability of the most likely token. "
                    "Supported: GGUF, Ollama, vLLM, SGLang, llama.cpp.",
                ),
                io.Combo.Input(
                    "mirostat",
                    options=["0 (off)", "1 (Mirostat)", "2 (Mirostat 2.0)"],
                    default=str(defaults.get("mirostat", "0 (off)")),
                    tooltip="Mirostat sampling dynamically adjusts temperature to maintain target text quality/entropy. "
                    "Supported: GGUF, Ollama, llama.cpp.",
                ),
                io.Float.Input(
                    "mirostat_eta",
                    default=float(defaults.get("mirostat_eta", 0.1)),
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Mirostat learning rate (eta). Controls how rapidly the temperature adjusts. Default is 0.1.",
                ),
                io.Float.Input(
                    "mirostat_tau",
                    default=float(defaults.get("mirostat_tau", 5.0)),
                    min=0.0,
                    max=10.0,
                    step=0.1,
                    tooltip="Mirostat target entropy (tau). Controls target complexity/perplexity of generated text. Default is 5.0.",
                ),
                io.Int.Input(
                    "repeat_last_n",
                    default=int(defaults.get("repeat_last_n", 64)),
                    min=-1,
                    max=8192,
                    step=1,
                    tooltip="Lookback window size (number of recent tokens) to evaluate for the repetition penalty. "
                    "Set to -1 to use full context, 0 to disable. Supported: GGUF, Ollama, llama.cpp.",
                ),
                io.String.Input(
                    "stop_sequences",
                    default=str(defaults.get("stop_sequences", "")),
                    tooltip="Custom strings (separated by commas or newlines) that act as stopping triggers. "
                    "The model stops generating immediately when any of these strings are output.",
                ),
                # ── WD14 widgets (hidden unless WD14 model) ──────────
                io.Float.Input(
                    "threshold",
                    default=0.35,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="WD14 Tagger only — minimum confidence threshold (0.0 to 1.0) for general tags (e.g. clothing, background) to be included in the output.",
                ),
                io.Float.Input(
                    "char_threshold",
                    default=0.85,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="WD14 Tagger only — minimum confidence threshold (0.0 to 1.0) for character/IP tags to be included.",
                ),

                io.Boolean.Input(
                    "replace_underscore",
                    default=True,
                    tooltip="WD14 Tagger only — replaces underscores in tags with spaces (e.g. 'blue_eyes' becomes 'blue eyes').",
                ),
                # ── Seed (rendered last among visible widgets; JS adds three buttons after) ──
                io.Int.Input(
                    "seed",
                    default=-1,
                    min=-3,
                    max=2**64 - 1,
                    step=1,
                    tooltip="Controls generation reproducibility. Use specific values for deterministic output:\n"
                    "• -1: Randomize the seed on every execution\n"
                    "• -2: Increment the seed by 1 after each run\n"
                    "• -3: Decrement the seed by 1 after each run",
                ),
                # ── Hidden backing widgets (mode bar syncs to these) ──
                io.Boolean.Input(
                    "memory_cleanup", default=True, label_on="ON", label_off="OFF"
                ),
                io.Boolean.Input(
                    "keep_model_loaded", default=False, label_on="ON", label_off="OFF"
                ),
                io.Boolean.Input(
                    "multi_task_mode", default=False, label_on="ON", label_off="OFF"
                ),
                io.Boolean.Input(
                    "show_advanced", default=False, label_on="ON", label_off="OFF"
                ),
                io.Boolean.Input(
                    "use_advanced",
                    default=True,
                    label_on="ON",
                    label_off="OFF",
                    tooltip="Apply advanced sampling parameters (temperature, top_p, top_k, etc.) to the model request. "
                    "When disabled, safe/conservative defaults are used regardless of widget settings.",
                ),
                io.Boolean.Input(
                    "use_few_shot_training",
                    default=True,
                    label_on="ON",
                    label_off="OFF",
                    tooltip="Append task-specific few-shot training examples (prompt/response pairs) to the context. "
                    "Helps the model follow formatting instructions but increases token count.",
                ),
                io.Boolean.Input(
                    "trust_remote_code",
                    default=False,
                    label_on="ON",
                    label_off="OFF",
                    tooltip="⚠ SECURITY: Permits execution of custom modeling code from the Hugging Face model repository. "
                    "ONLY enable this for trusted models. Pre-approved models (e.g. Florence-2, Ministral) are automatically trusted.",
                ),
                # ── Connection slots ──────────────────────────────────
                io.Image.Input(
                    "images",
                    optional=True,
                    tooltip="Input image or video batch. Required for vision tasks (VLM) and WD14 tagging. Ignored for text-only LLMs.",
                ),
                io.String.Input(
                    "system_prompt",
                    optional=True,
                    force_input=True,
                    tooltip="Override system instructions. Connecting this slot automatically switches the task to 'Direct Chat' and bypasses default task prompt templates.",
                ),
            ],
            is_input_list=True,
            outputs=[
                io.Image.Output(
                    "image", is_output_list=True, tooltip="Passthrough of input images."
                ),
                io.String.Output(
                    "text", is_output_list=True, tooltip="Generated text or tags."
                ),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo, io.Hidden.unique_id],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        seed = kwargs.get("seed", 0)
        if seed in (-1, -2, -3):
            return _new_random_seed()
        # Include system_prompt so upstream changes trigger re-execution
        system_prompt = kwargs.get("system_prompt")
        if system_prompt:
            return f"{seed}|{system_prompt}"
        return seed

    @classmethod
    def execute(
        cls,
        model,
        quantization,
        task,
        task_2,
        task_3,
        task_4,
        user_prompt,
        max_tokens,
        context_size,
        attention_mode,
        seed,
        # Advanced
        device,
        temperature,
        top_p,
        top_k,
        num_beams,
        do_sample,
        repetition_penalty,
        frame_count,
        use_torch_compile,
        # WD14
        threshold,
        char_threshold,
        replace_underscore,
        # Backing
        multi_task_mode,
        memory_cleanup,
        keep_model_loaded,
        show_advanced,
        use_advanced,
        use_few_shot_training,
        trust_remote_code,
        # Advanced sampling extras (appended widgets)
        min_p,
        mirostat,
        mirostat_eta,
        mirostat_tau,
        repeat_last_n,
        stop_sequences,
        # Optional connections
        images=None,
        system_prompt=None,
    ):
        start_time = time.time()

        # Extract unique_id (node ID) from hidden inputs
        uid = getattr(cls.hidden, "unique_id", None)
        node_id = None
        if uid is not None:
            if isinstance(uid, list):
                if len(uid) > 0:
                    try:
                        node_id = int(uid[0])
                    except Exception:
                        node_id = uid[0]
            else:
                try:
                    node_id = int(uid)
                except Exception:
                    node_id = uid

        initial_title = cls.define_schema().display_name
        extra_pnginfo = getattr(cls.hidden, "extra_pnginfo", None)
        if node_id is not None and extra_pnginfo is not None:
            pnginfo = (
                extra_pnginfo[0] if isinstance(extra_pnginfo, list) else extra_pnginfo
            )
            if isinstance(pnginfo, dict) and "workflow" in pnginfo:
                nodes = pnginfo["workflow"].get("nodes", [])
                node_info = next(
                    (n for n in nodes if str(n.get("id")) == str(node_id)), None
                )
                if node_info:
                    initial_title = node_info.get("title") or initial_title

        # Safeguard: if the canvas title was left stuck as a progress state (e.g. from an interrupted or errored previous run),
        # discard it and fallback to the default clean name derived from the node schema.
        if initial_title:
            temp_statuses = ["%", "Scanning", "Probing", "Loading", "Resizing", "Tagging"]
            is_temp = any(status in initial_title for status in temp_statuses) or initial_title.endswith("...")
            if is_temp:
                initial_title = cls.define_schema().display_name

        def update_title(sub_status: str):
            if node_id is not None:
                try:
                    from server import PromptServer

                    PromptServer.instance.send_sync(
                        "eclipse/update_node_title",
                        {"node_id": node_id, "title": sub_status},
                    )
                except Exception:
                    pass

        def restore_title():
            if node_id is not None:
                try:
                    from server import PromptServer

                    PromptServer.instance.send_sync(
                        "eclipse/update_node_title",
                        {"node_id": node_id, "title": initial_title},
                    )
                except Exception:
                    pass



        model = _unwrap_scalar(model, "")
        quantization = _unwrap_scalar(quantization, "")
        task = _unwrap_scalar(task, "")
        task_2 = _unwrap_scalar(task_2, "None")
        task_3 = _unwrap_scalar(task_3, "None")
        task_4 = _unwrap_scalar(task_4, "None")
        max_tokens = _unwrap_scalar(max_tokens, 2048)
        context_size = _unwrap_scalar(context_size, 8192)
        attention_mode = _unwrap_scalar(attention_mode, "auto")
        seed = _unwrap_scalar(seed, -1)
        device = _unwrap_scalar(device, "cuda")
        temperature = _unwrap_scalar(temperature, 0.7)
        top_p = _unwrap_scalar(top_p, 0.9)
        top_k = _unwrap_scalar(top_k, 50)
        num_beams = _unwrap_scalar(num_beams, 1)
        do_sample = _unwrap_scalar(do_sample, True)
        repetition_penalty = _unwrap_scalar(repetition_penalty, 1.0)
        frame_count = _unwrap_scalar(frame_count, 8)
        use_torch_compile = _unwrap_scalar(use_torch_compile, False)
        threshold = _unwrap_scalar(threshold, 0.35)
        char_threshold = _unwrap_scalar(char_threshold, 0.85)
        replace_underscore = _unwrap_scalar(replace_underscore, True)
        multi_task_mode = _unwrap_scalar(multi_task_mode, False)
        memory_cleanup = _unwrap_scalar(memory_cleanup, True)
        keep_model_loaded = _unwrap_scalar(keep_model_loaded, False)
        show_advanced = _unwrap_scalar(show_advanced, False)
        use_advanced = _unwrap_scalar(use_advanced, True)
        use_few_shot_training = _unwrap_scalar(use_few_shot_training, True)
        trust_remote_code = _unwrap_scalar(trust_remote_code, False)
        min_p = _unwrap_scalar(min_p, 0.0)
        mirostat = _unwrap_scalar(mirostat, "0 (off)")
        mirostat_eta = _unwrap_scalar(mirostat_eta, 0.1)
        mirostat_tau = _unwrap_scalar(mirostat_tau, 5.0)
        repeat_last_n = _unwrap_scalar(repeat_last_n, 64)
        stop_sequences = _unwrap_scalar(stop_sequences, "")
        system_prompt = _unwrap_scalar(system_prompt, None)

        # Normalize user_prompt to a list of strings
        if user_prompt is None:
            user_prompts = [""]
        elif isinstance(user_prompt, list):
            user_prompts = [str(p) if p is not None else "" for p in user_prompt]
        else:
            user_prompts = [str(user_prompt)]

        # Flatten/normalize input images to a list of tensors
        flat_images = []

        def _process_image(item):
            if isinstance(item, (list, tuple)):
                for sub in item:
                    _process_image(sub)
            elif isinstance(item, torch.Tensor):
                # If vision model is video task, keep the 4D batch as a single video input
                is_video_task = (
                    "video" in str(task).lower() or "timeline" in str(task).lower()
                )
                if item.dim() == 4:
                    if is_video_task:
                        flat_images.append(item)
                    else:
                        for i in range(item.shape[0]):
                            flat_images.append(item[i : i + 1, ...])
                elif item.dim() == 3:
                    flat_images.append(item.unsqueeze(0))
            elif item is not None:
                flat_images.append(item)

        if images is not None:
            _process_image(images)

        # ── Use Advanced gate ──────────────────────────────────
        # When OFF, override sampling params with conservative defaults (matches
        # widget defaults & backend function defaults). Keeps the "set and forget"
        # widget-hidden flow safe — values aren't silently sent if the chip is OFF.
        # Not gated: seed, max_tokens, context_size, device, frame_count,
        # use_torch_compile, attention_mode, WD14 params.
        if not use_advanced:
            temperature = 0.7
            top_p = 0.9
            top_k = 50
            num_beams = 1
            do_sample = True
            repetition_penalty = 1.0
            min_p = 0.0
            mirostat = "0 (off)"
            mirostat_eta = 0.1
            mirostat_tau = 5.0
            repeat_last_n = 64
            stop_sequences = ""

        # Parse mirostat combo "N (label)" → int
        try:
            mirostat_int = int(str(mirostat).split(" ")[0]) if mirostat else 0
        except (ValueError, AttributeError):
            mirostat_int = 0

        # Parse stop_sequences multiline → list[str] (None when empty so backends omit the option)
        stop_list = [
            s.strip()
            for s in (stop_sequences or "").replace("\n", ",").split(",")
            if s.strip()
        ] or None

        # ── 0. Server-side seed resolution (API fallback) ───────
        # Frontend normally resolves -1/-2/-3 before execution.
        # If it doesn't (API call, batch), resolve here and persist to workflow metadata.
        if seed in (-1, -2, -3):
            original_seed = seed
            seed = _new_random_seed()
            log.warning(
                _LOG_PREFIX,
                f"Server-generated random seed {seed} (was {original_seed})",
            )
            prompt = cls.hidden.prompt
            extra_pnginfo = cls.hidden.extra_pnginfo
            unique_id = cls.hidden.unique_id
            if unique_id is not None:
                if extra_pnginfo is not None:
                    wf_node = next(
                        (
                            x
                            for x in extra_pnginfo["workflow"]["nodes"]
                            if str(x["id"]) == unique_id
                        ),
                        None,
                    )
                    if wf_node and "widgets_values" in wf_node:
                        for idx, wv in enumerate(wf_node["widgets_values"]):
                            if wv == original_seed:
                                wf_node["widgets_values"][idx] = seed
                if prompt is not None:
                    pn = prompt.get(unique_id)
                    if pn and "inputs" in pn and "seed" in pn["inputs"]:
                        pn["inputs"]["seed"] = seed

        # ── 1. Registry lookup ──────────────────────────────────
        entry = get_model_entry(model)
        if entry is None:
            raise ValueError(f"Model '{model}' not found in registry")

        backend = entry["backend"]
        repo_id = entry.get("repo_id", "")
        name = entry["name"]
        family_str = entry.get("family", "")
        model_has_vision = entry.get("has_vision", False)
        loading_method = _BACKEND_TO_METHOD.get(backend, "Transformers")
        model_family = _FAMILY_TO_EXEC.get(
            family_str, "VLM" if model_has_vision else "LLM (Text-Only)"
        )

        # ── trust_remote_code policy ─────────────────────────────
        # Effective value = registry flag OR runtime chip override. Default False
        # (safe). Registry pre-flags models that legitimately need remote code
        # execution (Florence-2, Mistral-3/Pixtral); the chip is the runtime
        # opt-in for user-added or newly-released models.
        effective_trust_remote_code = is_trust_remote_code_allowed(
            model, override=bool(trust_remote_code)
        )
        if effective_trust_remote_code:
            source = "registry" if not trust_remote_code else "chip override"
            log.warning(
                _LOG_PREFIX,
                f"trust_remote_code=True for '{model}' (source: {source}) — model repo Python code will execute in-process",
            )

        log.msg(
            _LOG_PREFIX, f"Model: {model} | backend={backend} | family={model_family}"
        )

        # ── 2. WD14 fast-path ───────────────────────────────────
        if backend == "wd14":
            return _execute_wd14(
                repo_id=repo_id,
                images=images,
                threshold=threshold,
                char_threshold=char_threshold,
                replace_underscore=replace_underscore,
                keep_model_loaded=keep_model_loaded,
                node_id=node_id,
                initial_title=initial_title,
            )

        # ── 3. Resolve model path ──────────────────────────────
        model_path, needs_download = _resolve_model_path(entry, quantization)

        if needs_download:
            log.msg(_LOG_PREFIX, f"Model not found locally, downloading: {repo_id}")
            model_path = _ensure_downloaded(entry, quantization)
            # Reset progress bar after download so generation progress starts fresh
            import comfy.utils  # type: ignore

            comfy.utils.ProgressBar(1).update_absolute(0, 1)

        log.debug(_LOG_PREFIX, f"Model path: {model_path}")

        # ── 4. Build TemplateContext (adapter for load_model_with_backend) ──
        ctx = TemplateContext.from_widgets(
            model_family=model_family,
            model_type="",
            loading_method=loading_method,
            quantization=quantization if backend == "gguf" else "auto",
            attention_mode=attention_mode,
            repo_id=repo_id,
            local_path=model_path,
            quantized=False,
            default_task="",
            has_vision=model_has_vision,
            max_tokens=max_tokens,
            context_size=context_size,
        )

        # GGUF: set mmproj from registry
        if backend == "gguf" and entry.get("mmproj"):
            from pathlib import Path

            llm_base = get_llm_models_path()
            repo_folder = repo_id.split("/")[-1] if "/" in repo_id else name
            mmproj_path = llm_base / repo_folder / entry["mmproj"]
            if mmproj_path.exists():
                ctx.mmproj_path = str(mmproj_path)

        # Ollama: set model name
        if backend == "ollama":
            ctx.update(model_source="Ollama", ollama_model=repo_id)

        if loading_method in (
            "GGUF (llama-cpp-python)",
            "vLLM (Docker)",
            "SGLang (Docker)",
            "Ollama (Docker)",
            "llama.cpp (Docker)",
        ):
            ctx.context_size = context_size

        # ── 5. Load model ──────────────────────────────────────
        # Read n_batch from defaults (GGUF only, no widget)
        n_batch = int(load_defaults().get("n_batch", 512)) if backend == "gguf" else 512

        model_obj, processor, model_type = load_model_with_backend(
            loading_method=loading_method,
            model_family=model_family,
            model_path=model_path,
            ctx=ctx,
            quantization=quantization if backend == "gguf" else "auto",
            attention_mode=attention_mode,
            device=device,
            context_size=context_size,
            n_batch=n_batch,
            memory_cleanup=memory_cleanup,
            keep_model_loaded=keep_model_loaded,
            use_torch_compile=use_torch_compile,
            trust_remote_code=effective_trust_remote_code,
        )

        log.debug(_LOG_PREFIX, f"Model loaded: type={model_type}")

        # Build wrapper instance (same pattern as v3)
        if hasattr(model_obj, "is_vllm") and model_obj.is_vllm:
            instance = model_obj
            instance.model_type = model_type
        elif hasattr(model_obj, "is_sglang") and model_obj.is_sglang:
            instance = model_obj
            instance.model_type = model_type
        elif hasattr(model_obj, "is_ollama") and model_obj.is_ollama:
            instance = model_obj
            instance.model_type = model_type
        elif hasattr(model_obj, "is_llamacpp_docker") and model_obj.is_llamacpp_docker:
            instance = model_obj
            instance.model_type = model_type
        else:

            class _Wrapper:
                def __init__(self, m, p, mt, is_gguf, ctx, keep):
                    self.model = m
                    self.processor = p
                    self.model_type = mt
                    self.is_gguf = is_gguf
                    self.is_vllm = False
                    self.is_quantized = ctx.quantization not in (
                        None,
                        "auto",
                        "fp16",
                        "bf16",
                        "fp32",
                    )
                    self.keep_model_loaded = keep
                    self.tokenizer = getattr(p, "tokenizer", None) or p
                    self.chat_handler_ref = getattr(m, "_eclipse_chat_handler", None)

            instance = _Wrapper(
                model_obj,
                processor,
                model_type,
                is_gguf=(backend == "gguf"),
                ctx=ctx,
                keep=keep_model_loaded,
            )

        # ── 6. Resolve execution family ────────────────────────
        # Normalize unknown families to generic VLM or LLM path
        _KNOWN_FAMILIES = {
            "Qwen",
            "Florence",
            "Mistral",
            "LLaVA",
            "VLM",
            "LLM (Text-Only)",
        }
        if model_family not in _KNOWN_FAMILIES:
            model_family = "VLM" if model_has_vision else "LLM (Text-Only)"
            log.warning(
                _LOG_PREFIX,
                f"Unknown family '{family_str}' → routing via {model_family}",
            )

        # Align inputs for list execution
        num_runs = max(len(user_prompts), len(flat_images))
        prompts_aligned = [
            user_prompts[i if i < len(user_prompts) else -1] for i in range(num_runs)
        ]

        if len(flat_images) == 0:
            images_aligned = [None] * num_runs
        elif len(flat_images) == 1:
            images_aligned = [flat_images[0]] * num_runs
        else:
            images_aligned = [
                flat_images[i if i < len(flat_images) else -1] for i in range(num_runs)
            ]

        results = []
        output_images = []

        import comfy.utils  # type: ignore
        pbar = comfy.utils.ProgressBar(num_runs)

        try:
            for run_idx in range(num_runs):
                percent = (run_idx * 100) // num_runs
                update_title(f"{initial_title} ({percent}%)")

                single_prompt = prompts_aligned[run_idx]
                single_img = images_aligned[run_idx]

                # Prepare input image for generation
                input_image = None
                if single_img is not None and model_has_vision:
                    input_image = single_img
                elif model_has_vision and single_img is None:
                    log.warning(_LOG_PREFIX, "No image provided for vision model")

                # ── Generate (with system-prompt override if connected) ───
                _override_token = push_system_prompt_override(system_prompt)
                try:
                    run_res, run_data = _generate_for_family(
                        model_family=model_family,
                        instance=instance,
                        task_name=task,
                        user_prompt=single_prompt,
                        input_image=input_image,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=top_p,
                        top_k=top_k,
                        num_beams=num_beams,
                        do_sample=do_sample,
                        seed=seed,
                        repetition_penalty=repetition_penalty,
                        context_size=context_size,
                        frame_count=frame_count,
                        use_few_shot=use_few_shot_training,
                        min_p=min_p,
                        mirostat=mirostat_int,
                        mirostat_eta=mirostat_eta,
                        mirostat_tau=mirostat_tau,
                        repeat_last_n=repeat_last_n,
                        stop_sequences=stop_list,
                    )
                finally:
                    reset_system_prompt_override(_override_token)

                # ── Multi-task chaining ─────────────────────────────
                if multi_task_mode and model_family != "Florence":
                    tasks_to_run = [task]
                    for t in [task_2, task_3, task_4]:
                        if t and t != "None":
                            tasks_to_run.append(t)
                        else:
                            break

                    if len(tasks_to_run) > 1:
                        run_res, run_data = _run_multi_task_chain(
                            tasks_to_run=tasks_to_run,
                            first_result=run_res,
                            first_data=run_data,
                            instance=instance,
                            model_family=model_family,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            top_p=top_p,
                            top_k=top_k,
                            num_beams=num_beams,
                            do_sample=do_sample,
                            seed=seed,
                            repetition_penalty=repetition_penalty,
                            context_size=context_size,
                            frame_count=frame_count,
                            use_few_shot=use_few_shot_training,
                            min_p=min_p,
                            mirostat=mirostat_int,
                            mirostat_eta=mirostat_eta,
                            mirostat_tau=mirostat_tau,
                            repeat_last_n=repeat_last_n,
                            stop_sequences=stop_list,
                        )

                results.append(run_res)
                # Keep output_image passthrough matching input image layout
                img_out = (
                    single_img if single_img is not None else torch.zeros((1, 64, 64, 3))
                )
                output_images.append(img_out)
                pbar.update(1)
        finally:
            restore_title()

        elapsed = time.time() - start_time
        log.msg(
            _LOG_PREFIX, f"Done ({elapsed:.1f}s) — {sum(len(r) for r in results)} chars"
        )

        # ── 11. Persist-on-execute: save changed defaults ──────
        # Only persist sampling params when Use Advanced is ON — otherwise the
        # gated defaults would overwrite the user's saved tuning.
        if use_advanced:
            _persist_defaults(
                context_size=context_size,
                device=device,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                num_beams=num_beams,
                do_sample=do_sample,
                repetition_penalty=repetition_penalty,
                frame_count=frame_count,
                use_torch_compile=use_torch_compile,
                min_p=min_p,
                mirostat=mirostat,
                mirostat_eta=mirostat_eta,
                mirostat_tau=mirostat_tau,
                repeat_last_n=repeat_last_n,
                stop_sequences=stop_sequences,
            )
        else:
            _persist_defaults(
                context_size=context_size,
                device=device,
                frame_count=frame_count,
                use_torch_compile=use_torch_compile,
            )

        # ── 12. Cleanup ────────────────────────────────────────
        _cleanup_model(
            loading_method=loading_method,
            keep_model_loaded=keep_model_loaded,
            model_path=model_path,
            instance=instance,
        )

        return io.NodeOutput(output_images, results)


# ============================================================================
# Persist-on-Execute
# ============================================================================


def _persist_defaults(**kwargs):
    # Compare current values against stored defaults and save changes.
    defaults = load_defaults()
    updates = {}
    for key, value in kwargs.items():
        if defaults.get(key) != value:
            updates[key] = value
    if updates:
        save_defaults(updates)
