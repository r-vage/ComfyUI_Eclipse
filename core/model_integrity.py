# Stable SHA-256 metadata and bounded Safetensors inspection.

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .json_store import (
    JsonStoreError,
    read_json_object,
    write_json_object,
)
from .logger import log

_LOG_PREFIX = "ModelIntegrity"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_METADATA_VERSION = 2
_HASH_CACHE: OrderedDict[tuple[str, int, int, int, int], str] = OrderedDict()
_HASH_CACHE_MAX = 100
_HASH_CACHE_LOCK = threading.RLock()
MAX_SAFETENSORS_HEADER_BYTES = 16 * 1024 * 1024


def _normalized_path(path: str | os.PathLike[str]) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("Symlinked model files are not supported")
    return candidate.resolve(strict=True)


def _canonical_sidecar(path: Path) -> Path:
    return Path(f"{path}.sha256")


def _legacy_sidecar(path: Path) -> Path:
    return path.with_suffix(".sha256")


def _expected_sidecar(path: Path) -> Path:
    return Path(f"{path}.eclipse.json")


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value.strip()) is not None


def _stat_identity(path: Path) -> tuple[str, int, int, int, int]:
    stat_result = path.stat()
    return (
        str(path),
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_dev,
        stat_result.st_ino,
    )


def _read_metadata(path: Path, state: tuple[str, int, int, int, int]) -> str | None:
    sidecar = _canonical_sidecar(path)
    if sidecar.is_symlink():
        return None
    try:
        payload = read_json_object(sidecar)
    except (JsonStoreError, OSError):
        return None
    if (
        payload.get("version") != _METADATA_VERSION
        or payload.get("algorithm") != "sha256"
        or payload.get("size") != state[1]
        or payload.get("mtime_ns") != state[2]
        or not _valid_sha256(payload.get("sha256"))
    ):
        return None
    return payload["sha256"].lower()


def _read_legacy_digest(path: Path) -> tuple[str | None, Path | None]:
    for sidecar in (_canonical_sidecar(path), _legacy_sidecar(path)):
        if sidecar.is_symlink():
            continue
        try:
            raw = sidecar.read_text(encoding="utf-8", errors="strict").strip()
        except (FileNotFoundError, OSError, UnicodeError):
            continue
        token = raw.split()[0] if raw else ""
        if _valid_sha256(token):
            return token.lower(), sidecar
    return None, None


def _hash_stable_file(path: Path, progress_cb=None) -> tuple[str, tuple[str, int, int, int, int]]:
    before = _stat_identity(path)
    digest = hashlib.sha256()
    processed = 0
    with path.open("rb", buffering=0) as model_file:
        while True:
            chunk = model_file.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            processed += len(chunk)
            if progress_cb is not None:
                try:
                    progress_cb(processed, before[1])
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    log.debug(_LOG_PREFIX, "Hash progress callback failed")
        file_stat = os.fstat(model_file.fileno())
    after = _stat_identity(path)
    if before != after or (file_stat.st_size, file_stat.st_mtime_ns) != (before[1], before[2]):
        raise OSError("Model file changed while it was being hashed")
    return digest.hexdigest(), after


def _write_hash_metadata(
    path: Path,
    digest: str,
    state: tuple[str, int, int, int, int],
    *,
    reference_type: str | None,
    folder_role: str | None,
    relative_path: str | None,
) -> None:
    if _canonical_sidecar(path).is_symlink():
        raise ValueError("Symlinked integrity sidecars are forbidden")
    payload: dict[str, Any] = {
        "version": _METADATA_VERSION,
        "algorithm": "sha256",
        "sha256": digest,
        "size": state[1],
        "mtime_ns": state[2],
        "reference_type": reference_type or "local-baseline",
    }
    if folder_role:
        payload["folder_role"] = folder_role
    if relative_path:
        payload["relative_path"] = relative_path.replace("\\", "/")
    write_json_object(_canonical_sidecar(path), payload)


