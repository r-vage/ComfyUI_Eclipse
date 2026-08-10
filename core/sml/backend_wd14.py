# SmartLM WD14 Tagger Backend
#
# Self-contained ONNX-based image tagging using WD14 (WaifuDiffusion) tagger models
# from SmilingWolf on HuggingFace. Outputs booru-style tags with confidence thresholds.
#
# Architecture:
# - ONNX InferenceSession (CUDA or CPU) for fast inference (~1-2s per image)
# - CSV tag dictionary with categories: rating (indices 0-8), general (category=0),
#   character (category=4)
# - Module-level session cache (reuse same model between runs)
# - Auto-download from HuggingFace on first use
#
# Usage:
#     from .backend_wd14 import load_wd14_model, tag_image, unload_wd14_model
#
#     session, tags_data = load_wd14_model("wd-eva02-large-tagger-v3")
#     result = tag_image(pil_image, session, tags_data, threshold=0.35, ...)
#     unload_wd14_model()

import csv
import gc
import hashlib
from pathlib import Path
from typing import Any

import numpy as np  # type: ignore
from PIL import Image

from .logger import log

_LOG_PREFIX = "WD14"


# ============================================================================
# Model Storage
# ============================================================================


def _get_models_dir() -> Path:
    # Get the base models directory (models/LLM/ or user-configured path).
    # WD14 models are stored alongside other models — no separate subfolder.
    from .config_templates import get_llm_models_path

    return get_llm_models_path()


# ============================================================================
# Model Loading & Caching
# ============================================================================

# Module-level cache for loaded WD14 model
_wd14_cache: dict[str, Any] = {}
_provider_quarantine: dict[str, str] = {}
_PERSISTENT_PROVIDER_ERROR_MARKERS = {
    "cudnn_status_sublibrary_version_mismatch": "cudnn_sublibrary_version_mismatch",
    "cuda_error_system_driver_mismatch": "cuda_driver_mismatch",
}
_PROVIDER_ERROR_MARKERS = (
    "cuda",
    "cudnn",
    "cublas",
    "tensorrt",
    "rocm",
    "executionprovider",
    "execution provider",
    "failed to load dynamic library",
    "failed to load shared library",
)


def _resolve_model_paths(model_name: str) -> tuple[Path, Path]:
    # Resolve the ONNX model file and CSV tag file for a given model name.
    # Supports both subfolder format and flat file format.
    models_dir = _get_models_dir()

    # Try subfolder format first: {model_name}/model.onnx
    model_dir = models_dir / model_name
    onnx_path = model_dir / "model.onnx"
    csv_path = model_dir / "selected_tags.csv"
    if onnx_path.exists() and csv_path.exists():
        return onnx_path, csv_path

    # Try flat format: {model_name}.onnx + {model_name}.csv
    onnx_flat = models_dir / f"{model_name}.onnx"
    csv_flat = models_dir / f"{model_name}.csv"
    if onnx_flat.exists() and csv_flat.exists():
        return onnx_flat, csv_flat

    raise FileNotFoundError(
        f"WD14 model '{model_name}' not found. Expected:\n"
        f"  {onnx_path} + {csv_path}\n"
        f"  or {onnx_flat} + {csv_flat}"
    )


def _load_tags_csv(csv_path: Path) -> dict[str, Any]:
    # Load the selected_tags.csv file and parse tag categories.
    # Returns dict with keys: tags, general_index, character_index
    tags = []
    general_index = None
    character_index = None

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # Skip header row
        for row in reader:
            tag_name = row[1]
            category = row[2]
            tags.append(tag_name)

            if general_index is None and category == "0":
                general_index = len(tags) - 1
            elif character_index is None and category == "4":
                character_index = len(tags) - 1

    return {
        "tags": tags,
        "general_index": general_index or 0,
        "character_index": character_index or len(tags),
    }


