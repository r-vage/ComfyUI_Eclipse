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

import numpy as np  # type: ignore
import torch  # type: ignore

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.logger import log
from ..core.common import make_comfy_tqdm_class
from ..core.sml.model_registry import (
    get_detection_model_list,
    get_model_entry,
    is_model_separator,
    is_trust_remote_code_allowed,
    load_defaults,
    save_defaults,
    FAMILY_MAP,
)
from ..core.sml.tasks import (
    TASK_BY_NAME,
    get_detection_task_names,
    get_system_prompt,
)
from ..core.sml.config_templates import (
    TemplateContext,
    get_config_value,
    get_llm_models_path,
)
from ..core.sml.loader_base import load_model_with_backend
from ..core.sml.model_files import ensure_model_path, verify_model_integrity
from ..core.sml.backend_yolo import load_yolo_model, detect_yolo, unload_yolo_model, resolve_yolo_model_path, download_yolo_model
from ..core.sml.vlm_detection import (
    tensor_to_pil,
    nms_filter,
    draw_bboxes,
    combined_mask,
    select_detection,
    build_segs,
    scale_bboxes_to_original,
    parse_qwen_detection_json,
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

_BACKEND_TO_METHOD = {
    "transformers": "Transformers",
    "gguf": "GGUF (llama-cpp-python)",
    "ollama": "Ollama (Docker)",
    "vllm": "vLLM (Docker)",
    "sglang": "SGLang (Docker)",
}

_FAMILY_TO_EXEC = {
    "Qwen": "Qwen",
    "Florence": "Florence",
}

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
    def __init__(self, model, processor, model_type, is_gguf, is_quantized, keep_model_loaded, dtype=None, chat_handler_ref=None):
        self.model = model
        self.processor = processor
        self.model_type = model_type
        self.is_gguf = is_gguf
        self.is_vllm = False
        self.is_quantized = is_quantized
        self.keep_model_loaded = keep_model_loaded
        self.tokenizer = getattr(processor, "tokenizer", None) or processor
        self.dtype = dtype if dtype is not None else getattr(model, "dtype", torch.float16)
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
# Model Path Resolution (simplified for detection node)
# ============================================================================

def _resolve_model_path(entry, quantization=None):
    # Resolve local model path from a registry entry.
    # Returns (model_path, needs_download).
    from pathlib import Path
    import folder_paths  # type: ignore

    backend = entry["backend"]
    repo_id = entry.get("repo_id", "")
    name = entry["name"]

    # Docker backends — use model identifier
    if backend in ("ollama", "vllm", "sglang"):
        return repo_id, False

    llm_base = get_llm_models_path()

    if backend == "gguf":
        file_pattern = entry.get("file_pattern", "")
        repo_folder = repo_id.split("/")[-1] if "/" in repo_id else name

        gguf_filenames = []
        if file_pattern and quantization:
            gguf_filenames.append(file_pattern.replace("{quant}", quantization))
        if quantization:
            for variant in (f"{name}-{quantization}.gguf", f"{name}.{quantization}.gguf"):
                if variant not in gguf_filenames:
                    gguf_filenames.append(variant)

        seen = set()
        candidates = []
        for c in [repo_folder, name] + ([f"{name}-{quantization}"] if quantization else []):
            if c not in seen:
                seen.add(c)
                candidates.append(c)

        for folder_name in candidates:
            candidate_dir = llm_base / folder_name
            if not candidate_dir.exists():
                continue
            for fn in gguf_filenames:
                if (candidate_dir / fn).is_file():
                    return str(candidate_dir / fn), False

        for folder_name in candidates:
            candidate_dir = llm_base / folder_name
            if not candidate_dir.exists():
                continue
            gguf_files = list(candidate_dir.glob("*.gguf"))
            if len(gguf_files) == 1:
                return str(gguf_files[0]), False

        for fn in gguf_filenames:
            if (llm_base / fn).is_file():
                return str(llm_base / fn), False

        return str(llm_base / repo_folder), True

    # Transformers: check llm folder, florence2 folder
    model_dir = llm_base / name
    if model_dir.exists():
        return str(model_dir), False

    if "/" in repo_id:
        alt_dir = llm_base / repo_id.split("/")[-1]
        if alt_dir.exists():
            return str(alt_dir), False

    if entry.get("family") == "Florence":
        models_dir = Path(folder_paths.models_dir)
        florence_dir = models_dir / "florence2" / name
        if florence_dir.exists():
            return str(florence_dir), False

    return str(model_dir), True


def _get_model_source(entry):
    # Determine download source: per-entry "source" override → global defaults → "huggingface".
    source = entry.get("source")
    if not source:
        source = load_defaults().get("model_source", "huggingface")
    return source.lower()


def _get_auth_token(source):
    # Get authentication token for the download source.
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


def _download_single_file(repo_id, filename, local_dir, source="huggingface", token=None):
    # Download a single file from HuggingFace or ModelScope with ComfyUI progress bar.
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
            log.msg(_LOG_PREFIX, "Using Xet high-performance transfer for accelerated download")

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
    backend = entry["backend"]
    repo_id = entry.get("repo_id", "")

    # ── GGUF: single-file download ───────────────────────────
    if backend == "gguf" and quantization and repo_id:
        file_pattern = entry.get("file_pattern", "")
        if not file_pattern:
            log.warning(_LOG_PREFIX, "GGUF entry missing file_pattern, falling back to snapshot")
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
            log.msg(_LOG_PREFIX, f"Downloading GGUF file from {source_label}: {filename}")
            if token:
                log.debug(_LOG_PREFIX, f"Using {source_label} token for authenticated download")

            try:
                _download_single_file(repo_id, filename, str(target_dir), source, token)
            except Exception as e:
                err_name = type(e).__name__
                if "NotExist" in err_name or "EntryNotFound" in err_name or "404" in str(e):
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
                log.msg(_LOG_PREFIX, f"Downloading mmproj from {source_label}: {mmproj}")
                try:
                    _download_single_file(repo_id, mmproj, str(target_dir), source, token)
                except Exception as e:
                    err_name = type(e).__name__
                    if "NotExist" in err_name or "EntryNotFound" in err_name or "404" in str(e):
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
                    result = verify_model_integrity(Path(file_to_verify), repo_id, hf_filename=hf_name, return_details=True)
                    is_success = result.success if hasattr(result, "success") else result
                    if is_success:
                        break
                    if attempt < max_retries:
                        log.warning(_LOG_PREFIX, f"{label} verification failed, re-downloading (attempt {attempt + 2}/{max_retries + 1})")
                        try:
                            Path(file_to_verify).unlink(missing_ok=True)
                            sha_file = Path(file_to_verify).parent / f"{Path(file_to_verify).name}.sha256"
                            sha_file.unlink(missing_ok=True)
                            _download_single_file(repo_id, hf_name, str(target_dir), source, token)
                        except Exception as e:
                            log.error(_LOG_PREFIX, f"Re-download of {label} failed: {e}")
                            break
                    else:
                        log.error(_LOG_PREFIX, f"{label} integrity verification failed after {max_retries + 1} attempts")

            return str(target_file)

    # ── Non-GGUF: full snapshot via ensure_model_path ────────
    family_str = entry.get("family", "")
    loading_method = _BACKEND_TO_METHOD.get(backend, "Transformers")
    model_family = _FAMILY_TO_EXEC.get(family_str, family_str)

    temp_info = {
        "repo_id": repo_id,
        "local_path": "",
        "model_family": model_family,
        "loading_method": loading_method,
    }
    if backend == "gguf" and entry.get("mmproj"):
        temp_info["mmproj_url"] = ""
        temp_info["mmproj_path"] = ""

    model_path, _, _ = ensure_model_path(temp_info)
    return str(model_path)


# ============================================================================
# VLM Generation Dispatch (Florence + Qwen, simplified for detection)
# ============================================================================

def _generate_florence_detection(instance, image, task_name, user_input,
                                  max_tokens, num_beams, do_sample, seed,
                                  repetition_penalty, convert_to_bboxes, context_size,
                                  detection_filter_threshold, nms_iou_threshold):
    # Generate detection results with Florence-2.
    from ..core.sml.backend_transformers import generate_transformers

    task_obj = TASK_BY_NAME.get(task_name)
    if not task_obj or not task_obj.florence_id:
        raise ValueError(f"Task '{task_name}' not supported by Florence-2 (no florence_id)")

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


def _generate_qwen_detection(instance, image, task_name, user_input,
                               max_tokens, temperature, top_p, top_k, seed,
                               num_beams, do_sample, repetition_penalty,
                               context_size, backend):
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
                image, max_pixels=VLM_MAX_PIXELS_DOCKER)
            kw = dict(smart_lm_instance=instance, prompt=prompt,
                      image_paths=image_paths, max_tokens=max_tokens,
                      temperature=temperature, top_p=top_p, top_k=top_k, seed=seed,
                      vision_task=task_name)
            gen = generate_vllm(**kw)
            result = gen[0] if isinstance(gen, tuple) else gen

        elif hasattr(instance, "is_sglang") and instance.is_sglang:
            from ..core.sml.backend_sglang_docker import generate_sglang
            from ..core.sml.vlm_detection import VLM_MAX_PIXELS_DOCKER
            image_paths, original_size, resized_size = _tensor_to_temp_jpegs(
                image, max_pixels=VLM_MAX_PIXELS_DOCKER)
            kw = dict(smart_lm_instance=instance, prompt=prompt,
                      image_paths=image_paths, max_tokens=max_tokens,
                      temperature=temperature, top_p=top_p, top_k=top_k, seed=seed,
                      vision_task=task_name)
            gen = generate_sglang(**kw)
            result = gen[0] if isinstance(gen, tuple) else gen

        elif hasattr(instance, "is_ollama") and instance.is_ollama:
            from ..core.sml.backend_ollama_docker import generate_ollama
            from ..core.sml.vlm_detection import VLM_MAX_PIXELS_DOCKER
            image_paths, original_size, resized_size = _tensor_to_temp_jpegs(
                image, max_pixels=VLM_MAX_PIXELS_DOCKER)
            result, _ = generate_ollama(
                smart_lm_instance=instance, prompt=prompt,
                image_paths=image_paths, max_tokens=max_tokens,
                temperature=temperature, top_p=top_p, top_k=top_k,
                seed=seed, repetition_penalty=repetition_penalty,
                vision_task=task_name)

        elif hasattr(instance, "is_llamacpp_docker") and instance.is_llamacpp_docker:
            from ..core.sml.backend_llamacpp_docker import generate_llamacpp
            from ..core.sml.vlm_detection import VLM_MAX_PIXELS_DOCKER
            image_paths, original_size, resized_size = _tensor_to_temp_jpegs(
                image, max_pixels=VLM_MAX_PIXELS_DOCKER)
            result, _ = generate_llamacpp(
                smart_lm_instance=instance, prompt=prompt,
                image_paths=image_paths, max_tokens=max_tokens,
                temperature=temperature, top_p=top_p, top_k=top_k,
                seed=seed, repetition_penalty=repetition_penalty,
                vision_task=task_name)

        elif instance.is_gguf:
            from ..core.sml.backend_gguf import generate_gguf
            result = generate_gguf(
                smart_lm_instance=instance, model_type="vision",
                image=image, prompt=prompt,
                max_tokens=max_tokens, temperature=temperature,
                top_p=top_p, top_k=top_k, seed=seed,
                repetition_penalty=repetition_penalty,
                vision_task=task_name)

        else:
            # Transformers
            from ..core.sml.backend_transformers import generate_transformers
            result, data = generate_transformers(
                smart_lm_instance=instance, model_family="QwenVL",
                image=image, prompt=prompt,
                max_tokens=max_tokens, temperature=temperature,
                top_p=top_p, top_k=top_k, seed=seed,
                repetition_penalty=repetition_penalty, num_beams=num_beams,
                do_sample=do_sample, context_size=context_size,
                vision_task=task_name)
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
    if original_size and resized_size and original_size != resized_size and data.get("bboxes"):
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
        log.debug(_LOG_PREFIX, f"YOLO class filter: all {len(bboxes)} detection(s) match requested={sorted(requested)}")
        return data, instance_masks

    dropped = len(bboxes) - len(keep)
    for i in range(len(bboxes)):
        lbl = labels[i] if i < len(labels) else "?"
        conf = f"{confidences[i]:.2f}" if i < len(confidences) else "?"
        status = "KEEP" if i in keep else "DROP"
        log.debug(_LOG_PREFIX, f"  YOLO class filter [{status}] #{i}: '{lbl}' conf={conf} — requested={sorted(requested)}")
    log.debug(_LOG_PREFIX, f"YOLO class filter: {dropped}/{len(bboxes)} dropped, {len(keep)} kept")

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
    elif loading_method == "SGLang (Docker)" or (hasattr(instance, "is_sglang") and instance.is_sglang):
        from ..core.sml import backend_sglang_docker
        backend_sglang_docker.stop_sglang_container()
    elif loading_method == "Ollama (Docker)":
        from ..core.sml import backend_ollama_docker
        backend_ollama_docker.stop_ollama_container()
    elif loading_method == "llama.cpp (Docker)":
        from ..core.sml import backend_llamacpp_docker
        backend_llamacpp_docker.stop_llamacpp_container()

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
        from ..core.sml.model_cache import clear_transformers_cache, is_transformers_cache_empty
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
            log.debug(_LOG_PREFIX, f"  Area filter [KEEP] #{i}: '{lbl}' area_ratio={ratio:.2%} <= threshold={detection_filter:.0%}")
        else:
            log.debug(_LOG_PREFIX, f"  Area filter [DROP] #{i}: '{lbl}' area_ratio={ratio:.2%} > threshold={detection_filter:.0%}")

    if len(keep) == len(bboxes):
        return data, instance_masks

    log.debug(_LOG_PREFIX, f"Area filter: {len(bboxes) - len(keep)}/{len(bboxes)} dropped (threshold={detection_filter:.0%} of {image_w}x{image_h})")
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
            log.debug(_LOG_PREFIX, f"  Drop-size filter [DROP] #{i}: '{lbl}' size={w:.0f}x{h:.0f} <= min={drop_size}px")

    if len(keep) == len(bboxes):
        return data, instance_masks

    log.debug(_LOG_PREFIX, f"Drop-size filter: {len(bboxes) - len(keep)}/{len(bboxes)} dropped (min_size={drop_size}px)")
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
        log.debug(_LOG_PREFIX, f"  Confidence filter [{status}] #{i}: '{lbl}' conf={c:.2f} vs threshold={confidence:.2f}")
    log.debug(_LOG_PREFIX, f"Confidence filter: {len(bboxes) - len(keep)}/{len(bboxes)} dropped (threshold={confidence:.2f})")

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
        first_model = next((m for m in models if not is_model_separator(m)), models[0] if models else "")
        defaults = load_defaults()
        det_tasks = get_detection_task_names()
        quant_placeholders = defaults.get("quantizations", ["Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"])

        return io.Schema(
            node_id="Smart Detection [Eclipse]",
            display_name="Smart Detection",
            search_aliases=["Smart Detection SML", "YOLO", "VLM Detection", "WD14"],
            category=CATEGORY.MAIN.value + CATEGORY.LOADER.value,
            description="Detection node with Florence-2, Qwen VL, and YOLO backends. "
                        "Outputs: image (preview boxes), mask, SEGS (Impact Pack compatible), data (JSON).",
            inputs=[
                # ── Mode bar backing widgets (hidden, synced by JS chips) ──
                io.Boolean.Input("cleanup", default=True, socketless=True,
                    extra_dict={"hidden": True},
                    tooltip="VRAM garbage collection — clear VRAM cache and run Python garbage collection before loading the model."),
                io.Boolean.Input("keep_model_loaded", default=False, socketless=True,
                    extra_dict={"hidden": True},
                    tooltip="Keep the detection model cached in VRAM between runs to skip loading/unloading latency (highly recommended for performance)."),
                io.Boolean.Input("enable_preview_boxes", default=True, socketless=True,
                    extra_dict={"hidden": True},
                    tooltip="Superimpose colored bounding box outlines and text labels onto the output preview image."),
                io.Boolean.Input("show_adjust", default=False, socketless=True,
                    extra_dict={"hidden": True},
                    tooltip="Toggle visibility of post-processing filters/adjustments (box expansion, mask dilation, size filters)."),
                io.Boolean.Input("show_advanced", default=False, socketless=True,
                    extra_dict={"hidden": True},
                    tooltip="Toggle visibility of advanced hardware settings and backend sampling options."),

                # ── Main widgets ──────────────────────────────────────
                io.Combo.Input("model_name", options=models, default=first_model,
                    tooltip="Choose the object detection or VLM grounding model to load.\n"
                            "Suffixes indicate the backend engine (no suffix=Transformers, -GGUF=llama.cpp/GGUF local engine)."),
                io.Combo.Input("quantization", options=quant_placeholders, default="Q4_K_M",
                    tooltip="Choose GGUF quantization precision. Lower bits (e.g. Q4_K_M) use less VRAM but lose accuracy. "
                            "Higher bits (e.g. Q8_0) are more accurate but demand more VRAM. Only applies to GGUF models."),
                io.Combo.Input("task", options=det_tasks,
                    default=det_tasks[0] if det_tasks else "Caption to Phrase Grounding",
                    tooltip="Type of detection task to run:\n"
                            "• Caption to Phrase Grounding: Locates objects matching user_input query.\n"
                            "• Referring Expression Segmentation: Generates segmented mask regions for specified objects.\n"
                            "• Region Caption: Detects objects and labels them with descriptions.\n"
                            "• YOLO runs object detection automatically."),
                io.String.Input("user_input", default="", multiline=True,
                    tooltip="Input query for detection:\n"
                            "• Florence/Qwen grounding: The phrase/object you want to locate (e.g. 'the black cat').\n"
                            "• YOLO: Semicolon-separated target classes to filter (e.g. 'person;car;backpack'). Leave empty to output all classes."),

                # ── Detection parameters ──────────────────────────────
                io.Float.Input("confidence", default=float(defaults.get("confidence", 0.5)), min=0.0, max=1.0, step=0.01,
                    tooltip="Minimum confidence score (0.0 to 1.0) required for a detection to be kept. "
                            "Higher values reduce false positives, lower values capture more candidate objects."),
                io.Float.Input("nms_iou_threshold", default=float(defaults.get("nms_iou_threshold", 0.5)), min=0.0, max=1.0, step=0.01,
                    tooltip="Non-Maximum Suppression (NMS) intersection-over-union threshold. "
                            "Lower values (e.g. 0.3) aggressively merge overlapping boxes; higher values (e.g. 0.7) keep separate but close detections."),
                io.Float.Input("detection_filter", default=float(defaults.get("detection_filter", 0.8)), min=0.0, max=1.0, step=0.01,
                    tooltip="Max ratio of bounding box area to total image area. "
                            "Detections covering more than this ratio (e.g. 0.8) are ignored to filter out useless full-image bounding boxes."),
                io.Int.Input("drop_size", default=int(defaults.get("drop_size", 10)), min=1, max=8192, step=1,
                    tooltip="Minimum width or height (in pixels) for a bounding box. Smaller boxes are discarded (helps filter out tiny noise)."),
                io.Float.Input("crop_factor", default=float(defaults.get("crop_factor", 3.0)), min=1.0, max=100.0, step=0.1,
                    tooltip="Scale factor to expand the cropped region around detected objects when outputting to Impact Pack SEGS. "
                            "A value of 3.0 captures 3x the box size."),
                io.Int.Input("dilation", default=int(defaults.get("dilation", 0)), min=-512, max=512, step=1,
                    tooltip="Expand (positive values) or shrink (negative values) the boundaries of the output mask by a set number of pixels."),
                io.Int.Input("select_index", default=int(defaults.get("select_index", -1)), min=-1, max=999, step=1,
                    tooltip="Select a single detection: set to -1 to output all detections merged; "
                            "set to 0, 1, 2... to output only the N-th detection (useful for isolating objects)."),

                # ── Advanced widgets (hidden by default) ──────────────
                io.Combo.Input("device", options=["cuda", "cpu", "mps"],
                    default=str(defaults.get("device", "cuda")),
                    tooltip="Hardware device to load the model onto. "
                            "Use 'cuda' for NVIDIA GPUs, 'mps' for Apple Silicon, or 'cpu' (slow, fallback)."),
                io.Int.Input("num_beams", default=int(defaults.get("num_beams", 1)),
                    min=1, max=10, step=1,
                    tooltip="Number of parallel paths explored during beam search. "
                            "Values > 1 produce higher quality text/grounding but are significantly slower. Set to 1 for standard sampling."),
                io.Boolean.Input("do_sample", default=bool(defaults.get("do_sample", True)),
                    tooltip="When enabled, uses probabilistic sampling (temperature, top_p, top_k). "
                            "When disabled, uses greedy decoding (always picking the most likely next word, ignoring temperature)."),
                io.Boolean.Input("use_torch_compile", default=bool(defaults.get("use_torch_compile", False)),
                    tooltip="JIT compiles the model using PyTorch 2.x compile. "
                            "Increases initial startup/load time (~1-3 minutes first run) but speeds up subsequent inference runs."),
                io.Boolean.Input("convert_to_bboxes", default=bool(defaults.get("convert_to_bboxes", False)),
                    tooltip="Florence-2 only: Convert quad/polygon coordinates (which outline precise shapes) to standard rectangular bounding boxes."),
                io.Float.Input("temperature", default=float(defaults.get("temperature", 0.7)),
                    min=0.1, max=2.0, step=0.1,
                    tooltip="Controls randomness for generative VLMs: higher values (e.g. 0.8+) make output more creative/diverse; "
                            "lower values make it more deterministic. Florence-2 ignores this."),
                io.Float.Input("top_p", default=float(defaults.get("top_p", 0.9)),
                    min=0.1, max=1.0, step=0.05,
                    tooltip="Nucleus sampling: limits generation to the top cumulative probability tokens (e.g. 0.9 keeps top 90% likely words). "
                            "Filters out low-probability gibberish. Florence-2 ignores this."),
                io.Int.Input("top_k", default=int(defaults.get("top_k", 50)),
                    min=0, max=1000, step=1,
                    tooltip="Limits generation to the top K most likely next words. Lower values (e.g. 40) make output more focused; 0 disables it. Florence-2 ignores this."),
                io.Float.Input("repetition_penalty",
                    default=float(defaults.get("repetition_penalty", 1.0)),
                    min=1.0, max=2.0, step=0.1,
                    tooltip="Penalizes repeating the same phrases or words. Values > 1.0 (e.g. 1.1 or 1.2) help reduce loops. Florence-2 ignores this."),

                # ── Seed (last — JS adds buttons after it) ───────────
                io.Int.Input("seed", default=-1, min=-3, max=2**64 - 1, step=1,
                    tooltip="Controls generation reproducibility. Use specific values for deterministic output:\n"
                            "• -1: Randomize the seed on every execution\n"
                            "• -2: Increment the seed by 1 after each run\n"
                            "• -3: Decrement the seed by 1 after each run"),

                # ── Image input ───────────────────────────────────────
                io.Image.Input("image", tooltip="Input image or video batch to detect objects, segments, or text in."),
            ],
            outputs=[
                io.Image.Output("image",
                    tooltip="Preview boxes or passthrough."),
                io.Mask.Output("mask",
                    tooltip="Binary mask of detections."),
                io.Custom("SEGS").Output("segs",
                    tooltip="Impact Pack compatible SEGS tuple."),
                io.Custom("JSON").Output("data",
                    tooltip="Detection data dict (bboxes, labels, coord_range). Connect to Detection to Bboxes."),
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

        # ── Persist user-tweaked defaults ──────────────────────
        # Run early so values are saved even if execution fails downstream.
        # All advanced/adjust widgets are always populated regardless of
        # show_advanced/show_adjust chip state, so user values are always
        # honored at execution time.
        _persist_defaults(
            confidence=confidence, nms_iou_threshold=nms_iou_threshold,
            detection_filter=detection_filter, drop_size=drop_size,
            crop_factor=crop_factor, dilation=dilation, select_index=select_index,
            device=device, num_beams=num_beams, do_sample=do_sample,
            use_torch_compile=use_torch_compile, convert_to_bboxes=convert_to_bboxes,
            temperature=temperature, top_p=top_p, top_k=top_k,
            repetition_penalty=repetition_penalty,
        )

        # ── Seed resolution ─────────────────────────────────────
        if seed in (-1, -2, -3):
            seed = _new_random_seed()
        # Persist resolved seed into workflow metadata
        prompt = cls.hidden.prompt
        extra_pnginfo = cls.hidden.extra_pnginfo
        unique_id = cls.hidden.unique_id
        if unique_id is not None:
            uid = unique_id
            if prompt and uid in prompt:
                prompt[uid]["inputs"]["seed"] = seed
            if extra_pnginfo and "workflow" in extra_pnginfo:
                for node in extra_pnginfo["workflow"].get("nodes", []):
                    if str(node.get("id")) == uid:
                        wv = node.get("widgets_values")
                        if isinstance(wv, dict) and "seed" in wv:
                            wv["seed"] = seed
                        elif isinstance(wv, list):
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
        model_family = _FAMILY_TO_EXEC.get(family_str, family_str)

        log.msg(_LOG_PREFIX, f"Model: {model_name} | family={family_str} | backend={backend}")

        # ── 2. Image prep ───────────────────────────────────────
        if image.dim() == 4 and image.shape[0] > 1:
            log.warning(_LOG_PREFIX, "Multi-frame input, using first frame")
            image = image[0:1]

        pil_image = tensor_to_pil(image)
        if pil_image is None:
            raise ValueError("Failed to convert input tensor to PIL Image")
        image_h, image_w = pil_image.height, pil_image.width

        # Empty output helpers
        empty_mask = torch.zeros((1, image_h, image_w), dtype=torch.float32)
        empty_segs = ((image_h, image_w), [])

        # ── 3. Pre-validate ─────────────────────────────────────
        if family_str != "YOLO" and task in _REQUIRES_USER_INPUT and (not user_input or not user_input.strip()):
            raise ValueError(f"user_input is required for '{task}' — describe what to detect")

        # ── 4. Family dispatch ──────────────────────────────────
        data = {}
        instance_masks = []
        instance = None

        if family_str == "YOLO":
            # YOLO — resolve path, auto-download if missing
            filename = entry.get("filename", name)
            if not isinstance(filename, str):
                filename = str(filename) if filename is not None else ""
            model_path = resolve_yolo_model_path(filename)
            if model_path is None:
                # Not on disk — download if registry entry has a URL
                try:
                    model_path = download_yolo_model(entry)
                except FileNotFoundError:
                    log.warning(_LOG_PREFIX, f"YOLO model '{filename}' not found locally or online — skipping detection. "
                                f"Place the .pt file in ultralytics/bbox/ or ultralytics/segm/.")
                    return io.NodeOutput(image, empty_mask, empty_segs, {"bboxes": [], "labels": [], "error": f"Model '{filename}' not found"})
                # Reset progress bar after download
                import comfy.utils  # type: ignore
                comfy.utils.ProgressBar(1).update_absolute(0, 1)
            yolo_model = load_yolo_model(model_path, device=device)
            _, data, instance_masks = detect_yolo(yolo_model, pil_image, confidence=confidence, device=device)

            # Filter by user_input class names
            data, instance_masks = _filter_yolo_by_class(data, instance_masks, user_input)

        elif family_str == "Florence":
            # Florence-2 — Transformers only
            model_path, needs_download = _resolve_model_path(entry)
            if needs_download:
                log.msg(_LOG_PREFIX, f"Downloading Florence model: {repo_id}")
                model_path = _ensure_downloaded(entry)
                # Reset progress bar after download so generation progress starts fresh
                import comfy.utils  # type: ignore
                comfy.utils.ProgressBar(1).update_absolute(0, 1)

            ctx = TemplateContext.from_widgets(
                model_family="Florence", model_type="",
                loading_method="Transformers", quantization="auto",
                attention_mode="auto", repo_id=repo_id,
                local_path=model_path, quantized=False, default_task="",
                has_vision=True, max_tokens=4096, context_size=4096,
            )
            model_obj, processor, model_type = load_model_with_backend(
                loading_method="Transformers", model_family="Florence",
                model_path=model_path, ctx=ctx,
                quantization="auto", attention_mode="auto",
                device=device, memory_cleanup=cleanup,
                keep_model_loaded=keep_model_loaded,
                use_torch_compile=use_torch_compile,
                trust_remote_code=is_trust_remote_code_allowed(name),
            )

            instance = _Wrapper(
                model=model_obj, processor=processor, model_type=model_type,
                is_gguf=False, is_quantized=False, keep_model_loaded=keep_model_loaded,
                dtype=getattr(model_obj, "dtype", torch.float16),
            )

            # Check for text-mode tasks
            if task in _TEXT_MODE_TASKS:
                result_text, result_data = _generate_florence_detection(
                    instance, image, task, user_input,
                    max_tokens=4096, num_beams=num_beams, do_sample=do_sample,
                    seed=seed, repetition_penalty=repetition_penalty,
                    convert_to_bboxes=False, context_size=4096,
                    detection_filter_threshold=detection_filter,
                    nms_iou_threshold=nms_iou_threshold,
                )
                text_data = {
                    "bboxes": [], "labels": [], "text": result_text,
                    "coord_range": 0, "backend": "Florence-2", "model": name, "task": task,
                }
                elapsed = time.time() - start_time
                log.msg(_LOG_PREFIX, f"Text-mode complete ({elapsed:.1f}s): {result_text[:100]}...")

                if not keep_model_loaded and instance is not None:
                    _cleanup_model(loading_method="Transformers", keep_model_loaded=False,
                                   model_path=model_path, instance=instance)
                _selective_cleanup()

                return io.NodeOutput(image, empty_mask, empty_segs, text_data)

            # Detection mode
            _, data = _generate_florence_detection(
                instance, image, task, user_input,
                max_tokens=4096, num_beams=num_beams, do_sample=do_sample,
                seed=seed, repetition_penalty=repetition_penalty,
                convert_to_bboxes=convert_to_bboxes, context_size=4096,
                detection_filter_threshold=detection_filter,
                nms_iou_threshold=nms_iou_threshold,
            )
            # Ensure coord_range is set
            data.setdefault("coord_range", 0)
            data.setdefault("backend", "Florence-2")
            data.setdefault("model", name)
            data.setdefault("task", task)

            # Apply confidence filter for Florence (no native confidence scores usually)
            data, instance_masks = _apply_confidence_filter(data, instance_masks, confidence)

        elif family_str == "Qwen":
            # Qwen VL — all backends
            model_path, needs_download = _resolve_model_path(entry, quantization if backend == "gguf" else None)
            if needs_download:
                log.msg(_LOG_PREFIX, f"Downloading Qwen model: {repo_id}")
                model_path = _ensure_downloaded(entry, quantization if backend == "gguf" else None)
                # Reset progress bar after download so generation progress starts fresh
                import comfy.utils  # type: ignore
                comfy.utils.ProgressBar(1).update_absolute(0, 1)

            ctx = TemplateContext.from_widgets(
                model_family="Qwen", model_type="",
                loading_method=loading_method, quantization=quantization if backend == "gguf" else "auto",
                attention_mode="auto", repo_id=repo_id,
                local_path=model_path, quantized=False, default_task="",
                has_vision=True, max_tokens=4096, context_size=8192,
            )
            if backend == "ollama":
                ctx.update(model_source="Ollama", ollama_model=repo_id)
            if backend == "gguf" and entry.get("mmproj"):
                from pathlib import Path
                mmproj_path = get_llm_models_path() / (repo_id.split("/")[-1] if "/" in repo_id else name) / entry["mmproj"]
                if mmproj_path.exists():
                    ctx.mmproj_path = str(mmproj_path)

            n_batch = int(load_defaults().get("n_batch", 512)) if backend == "gguf" else 512
            model_obj, processor, model_type = load_model_with_backend(
                loading_method=loading_method, model_family="Qwen",
                model_path=model_path, ctx=ctx,
                quantization=quantization if backend == "gguf" else "auto",
                attention_mode="auto", device=device,
                context_size=8192, n_batch=n_batch,
                memory_cleanup=cleanup, keep_model_loaded=keep_model_loaded,
                use_torch_compile=use_torch_compile,
                trust_remote_code=is_trust_remote_code_allowed(name),
            )

            # Build wrapper
            if hasattr(model_obj, "is_vllm") or hasattr(model_obj, "is_sglang") or \
               hasattr(model_obj, "is_ollama") or hasattr(model_obj, "is_llamacpp_docker"):
                instance = model_obj
                instance.model_type = model_type
            else:
                instance = _Wrapper(
                    model=model_obj, processor=processor, model_type=model_type,
                    is_gguf=(backend == "gguf"),
                    is_quantized=(ctx.quantization not in (None, "auto", "fp16", "bf16", "fp32")),
                    keep_model_loaded=keep_model_loaded,
                    chat_handler_ref=getattr(model_obj, "_eclipse_chat_handler", None),
                )

            result_text, data, original_size, resized_size = _generate_qwen_detection(
                instance, image, task, user_input,
                max_tokens=4096, temperature=temperature, top_p=top_p,
                top_k=top_k, seed=seed, num_beams=num_beams, do_sample=do_sample,
                repetition_penalty=repetition_penalty, context_size=8192,
                backend=backend,
            )

            data.setdefault("coord_range", 0)
            data.setdefault("backend", "Qwen")
            data.setdefault("model", name)
            data.setdefault("task", task)

            # Apply confidence filter for Qwen
            data, instance_masks = _apply_confidence_filter(data, instance_masks, confidence)

        else:
            raise ValueError(f"Unsupported model family: {family_str}")

        # ── 5. Post-process (shared for all families) ───────────
        bboxes = data.get("bboxes", [])
        if not bboxes:
            log.msg(_LOG_PREFIX, "No detections")
            empty_data = dict(data)
            empty_data.setdefault("bboxes", [])
            empty_data.setdefault("labels", [])

            if not keep_model_loaded and instance is not None:
                _cleanup_model(loading_method=loading_method, keep_model_loaded=False,
                               model_path=locals().get("model_path", ""), instance=instance)
            _selective_cleanup()

            return io.NodeOutput(image, empty_mask, empty_segs, empty_data)

        # 5a. NMS
        pre_nms = len(data["bboxes"])
        nms_bboxes, nms_labels, keep_indices = nms_filter(
            data["bboxes"], data.get("labels", []), nms_iou_threshold)
        data["bboxes"] = nms_bboxes
        data["labels"] = nms_labels
        confs = data.get("confidences", [])
        if confs:
            data["confidences"] = [confs[i] for i in keep_indices if i < len(confs)]
        if instance_masks:
            instance_masks = [instance_masks[i] for i in keep_indices if i < len(instance_masks)]
        nms_dropped = pre_nms - len(nms_bboxes)
        if nms_dropped > 0:
            log.debug(_LOG_PREFIX, f"NMS: {nms_dropped}/{pre_nms} suppressed (iou_threshold={nms_iou_threshold})")

        # 5b. Detection filter (VLM only — YOLO confidence is reliable, skip area filter)
        if family_str != "YOLO":
            data, instance_masks = _apply_detection_filter(data, instance_masks, detection_filter, image_h, image_w)
        else:
            log.debug(_LOG_PREFIX, "Area filter skipped for YOLO backend")

        # 5c. Drop size
        data, instance_masks = _apply_drop_size(data, instance_masks, drop_size)

        # 5d. Zero-detection check after filtering
        if not data.get("bboxes"):
            log.msg(_LOG_PREFIX, "No detections remaining after filtering")

            if not keep_model_loaded and instance is not None:
                _cleanup_model(loading_method=loading_method, keep_model_loaded=False,
                               model_path=locals().get("model_path", ""), instance=instance)
            _selective_cleanup()

            return io.NodeOutput(image, empty_mask, empty_segs, data)

        # ── 6. Build SEGS ───────────────────────────────────────
        all_segs = build_segs(data, image_h, image_w,
                              crop_factor=crop_factor, dilation=dilation,
                              instance_masks=instance_masks)

        # ── 7. Apply select_index ───────────────────────────────
        if select_index >= 0:
            output_data, output_masks = select_detection(
                data, select_index, image_h, image_w, instance_masks)
            mask_tensor = combined_mask(image_h, image_w, output_data, output_masks)

            # Single SEG
            idx = min(select_index, len(all_segs[1]) - 1) if all_segs[1] else 0
            if all_segs[1]:
                output_segs = ((image_h, image_w), [all_segs[1][idx]])
            else:
                output_segs = empty_segs
        else:
            output_data = data
            mask_tensor = combined_mask(image_h, image_w, data, instance_masks)
            output_segs = all_segs

        # ── 8. Image output ─────────────────────────────────────
        # NOTE: image always draws ALL bboxes (debug preview) — not affected by select_index
        if enable_preview_boxes and data.get("bboxes"):
            image_out = draw_bboxes(image, data)
        else:
            image_out = image

        # ── 9. Logging ──────────────────────────────────────────
        n_det = len(data.get("bboxes", []))
        elapsed = time.time() - start_time
        log.msg(_LOG_PREFIX, f"Done ({elapsed:.1f}s) — {n_det} detection(s)")

        # ── 10. Cleanup ─────────────────────────────────────────
        if family_str == "YOLO":
            if not keep_model_loaded:
                unload_yolo_model()
        elif instance is not None:
            _cleanup_model(
                loading_method=loading_method,
                keep_model_loaded=keep_model_loaded,
                model_path=locals().get("model_path", ""),
                instance=instance,
            )

        _selective_cleanup()

        return io.NodeOutput(image_out, mask_tensor, output_segs, output_data)


# ============================================================================
# Persist-on-Execute
# ============================================================================

def _persist_defaults(**kwargs):
    # Compare current values against stored defaults and save changes.
    # Only writes if at least one value differs.
    defaults = load_defaults()
    updates = {key: value for key, value in kwargs.items() if defaults.get(key) != value}
    if updates:
        save_defaults(updates)
