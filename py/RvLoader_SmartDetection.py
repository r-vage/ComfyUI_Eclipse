# RvLoader_Detection - Smart Detection Node
#
# Unified detection node with Florence-2, Qwen VL, and YOLO backends.
# Model-driven UI — backend and family inferred from model selection.
# Outputs: image (preview boxes), mask, SEGS (Impact Pack compatible), data (JSON).
#
# Node ID: "Smart Detection [Eclipse]"

import gc
import os
import random
import time
import uuid
from datetime import datetime

import torch  # type: ignore
from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.image_helpers import unwrap_value as _unwrap_scalar
from ..core.logger import log
from ..core.sml.backend_yolo import (
    detect_yolo,
    download_yolo_model,
    load_yolo_model,
    resolve_yolo_model_path,
    unload_yolo_model,
)
from ..core.sml.config_templates import TemplateContext
from ..core.sml.lifecycle import (
    register_execution_cleanup,
    with_execution_cleanup,
)
from ..core.sml.loader_base import load_model_with_backend
from ..core.sml.model_acquisition import (
    BACKEND_TO_METHOD as _BACKEND_TO_METHOD,
)
from ..core.sml.model_acquisition import (
    GGUF_BACKENDS as _GGUF_BACKENDS,
)
from ..core.sml.model_acquisition import (
    acquire_registered_model as _ensure_downloaded,
)
from ..core.sml.model_acquisition import (
    resolve_registered_mmproj_path as _resolve_mmproj_path,
)
from ..core.sml.model_acquisition import (
    resolve_registered_model_path as _resolve_model_path,
)
from ..core.sml.model_registry import (
    get_detection_model_list,
    get_model_entry,
    is_model_separator,
    is_trust_remote_code_allowed,
    load_defaults,
    save_defaults,
)
from ..core.sml.tasks import (
    TASK_BY_NAME,
    get_detection_task_names,
    get_system_prompt,
)
from ..core.sml.vlm_detection import (
    build_segs,
    combined_mask,
    draw_bboxes,
    nms_filter,
    parse_qwen_detection_json,
    scale_bboxes_to_original,
    scale_normalized_detection_to_pixels,
    select_detection,
    tensor_to_pil,
)

_LOG_PREFIX = "Detection"

# Isolated random state for seed generation (avoids interference with other extensions)
_det_seed_random_state = random.getstate()
random.seed(datetime.now().timestamp())
_det_seed_random_state = random.getstate()
random.setstate(_det_seed_random_state)


def _new_random_seed():
    global _det_seed_random_state
    old_state = random.getstate()
    random.setstate(_det_seed_random_state)
    seed = random.randint(0, 2**64 - 1)
    _det_seed_random_state = random.getstate()
    random.setstate(old_state)
    return seed


# ============================================================================
# Constants
# ============================================================================

# Tasks that require user_input
_REQUIRES_USER_INPUT = {
    "Caption to Phrase Grounding",
    "Referring Expression Segmentation",
    "DocVQA",
}

# Text-mode tasks (return text, no bboxes)
_TEXT_MODE_TASKS = {"OCR", "DocVQA"}


class _Wrapper:
    # A dummy wrapper class to hold dynamically loaded model references and properties
    def __init__(
        self,
        model,
        processor,
        model_type,
        is_gguf,
        is_quantized,
        keep_model_loaded,
        dtype=None,
        chat_handler_ref=None,
    ):
        self.model = model
        self.processor = processor
        self.model_type = model_type
        self.is_gguf = is_gguf
        self.is_vllm = False
        effective_quantization = getattr(
            model, "_sml_effective_quantization", None
        )
        self.is_quantized = (
            effective_quantization
            not in (None, "auto", "fp16", "bf16", "fp32", "none")
            if effective_quantization is not None
            else is_quantized
        )
        self.effective_device = getattr(model, "_sml_effective_device", None)
        self.effective_dtype = getattr(model, "_sml_effective_dtype", None)
        self.keep_model_loaded = keep_model_loaded
        self.tokenizer = getattr(processor, "tokenizer", None) or processor
        self.dtype = (
            dtype if dtype is not None else getattr(model, "dtype", torch.float16)
        )
        self.chat_handler_ref = chat_handler_ref


# ============================================================================
# Image Utilities
# ============================================================================


def _get_temp_image_path(suffix: str = ".jpg") -> str:
    import folder_paths  # type: ignore

    return os.path.join(
        folder_paths.get_temp_directory(),
        f"sml_det_{uuid.uuid4().hex}{suffix}",
    )


def _tensor_to_temp_jpegs(input_image, max_pixels: int = 0):
    from ..core.sml.vlm_detection import smart_resize_for_vlm

    img = tensor_to_pil(input_image)
    if img is None:
        raise ValueError("Failed to convert image tensor to PIL Image")
    original_size = (img.width, img.height)
    if max_pixels > 0:
        img, _ = smart_resize_for_vlm(img, max_pixels=max_pixels)
    resized_size = (img.width, img.height)
    path = _get_temp_image_path()
    img.save(path, "JPEG", quality=95)
    return [path], original_size, resized_size


def _cleanup_temp_files(paths):
    if paths:
        for p in paths:
            try:
                os.remove(p)
            except Exception:
                pass


# ============================================================================
# VLM Generation Dispatch (Florence + Qwen, simplified for detection)
# ============================================================================


def _generate_florence_detection(
    instance,
    image,
    task_name,
    user_input,
    max_tokens,
    num_beams,
    do_sample,
    seed,
    repetition_penalty,
    convert_to_bboxes,
    context_size,
    detection_filter_threshold,
    nms_iou_threshold,
):
    # Generate detection results with Florence-2.
    from ..core.sml.backend_transformers import generate_transformers

    task_obj = TASK_BY_NAME.get(task_name)
    if not task_obj or not task_obj.florence_id:
        raise ValueError(
            f"Task '{task_name}' not supported by Florence-2 (no florence_id)"
        )

    return generate_transformers(
        smart_lm_instance=instance,
        model_family="Florence2",
        image=image,
        prompt=task_obj.florence_id,
        max_tokens=max_tokens,
        temperature=0.0,
        top_p=1.0,
        top_k=0,
        num_beams=num_beams,
        do_sample=do_sample,
        seed=seed,
        repetition_penalty=repetition_penalty,
        text_input=user_input or "",
        convert_to_bboxes=convert_to_bboxes,
        detection_filter_threshold=detection_filter_threshold,
        nms_iou_threshold=nms_iou_threshold,
        context_size=context_size,
    )