def _get_ort_runtime_fingerprint(ort: Any) -> str:
    # Scope persistent provider failures to the installed runtime stack.
    components = [
        str(getattr(ort, "__version__", "unknown")),
        ",".join(sorted(ort.get_available_providers())),
    ]
    try:
        import torch

        components.extend(
            [
                str(torch.__version__),
                str(getattr(torch.version, "cuda", None)),
                str(torch.backends.cudnn.version()),
            ]
        )
    except (ImportError, AttributeError, OSError, RuntimeError, TypeError):
        components.extend(["torch-unavailable", "cuda-unknown", "cudnn-unknown"])
    payload = "|".join(components).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _classify_provider_error(error: Exception) -> tuple[str, bool] | None:
    # Return a stable category and whether the runtime should be quarantined.
    message = str(error).lower()
    for marker, category in _PERSISTENT_PROVIDER_ERROR_MARKERS.items():
        if marker in message:
            return category, True
    if any(marker in message for marker in _PROVIDER_ERROR_MARKERS):
        return "accelerator_provider_failure", False
    return None


def _resolve_ort_provider_plan(
    ort: Any,
    requested_device: str,
) -> tuple[list[str], str, str | None]:
    # Translate the node device into an explicit ONNX Runtime provider order.
    device = requested_device.strip().lower()
    if device not in {"auto", "cuda", "cpu", "mps"}:
        raise ValueError(f"Unsupported WD14 device: {requested_device!r}")
    if device == "mps":
        raise RuntimeError(
            "WD14 does not currently provide an ONNX Runtime MPS/CoreML backend. "
            "Select CPU, CUDA, or auto."
        )

    available = ort.get_available_providers()
    if "CPUExecutionProvider" not in available:
        raise RuntimeError("ONNX Runtime CPUExecutionProvider is unavailable")

    runtime_fingerprint = _get_ort_runtime_fingerprint(ort)
    quarantine_category = _provider_quarantine.get(runtime_fingerprint)
    cuda_available = "CUDAExecutionProvider" in available

    if device == "cpu":
        providers = ["CPUExecutionProvider"]
    elif quarantine_category:
        providers = ["CPUExecutionProvider"]
        log.warning(
            _LOG_PREFIX,
            "CUDA provider quarantined for the current runtime after "
            f"{quarantine_category}; using CPUExecutionProvider",
        )
    elif cuda_available:
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    elif device == "cuda":
        raise RuntimeError(
            "WD14 requested CUDA, but ONNX Runtime does not expose "
            "CUDAExecutionProvider"
        )
    else:
        providers = ["CPUExecutionProvider"]

    log.debug(
        _LOG_PREFIX,
        f"ORT providers: requested={device}, effective={providers}, "
        f"available={available}, runtime={runtime_fingerprint}",
    )
    return providers, runtime_fingerprint, quarantine_category


def _get_ort_providers(requested_device: str = "auto") -> list[str]:
    # Compatibility helper returning the effective provider list.
    try:
        import onnxruntime as ort  # type: ignore

        providers, _, _ = _resolve_ort_provider_plan(ort, requested_device)
        return providers
    except ImportError:
        log.error(
            _LOG_PREFIX,
            "onnxruntime not installed. Install with: pip install onnxruntime-gpu",
        )
        raise


def _quarantine_runtime_provider(runtime_fingerprint: str, category: str) -> None:
    _provider_quarantine[runtime_fingerprint] = category


def reset_wd14_provider_quarantine() -> None:
    # Intentional manual retry boundary; ordinary model unload keeps quarantine.
    _provider_quarantine.clear()
    log.msg(_LOG_PREFIX, "WD14 provider quarantine cleared; CUDA may be probed again")


def _raise_cpu_fallback_failure(
    accelerator_error: Exception,
    cpu_error: Exception,
) -> None:
    raise RuntimeError(
        "WD14 accelerator inference failed and the CPU fallback also failed. "
        f"Accelerator error: {accelerator_error}. CPU error: {cpu_error}"
    ) from cpu_error


