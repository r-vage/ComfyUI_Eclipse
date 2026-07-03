# Centralized loader template management for Smart Loader nodes.
#
# This module provides shared template functions for:
# - RvLoader_SmartLoader.py
# - RvLoader_SmartLoader_Plus.py
#
# Templates are stored in the repo's templates/ folder.
# Git tracks .defaults/**/*.json.example; extracted to repo root on first run.

import os
import json
from pathlib import Path
from typing import Dict, List, Optional

from .logger import log

# Module log prefix - change here to update all log messages
_LOG_PREFIX = "LoaderTemplates"

# Fields in template configs that contain file paths (need cross-platform normalization)
_PATH_FIELDS = [
    'ckpt_name', 'unet_name', 'nunchaku_name', 'qwen_name', 'zimage_name', 'gguf_name',
    'clip_name1', 'clip_name2', 'clip_name3', 'clip_name4', 'vae_name',
] + [f'lora_name_{i}' for i in range(1, 11)]


def normalize_template_paths(config: Dict) -> Dict:
    # Normalize backslashes to forward slashes in all path fields.
    # Ensures templates created on Windows work on Linux and vice versa.
    for field in _PATH_FIELDS:
        if field in config and isinstance(config[field], str):
            config[field] = config[field].replace('\\', '/')
    return config


# Centralized filename validation (same logic as server_endpoints.is_safe_filename)
def is_safe_template_name(name: str) -> bool:
    # Validate template name to prevent path traversal attacks.
    # Returns True if name is safe (no path separators or traversal).
    if not name or name == "None":
        return False
    # Block path traversal attempts
    if '..' in name or '/' in name or '\\' in name:
        log.warning(_LOG_PREFIX, f"Blocked path traversal attempt: {name}")
        return False
    # Block null bytes
    if '\x00' in name:
        log.warning(_LOG_PREFIX, f"Blocked null byte in name: {repr(name)}")
        return False
    return True

# Repo root directory (ComfyUI_Eclipse/)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_template_dir() -> str:
    # Get current template directory.
    # Uses the repo's templates/ folder directly.
    #
    # Returns:
    #     Path to loader templates directory
    template_dir = os.path.join(_REPO_ROOT, "templates")
    os.makedirs(template_dir, exist_ok=True)
    return template_dir


# Module-level template directory (evaluated at import time)
TEMPLATE_DIR = get_template_dir()

# Mapping between features array values and configure_* boolean keys
_FEATURE_BOOL_MAP = {
    'clip': 'configure_clip',
    'vae': 'configure_vae',
    'latent': 'configure_latent',
    'sampler': 'configure_sampler',
    'lora': 'configure_model_only_lora',
    'model_sampling': 'configure_model_sampling',
    'block_swap': 'configure_blockswap',
}


def _ensure_template_compat(config: Dict) -> Dict:
    # Ensure both features array and configure_* booleans are present.
    # This allows templates saved by the new Smart Model Loader (features array)
    # to be read by old Smart Loader variants (configure_* booleans), and vice versa.
    has_features = 'features' in config and isinstance(config['features'], list)
    has_booleans = any(k in config for k in _FEATURE_BOOL_MAP.values())

    if has_features and not has_booleans:
        # Derive configure_* booleans from features array
        feats = set(config['features'])
        for feat, bool_key in _FEATURE_BOOL_MAP.items():
            config[bool_key] = feat in feats

    elif has_booleans and not has_features:
        # Derive features array from configure_* booleans
        feats = []
        for feat, bool_key in _FEATURE_BOOL_MAP.items():
            if config.get(bool_key, False):
                feats.append(feat)
        config['features'] = feats

    return config


def ensure_template_dir() -> None:
    # Ensure template directory exists.
    os.makedirs(TEMPLATE_DIR, exist_ok=True)


def get_template_list() -> List[str]:
    # Get list of available templates.
    #
    # Returns:
    #     List of template names (without .json extension), with "None" as first item
    ensure_template_dir()
    templates = []
    try:
        for f in os.listdir(TEMPLATE_DIR):
            if f.endswith('.json'):
                templates.append(f[:-5])
    except Exception:
        pass
    templates.sort(key=str.lower)
    return ["None"] + templates


def save_template(name: str, config: Dict) -> bool:
    # Save a configuration template.
    #
    # Args:
    #     name: Template name (without .json extension)
    #     config: Dictionary of configuration values to save
    #
    # Returns:
    #     True if saved successfully, False otherwise
    
    # Security: validate name to prevent path traversal
    if not is_safe_template_name(name):
        return False
    
    ensure_template_dir()
    template_path = os.path.join(TEMPLATE_DIR, f"{name}.json")
    
    # Security: verify resolved path stays within template directory
    if not os.path.abspath(template_path).startswith(os.path.abspath(TEMPLATE_DIR)):
        log.warning(_LOG_PREFIX, f"Blocked path traversal in save: {name}")
        return False
    
    try:
        # Normalize paths before saving (Windows backslashes → forward slashes)
        config = normalize_template_paths(config)
        with open(template_path, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error saving template: {e}")
        return False


def load_template(name: str) -> Dict:
    # Load a configuration template.
    #
    # Args:
    #     name: Template name (without .json extension)
    #
    # Returns:
    #     Dictionary of configuration values, or empty dict if not found
    if name == "None" or not name:
        return {}
    
    # Security: validate name to prevent path traversal
    if not is_safe_template_name(name):
        return {}
    
    template_path = os.path.join(TEMPLATE_DIR, f"{name}.json")
    
    # Security: verify resolved path stays within template directory
    if not os.path.abspath(template_path).startswith(os.path.abspath(TEMPLATE_DIR)):
        log.warning(_LOG_PREFIX, f"Blocked path traversal in load: {name}")
        return {}
    
    try:
        if os.path.exists(template_path):
            with open(template_path, 'r') as f:
                config = json.load(f)
            # Normalize paths on load (handles templates created on Windows)
            config = normalize_template_paths(config)
            # Cross-format compat: ensure both features array and configure_* booleans exist
            config = _ensure_template_compat(config)
            return config
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error loading template: {e}")
    return {}


def delete_template(name: str) -> bool:
    # Delete a configuration template.
    #
    # Args:
    #     name: Template name (without .json extension)
    #
    # Returns:
    #     True if deleted successfully, False otherwise
    if name == "None" or not name:
        return False
    
    # Security: validate name to prevent path traversal
    if not is_safe_template_name(name):
        return False
    
    template_path = os.path.join(TEMPLATE_DIR, f"{name}.json")
    
    # Security: verify resolved path stays within template directory
    if not os.path.abspath(template_path).startswith(os.path.abspath(TEMPLATE_DIR)):
        log.warning(_LOG_PREFIX, f"Blocked path traversal in delete: {name}")
        return False
    
    try:
        if os.path.exists(template_path):
            os.remove(template_path)
            return True
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error deleting template: {e}")
    return False


def get_template_mtime() -> Optional[float]:
    # Get the maximum modification time of all templates.
    #
    # Used by IS_CHANGED to detect template file changes.
    #
    # Returns:
    #     Maximum mtime of template files, or None if directory doesn't exist
    if os.path.exists(TEMPLATE_DIR):
        try:
            json_files = [f for f in os.listdir(TEMPLATE_DIR) if f.endswith('.json')]
            if json_files:
                return max(os.path.getmtime(os.path.join(TEMPLATE_DIR, f)) for f in json_files)
        except Exception:
            pass
    return None