def _generate_qwen_detection(
    instance,
    image,
    task_name,
    user_input,
    max_tokens,
    temperature,
    top_p,
    top_k,
    seed,
    num_beams,
    do_sample,
    repetition_penalty,
    context_size,
    backend,
):
    # Generate detection results with Qwen VL.
    # Routes to correct backend (Transformers, GGUF, Docker).
    system_prompt = get_system_prompt(task_name)
    prompt = system_prompt or "Detect all objects in this image."
    if user_input and user_input.strip():
        prompt += f"\n\n{user_input.strip()}"

    image_paths = None
    original_size = resized_size = None

    try:
        # Docker backends need temp files
        if hasattr(instance, "is_vllm") and instance.is_vllm:
            native = hasattr(instance, "is_vllm_native") and instance.is_vllm_native
            if native:
                from ..core.sml.backend_vllm_native import generate_vllm
            else:
                from ..core.sml.backend_vllm_docker import generate_vllm
            from ..core.sml.vlm_detection import VLM_MAX_PIXELS_DOCKER

            image_paths, original_size, resized_size = _tensor_to_temp_jpegs(
                image, max_pixels=VLM_MAX_PIXELS_DOCKER
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
                vision_task=task_name,
            )
            gen = generate_vllm(**kw)
            result = gen[0] if isinstance(gen, tuple) else gen

        elif hasattr(instance, "is_sglang") and instance.is_sglang:
            from ..core.sml.backend_sglang_docker import generate_sglang
            from ..core.sml.vlm_detection import VLM_MAX_PIXELS_DOCKER

            image_paths, original_size, resized_size = _tensor_to_temp_jpegs(
                image, max_pixels=VLM_MAX_PIXELS_DOCKER
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
                vision_task=task_name,
            )
            gen = generate_sglang(**kw)
            result = gen[0] if isinstance(gen, tuple) else gen

        elif hasattr(instance, "is_ollama") and instance.is_ollama:
            from ..core.sml.backend_ollama_docker import generate_ollama
            from ..core.sml.vlm_detection import VLM_MAX_PIXELS_DOCKER

            image_paths, original_size, resized_size = _tensor_to_temp_jpegs(
                image, max_pixels=VLM_MAX_PIXELS_DOCKER
            )
            result, _ = generate_ollama(
                smart_lm_instance=instance,
                prompt=prompt,
                image_paths=image_paths,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                seed=seed,
                repetition_penalty=repetition_penalty,
                vision_task=task_name,
            )

        elif hasattr(instance, "is_llamacpp_docker") and instance.is_llamacpp_docker:
            from ..core.sml.backend_llamacpp_docker import generate_llamacpp
            from ..core.sml.vlm_detection import VLM_MAX_PIXELS_DOCKER

            image_paths, original_size, resized_size = _tensor_to_temp_jpegs(
                image, max_pixels=VLM_MAX_PIXELS_DOCKER
            )
            result, _ = generate_llamacpp(
                smart_lm_instance=instance,
                prompt=prompt,
                image_paths=image_paths,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                seed=seed,
                repetition_penalty=repetition_penalty,
                vision_task=task_name,
            )

        elif instance.is_gguf:
            from ..core.sml.backend_gguf import generate_gguf

            result = generate_gguf(
                smart_lm_instance=instance,
                model_type="vision",
                image=image,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                seed=seed,
                repetition_penalty=repetition_penalty,
                vision_task=task_name,
            )

        else:
            # Transformers
            from ..core.sml.backend_transformers import generate_transformers

            result, data = generate_transformers(
                smart_lm_instance=instance,
                model_family="QwenVL",
                image=image,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                seed=seed,
                repetition_penalty=repetition_penalty,
                num_beams=num_beams,
                do_sample=do_sample,
                context_size=context_size,
                vision_task=task_name,
            )
            # Transformers path already parses Qwen detection JSON internally
            return result, data, None, None
    finally:
        _cleanup_temp_files(image_paths)

    # For non-Transformers backends: parse Qwen detection JSON from raw text
    pil_img = tensor_to_pil(image)
    if pil_img is None:
        raise ValueError("Failed to convert image tensor to PIL Image")
    img_size = original_size or (pil_img.width, pil_img.height)
    data, cleaned = parse_qwen_detection_json(result, image_size=img_size)
    if not data:
        data = {}

    # Scale bboxes back to original image size if resized
    if (
        original_size
        and resized_size
        and original_size != resized_size
        and data.get("bboxes")
    ):
        data = scale_bboxes_to_original(data, resized_size, original_size)

    return cleaned or result, data, original_size, resized_size


# ============================================================================
# YOLO Class Filtering
# ============================================================================


def _yolo_class_matches(label: str, requested: set) -> bool:
    # Fuzzy class matching: exact, substring, or plural/singular.
    # "breast" matches "Breasts", "eye" matches "eyes", etc.
    lbl = label.lower()
    for req in requested:
        if req == lbl or req in lbl or lbl in req:
            return True
    return False