def load_wd14_model(
    model_name: str,
    device: str = "auto",
) -> tuple[Any, dict[str, Any]]:
    # Load a WD14 ONNX model and its tag dictionary.
    # Caches the session — reuses if same model requested, reloads if different.
    #
    # Returns:
    #     (InferenceSession, tags_data) where tags_data has keys: tags, general_index, character_index
    global _wd14_cache

    import onnxruntime as ort  # type: ignore

    providers, runtime_fingerprint, _ = _resolve_ort_provider_plan(ort, device)
    normalized_device = device.strip().lower()
    cache_identity = (
        model_name,
        normalized_device,
        runtime_fingerprint,
        tuple(providers),
    )

    if (
        _wd14_cache.get("cache_identity") == cache_identity
        and _wd14_cache.get("session") is not None
    ):
        log.debug(_LOG_PREFIX, f"Using cached WD14 model: {model_name}")
        return _wd14_cache["session"], _wd14_cache["tags_data"]

    if _wd14_cache.get("session") is not None:
        log.msg(
            _LOG_PREFIX,
            f"Switching WD14 model/provider: "
            f"{_wd14_cache.get('model_name')} → {model_name}",
        )
        unload_wd14_model()

    onnx_path, csv_path = _resolve_model_paths(model_name)

    log.msg(_LOG_PREFIX, f"Loading WD14 model: {model_name}")
    try:
        session = ort.InferenceSession(str(onnx_path), providers=providers)
    except Exception as e:
        classification = _classify_provider_error(e)
        attempted_cuda = providers[0] == "CUDAExecutionProvider"
        if not attempted_cuda or classification is None:
            raise
        category, persistent = classification
        if persistent:
            _quarantine_runtime_provider(runtime_fingerprint, category)
        log.warning(
            _LOG_PREFIX,
            f"WD14 CUDA provider initialization failed ({category}: {e}); "
            "retrying once with CPUExecutionProvider",
        )
        try:
            session = ort.InferenceSession(
                str(onnx_path), providers=["CPUExecutionProvider"]
            )
        except Exception as cpu_error:  # noqa: BLE001 - preserve both diagnostics
            _raise_cpu_fallback_failure(e, cpu_error)
        providers = ["CPUExecutionProvider"]
        cache_identity = (
            model_name,
            normalized_device,
            runtime_fingerprint,
            tuple(providers),
        )

    # Load tag dictionary
    tags_data = _load_tags_csv(csv_path)
    tag_count = len(tags_data["tags"])
    log.msg(_LOG_PREFIX, f"WD14 model loaded: {model_name} ({tag_count} tags)")

    # Cache
    _wd14_cache = {
        "model_name": model_name,
        "session": session,
        "tags_data": tags_data,
        "requested_device": normalized_device,
        "providers": providers,
        "runtime_fingerprint": runtime_fingerprint,
        "cache_identity": cache_identity,
    }

    return session, tags_data


def unload_wd14_model():
    # Unload the cached WD14 ONNX session to free memory.
    if _wd14_cache.get("session") is not None:
        model_name = _wd14_cache.get("model_name", "unknown")
        log.msg(_LOG_PREFIX, f"Unloading WD14 model: {model_name}")
        _wd14_cache["session"] = None
        _wd14_cache["tags_data"] = None
        _wd14_cache["model_name"] = None
        gc.collect()


def is_wd14_cached() -> bool:
    # Check if a WD14 model is currently cached.
    return _wd14_cache.get("session") is not None


# ============================================================================
# Image Preprocessing
# ============================================================================


def _preprocess_image(pil_image: Image.Image, target_size: int) -> np.ndarray:
    # Preprocess a PIL image for WD14 ONNX inference.
    #
    # Steps:
    # 1. Resize to fit within target_size while preserving aspect ratio
    # 2. Pad to square with white background
    # 3. Convert RGB → BGR (WD14 models expect BGR input)
    # 4. Convert to float32
    # 5. Expand to batch dimension (1, H, W, 3)
    #
    # Args:
    #     pil_image: Input PIL Image (any mode, will be converted to RGB)
    #     target_size: Target square size (typically 448, read from model metadata)
    #
    # Returns:
    #     numpy array with shape (1, target_size, target_size, 3), dtype float32

    # Ensure RGB
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")

    # Resize maintaining aspect ratio
    ratio = float(target_size) / max(pil_image.size)
    new_size = (int(pil_image.size[0] * ratio), int(pil_image.size[1] * ratio))
    if hasattr(Image, "Resampling"):
        resample_filter = Image.Resampling.LANCZOS
    else:
        resample_filter = Image.LANCZOS
    pil_image = pil_image.resize(new_size, resample_filter)

    # Pad to square with white background
    square = Image.new("RGB", (target_size, target_size), (255, 255, 255))
    offset_x = (target_size - new_size[0]) // 2
    offset_y = (target_size - new_size[1]) // 2
    square.paste(pil_image, (offset_x, offset_y))

    # Convert to numpy float32, RGB → BGR
    img_array = np.array(square).astype(np.float32)
    img_array = img_array[:, :, ::-1]  # RGB → BGR

    # Add batch dimension
    return np.expand_dims(img_array, 0)


