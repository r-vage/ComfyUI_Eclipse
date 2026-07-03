# SML Model Registry
#
# Loads backend-split registry JSON files from registry/ and merges them
# into a single flat model list with backend suffixes. Provides lookup,
# defaults persistence, and helper functions for the new Smart Model Loader.
#
# Registry files:
#   registry/transformers_models.json  — no suffix (default backend)
#   registry/gguf_models.json          — "-GGUF" suffix
#   registry/ollama_models.json        — "-Ollama" suffix
#   registry/vllm_models.json          — "-vLLM" suffix
#   registry/sglang_models.json        — "-SGLang" suffix
#   registry/wd14_models.json          — no suffix (WD14- prefix is distinct)
#   registry/user_models.json          — per-backend sections, merged on top
#   registry/defaults.json             — global defaults (context_size, etc.)

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .logger import log
from .model_types import ModelFamily

_LOG_PREFIX = "Registry"

# ============================================================================
# Constants
# ============================================================================

_REGISTRY_DIR = Path(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))) / "registry"

# Backend file → display suffix mapping
# Transformers and WD14 get no suffix (Transformers is default, WD14 names are already distinct)
_BACKEND_FILES = {
    "transformers": ("transformers_models.json", ""),
    "gguf":         ("gguf_models.json",         "-GGUF"),
    "ollama":       ("ollama_models.json",        "-Ollama"),
    "vllm":         ("vllm_models.json",          "-vLLM"),
    "sglang":       ("sglang_models.json",        "-SGLang"),
    "wd14":         ("wd14_models.json",          ""),
}

# Registry family string → ModelFamily enum
FAMILY_MAP: Dict[str, ModelFamily] = {
    "Qwen": ModelFamily.QWEN,
    "Mistral": ModelFamily.MISTRAL,
    "Florence": ModelFamily.FLORENCE,
    "LLaVA": ModelFamily.LLAVA,
    "LLM_TEXT": ModelFamily.LLM_TEXT,
    "VLM": ModelFamily.VLM,
    "WD14": ModelFamily.WD14,
    "YOLO": ModelFamily.YOLO,
}

# Reverse: suffix → backend key
_SUFFIX_TO_BACKEND = {v[1]: k for k, v in _BACKEND_FILES.items() if v[1]}
# e.g. {"-GGUF": "gguf", "-Ollama": "ollama", "-vLLM": "vllm", "-SGLang": "sglang"}


# ============================================================================
# Module State
# ============================================================================

_lock = threading.Lock()
_merged_registry: Optional[Dict[str, Dict[str, Any]]] = None
# display_name → {"backend": str, "name": str, **entry_fields}

_defaults_cache: Optional[Dict[str, Any]] = None
_defaults_mtime: float = 0.0


# ============================================================================
# Internal Loaders
# ============================================================================

def _load_json(path: Path) -> Any:
    # Load a JSON file, return empty dict on error.
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(_LOG_PREFIX, f"Failed to load {path.name}: {e}")
        return {}


def _merge_backend(registry: Dict[str, Dict], backend: str, models: Dict[str, Dict], suffix: str):
    # Merge models from one backend into the flat registry dict.
    # Skips entries whose keys start with "_" (reserved for metadata).
    for name, entry in models.items():
        if name.startswith("_"):
            continue
        if not isinstance(entry, dict):
            continue

        display_name = f"{name}{suffix}"
        if display_name in registry:
            log.warning(_LOG_PREFIX, f"Duplicate model name '{display_name}' — skipping (already registered)")
            continue
        registry[display_name] = {
            "backend": backend,
            "name": name,
            **entry,
        }


def _build_registry() -> Dict[str, Dict[str, Any]]:
    # Load all registry files and build the merged flat dict.
    registry: Dict[str, Dict[str, Any]] = {}

    # 1. Load per-backend files
    for backend, (filename, suffix) in _BACKEND_FILES.items():
        path = _REGISTRY_DIR / filename
        models = _load_json(path)
        if models:
            _merge_backend(registry, backend, models, suffix)
            log.debug(_LOG_PREFIX, f"Loaded {len(models)} models from {filename}")

    # 2. Load user_models.json — sectioned by backend
    user_path = _REGISTRY_DIR / "user_models.json"
    user_data = _load_json(user_path)
    if user_data:
        for backend, (_, suffix) in _BACKEND_FILES.items():
            section = user_data.get(backend, {})
            if section:
                _merge_backend(registry, backend, section, suffix)
                log.debug(_LOG_PREFIX, f"Loaded {len(section)} user models for {backend}")

    log.msg(_LOG_PREFIX, f"Registry loaded: {len(registry)} models across {len(_BACKEND_FILES)} backends")
    return registry


