import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Literal, Optional, TypedDict, Union

from .common import calculate_file_hash
from .logger import log

_LOG_PREFIX = "ModelIntegrity"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# Cache by concrete file state so changed files trigger recompute.
# Bounded LRU: oldest entries are evicted when the cap is reached.
_HASH_CACHE: OrderedDict[str, str] = OrderedDict()
_HASH_CACHE_MAX = 100


class VerifyResult(TypedDict):
    status: Literal["ok", "mismatch", "no-expected", "missing", "unverifiable"]
    actual: Optional[str]
    expected: Optional[str]


def _normalize_path(path: Union[str, Path]) -> Path:
    return Path(path).expanduser().resolve()


def _sha_sidecar_canonical(path: Path) -> Path:
    # Canonical form: <file.ext>.sha256
    return Path(str(path) + ".sha256")


def _sha_sidecar_legacy(path: Path) -> Path:
    # Legacy form: <file-no-ext>.sha256
    return path.with_suffix(".sha256")


def _expected_sidecar(path: Path) -> Path:
    return Path(str(path) + ".eclipse.json")


def _is_sha256_hex(value: Optional[str]) -> bool:
    return bool(value and _SHA256_RE.match(value))


def _parse_sha_sidecar_text(content: str) -> Optional[str]:
    token = content.strip().split()[0] if content.strip() else ""
    if _is_sha256_hex(token):
        return token.lower()
    return None


def _read_sha_from_sidecar(sidecar_path: Path, file_mtime_ns: int) -> Optional[str]:
    try:
        if not sidecar_path.exists() or not sidecar_path.is_file():
            return None

        # If the file is newer than the sidecar, sidecar is stale.
        if file_mtime_ns > sidecar_path.stat().st_mtime_ns:
            return None

        raw = sidecar_path.read_text(encoding="utf-8", errors="ignore")
        return _parse_sha_sidecar_text(raw)
    except Exception as e:
        log.warning(_LOG_PREFIX, f"Failed reading sidecar {sidecar_path}: {e}")
        return None


def sha256_for(
    path: Union[str, Path],
    *,
    use_sidecar: bool = True,
    write_sidecar: bool = True,
    show_progress: bool = True,
    progress_cb=None,
) -> Optional[str]:
    if not path:
        return None

    try:
        resolved = _normalize_path(path)
        if not resolved.exists() or not resolved.is_file():
            return None

        stat = resolved.stat()
        state_key = f"{resolved}|{stat.st_size}|{stat.st_mtime_ns}"
        cached = _HASH_CACHE.get(state_key)
        if cached:
            return cached

        hash_value: Optional[str] = None
        from_legacy = False
        if use_sidecar:
            hash_value = _read_sha_from_sidecar(_sha_sidecar_canonical(resolved), stat.st_mtime_ns)
            if not hash_value:
                hash_value = _read_sha_from_sidecar(_sha_sidecar_legacy(resolved), stat.st_mtime_ns)
                from_legacy = hash_value is not None

        if not hash_value:
            hash_value = calculate_file_hash(resolved, show_progress=show_progress, progress_cb=progress_cb).lower()

        _HASH_CACHE[state_key] = hash_value
        _HASH_CACHE.move_to_end(state_key)
        if len(_HASH_CACHE) > _HASH_CACHE_MAX:
            _HASH_CACHE.popitem(last=False)

        if write_sidecar and hash_value:
            canonical = _sha_sidecar_canonical(resolved)
            legacy = _sha_sidecar_legacy(resolved)
            try:
                canonical.write_text(hash_value + "\n", encoding="utf-8")
            except Exception as e:
                log.warning(_LOG_PREFIX, f"Failed writing sidecar for {resolved.name}: {e}")
            # Migrate: once the canonical sidecar exists, remove the legacy one
            # (only when the two paths actually differ, e.g. files with an extension).
            if from_legacy and canonical != legacy and canonical.exists():
                try:
                    legacy.unlink()
                    log.debug(_LOG_PREFIX, f"Migrated legacy sidecar → {canonical.name}")
                except Exception as e:
                    log.warning(_LOG_PREFIX, f"Failed removing legacy sidecar {legacy.name}: {e}")

        return hash_value
    except Exception as e:
        log.error(_LOG_PREFIX, f"Hash calculation failed for {path}: {e}")
        return None