def _filter_yolo_by_class(data, instance_masks, user_input):
    # Filter YOLO detections by class names from user_input.
    # user_input can be semicolon-separated (e.g., "face;person").
    # Matching is fuzzy: substring + plural/singular tolerance.
    # If empty, return all detections unchanged.
    if not user_input or not user_input.strip():
        return data, instance_masks

    requested = {c.strip().lower() for c in user_input.split(";") if c.strip()}
    if not requested:
        return data, instance_masks

    bboxes = data.get("bboxes", [])
    labels = data.get("labels", [])
    confidences = data.get("confidences", [])

    keep = [i for i, lbl in enumerate(labels) if _yolo_class_matches(lbl, requested)]
    if len(keep) == len(bboxes):
        log.debug(
            _LOG_PREFIX,
            f"YOLO class filter: all {len(bboxes)} detection(s) match requested={sorted(requested)}",
        )
        return data, instance_masks

    dropped = len(bboxes) - len(keep)
    for i in range(len(bboxes)):
        lbl = labels[i] if i < len(labels) else "?"
        conf = f"{confidences[i]:.2f}" if i < len(confidences) else "?"
        status = "KEEP" if i in keep else "DROP"
        log.debug(
            _LOG_PREFIX,
            f"  YOLO class filter [{status}] #{i}: '{lbl}' conf={conf} — requested={sorted(requested)}",
        )
    log.debug(
        _LOG_PREFIX,
        f"YOLO class filter: {dropped}/{len(bboxes)} dropped, {len(keep)} kept",
    )

    filtered = dict(data)
    filtered["bboxes"] = [bboxes[i] for i in keep]
    filtered["labels"] = [labels[i] for i in keep]
    if confidences:
        filtered["confidences"] = [confidences[i] for i in keep]

    filtered_masks = None
    if instance_masks:
        filtered_masks = [instance_masks[i] for i in keep if i < len(instance_masks)]

    return filtered, filtered_masks


# ============================================================================
# Model Cleanup
# ============================================================================


def _cleanup_model(*, loading_method, keep_model_loaded, model_path, instance):
    if keep_model_loaded:
        return

    # Docker auto-stop — bound to keep_model_loaded (OFF = stop container)
    if loading_method == "vLLM (Docker)":
        from ..core.sml import backend_vllm_docker

        backend_vllm_docker.stop_vllm_container()
    elif loading_method == "SGLang (Docker)" or (
        hasattr(instance, "is_sglang") and instance.is_sglang
    ):
        from ..core.sml import backend_sglang_docker

        backend_sglang_docker.stop_sglang_container()
    elif loading_method == "Ollama (Docker)":
        from ..core.sml import backend_ollama_docker

        backend_ollama_docker.stop_ollama_container()
    elif loading_method == "llama.cpp (Docker)":
        from ..core.sml import backend_llamacpp_docker

        backend_llamacpp_docker.stop_llamacpp_container(
            getattr(instance, "llamacpp_container_name", None)
        )

    is_gguf = loading_method == "GGUF (llama-cpp-python)"
    is_transformers = loading_method.lower() == "transformers"

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
        if hasattr(instance, "model"):
            instance.model = None
        if hasattr(instance, "processor"):
            instance.processor = None

    for _ in range(3):
        gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()


def _selective_cleanup():
    # Selective VRAM cleanup — GC + CUDA cache, preserves model cache.
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


# ============================================================================
# Post-Processing (shared for all families)
# ============================================================================


def _apply_detection_filter(data, instance_masks, detection_filter, image_h, image_w):
    # Remove detections whose bbox area exceeds detection_filter * image area.
    if detection_filter >= 1.0:
        return data, instance_masks

    image_area = image_h * image_w
    max_area = detection_filter * image_area

    bboxes = data.get("bboxes", [])
    labels = data.get("labels", [])
    confidences = data.get("confidences", [])

    keep = []
    for i, bbox in enumerate(bboxes):
        x1, y1, x2, y2 = bbox
        area = (x2 - x1) * (y2 - y1)
        ratio = area / image_area if image_area > 0 else 0
        lbl = labels[i] if i < len(labels) else "?"
        if area <= max_area:
            keep.append(i)
            log.debug(
                _LOG_PREFIX,
                f"  Area filter [KEEP] #{i}: '{lbl}' area_ratio={ratio:.2%} <= threshold={detection_filter:.0%}",
            )
        else:
            log.debug(
                _LOG_PREFIX,
                f"  Area filter [DROP] #{i}: '{lbl}' area_ratio={ratio:.2%} > threshold={detection_filter:.0%}",
            )

    if len(keep) == len(bboxes):
        return data, instance_masks

    log.debug(
        _LOG_PREFIX,
        f"Area filter: {len(bboxes) - len(keep)}/{len(bboxes)} dropped (threshold={detection_filter:.0%} of {image_w}x{image_h})",
    )
    filtered = dict(data)
    filtered["bboxes"] = [bboxes[i] for i in keep]
    filtered["labels"] = [labels[i] for i in keep] if labels else []
    filtered["confidences"] = [confidences[i] for i in keep] if confidences else []

    filtered_masks = None
    if instance_masks:
        filtered_masks = [instance_masks[i] for i in keep if i < len(instance_masks)]

    return filtered, filtered_masks


def _apply_drop_size(data, instance_masks, drop_size):
    # Remove detections with width or height <= drop_size (strict >, matching Impact Pack).
    if drop_size <= 0:
        return data, instance_masks

    bboxes = data.get("bboxes", [])
    labels = data.get("labels", [])
    confidences = data.get("confidences", [])

    keep = []
    for i, bbox in enumerate(bboxes):
        x1, y1, x2, y2 = bbox
        w = x2 - x1
        h = y2 - y1
        lbl = labels[i] if i < len(labels) else "?"
        if w > drop_size and h > drop_size:
            keep.append(i)
        else:
            log.debug(
                _LOG_PREFIX,
                f"  Drop-size filter [DROP] #{i}: '{lbl}' size={w:.0f}x{h:.0f} <= min={drop_size}px",
            )

    if len(keep) == len(bboxes):
        return data, instance_masks

    log.debug(
        _LOG_PREFIX,
        f"Drop-size filter: {len(bboxes) - len(keep)}/{len(bboxes)} dropped (min_size={drop_size}px)",
    )
    filtered = dict(data)
    filtered["bboxes"] = [bboxes[i] for i in keep]
    filtered["labels"] = [labels[i] for i in keep] if labels else []
    filtered["confidences"] = [confidences[i] for i in keep] if confidences else []

    filtered_masks = None
    if instance_masks:
        filtered_masks = [instance_masks[i] for i in keep if i < len(instance_masks)]

    return filtered, filtered_masks


