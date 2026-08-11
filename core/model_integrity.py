# Compatibility re-export. Integrity now lives under core.model_loader.

from .model_loader.integrity import (
    MAX_SAFETENSORS_HEADER_BYTES,
    VerifyResult,
    integrity_key,
    invalidate_cache_entry,
    read_expected,
    read_safetensors_header,
    sha256_for,
    verify,
    write_expected,
)

__all__ = [
    "MAX_SAFETENSORS_HEADER_BYTES",
    "VerifyResult",
    "integrity_key",
    "invalidate_cache_entry",
    "read_expected",
    "read_safetensors_header",
    "sha256_for",
    "verify",
    "write_expected",
]
