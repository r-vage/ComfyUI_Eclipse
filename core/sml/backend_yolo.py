# SmartLM YOLO Detection Backend
#
# Self-contained Ultralytics YOLO inference for object detection and instance
# segmentation. Caches loaded models at module level for reuse between runs.
#
# Architecture:
# - Conditional ultralytics import (not in requirements.txt)
# - Module-level model cache (reuse same model between runs)
# - Auto-discovers models from ComfyUI's ultralytics/bbox/ and ultralytics/segm/ folders
# - Returns unified detection_data dict compatible with vlm_detection.py
#
# Usage:
#     from .backend_yolo import load_yolo_model, detect_yolo, unload_yolo_model
#
#     model = load_yolo_model("/path/to/face_yolov8m.pt")
#     summary, data, instance_masks = detect_yolo(model, pil_image, confidence=0.5)
#     unload_yolo_model()

import gc
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np  # type: ignore
from PIL import Image

from .logger import log
from ..common import make_comfy_tqdm_class


_LOG_PREFIX = "YOLO"


# ============================================================================
# Model Discovery Paths
# ============================================================================

def _get_ultralytics_dirs() -> Dict[str, List[str]]:
    # Get the standard Ultralytics model directories.
    # Returns dict with "bbox" and "segm" keys, each containing a list of search paths.
    try:
        import folder_paths  # type: ignore
        models_dir = folder_paths.models_dir
    except ImportError:
        models_dir = ""

    dirs: Dict[str, List[str]] = {"bbox": [], "segm": []}

    if models_dir:
        bbox_dir = os.path.join(models_dir, "ultralytics", "bbox")
        segm_dir = os.path.join(models_dir, "ultralytics", "segm")
        yolov8_dir = os.path.join(models_dir, "yolov8")

        if os.path.isdir(bbox_dir):
            dirs["bbox"].append(bbox_dir)
        if os.path.isdir(segm_dir):
            dirs["segm"].append(segm_dir)
        # Legacy location — treated as bbox
        if os.path.isdir(yolov8_dir):
            dirs["bbox"].append(yolov8_dir)

    return dirs


def get_yolo_model_files() -> Dict[str, List[str]]:
    # Scan disk for all YOLO .pt files in ultralytics/bbox/ and ultralytics/segm/.
    # Returns {"bbox": [filename, ...], "segm": [filename, ...]}.
    dirs = _get_ultralytics_dirs()
    result: Dict[str, List[str]] = {"bbox": [], "segm": []}
    seen: set = set()

    for det_type in ("bbox", "segm"):
        for folder in dirs[det_type]:
            if not os.path.isdir(folder):
                continue
            for f in sorted(os.listdir(folder)):
                if f.endswith(".pt") and f not in seen:
                    result[det_type].append(f)
                    seen.add(f)

    return result


def resolve_yolo_model_path(filename: str) -> Optional[str]:
    # Resolve a YOLO model filename to its full path on disk.
    # Searches ultralytics/bbox/, ultralytics/segm/, and yolov8/ folders.
    dirs = _get_ultralytics_dirs()
    for det_type in ("bbox", "segm"):
        for folder in dirs[det_type]:
            full_path = os.path.join(folder, filename)
            if os.path.isfile(full_path):
                return full_path
    return None