def _apply_confidence_filter(data, instance_masks, confidence):
    # Filter detections below confidence threshold.
    # Only needed for Florence/Qwen — YOLO applies confidence internally.
    if confidence <= 0.0:
        return data, instance_masks

    bboxes = data.get("bboxes", [])
    labels = data.get("labels", [])
    confidences_list = data.get("confidences", [])
    if not confidences_list:
        return data, instance_masks

    keep = [i for i, c in enumerate(confidences_list) if c >= confidence]
    if len(keep) == len(bboxes):
        return data, instance_masks

    for i, c in enumerate(confidences_list):
        lbl = labels[i] if i < len(labels) else "?"
        status = "KEEP" if i in keep else "DROP"
        log.debug(
            _LOG_PREFIX,
            f"  Confidence filter [{status}] #{i}: '{lbl}' conf={c:.2f} vs threshold={confidence:.2f}",
        )
    log.debug(
        _LOG_PREFIX,
        f"Confidence filter: {len(bboxes) - len(keep)}/{len(bboxes)} dropped (threshold={confidence:.2f})",
    )

    filtered = dict(data)
    filtered["bboxes"] = [bboxes[i] for i in keep]
    filtered["labels"] = [labels[i] for i in keep] if labels else []
    filtered["confidences"] = [confidences_list[i] for i in keep]

    filtered_masks = None
    if instance_masks:
        filtered_masks = [instance_masks[i] for i in keep if i < len(instance_masks)]

    return filtered, filtered_masks


# ============================================================================
# Node Class
# ============================================================================


