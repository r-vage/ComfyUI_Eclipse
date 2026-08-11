# Compatibility re-export. Shared persistence now lives at core.json_store.

from ..json_store import (
    JsonObject,
    JsonObjectUpdater,
    JsonStoreError,
    locked_path,
    read_json_object,
    update_json_object,
    write_json_object,
)

__all__ = [
    "JsonObject",
    "JsonObjectUpdater",
    "JsonStoreError",
    "locked_path",
    "read_json_object",
    "update_json_object",
    "write_json_object",
]