def download_yolo_model(entry: Dict[str, Any]) -> str:
    # Download a YOLO model from its registry repo_id to the appropriate ultralytics folder.
    # Returns the full path to the downloaded file.
    #
    # repo_id formats:
    #   "owner/repo/filename.pt"  — standard (e.g. "Bingsu/adetailer/face_yolov8m.pt")
    #   "https://huggingface.co/owner/repo/resolve/main/filename.pt" — full URL (auto-parsed)
    # Split into HF repo ("owner/repo") + remote filename ("filename.pt").
    # For local_only entries without repo_id, falls back to Bingsu/adetailer.
    #
    # Args:
    #     entry: Registry entry dict with "repo_id" and/or "filename", "detection_type"
    #
    # Raises:
    #     FileNotFoundError: If model not found locally and no repo_id / fallback fails
    #     RuntimeError: If download fails
    import re
    _FALLBACK_REPO = "Bingsu/adetailer"

    repo_id_full = entry.get("repo_id", "")
    filename = entry.get("filename", "")
    is_fallback = False

    # Handle full HuggingFace URLs in repo_id field
    if repo_id_full.startswith(("http://", "https://")):
        hf_match = re.match(
            r"https?://huggingface\.co/([^/]+/[^/]+)/resolve/[^/]+/(.+)",
            repo_id_full,
        )
        if hf_match:
            repo_id_full = f"{hf_match.group(1)}/{hf_match.group(2)}"
        else:
            raise ValueError(f"Unsupported URL format in repo_id: {repo_id_full}")

    # Split repo_id into HF repo + remote filename
    if repo_id_full:
        parts = repo_id_full.split("/", 2)
        if len(parts) == 3:
            hf_repo = f"{parts[0]}/{parts[1]}"
            remote_filename = parts[2]
            if not filename:
                filename = remote_filename
        else:
            hf_repo = repo_id_full
            remote_filename = filename
    elif filename and filename.endswith(".pt"):
        # Fallback: no repo_id, try Bingsu/adetailer
        hf_repo = _FALLBACK_REPO
        remote_filename = filename
        log.msg(_LOG_PREFIX, f"No repo_id in registry for '{filename}', trying {_FALLBACK_REPO}")
        is_fallback = True
    else:
        raise FileNotFoundError(
            f"YOLO model not found locally and has no repo_id. "
            f"Place the .pt file in ultralytics/bbox/ or ultralytics/segm/."
        )

    det_type = entry.get("detection_type", "bbox")

    # Determine target directory
    try:
        import folder_paths  # type: ignore
        models_dir = folder_paths.models_dir
    except ImportError:
        raise RuntimeError("Cannot determine models directory for YOLO download")

    target_dir = os.path.join(models_dir, "ultralytics", det_type)
    os.makedirs(target_dir, exist_ok=True)

    log.msg(_LOG_PREFIX, f"Downloading YOLO model: {filename} from {hf_repo}")

    from huggingface_hub import hf_hub_download  # type: ignore
    import inspect
    ComfyTqdm = make_comfy_tqdm_class()

    dl_kwargs = {
        "repo_id": hf_repo,
        "filename": remote_filename,
        "local_dir": target_dir,
        "tqdm_class": ComfyTqdm,
    }
    if "local_dir_use_symlinks" in inspect.signature(hf_hub_download).parameters:
        dl_kwargs["local_dir_use_symlinks"] = False

    try:
        hf_hub_download(**dl_kwargs)
    except Exception as e:
        err_name = type(e).__name__
        if is_fallback and ("EntryNotFound" in err_name or "404" in str(e)):
            raise FileNotFoundError(
                f"YOLO model '{filename}' not found locally or in fallback repo '{hf_repo}'. "
                f"Place the .pt file in ultralytics/bbox/ or ultralytics/segm/."
            ) from None
        raise

    # Verify downloaded file exists
    result_path = os.path.join(target_dir, filename)
    if not os.path.isfile(result_path):
        raise RuntimeError(f"Download completed but file not found at {result_path}")

    log.msg(_LOG_PREFIX, f"Downloaded YOLO model: {filename} → {det_type}/")
    return result_path


# ============================================================================
# Model Loading & Caching
# ============================================================================

# Module-level cache for loaded YOLO model
_yolo_cache: Dict[str, Any] = {}


def _ensure_ultralytics():
    # Import ultralytics, raise helpful error if not installed.
    try:
        from ultralytics import YOLO  # type: ignore
        return YOLO
    except ImportError:
        raise ImportError(
            "ultralytics is required for YOLO detection.\n"
            "Install with: pip install ultralytics"
        )


def load_yolo_model(model_path: str, device: str = "") -> Any:
    # Load a YOLO model with caching.
    # Caches the model — reuses if same path requested, reloads if different.
    #
    # Args:
    #     model_path: Full path to the .pt model file, or just a filename to resolve
    #     device: Device to load on ("cuda", "cpu", or "" for auto)
    #
    # Returns:
    #     Loaded YOLO model instance
    global _yolo_cache

    # Resolve filename to full path if needed
    if not os.path.isabs(model_path) and not os.path.isfile(model_path):
        resolved = resolve_yolo_model_path(model_path)
        if resolved:
            model_path = resolved
        else:
            raise FileNotFoundError(
                f"YOLO model '{model_path}' not found in ultralytics/bbox/, "
                f"ultralytics/segm/, or yolov8/ model folders."
            )

    # Return cached if same model
    if _yolo_cache.get("model_path") == model_path and _yolo_cache.get("model") is not None:
        log.debug(_LOG_PREFIX, f"Using cached YOLO model: {os.path.basename(model_path)}")
        return _yolo_cache["model"]

    # Unload previous model if different
    if _yolo_cache.get("model") is not None:
        log.msg(_LOG_PREFIX, f"Switching YOLO model: {os.path.basename(_yolo_cache.get('model_path', ''))} → {os.path.basename(model_path)}")
        unload_yolo_model()

    # Load model
    YOLO = _ensure_ultralytics()

    log.msg(_LOG_PREFIX, f"Loading YOLO model: {os.path.basename(model_path)}")
    model = YOLO(model_path)

    # Cache
    _yolo_cache = {
        "model_path": model_path,
        "model": model,
    }

    return model


