# Shared, private, atomic persistence for Eclipse's root config.json.

import copy
import json
import os
import stat
import threading
import time
from pathlib import Path
from typing import Any

from .json_store import (
    JsonStoreError,
    read_json_object,
    update_json_object,
    write_json_object,
)
from .logger import log

_LOG_PREFIX = "Config"
NODE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = NODE_DIR / "config.json"
DEFAULT_CONFIG_PATH = NODE_DIR / ".defaults" / "config.json.example"

_CONFIG_CACHE_TTL = 5.0
_config_cache: dict[str, Any] = {}
_config_cache_time = 0.0
_config_cache_lock = threading.RLock()
DEFAULT_CHIP_COLOR = "2a5a3a"


def normalize_chip_color(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("Chip color must be a six-digit hexadecimal string")
    normalized = value.strip().removeprefix("#").lower()
    if len(normalized) != 6 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("Chip color must be a six-digit hexadecimal string")
    return normalized


def _fallback_config() -> dict[str, Any]:
    return {
        "_comments": {
            "description": "Eclipse ComfyUI Node Configuration",
            "log_level_options": "error | warning | info | debug",
            "chip_color": "Six-digit hexadecimal accent for Eclipse chip bars and selected chips.",
        },
        "log_level": "warning",
        "vue_size_fix": True,
        "chip_color": DEFAULT_CHIP_COLOR,
    }


def _read_default_config() -> dict[str, Any]:
    try:
        with DEFAULT_CONFIG_PATH.open("r", encoding="utf-8") as default_file:
            defaults = json.load(default_file)
    except (OSError, json.JSONDecodeError) as error:
        raise JsonStoreError(
            f"Could not read default config '{DEFAULT_CONFIG_PATH}': {error}"
        ) from error
    if not isinstance(defaults, dict):
        raise JsonStoreError(
            f"Default config root must be an object: {DEFAULT_CONFIG_PATH}"
        )
    return defaults


def ensure_private_config_permissions(
    path: str | os.PathLike[str] | None = None,
) -> bool:
    # POSIX mode is exact. Windows chmod remains best effort and is not an ACL guarantee.
    target = Path(path) if path is not None else CONFIG_PATH
    try:
        current_mode = stat.S_IMODE(target.stat().st_mode)
        if current_mode != 0o600:
            target.chmod(0o600)
        return True
    except FileNotFoundError:
        return False
    except OSError as error:
        log.warning(_LOG_PREFIX, f"Could not restrict config permissions: {error}")
        return False


def ensure_config_exists() -> bool:
    # Create config.json and always enforce private permissions.
    try:
        if CONFIG_PATH.exists():
            ensure_private_config_permissions()
            return False

        if DEFAULT_CONFIG_PATH.exists():
            defaults = _read_default_config()
        else:
            defaults = _fallback_config()
        write_json_object(CONFIG_PATH, defaults, private=True)
        invalidate_config_cache()
        log.msg(_LOG_PREFIX, "Created private config.json from defaults")
        return True
    except (JsonStoreError, OSError) as error:
        log.error(_LOG_PREFIX, f"Failed to create config.json: {error}")
        return False


def invalidate_config_cache() -> None:
    global _config_cache, _config_cache_time
    with _config_cache_lock:
        _config_cache = {}
        _config_cache_time = 0.0


def get_config_snapshot() -> dict[str, Any]:
    # Return one detached Eclipse configuration generation.
    global _config_cache, _config_cache_time
    now = time.monotonic()
    with _config_cache_lock:
        if _config_cache and now - _config_cache_time < _CONFIG_CACHE_TTL:
            return copy.deepcopy(_config_cache)

        if not CONFIG_PATH.exists() and not ensure_config_exists():
            return {}
        try:
            current = read_json_object(CONFIG_PATH)
        except JsonStoreError:
            return {}
        _config_cache = copy.deepcopy(current)
        _config_cache_time = now
        return copy.deepcopy(current)


def get_config_value(key: str, default: Any = None) -> Any:
    return get_config_snapshot().get(key, default)


def update_config_values(values: dict[str, Any]) -> bool:
    # Commit a validated group of top-level values as one transaction.
    if not isinstance(values, dict):
        log.error(_LOG_PREFIX, "Config update must be a JSON object")
        return False

    try:
        def apply_values(config: dict[str, Any]) -> None:
            config.update(copy.deepcopy(values))

        update_json_object(
            CONFIG_PATH,
            apply_values,
            default={},
            private=True,
        )
        # Do not publish the returned snapshot: another writer may commit after
        # our file lock is released but before this thread reaches the cache.
        invalidate_config_cache()
        return True
    except (JsonStoreError, OSError) as error:
        log.error(_LOG_PREFIX, f"Failed to update config: {error}")
        return False


def update_config_value(
    key: str,
    value: Any,
    nested_key: str | None = None,
) -> bool:
    try:
        def apply_value(config: dict[str, Any]) -> None:
            if nested_key is None:
                config[key] = copy.deepcopy(value)
                return
            nested = config.get(key)
            if not isinstance(nested, dict):
                nested = {}
                config[key] = nested
            nested[nested_key] = copy.deepcopy(value)

        update_json_object(
            CONFIG_PATH,
            apply_value,
            default={},
            private=True,
        )
        invalidate_config_cache()
        return True
    except (JsonStoreError, OSError) as error:
        log.error(_LOG_PREFIX, f"Failed to update {key}: {error}")
        return False