def invalidate_cache_entry(path: Union[str, Path]) -> None:
    """Remove all cached hashes for a given file path.

    Call after a promote/rename operation replaces a file so the next
    ``sha256_for`` call recomputes instead of returning the old hash.
    """
    try:
        resolved = str(_normalize_path(path))
        keys_to_remove = [k for k in _HASH_CACHE if k.startswith(f"{resolved}|")]
        for k in keys_to_remove:
            _HASH_CACHE.pop(k, None)
    except Exception:
        pass


def read_expected(path: Union[str, Path]) -> Optional[Dict[str, str]]:
    if not path:
        return None

    try:
        resolved = _normalize_path(path)
        sidecar = _expected_sidecar(resolved)
        if not sidecar.exists() or not sidecar.is_file():
            return None

        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None

        out: Dict[str, str] = {}

        air = payload.get("air")
        if isinstance(air, str) and air.strip().startswith("urn:air:"):
            out["air"] = air.strip()

        sha256 = payload.get("sha256")
        if isinstance(sha256, str) and _is_sha256_hex(sha256.strip()):
            out["sha256"] = sha256.strip().lower()

        precision = payload.get("precision")
        if isinstance(precision, str) and precision.strip():
            out["precision"] = precision.strip()

        return out if out else None
    except Exception as e:
        log.warning(_LOG_PREFIX, f"Failed reading expected metadata for {path}: {e}")
        return None


def write_expected(
    path: Union[str, Path],
    *,
    air: Optional[str] = None,
    sha256: Optional[str] = None,
    precision: Optional[str] = None,
) -> bool:
    if not path:
        return False

    try:
        resolved = _normalize_path(path)
        sidecar = _expected_sidecar(resolved)

        existing: Dict[str, str] = {}
        if sidecar.exists() and sidecar.is_file():
            try:
                loaded = json.loads(sidecar.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    for key in ("air", "sha256", "precision"):
                        value = loaded.get(key)
                        if isinstance(value, str) and value.strip():
                            existing[key] = value.strip()
            except Exception:
                # Overwrite malformed sidecar with clean payload below.
                existing = {}

        updated = dict(existing)

        if isinstance(air, str) and air.strip().startswith("urn:air:"):
            updated["air"] = air.strip()

        if isinstance(sha256, str) and _is_sha256_hex(sha256.strip()):
            updated["sha256"] = sha256.strip().lower()

        if isinstance(precision, str) and precision.strip():
            updated["precision"] = precision.strip()

        if not updated:
            return False

        sidecar.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception as e:
        log.warning(_LOG_PREFIX, f"Failed writing expected metadata for {path}: {e}")
        return False


def verify(
    path: Union[str, Path],
    expected_sha256: Optional[str],
    *,
    on_mismatch: Literal["warn", "error", "ignore"] = "warn",
    progress_cb=None,
) -> VerifyResult:
    if not path:
        return {"status": "missing", "actual": None, "expected": None}

    resolved = _normalize_path(path)
    if not resolved.exists() or not resolved.is_file():
        return {"status": "missing", "actual": None, "expected": expected_sha256}

    log.msg(_LOG_PREFIX, f"Verifying integrity for {resolved.name}...")

    normalized_expected = expected_sha256.strip().lower() if isinstance(expected_sha256, str) else None

    actual = sha256_for(resolved, use_sidecar=True, write_sidecar=True, show_progress=True, progress_cb=progress_cb)

    if not _is_sha256_hex(normalized_expected):
        return {"status": "no-expected", "actual": actual, "expected": None}

    if not actual:
        return {"status": "unverifiable", "actual": None, "expected": normalized_expected}

    if actual.lower() == normalized_expected:
        return {"status": "ok", "actual": actual, "expected": normalized_expected}

    msg = (
        f"Hash mismatch for {resolved.name}: expected {normalized_expected}, got {actual}. "
        "Generation may continue, but metadata match on CivitAI may fail."
    )
    if on_mismatch == "warn":
        log.warning(_LOG_PREFIX, msg)
    elif on_mismatch == "error":
        log.error(_LOG_PREFIX, msg)

    return {"status": "mismatch", "actual": actual, "expected": normalized_expected}