# ============================================================================
# Tag Inference
# ============================================================================


def _run_wd14_session(pil_image: Image.Image, session: Any) -> np.ndarray:
    input_info = session.get_inputs()[0]
    target_size = input_info.shape[1]
    img_input = _preprocess_image(pil_image, target_size)
    output_name = session.get_outputs()[0].name
    return session.run([output_name], {input_info.name: img_input})[0]


def tag_image(
    pil_image: Image.Image,
    session: Any,
    tags_data: dict[str, Any],
    threshold: float = 0.35,
    char_threshold: float = 0.85,
    replace_underscore: bool = True,
    trailing_comma: bool = False,
) -> str:
    # Run WD14 tag inference on a single image.
    #
    # Args:
    #     pil_image: PIL Image to tag
    #     session: ONNX InferenceSession (from load_wd14_model)
    #     tags_data: Tag dictionary (from load_wd14_model) with keys: tags, general_index, character_index
    #     threshold: Confidence threshold for general tags (default 0.35)
    #     char_threshold: Confidence threshold for character tags (default 0.85)
    #     replace_underscore: Replace underscores with spaces in tag names
    #     trailing_comma: Add trailing comma after each tag
    #
    # Returns:
    #     Comma-separated string of detected tags, sorted by confidence

    cached_session = _wd14_cache.get("session")
    if cached_session is not None and cached_session is not session:
        session = cached_session

    try:
        probs = _run_wd14_session(pil_image, session)
    except Exception as e:
        attempted_providers = _wd14_cache.get("providers", [])
        attempted_cuda = bool(
            attempted_providers
            and attempted_providers[0] == "CUDAExecutionProvider"
        )
        classification = _classify_provider_error(e)
        if not attempted_cuda or classification is None:
            raise

        category, persistent = classification
        runtime_fingerprint = _wd14_cache.get("runtime_fingerprint", "unknown")
        if persistent:
            _quarantine_runtime_provider(runtime_fingerprint, category)
        log.warning(
            _LOG_PREFIX,
            f"ONNX CUDA inference failed ({category}: {e}); retrying once "
            "with CPUExecutionProvider",
        )
        import onnxruntime as ort  # type: ignore

        onnx_path, _ = _resolve_model_paths(_wd14_cache["model_name"])
        try:
            cpu_session = ort.InferenceSession(
                str(onnx_path), providers=["CPUExecutionProvider"]
            )
            probs = _run_wd14_session(pil_image, cpu_session)
        except Exception as cpu_error:  # noqa: BLE001 - preserve both diagnostics
            _raise_cpu_fallback_failure(e, cpu_error)

        _wd14_cache["session"] = cpu_session
        _wd14_cache["providers"] = ["CPUExecutionProvider"]
        _wd14_cache["cache_identity"] = (
            _wd14_cache["model_name"],
            _wd14_cache["requested_device"],
            runtime_fingerprint,
            ("CPUExecutionProvider",),
        )

    # Extract tags
    tags = tags_data["tags"]
    general_index = tags_data["general_index"]
    character_index = tags_data["character_index"]

    # Build tag-probability pairs
    result = list(zip(tags, probs[0]))

    # Filter by category and threshold
    general = [
        item for item in result[general_index:character_index] if item[1] > threshold
    ]
    character = [item for item in result[character_index:] if item[1] > char_threshold]

    # Combine: characters first, then general tags
    all_tags = character + general

    # Sort by confidence (highest first)
    all_tags.sort(key=lambda x: x[1], reverse=True)

    # Format output
    formatted = []
    for tag_name, _confidence in all_tags:
        name = tag_name
        if replace_underscore:
            name = name.replace("_", " ")
        # Escape parentheses (booru tag convention for ComfyUI prompt weighting)
        name = name.replace("(", "\\(").replace(")", "\\)")
        formatted.append(name)

    # Join with commas
    if trailing_comma:
        result_str = ", ".join(f"{t}," for t in formatted)
        # Clean double trailing comma
        result_str = result_str.rstrip(",").rstrip()
    else:
        result_str = ", ".join(formatted)

    return result_str