# ============================================================================
# Public API
# ============================================================================

def load_all_registries(force: bool = False) -> Dict[str, Dict[str, Any]]:
    # Load (or reload) all registry files into the merged dict.
    # Thread-safe. Cached after first load unless force=True.
    global _merged_registry
    with _lock:
        if _merged_registry is None or force:
            _merged_registry = _build_registry()
        return _merged_registry


# Separator tokens for model dropdown grouping (matched in JS frontend)
MODEL_SEP_VISION = "__SEP__VISION_MODELS__"
MODEL_SEP_TEXT   = "__SEP__TEXT_MODELS__"
MODEL_SEP_WD14   = "__SEP__WD14_MODELS__"
_MODEL_SEPARATORS = {MODEL_SEP_VISION, MODEL_SEP_TEXT, MODEL_SEP_WD14}


def get_model_list() -> List[str]:
    # Get model display names grouped by type with separator tokens.
    # Order: Vision models → Text models → WD14 models
    registry = load_all_registries()
    vision: List[str] = []
    text: List[str] = []
    wd14: List[str] = []
    for name, entry in registry.items():
        if entry.get("backend") == "wd14":
            wd14.append(name)
        elif entry.get("has_vision", False):
            vision.append(name)
        else:
            text.append(name)
    result: List[str] = []
    if vision:
        result.append(MODEL_SEP_VISION)
        result.extend(sorted(vision))
    if text:
        result.append(MODEL_SEP_TEXT)
        result.extend(sorted(text))
    if wd14:
        result.append(MODEL_SEP_WD14)
        result.extend(sorted(wd14))
    return result


def is_model_separator(name: str) -> bool:
    # Check if a name is a separator token (not a real model).
    return name in _MODEL_SEPARATORS


def get_model_entry(display_name: str) -> Optional[Dict[str, Any]]:
    # Look up a model by its display name (with suffix).
    # Returns the full entry dict with "backend" and "name" fields, or None.
    # Falls back to YOLO registry if not found in shared registry.
    registry = load_all_registries()
    entry = registry.get(display_name)
    if entry is not None:
        return entry
    # Fallback: check YOLO registry
    yolo_reg = _load_yolo_registry()
    return yolo_reg.get(display_name)


def resolve_model(display_name: str) -> Optional[Tuple[str, Dict[str, Any], str]]:
    # Resolve a display name to (backend, entry_data, clean_name).
    # Returns None if model not found.
    entry = get_model_entry(display_name)
    if entry is None:
        return None
    return (entry["backend"], entry, entry["name"])


def get_model_family(display_name: str) -> Optional[ModelFamily]:
    # Get the ModelFamily enum for a display name.
    entry = get_model_entry(display_name)
    if entry is None:
        return None
    family_str = entry.get("family", "")
    return FAMILY_MAP.get(family_str)


def get_backend_for_display_name(display_name: str) -> Optional[str]:
    # Extract backend key from a display name by checking suffix.
    # Falls back to registry lookup if no suffix matches.
    for suffix, backend in _SUFFIX_TO_BACKEND.items():
        if display_name.endswith(suffix):
            return backend
    # No suffix → could be Transformers or WD14
    entry = get_model_entry(display_name)
    if entry:
        return entry["backend"]
    return None


def is_wd14_model(display_name: str) -> bool:
    # Check if a display name is a WD14 tagger model.
    entry = get_model_entry(display_name)
    if entry:
        return entry["backend"] == "wd14"
    return display_name.startswith("WD14-")


def get_quantizations(display_name: str = "") -> List[str]:
    # Get the global GGUF quantization list from defaults.json.
    # Returns empty list for non-GGUF models (if display_name given).
    if display_name:
        entry = get_model_entry(display_name)
        if entry is None or entry.get("backend") != "gguf":
            return []
    defaults = load_defaults()
    return defaults.get("quantizations", ["Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"])


def has_vision(display_name: str) -> bool:
    # Check if a model has vision capabilities.
    entry = get_model_entry(display_name)
    if entry is None:
        return False
    # WD14 models are always vision (no explicit field)
    if entry.get("backend") == "wd14":
        return True
    return entry.get("has_vision", False)


# ============================================================================
# Defaults Persistence
# ============================================================================

def _defaults_path() -> Path:
    return _REGISTRY_DIR / "defaults.json"


def load_defaults() -> Dict[str, Any]:
    # Load global defaults from registry/defaults.json.
    # Caches result, reloads if file has been modified.
    global _defaults_cache, _defaults_mtime
    path = _defaults_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}

    if _defaults_cache is not None and mtime == _defaults_mtime:
        return _defaults_cache

    data = _load_json(path)
    _defaults_cache = data
    _defaults_mtime = mtime
    return data