def unload_yolo_model():
    # Unload the cached YOLO model to free memory.
    global _yolo_cache

    if _yolo_cache.get("model") is not None:
        model_path = _yolo_cache.get("model_path", "unknown")
        log.msg(_LOG_PREFIX, f"Unloading YOLO model: {os.path.basename(model_path)}")
        _yolo_cache["model"] = None
        _yolo_cache["model_path"] = None
        gc.collect()


def is_yolo_cached() -> bool:
    # Check if a YOLO model is currently cached.
    return _yolo_cache.get("model") is not None


# ============================================================================
# Inference
# ============================================================================

def detect_yolo(
    model: Any,
    pil_image: Image.Image,
    confidence: float = 0.5,
    device: str = "",
) -> Tuple[str, Dict[str, Any], List[np.ndarray]]:
    # Run YOLO inference on a PIL image.
    #
    # Args:
    #     model: Loaded YOLO model instance (from load_yolo_model)
    #     pil_image: Input PIL Image
    #     confidence: Minimum confidence threshold (0-1)
    #     device: Device for inference ("cuda", "cpu", or "" for auto)
    #
    # Returns:
    #     (summary_text, detection_data, instance_masks) where:
    #     - summary_text: Human-readable summary like "Detected 3 objects: 2 person, 1 face"
    #     - detection_data: Unified dict with bboxes, labels, confidences, etc.
    #     - instance_masks: List of numpy arrays [H,W] float32 for seg models, empty for bbox-only

    # Run inference
    kwargs: Dict[str, Any] = {"conf": confidence, "verbose": False}
    if device:
        kwargs["device"] = device

    results = model(pil_image, **kwargs)

    if not results or len(results) == 0:
        return "No detections", {}, []

    result = results[0]

    # Extract bounding boxes
    bboxes: List[List[float]] = []
    labels: List[str] = []
    confidences: List[float] = []
    instance_masks: List[np.ndarray] = []

    if result.boxes is not None and len(result.boxes) > 0:
        boxes_data = result.boxes
        class_names = result.names  # {class_id: class_name}

        for i in range(len(boxes_data)):
            # xyxy format: [x1, y1, x2, y2]
            box = boxes_data.xyxy[i].cpu().numpy().tolist()
            bboxes.append(box)

            cls_id = int(boxes_data.cls[i].item())
            label = class_names.get(cls_id, f"class_{cls_id}")
            labels.append(label)

            conf = float(boxes_data.conf[i].item())
            confidences.append(conf)

    # Extract instance masks for segmentation models
    if result.masks is not None and len(result.masks) > 0:
        masks_data = result.masks.data  # Tensor [N, H, W]
        for i in range(masks_data.shape[0]):
            mask = masks_data[i].cpu().numpy().astype(np.float32)
            # Resize mask to original image size if needed
            img_w, img_h = pil_image.size
            if mask.shape[0] != img_h or mask.shape[1] != img_w:
                from PIL import Image as PILImage
                mask_pil = PILImage.fromarray((mask * 255).astype(np.uint8))
                if hasattr(PILImage, "Resampling"):
                    resample_filter = PILImage.Resampling.NEAREST
                else:
                    resample_filter = getattr(PILImage, "NEAREST")
                mask_pil = mask_pil.resize((img_w, img_h), resample_filter)
                mask = np.array(mask_pil).astype(np.float32) / 255.0
            instance_masks.append(mask)

    # Build summary text
    if not bboxes:
        return "No detections", {}, []

    label_counts = Counter(labels)
    count_parts = [f"{count} {name}" for name, count in label_counts.most_common()]
    summary = f"Detected {len(bboxes)} object(s): {', '.join(count_parts)}"

    # Build detection data dict (unified format)
    model_path = _yolo_cache.get("model_path", "")
    detection_data: Dict[str, Any] = {
        "bboxes": bboxes,
        "labels": labels,
        "confidences": confidences,
        "coord_range": 0,  # Always pixel coordinates
        "backend": "YOLO",
        "model": os.path.basename(model_path) if model_path else "",
    }

    log.msg(_LOG_PREFIX, summary)

    return summary, detection_data, instance_masks
