"""Prepare Eclipse runtime compatibility before custom-node imports."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path, PurePosixPath
from typing import Any

_LOG_PREFIX = "Eclipse prestartup"
_LOGGER = logging.getLogger(__name__)
_INVENTORY_NAME = ".eclipse-retired-sources.json"
_SCHEMA_VERSION = 1
_MANAGED_SUFFIXES = {"js": ".js", "py": ".py"}
_cffi_runtime_guard: tuple[Any, Any] | None = None


def prime_linux_cffi_callbacks() -> bool:
    """Bind CFFI callbacks before ComfyUI globally loads ANGLE's libffi."""
    global _cffi_runtime_guard
    if sys.platform != "linux":
        return False
    try:
        import cffi

        ffi = cffi.FFI()
        callback = ffi.callback("int(int)", lambda value: value)
        if callback(1) != 1:
            raise RuntimeError("CFFI callback self-test returned an invalid value")
    except ImportError:
        return False
    except Exception as error:  # noqa: BLE001 - prestartup must remain non-fatal
        _LOGGER.warning(
            "%s: could not initialize Linux CFFI callback compatibility: %s",
            _LOG_PREFIX,
            error,
        )
        return False
    _cffi_runtime_guard = (ffi, callback)
    return True


def _load_retired_sources(repo_root: Path) -> tuple[Path, ...] | None:
    inventory_path = repo_root / _INVENTORY_NAME
    try:
        document: Any = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _LOGGER.error(
            "%s: could not read %s; skipped retired-source cleanup: %s",
            _LOG_PREFIX,
            _INVENTORY_NAME,
            error,
        )
        return None

    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "retired_sources"}
        or document.get("schema_version") != _SCHEMA_VERSION
        or not isinstance(document.get("retired_sources"), list)
    ):
        _LOGGER.error(
            "%s: %s is malformed; skipped retired-source cleanup",
            _LOG_PREFIX,
            _INVENTORY_NAME,
        )
        return None

    entries = document["retired_sources"]
    if (
        any(not isinstance(entry, str) for entry in entries)
        or entries != sorted(set(entries))
    ):
        _LOGGER.error(
            "%s: %s has invalid or duplicate entries; "
            "skipped retired-source cleanup",
            _LOG_PREFIX,
            _INVENTORY_NAME,
        )
        return None

    retired: list[Path] = []
    for entry in entries:
        relative = PurePosixPath(entry)
        expected_suffix = (
            _MANAGED_SUFFIXES.get(relative.parts[0]) if relative.parts else None
        )
        if (
            relative.is_absolute()
            or len(relative.parts) < 2
            or ".." in relative.parts
            or relative.as_posix() != entry
            or relative.suffix != expected_suffix
        ):
            _LOGGER.error(
                "%s: %s contains an unsafe entry; "
                "skipped retired-source cleanup",
                _LOG_PREFIX,
                _INVENTORY_NAME,
            )
            return None
        retired.append(Path(*relative.parts))
    return tuple(retired)


def _has_symlink_parent(repo_root: Path, relative: Path) -> bool:
    current = repo_root
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            return True
    return False


def _next_backup_path(path: Path) -> Path:
    backup = path.with_name(f"{path.name}.bak")
    index = 1
    while os.path.lexists(backup):
        backup = path.with_name(f"{path.name}.{index}.bak")
        index += 1
    return backup


def deactivate_retired_sources(
    repo_root: str | Path,
) -> tuple[tuple[Path, Path], ...]:
    """Move known retired Python and JavaScript files to .bak paths."""
    repo_path = Path(repo_root).resolve()
    retired = _load_retired_sources(repo_path)
    if retired is None:
        return ()

    moved: list[tuple[Path, Path]] = []
    for relative in retired:
        source = repo_path / relative
        if not os.path.lexists(source):
            continue
        if _has_symlink_parent(repo_path, relative):
            _LOGGER.error(
                "%s: refused to move %s through a symlinked directory",
                _LOG_PREFIX,
                relative.as_posix(),
            )
            continue
        if source.is_dir() and not source.is_symlink():
            _LOGGER.error(
                "%s: expected retired source %s to be a file; left it unchanged",
                _LOG_PREFIX,
                relative.as_posix(),
            )
            continue

        backup = _next_backup_path(source)
        try:
            source.replace(backup)
        except OSError as error:
            _LOGGER.error(
                "%s: could not deactivate retired source %s: %s",
                _LOG_PREFIX,
                relative.as_posix(),
                error,
            )
            continue
        moved.append((relative, backup.relative_to(repo_path)))

    if moved:
        _LOGGER.warning(
            "%s: deactivated %d retired source file(s): %s",
            _LOG_PREFIX,
            len(moved),
            ", ".join(
                f"{source.as_posix()} -> {backup.as_posix()}"
                for source, backup in moved
            ),
        )
    return tuple(moved)


prime_linux_cffi_callbacks()
deactivate_retired_sources(Path(__file__).resolve().parent)