def get_default(key: str, fallback: Any = None) -> Any:
    # Get a single default value.
    return load_defaults().get(key, fallback)


def save_defaults(updates: Dict[str, Any]) -> bool:
    # Persist updated default values to registry/defaults.json.
    # Only writes if at least one value actually changed.
    global _defaults_cache, _defaults_mtime
    path = _defaults_path()
    current = _load_json(path) if path.is_file() else {}

    # Filter to only changed values
    changed = {k: v for k, v in updates.items() if current.get(k) != v}
    if not changed:
        return False

    current.update(changed)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
            f.write("\n")
        _defaults_cache = current
        _defaults_mtime = path.stat().st_mtime
        log.debug(_LOG_PREFIX, f"Saved defaults: {', '.join(changed.keys())}")
        return True
    except OSError as e:
        log.error(_LOG_PREFIX, f"Failed to save defaults: {e}")
        return False


# ============================================================================
# Serialization for Endpoints
# ============================================================================

def get_model_list_for_api() -> List[Dict[str, Any]]:
    # Build the model list payload for the /smartlml/model_list endpoint.
    # Returns a list of dicts with display_name, backend, family, has_vision,
    # and quantizations (if any).
    registry = load_all_registries()
    result = []
    for display_name in sorted(registry.keys()):
        entry = registry[display_name]
        item = {
            "display_name": display_name,
            "backend": entry["backend"],
            "name": entry["name"],
            "family": entry.get("family", ""),
            "has_vision": entry.get("has_vision", entry["backend"] == "wd14"),
        }
        if "quantizations" in entry:
            item["quantizations"] = entry["quantizations"]
        result.append(item)
    return result


def get_model_entry_for_api(display_name: str) -> Optional[Dict[str, Any]]:
    # Build the model entry payload for the /smartlml/model_entry endpoint.
    # Returns the full entry dict (safe for JSON serialization).
    # GGUF models get quantizations injected from defaults.json.
    entry = get_model_entry(display_name)
    if entry is None:
        return None
    result = {
        "display_name": display_name,
        **entry,
    }
    if entry.get("backend") == "gguf":
        result["quantizations"] = get_quantizations()
    if entry.get("backend") == "wd14":
        result["wd14_exclude_tags"] = get_default("wd14_exclude_tags", "")
    return result


def invalidate_cache():
    # Force the registry to reload on next access.
    global _merged_registry
    with _lock:
        _merged_registry = None
    log.debug(_LOG_PREFIX, "Registry cache invalidated")


def is_trust_remote_code_allowed(display_name: str, override: bool = False) -> bool:
    # Check whether a model is allowed to execute remote code from its HF repo
    # (auto_map / modeling_*.py via transformers `trust_remote_code=True`).
    #
    # Security model:
    #   - Default is False (safe). Only models explicitly flagged in their
    #     registry entry (`"trust_remote_code": true`) are auto-allowed.
    #   - `override` from the runtime "⚠ Trust Remote Code" chip can opt-in
    #     additional models (user-added entries, new releases). It can only
    #     enable, never disable, an already-allowed model.
    if override:
        return True
    entry = get_model_entry(display_name)
    if entry is None:
        return False
    return bool(entry.get("trust_remote_code", False))


# ============================================================================
# YOLO Registry (separate from shared SML registry)
# ============================================================================

_YOLO_REGISTRY_FILE = "yolo_models.json"

# Module-level YOLO registry cache
_yolo_registry: Optional[Dict[str, Dict[str, Any]]] = None
_yolo_lock = threading.Lock()

# Separator tokens for detection model dropdown
MODEL_SEP_DETECTION_VLM = "__SEP__DETECTION_VLM__"
MODEL_SEP_YOLO = "__SEP__YOLO__"


def _load_yolo_registry(force: bool = False) -> Dict[str, Dict[str, Any]]:
    # Load the YOLO registry JSON and build a flat lookup dict.
    # Display names: "{name} [{detection_type}]" (e.g. "face_yolov8m [bbox]")
    # Thread-safe, cached.
    global _yolo_registry
    with _yolo_lock:
        if _yolo_registry is not None and not force:
            return _yolo_registry

        path = _REGISTRY_DIR / _YOLO_REGISTRY_FILE
        raw = _load_json(path)
        registry: Dict[str, Dict[str, Any]] = {}

        for name, entry in raw.items():
            if name.startswith("_") or not isinstance(entry, dict):
                continue
            det_type = entry.get("detection_type", "bbox")
            display_name = f"{name} [{det_type}]"
            registry[display_name] = {
                "backend": "yolo",
                "name": name,
                **entry,
            }

        _yolo_registry = registry
        log.debug(_LOG_PREFIX, f"YOLO registry loaded: {len(registry)} models")
        return registry