class RvLoader_Detection(io.ComfyNode):

    @classmethod
    def define_schema(cls):
        models = get_detection_model_list()
        first_model = next(
            (m for m in models if not is_model_separator(m)),
            models[0] if models else "",
        )
        defaults = load_defaults()
        det_tasks = get_detection_task_names()
        quant_placeholders = defaults.get(
            "quantizations", ["Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"]
        )

        return io.Schema(
            node_id="Smart Detection [Eclipse]",
            display_name="Smart Detection",
            search_aliases=["Smart Detection SML", "YOLO", "VLM Detection", "WD14"],
            category=CATEGORY.MAIN.value + CATEGORY.LOADER.value,
            description="Detection node with Florence-2, Qwen VL, and YOLO backends. "
            "Outputs: image (preview boxes), mask, SEGS (Impact Pack compatible), data (JSON).",
            inputs=[
                # ── Mode bar backing widgets (hidden, synced by JS chips) ──
                io.Boolean.Input(
                    "cleanup",
                    default=True,
                    socketless=True,
                    extra_dict={"hidden": True},
                    tooltip="VRAM garbage collection — clear VRAM cache and run Python garbage collection before loading the model.",
                ),
                io.Boolean.Input(
                    "keep_model_loaded",
                    default=False,
                    socketless=True,
                    extra_dict={"hidden": True},
                    tooltip="Keep the detection model cached in VRAM between runs to skip loading/unloading latency (highly recommended for performance).",
                ),
                io.Boolean.Input(
                    "enable_preview_boxes",
                    default=True,
                    socketless=True,
                    extra_dict={"hidden": True},
                    tooltip="Superimpose colored bounding box outlines and text labels onto the output preview image.",
                ),
                io.Boolean.Input(
                    "show_adjust",
                    default=False,
                    socketless=True,
                    extra_dict={"hidden": True},
                    tooltip="Toggle visibility of post-processing filters/adjustments (box expansion, mask dilation, size filters).",
                ),
                io.Boolean.Input(
                    "show_advanced",
                    default=False,
                    socketless=True,
                    extra_dict={"hidden": True},
                    tooltip="Toggle visibility of advanced hardware settings and backend sampling options.",
                ),
                # ── Main widgets ──────────────────────────────────────
                io.Combo.Input(
                    "model_name",
                    options=models,
                    default=first_model,
                    tooltip="Choose the object detection or VLM grounding model to load.\n"
                    "Suffixes indicate the backend engine (no suffix=Transformers, -GGUF=native llama-cpp-python, "
                    "-llama.cpp=llama.cpp Docker).",
                ),
                io.Combo.Input(
                    "quantization",
                    options=quant_placeholders,
                    default="Q4_K_M",
                    tooltip="Choose GGUF quantization precision. Lower bits (e.g. Q4_K_M) use less VRAM but lose accuracy. "
                    "Higher bits (e.g. Q8_0) are more accurate but demand more VRAM. Applies to native GGUF and llama.cpp Docker models.",
                ),
                io.Combo.Input(
                    "task",
                    options=det_tasks,
                    default=(
                        det_tasks[0] if det_tasks else "Caption to Phrase Grounding"
                    ),
                    tooltip="Type of detection task to run:\n"
                    "• Caption to Phrase Grounding: Locates objects matching user_input query.\n"
                    "• Referring Expression Segmentation: Generates segmented mask regions for specified objects.\n"
                    "• Region Caption: Detects objects and labels them with descriptions.\n"
                    "• YOLO runs object detection automatically.",
                ),
                io.String.Input(
                    "user_input",
                    default="",
                    multiline=True,
                    tooltip="Input query for detection:\n"
                    "• Florence/Qwen grounding: The phrase/object you want to locate (e.g. 'the black cat').\n"
                    "• YOLO: Semicolon-separated target classes to filter (e.g. 'person;car;backpack'). Leave empty to output all classes.",
                ),
                # ── Detection parameters ──────────────────────────────
                io.Float.Input(
                    "confidence",
                    default=float(defaults.get("confidence", 0.5)),
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Minimum confidence score (0.0 to 1.0) required for a detection to be kept. "
                    "Higher values reduce false positives, lower values capture more candidate objects.",
                ),
                io.Float.Input(
                    "nms_iou_threshold",
                    default=float(defaults.get("nms_iou_threshold", 0.5)),
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Non-Maximum Suppression (NMS) intersection-over-union threshold. "
                    "Lower values (e.g. 0.3) aggressively merge overlapping boxes; higher values (e.g. 0.7) keep separate but close detections.",
                ),
                io.Float.Input(
                    "detection_filter",
                    default=float(defaults.get("detection_filter", 0.8)),
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Max ratio of bounding box area to total image area. "
                    "Detections covering more than this ratio (e.g. 0.8) are ignored to filter out useless full-image bounding boxes.",
                ),
                io.Int.Input(
                    "drop_size",
                    default=int(defaults.get("drop_size", 10)),
                    min=1,
                    max=8192,
                    step=1,
                    tooltip="Minimum width or height (in pixels) for a bounding box. Smaller boxes are discarded (helps filter out tiny noise).",
                ),
                io.Float.Input(
                    "crop_factor",
                    default=float(defaults.get("crop_factor", 3.0)),
                    min=1.0,
                    max=100.0,
                    step=0.1,
                    tooltip="Scale factor to expand the cropped region around detected objects when outputting to Impact Pack SEGS. "
                    "A value of 3.0 captures 3x the box size.",
                ),
                io.Int.Input(
                    "dilation",
                    default=int(defaults.get("dilation", 0)),
                    min=-512,
                    max=512,
                    step=1,
                    tooltip="Expand (positive values) or shrink (negative values) the boundaries of the output mask by a set number of pixels.",
                ),
                io.Int.Input(
                    "select_index",
                    default=int(defaults.get("select_index", -1)),
                    min=-1,
                    max=999,
                    step=1,
                    tooltip="Select a single detection: set to -1 to output all detections merged; "
                    "set to 0, 1, 2... to output only the N-th detection (useful for isolating objects).",
                ),
                # ── Advanced widgets (hidden by default) ──────────────
                io.Combo.Input(
                    "device",
                    options=["auto", "cuda", "cpu", "mps"],
                    default=str(defaults.get("device", "cuda")),
                    tooltip="Hardware device to load the model onto. "
                    "Use 'auto' to prefer CUDA/ROCm and let Florence-2 fall back "
                    "to CPU when native GPU precision cannot safely fit; explicit "
                    "'cuda', 'mps', and 'cpu' selections remain strict.",
                ),
                io.Int.Input(
                    "num_beams",
                    default=int(defaults.get("num_beams", 1)),
                    min=1,
                    max=10,
                    step=1,
                    tooltip="Number of parallel paths explored during beam search. "
                    "Values > 1 produce higher quality text/grounding but are significantly slower. Set to 1 for standard sampling.",
                ),
                io.Boolean.Input(
                    "do_sample",
                    default=bool(defaults.get("do_sample", True)),
                    tooltip="When enabled, uses probabilistic sampling (temperature, top_p, top_k). "
                    "When disabled, uses greedy decoding (always picking the most likely next word, ignoring temperature).",
                ),
                io.Boolean.Input(
                    "use_torch_compile",
                    default=bool(defaults.get("use_torch_compile", False)),
                    tooltip="JIT compiles the model using PyTorch 2.x compile. "
                    "Increases initial startup/load time (~1-3 minutes first run) but speeds up subsequent inference runs.",
                ),
                io.Boolean.Input(
                    "convert_to_bboxes",
                    default=bool(defaults.get("convert_to_bboxes", False)),
                    tooltip="Florence-2 only: Convert quad/polygon coordinates (which outline precise shapes) to standard rectangular bounding boxes.",
                ),
                io.Float.Input(
                    "temperature",
                    default=float(defaults.get("temperature", 0.7)),
                    min=0.1,
                    max=2.0,
                    step=0.1,
                    tooltip="Controls randomness for generative VLMs: higher values (e.g. 0.8+) make output more creative/diverse; "
                    "lower values make it more deterministic. Florence-2 ignores this.",
                ),
                io.Float.Input(
                    "top_p",
                    default=float(defaults.get("top_p", 0.9)),
                    min=0.1,
                    max=1.0,
                    step=0.05,
                    tooltip="Nucleus sampling: limits generation to the top cumulative probability tokens (e.g. 0.9 keeps top 90% likely words). "
                    "Filters out low-probability gibberish. Florence-2 ignores this.",
                ),
                io.Int.Input(
                    "top_k",
                    default=int(defaults.get("top_k", 50)),
                    min=0,
                    max=1000,
                    step=1,
                    tooltip="Limits generation to the top K most likely next words. Lower values (e.g. 40) make output more focused; 0 disables it. Florence-2 ignores this.",
                ),
                io.Float.Input(
                    "repetition_penalty",
                    default=float(defaults.get("repetition_penalty", 1.0)),
                    min=1.0,
                    max=2.0,
                    step=0.1,
                    tooltip="Penalizes repeating the same phrases or words. Values > 1.0 (e.g. 1.1 or 1.2) help reduce loops. Florence-2 ignores this.",
                ),
                # ── Seed (last — JS adds buttons after it) ───────────
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
                # ── Image input ───────────────────────────────────────
                io.Image.Input(
                    "image",
                    tooltip="Input image or video batch to detect objects, segments, or text in.",
                ),
            ],
            is_input_list=True,
            outputs=[
                io.Image.Output(
                    "image",
                    is_output_list=True,
                    tooltip="Preview boxes or passthrough.",
                ),
                io.Mask.Output(
                    "mask", is_output_list=True, tooltip="Binary mask of detections."
                ),
                io.Custom("SEGS").Output(
                    "segs",
                    is_output_list=True,
                    tooltip="Impact Pack compatible SEGS tuple.",
                ),
                io.Custom("JSON").Output(
                    "data",
                    is_output_list=True,
                    tooltip="Detection data dict (bboxes, labels, coord_range). Connect to Detection to Bboxes.",
                ),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo, io.Hidden.unique_id],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        seed = kwargs.get("seed", 0)
        if seed in (-1, -2, -3):
            return _new_random_seed()
        return seed

    @classmethod
    @with_execution_cleanup(_LOG_PREFIX)
    def execute(
        cls,
        # Mode bar backing
        cleanup,
        keep_model_loaded,
        enable_preview_boxes,
        show_advanced,
        show_adjust,
        # Main widgets
        model_name,
        quantization,
        task,
        user_input,
        # Detection params
        confidence,
        nms_iou_threshold,
        detection_filter,
        drop_size,
        crop_factor,
        dilation,
        select_index,
        seed,
        # Advanced
        device,
        num_beams,
        do_sample,
        use_torch_compile,
        convert_to_bboxes,
        temperature,
        top_p,
        top_k,
        repetition_penalty,
        # Image
        image,
    ):
        start_time = time.time()



        # Unwrap all widgets
        cleanup = _unwrap_scalar(cleanup, True)
        keep_model_loaded = _unwrap_scalar(keep_model_loaded, False)
        enable_preview_boxes = _unwrap_scalar(enable_preview_boxes, True)
        show_advanced = _unwrap_scalar(show_advanced, False)
        show_adjust = _unwrap_scalar(show_adjust, False)
        model_name = _unwrap_scalar(model_name, "")
        quantization = _unwrap_scalar(quantization, "")
        task = _unwrap_scalar(task, "")
        user_input = _unwrap_scalar(user_input, "")
        confidence = _unwrap_scalar(confidence, 0.5)
        nms_iou_threshold = _unwrap_scalar(nms_iou_threshold, 0.5)
        detection_filter = _unwrap_scalar(detection_filter, 0.8)
        drop_size = _unwrap_scalar(drop_size, 10)
        crop_factor = _unwrap_scalar(crop_factor, 3.0)
        dilation = _unwrap_scalar(dilation, 0)
        select_index = _unwrap_scalar(select_index, -1)
        seed = _unwrap_scalar(seed, -1)
        device = _unwrap_scalar(device, "cuda")
        num_beams = _unwrap_scalar(num_beams, 1)
        do_sample = _unwrap_scalar(do_sample, True)
        use_torch_compile = _unwrap_scalar(use_torch_compile, False)
        convert_to_bboxes = _unwrap_scalar(convert_to_bboxes, False)
        temperature = _unwrap_scalar(temperature, 0.7)
        top_p = _unwrap_scalar(top_p, 0.9)
        top_k = _unwrap_scalar(top_k, 50)
        repetition_penalty = _unwrap_scalar(repetition_penalty, 1.0)

        # ── Persist user-tweaked defaults ──────────────────────
        _persist_defaults(
            confidence=confidence,
            nms_iou_threshold=nms_iou_threshold,
            detection_filter=detection_filter,
            drop_size=drop_size,
            crop_factor=crop_factor,
            dilation=dilation,
            select_index=select_index,
            device=device,
            num_beams=num_beams,
            do_sample=do_sample,
            use_torch_compile=use_torch_compile,
            convert_to_bboxes=convert_to_bboxes,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            repetition_penalty=repetition_penalty,
        )

        # ── Seed resolution ─────────────────────────────────────
        if seed in (-1, -2, -3):
            seed = _new_random_seed()
        # Persist resolved seed into workflow metadata
        extra_pnginfo = cls.hidden.extra_pnginfo
        unique_id = cls.hidden.unique_id
        if unique_id is not None:
            uid = unique_id
            if extra_pnginfo is not None and "workflow" in extra_pnginfo:
                for x in extra_pnginfo["workflow"]["nodes"]:
                    if str(x["id"]) == uid:
                        wv = x.get("widgets_values")
                        if wv:
                            for i, v in enumerate(wv):
                                if v in (-1, -2, -3):
                                    wv[i] = seed
                                    break
                        break

        # ── 1. Registry lookup ──────────────────────────────────
        entry = get_model_entry(model_name)
        if entry is None:
            raise ValueError(f"Model '{model_name}' not found in registry")

        backend = entry["backend"]
        repo_id = entry.get("repo_id", "")
        name = entry["name"]
        family_str = entry.get("family", "")
        loading_method = _BACKEND_TO_METHOD.get(backend, "Transformers")

        log.msg(
            _LOG_PREFIX,
            f"Model: {model_name} | family={family_str} | backend={backend}",
        )

        # ── 2. Flatten/normalize input images ──────────────────
        flat_images = []

        def _process_image(item):
            if isinstance(item, (list, tuple)):
                for sub in item:
                    _process_image(sub)
            elif isinstance(item, torch.Tensor):
                if item.dim() == 4:
                    for i in range(item.shape[0]):
                        flat_images.append(item[i : i + 1, ...])
                elif item.dim() == 3:
                    flat_images.append(item.unsqueeze(0))
            elif item is not None:
                flat_images.append(item)

        _process_image(image)

        if not flat_images:
            raise ValueError("No input images provided for detection")

        # ── 3. Pre-validate ─────────────────────────────────────
        if (
            family_str != "YOLO"
            and task in _REQUIRES_USER_INPUT
            and (not user_input or not user_input.strip())
        ):
            raise ValueError(
                f"user_input is required for '{task}' — describe what to detect"
            )

        # ── 4. Model Loading (loaded once before the loop) ─────
        register_execution_cleanup(_selective_cleanup)
        instance = None
        model_path = ""
        cleanup_state = {"instance": None}

        if family_str == "YOLO":
            filename = entry.get("filename", name)
            if not isinstance(filename, str):
                filename = str(filename) if filename is not None else ""
            model_path = resolve_yolo_model_path(filename)
            enforce_registry_provenance = bool(
                entry.get("revision") is not None
                or entry.get("expected_sha256") is not None
            )
            if model_path is None or enforce_registry_provenance:
                try:
                    model_path = download_yolo_model(
                        entry,
                        local_model_path=model_path,
                    )
                except FileNotFoundError:
                    log.warning(
                        _LOG_PREFIX,
                        f"YOLO model '{filename}' not found locally or online — skipping detection.",
                    )
                    empty_mask = torch.zeros((1, 64, 64), dtype=torch.float32)
                    empty_segs = ((64, 64), [])
                    empty_data = {
                        "bboxes": [],
                        "labels": [],
                        "error": f"Model '{filename}' not found",
                    }
                    return io.NodeOutput(
                        flat_images,
                        [empty_mask] * len(flat_images),
                        [empty_segs] * len(flat_images),
                        [empty_data] * len(flat_images),
                    )
                import comfy.utils

                comfy.utils.ProgressBar(1).update_absolute(0, 1)
            if not keep_model_loaded:
                register_execution_cleanup(unload_yolo_model)
            yolo_model = load_yolo_model(model_path, device=device)

        elif family_str == "Florence":
            model_path, needs_download = _resolve_model_path(entry)
            enforce_registry_provenance = bool(
                entry.get("revision") is not None
                or entry.get("expected_sha256") is not None
            )
            if needs_download or enforce_registry_provenance:
                if needs_download:
                    log.msg(_LOG_PREFIX, f"Downloading Florence model: {repo_id}")
                model_path = _ensure_downloaded(
                    entry,
                    local_model_path=None if needs_download else model_path,
                )
                import comfy.utils

                comfy.utils.ProgressBar(1).update_absolute(0, 1)

            if not keep_model_loaded:
                register_execution_cleanup(
                    lambda: _cleanup_model(
                        loading_method=loading_method,
                        keep_model_loaded=keep_model_loaded,
                        model_path=model_path,
                        instance=cleanup_state["instance"],
                    )
                )

            ctx = TemplateContext.from_widgets(
                model_family="Florence",
                model_type="",
                loading_method="Transformers",
                quantization="auto",
                attention_mode="auto",
                repo_id=repo_id,
                local_path=model_path,
                quantized=False,
                default_task="",
                has_vision=True,
                max_tokens=4096,
                context_size=4096,
            )
            model_obj, processor, model_type = load_model_with_backend(
                loading_method="Transformers",
                model_family="Florence",
                model_path=model_path,
                ctx=ctx,
                quantization="auto",
                attention_mode="auto",
                device=device,
                memory_cleanup=cleanup,
                keep_model_loaded=keep_model_loaded,
                use_torch_compile=use_torch_compile,
                trust_remote_code=is_trust_remote_code_allowed(name),
                repo_id=repo_id,
                revision=entry.get("revision"),
                expected_sha256=entry.get("expected_sha256"),
            )
            cleanup_state["instance"] = model_obj
            instance = _Wrapper(
                model=model_obj,
                processor=processor,
                model_type=model_type,
                is_gguf=False,
                is_quantized=False,
                keep_model_loaded=keep_model_loaded,
                dtype=getattr(model_obj, "dtype", torch.float16),
            )
            cleanup_state["instance"] = instance

        elif family_str == "Qwen":
            model_path, needs_download = _resolve_model_path(
                entry, quantization if backend in _GGUF_BACKENDS else None
            )
            enforce_registry_provenance = bool(
                backend in ("transformers", "gguf", "llamacpp")
                and (
                    entry.get("revision") is not None
                    or entry.get("expected_sha256") is not None
                )
            )
            if needs_download or enforce_registry_provenance:
                if needs_download:
                    log.msg(_LOG_PREFIX, f"Downloading Qwen model: {repo_id}")
                model_path = _ensure_downloaded(
                    entry,
                    quantization if backend in _GGUF_BACKENDS else None,
                    local_model_path=None if needs_download else model_path,
                )
                import comfy.utils

                comfy.utils.ProgressBar(1).update_absolute(0, 1)

            if not keep_model_loaded:
                register_execution_cleanup(
                    lambda: _cleanup_model(
                        loading_method=loading_method,
                        keep_model_loaded=keep_model_loaded,
                        model_path=model_path,
                        instance=cleanup_state["instance"],
                    )
                )

            ctx = TemplateContext.from_widgets(
                model_family="Qwen",
                model_type="",
                loading_method=loading_method,
                quantization=quantization if backend in _GGUF_BACKENDS else "auto",
                attention_mode="auto",
                repo_id=repo_id,
                local_path=model_path,
                quantized=False,
                default_task="",
                has_vision=True,
                max_tokens=4096,
                context_size=8192,
            )
            if backend == "ollama":
                ctx.update(model_source="Ollama", ollama_model=repo_id)
            if backend in _GGUF_BACKENDS and entry.get("mmproj"):
                mmproj_path = _resolve_mmproj_path(entry, model_path)
                if mmproj_path:
                    ctx.mmproj_path = mmproj_path

            n_batch = (
                int(load_defaults().get("n_batch", 512)) if backend == "gguf" else 512
            )
            model_obj, processor, model_type = load_model_with_backend(
                loading_method=loading_method,
                model_family="Qwen",
                model_path=model_path,
                ctx=ctx,
                quantization=quantization if backend in _GGUF_BACKENDS else "auto",
                attention_mode="auto",
                device=device,
                context_size=8192,
                n_batch=n_batch,
                memory_cleanup=cleanup,
                keep_model_loaded=keep_model_loaded,
                use_torch_compile=use_torch_compile,
                trust_remote_code=is_trust_remote_code_allowed(name),
                repo_id=repo_id,
                revision=entry.get("revision"),
                expected_sha256=entry.get("expected_sha256"),
            )
            cleanup_state["instance"] = model_obj
            if (
                hasattr(model_obj, "is_vllm")
                or hasattr(model_obj, "is_sglang")
                or hasattr(model_obj, "is_ollama")
                or hasattr(model_obj, "is_llamacpp_docker")
            ):
                instance = model_obj
                instance.model_type = model_type
            else:
                instance = _Wrapper(
                    model=model_obj,
                    processor=processor,
                    model_type=model_type,
                    is_gguf=(backend == "gguf"),
                    is_quantized=(
                        ctx.quantization not in (None, "auto", "fp16", "bf16", "fp32")
                    ),
                    keep_model_loaded=keep_model_loaded,
                    chat_handler_ref=getattr(model_obj, "_eclipse_chat_handler", None),
                )
            cleanup_state["instance"] = instance
        else:
            raise ValueError(f"Unsupported model family: {family_str}")

        # ── 5. Run Slicing / Detection Loop ──────────────────
        out_images = []
        out_masks = []
        out_segs = []
        out_datas = []

        for run_idx, single_image in enumerate(flat_images):
            # Prep single image PIL and shape parameters
            pil_image = tensor_to_pil(single_image)
            if pil_image is None:
                raise ValueError("Failed to convert input tensor to PIL Image")
            image_h, image_w = pil_image.height, pil_image.width

            empty_mask = torch.zeros((1, image_h, image_w), dtype=torch.float32)
            empty_segs = ((image_h, image_w), [])

            # Florence Text-Mode fast-path
            if family_str == "Florence" and task in _TEXT_MODE_TASKS:
                result_text, _ = _generate_florence_detection(
                    instance,
                    single_image,
                    task,
                    user_input,
                    max_tokens=4096,
                    num_beams=num_beams,
                    do_sample=do_sample,
                    seed=seed,
                    repetition_penalty=repetition_penalty,
                    convert_to_bboxes=False,
                    context_size=4096,
                    detection_filter_threshold=detection_filter,
                    nms_iou_threshold=nms_iou_threshold,
                )
                text_data = {
                    "bboxes": [],
                    "labels": [],
                    "text": result_text,
                    "coord_range": 0,
                    "backend": "Florence-2",
                    "model": name,
                    "task": task,
                }
                out_images.append(single_image)
                out_masks.append(empty_mask)
                out_segs.append(empty_segs)
                out_datas.append(text_data)
                continue

            # Run Inference
            data = {}
            instance_masks = []

            if family_str == "YOLO":
                _, data, instance_masks = detect_yolo(
                    yolo_model, pil_image, confidence=confidence, device=device
                )
                data, instance_masks = _filter_yolo_by_class(
                    data, instance_masks, user_input
                )
            elif family_str == "Florence":
                _, data = _generate_florence_detection(
                    instance,
                    single_image,
                    task,
                    user_input,
                    max_tokens=4096,
                    num_beams=num_beams,
                    do_sample=do_sample,
                    seed=seed,
                    repetition_penalty=repetition_penalty,
                    convert_to_bboxes=convert_to_bboxes,
                    context_size=4096,
                    detection_filter_threshold=detection_filter,
                    nms_iou_threshold=nms_iou_threshold,
                )
                data.setdefault("coord_range", 0)
                data.setdefault("backend", "Florence-2")
                data.setdefault("model", name)
                data.setdefault("task", task)
                data, instance_masks = _apply_confidence_filter(
                    data, instance_masks, confidence
                )
            elif family_str == "Qwen":
                _, data, _, _ = _generate_qwen_detection(
                    instance,
                    single_image,
                    task,
                    user_input,
                    max_tokens=4096,
                    temperature=temperature,
                    top_p=top_p,
                    top_k=top_k,
                    seed=seed,
                    num_beams=num_beams,
                    do_sample=do_sample,
                    repetition_penalty=repetition_penalty,
                    context_size=8192,
                    backend=backend,
                )
                data.setdefault("coord_range", 0)
                data.setdefault("backend", "Qwen")
                data.setdefault("model", name)
                data.setdefault("task", task)
                data, instance_masks = _apply_confidence_filter(
                    data, instance_masks, confidence
                )

            # Filtering & Post-process
            data = scale_normalized_detection_to_pixels(
                data, (image_w, image_h)
            )
            bboxes = data.get("bboxes", [])
            if not bboxes:
                # No detections
                out_images.append(single_image)
                out_masks.append(empty_mask)
                out_segs.append(empty_segs)
                empty_data = dict(data)
                empty_data.setdefault("bboxes", [])
                empty_data.setdefault("labels", [])
                out_datas.append(empty_data)
                continue

            # NMS filter
            nms_bboxes, nms_labels, keep_indices = nms_filter(
                data["bboxes"], data.get("labels", []), nms_iou_threshold
            )
            data["bboxes"] = nms_bboxes
            data["labels"] = nms_labels
            confs = data.get("confidences", [])
            if confs:
                data["confidences"] = [confs[i] for i in keep_indices if i < len(confs)]
            if instance_masks:
                instance_masks = [
                    instance_masks[i] for i in keep_indices if i < len(instance_masks)
                ]

            # Area filter
            if family_str != "YOLO":
                data, instance_masks = _apply_detection_filter(
                    data, instance_masks, detection_filter, image_h, image_w
                )

            # Drop size filter
            data, instance_masks = _apply_drop_size(data, instance_masks, drop_size)

            if not data.get("bboxes"):
                out_images.append(single_image)
                out_masks.append(empty_mask)
                out_segs.append(empty_segs)
                out_datas.append(data)
                continue

            # Build SEGS
            all_segs = build_segs(
                data,
                image_h,
                image_w,
                crop_factor=crop_factor,
                dilation=dilation,
                instance_masks=instance_masks,
            )

            # Apply select_index
            if select_index >= 0:
                output_data, output_masks = select_detection(
                    data, select_index, image_h, image_w, instance_masks
                )
                mask_tensor = combined_mask(image_h, image_w, output_data, output_masks)
                idx = min(select_index, len(all_segs[1]) - 1) if all_segs[1] else 0
                if all_segs[1]:
                    output_segs = ((image_h, image_w), [all_segs[1][idx]])
                else:
                    output_segs = empty_segs
            else:
                output_data = data
                mask_tensor = combined_mask(image_h, image_w, data, instance_masks)
                output_segs = all_segs

            # Image preview output with box overlay
            if enable_preview_boxes and data.get("bboxes"):
                image_out = draw_bboxes(single_image, data)
            else:
                image_out = single_image

            out_images.append(image_out)
            out_masks.append(mask_tensor)
            out_segs.append(output_segs)
            out_datas.append(output_data)

        # ── 10. Logging ──────────────────────────────────────────
        total_detections = sum(len(d.get("bboxes", [])) for d in out_datas)
        elapsed = time.time() - start_time
        log.msg(
            _LOG_PREFIX,
            f"Done ({elapsed:.1f}s) — processed {len(flat_images)} images, {total_detections} total detections",
        )

        return io.NodeOutput(out_images, out_masks, out_segs, out_datas)


# ============================================================================
# Persist-on-Execute
# ============================================================================


def _persist_defaults(**kwargs):
    # Compare current values against stored defaults and save changes.
    # Only writes if at least one value differs.
    defaults = load_defaults()
    updates = {
        key: value for key, value in kwargs.items() if defaults.get(key) != value
    }
    if updates:
        save_defaults(updates)
