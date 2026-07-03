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
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np #type: ignore
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
_wd14_cache: Dict[str, Any] = {}


def _resolve_model_paths(model_name: str) -> Tuple[Path, Path]:
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


def _load_tags_csv(csv_path: Path) -> Dict[str, Any]:
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


def _get_ort_providers() -> List[str]:
    # Get available ONNX Runtime execution providers.
    # Prefers CUDA if available, falls back to CPU.
    try:
        import onnxruntime as ort  # type: ignore
        available = ort.get_available_providers()
        providers = []
        if "CUDAExecutionProvider" in available:
            providers.append("CUDAExecutionProvider")
        providers.append("CPUExecutionProvider")
        log.debug(_LOG_PREFIX, f"ORT providers: {providers} (available: {available})")
        return providers
    except ImportError:
        log.error(_LOG_PREFIX, "onnxruntime not installed. Install with: pip install onnxruntime-gpu")
        raise


def load_wd14_model(model_name: str) -> Tuple[Any, Dict[str, Any]]:
    # Load a WD14 ONNX model and its tag dictionary.
    # Caches the session — reuses if same model requested, reloads if different.
    #
    # Returns:
    #     (InferenceSession, tags_data) where tags_data has keys: tags, general_index, character_index
    global _wd14_cache

    # Return cached if same model
    if _wd14_cache.get("model_name") == model_name and _wd14_cache.get("session") is not None:
        log.debug(_LOG_PREFIX, f"Using cached WD14 model: {model_name}")
        return _wd14_cache["session"], _wd14_cache["tags_data"]

    # Unload previous model if different
    if _wd14_cache.get("session") is not None:
        log.msg(_LOG_PREFIX, f"Switching WD14 model: {_wd14_cache.get('model_name')} → {model_name}")
        unload_wd14_model()

    # Resolve paths
    onnx_path, csv_path = _resolve_model_paths(model_name)

    # Load ONNX session
    import onnxruntime as ort  # type: ignore
    providers = _get_ort_providers()

    log.msg(_LOG_PREFIX, f"Loading WD14 model: {model_name}")
    session = ort.InferenceSession(str(onnx_path), providers=providers)

    # Load tag dictionary
    tags_data = _load_tags_csv(csv_path)
    tag_count = len(tags_data["tags"])
    log.msg(_LOG_PREFIX, f"WD14 model loaded: {model_name} ({tag_count} tags)")

    # Cache
    _wd14_cache = {
        "model_name": model_name,
        "session": session,
        "tags_data": tags_data,
    }

    return session, tags_data


def unload_wd14_model():
    # Unload the cached WD14 ONNX session to free memory.
    global _wd14_cache

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
        resample_filter = getattr(Image, "LANCZOS")
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

def tag_image(
    pil_image: Image.Image,
    session: Any,
    tags_data: Dict[str, Any],
    threshold: float = 0.35,
    char_threshold: float = 0.85,
    exclude_tags: str = "",
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
    #     exclude_tags: Comma-separated list of tags to exclude
    #     replace_underscore: Replace underscores with spaces in tag names
    #     trailing_comma: Add trailing comma after each tag
    #
    # Returns:
    #     Comma-separated string of detected tags, sorted by confidence

    # Get model input size from the session
    input_info = session.get_inputs()[0]
    target_size = input_info.shape[1]  # Typically 448

    # Preprocess image
    img_input = _preprocess_image(pil_image, target_size)

    # Run inference
    output_name = session.get_outputs()[0].name
    input_name = input_info.name
    probs = session.run([output_name], {input_name: img_input})[0]

    # Extract tags
    tags = tags_data["tags"]
    general_index = tags_data["general_index"]
    character_index = tags_data["character_index"]

    # Build tag-probability pairs
    result = list(zip(tags, probs[0]))

    # Filter by category and threshold
    general = [item for item in result[general_index:character_index] if item[1] > threshold]
    character = [item for item in result[character_index:] if item[1] > char_threshold]

    # Combine: characters first, then general tags
    all_tags = character + general

    # Exclude user-specified tags
    # Normalize: accept both "long hair" and "long_hair" from the user
    if exclude_tags.strip():
        remove_set = set()
        for s in exclude_tags.split(","):
            s = s.strip().lower()
            if s:
                remove_set.add(s)
                remove_set.add(s.replace(" ", "_"))
                remove_set.add(s.replace("_", " "))
        all_tags = [t for t in all_tags if t[0].lower() not in remove_set]

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