def sync_yolo_registry():
    # Sync registry/yolo_models.json with on-disk YOLO model files.
    # Called at startup. Adds newly discovered models, removes stale local_only entries,
    # and updates availability of curated entries.
    from .backend_yolo import get_yolo_model_files

    path = _REGISTRY_DIR / _YOLO_REGISTRY_FILE
    registry = _load_json(path)
    if not isinstance(registry, dict):
        registry = {}
    changed = False

    # 1. Scan disk for all YOLO .pt files
    on_disk = get_yolo_model_files()
    disk_files: Dict[str, str] = {}  # {filename: detection_type}
    for det_type, files in on_disk.items():
        for f in files:
            disk_files[f] = det_type

    # 2. Build filename → registry_key lookup for existing entries
    filename_to_key: Dict[str, str] = {}
    for key, entry in registry.items():
        if key.startswith("_") or not isinstance(entry, dict):
            continue
        fn = entry.get("filename", f"{key}.pt")
        filename_to_key[fn] = key

    # 3. Add newly discovered models (local_only: true)
    for filename, det_type in disk_files.items():
        if filename not in filename_to_key:
            name = os.path.splitext(filename)[0]
            # Skip reserved keys
            if name.startswith("_"):
                continue
            registry[name] = {
                "filename": filename,
                "family": "YOLO",
                "detection_type": det_type,
                "description": f"Auto-discovered: {filename}",
                "local_only": True,
                "available": True,
            }
            filename_to_key[filename] = name
            changed = True
            log.debug(_LOG_PREFIX, f"Auto-discovered YOLO model: {filename} ({det_type})")

    # 4. Update existing entries
    keys_to_remove = []
    for key, entry in list(registry.items()):
        if key.startswith("_") or not isinstance(entry, dict):
            continue
        fn = entry.get("filename", f"{key}.pt")
        is_on_disk = fn in disk_files

        if entry.get("local_only", False):
            # Local-only: remove if file no longer exists
            if not is_on_disk:
                keys_to_remove.append(key)
                changed = True
                log.debug(_LOG_PREFIX, f"Removing stale local YOLO model: {key}")
        else:
            # Curated/user entry: update availability flag
            was_available = entry.get("available", False)
            if was_available != is_on_disk:
                entry["available"] = is_on_disk
                changed = True

    for key in keys_to_remove:
        del registry[key]

    # 5. Write back if changed
    if changed:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(registry, f, indent=4, ensure_ascii=False)
                f.write("\n")
            log.msg(_LOG_PREFIX, f"YOLO registry synced: {len([k for k in registry if not k.startswith('_')])} models")
        except OSError as e:
            log.error(_LOG_PREFIX, f"Failed to write YOLO registry: {e}")

    # Invalidate YOLO cache so next load picks up changes
    global _yolo_registry
    with _yolo_lock:
        _yolo_registry = None


def get_detection_model_list() -> List[str]:
    # Get detection-capable models for the Detection node dropdown.
    # Combines VLM models (Florence + Qwen from shared registry) with YOLO models.
    # Returns list with separator tokens for frontend grouping.
    shared_registry = load_all_registries()
    yolo_registry = _load_yolo_registry()

    # Filter shared registry to detection-capable VLM families
    detection_families = {"Florence", "Qwen"}
    vlm_models: List[str] = []
    for name, entry in shared_registry.items():
        family = entry.get("family", "")
        if family in detection_families and entry.get("has_vision", False):
            vlm_models.append(name)

    # YOLO models (only available ones)
    yolo_models: List[str] = []
    for name, entry in yolo_registry.items():
        if entry.get("available", False):
            yolo_models.append(name)

    # Build grouped list with separators
    result: List[str] = []
    if vlm_models:
        result.append(MODEL_SEP_DETECTION_VLM)
        result.extend(sorted(vlm_models))
    if yolo_models:
        result.append(MODEL_SEP_YOLO)
        result.extend(sorted(yolo_models))

    return result


def is_yolo_model(display_name: str) -> bool:
    # Check if a display name refers to a YOLO model.
    yolo_registry = _load_yolo_registry()
    return display_name in yolo_registry


def invalidate_yolo_cache():
    # Force the YOLO registry to reload on next access.
    global _yolo_registry
    with _yolo_lock:
        _yolo_registry = None
    log.debug(_LOG_PREFIX, "YOLO registry cache invalidated")