def sha256_for(
    path: str | os.PathLike[str],
    *,
    use_sidecar: bool = True,
    write_sidecar: bool = True,
    show_progress: bool = True,
    progress_cb=None,
    reference_type: str | None = None,
    folder_role: str | None = None,
    relative_path: str | None = None,
) -> str | None:
    del show_progress
    if not path:
        return None
    try:
        resolved = _normalized_path(path)
        if not resolved.is_file() or not os.access(resolved, os.R_OK):
            return None
        state = _stat_identity(resolved)
        with _HASH_CACHE_LOCK:
            cached = _HASH_CACHE.get(state)
            if cached:
                _HASH_CACHE.move_to_end(state)
                return cached

        metadata_digest = _read_metadata(resolved, state) if use_sidecar else None
        if metadata_digest:
            digest = metadata_digest
        else:
            canonical_sidecar_present = _canonical_sidecar(resolved).exists()
            legacy_digest, legacy_path = _read_legacy_digest(resolved) if use_sidecar else (None, None)
            digest, state = _hash_stable_file(resolved, progress_cb=progress_cb)
            legacy_matches = legacy_digest is None or hmac.compare_digest(digest, legacy_digest)
            malformed_canonical_sidecar = (
                use_sidecar
                and canonical_sidecar_present
                and legacy_digest is None
            )
            if not legacy_matches:
                log.warning(_LOG_PREFIX, f"Ignored stale legacy hash sidecar for {resolved.name}")
                legacy_path = None
            if malformed_canonical_sidecar:
                log.warning(
                    _LOG_PREFIX,
                    f"Preserved malformed hash metadata for {resolved.name}",
                )
            if write_sidecar and legacy_matches and not malformed_canonical_sidecar:
                _write_hash_metadata(
                    resolved,
                    digest,
                    state,
                    reference_type=reference_type,
                    folder_role=folder_role,
                    relative_path=relative_path,
                )
                if legacy_path and legacy_path != _canonical_sidecar(resolved):
                    try:
                        legacy_path.unlink()
                    except OSError:
                        log.warning(_LOG_PREFIX, f"Could not remove legacy sidecar {legacy_path.name}")

        with _HASH_CACHE_LOCK:
            _HASH_CACHE[state] = digest
            _HASH_CACHE.move_to_end(state)
            while len(_HASH_CACHE) > _HASH_CACHE_MAX:
                _HASH_CACHE.popitem(last=False)
        return digest
    except (JsonStoreError, OSError, ValueError) as error:
        log.error(_LOG_PREFIX, f"Hash calculation failed: {type(error).__name__}: {error}")
        return None


def invalidate_cache_entry(path: str | os.PathLike[str]) -> None:
    try:
        resolved = str(Path(path).expanduser().resolve(strict=False))
    except (OSError, ValueError):
        return
    with _HASH_CACHE_LOCK:
        for key in [key for key in _HASH_CACHE if key[0] == resolved]:
            _HASH_CACHE.pop(key, None)


def read_expected(path: str | os.PathLike[str]) -> dict[str, str] | None:
    if not path:
        return None
    try:
        resolved = _normalized_path(path)
        sidecar = _expected_sidecar(resolved)
        if sidecar.is_symlink():
            raise ValueError("Symlinked expected metadata is forbidden")
        payload = read_json_object(sidecar)
    except (FileNotFoundError, JsonStoreError, OSError, ValueError) as error:
        if not isinstance(error, FileNotFoundError):
            log.warning(_LOG_PREFIX, f"Expected metadata is unreadable: {type(error).__name__}")
        return None

    result: dict[str, str] = {}
    air = payload.get("air")
    if isinstance(air, str) and air.startswith("urn:air:"):
        result["air"] = air
    sha256 = payload.get("sha256")
    if _valid_sha256(sha256):
        result["sha256"] = sha256.strip().lower()
    for key in ("precision", "reference_type", "folder_role", "relative_path"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    return result or None
