# Compatibility re-export. Templates now live under core.model_loader.

from .model_loader.templates import (
    MAX_TEMPLATE_BYTES,
    TEMPLATE_DIR,
    _ensure_template_compat,
    delete_template,
    ensure_template_dir,
    get_template_dir,
    get_template_list,
    get_template_mtime,
    is_safe_template_name,
    load_template,
    normalize_template_paths,
    save_template,
)

__all__ = [
    "MAX_TEMPLATE_BYTES",
    "TEMPLATE_DIR",
    "_ensure_template_compat",
    "delete_template",
    "ensure_template_dir",
    "get_template_dir",
    "get_template_list",
    "get_template_mtime",
    "is_safe_template_name",
    "load_template",
    "normalize_template_paths",
    "save_template",
]
