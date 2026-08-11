# Trust-boundary, request parsing, and transactional maintenance helpers.

from __future__ import annotations

import ipaddress
import json
import os
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import folder_paths  # type: ignore
from aiohttp import web  # type: ignore

from ..logger import log
from .integrity import invalidate_cache_entry, verify
from .lifecycle import maintenance_if_idle
from .templates import TEMPLATE_DIR, load_template
from .validation import resolve_model_file

MAX_JSON_REQUEST_BYTES = 64 * 1024
_LOG_PREFIX = "ModelLoaderEndpoints"

_ROLE_ALIASES = {
    "unet": "diffusion_models",
    "checkpoints": "checkpoints",
    "diffusion_models": "diffusion_models",
    "diffusion_models_gguf": "diffusion_models_gguf",
    "vae": "vae",
    "text_encoders": "text_encoders",
    "clip": "clip",
    "loras": "loras",
    "embeddings": "embeddings",
    "clip_vision": "clip_vision",
}
_MODEL_FIELDS = {
    "Standard Checkpoint": ("checkpoints", "ckpt_name", "model"),
    "UNet Model": ("diffusion_models", "unet_name", "model"),
    "Nunchaku Flux": ("diffusion_models", "nunchaku_name", "model"),
    "Nunchaku Qwen": ("diffusion_models", "qwen_name", "model"),
    "Nunchaku ZImage": ("diffusion_models", "zimage_name", "model"),
    "GGUF Model": ("diffusion_models_gguf", "gguf_name", "model_gguf"),
}


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def same_origin_browser_request(request: web.Request) -> bool:
    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return False
    origin = request.headers.get("Origin")
    host = request.headers.get("Host")
    if not origin or not host:
        return True
    try:
        origin_parts = urlsplit(origin)
        host_parts = urlsplit(f"//{host}")
        if origin_parts.scheme not in {"http", "https"}:
            return False
        if origin_parts.username is not None or origin_parts.password is not None:
            return False
        if not origin_parts.hostname or not host_parts.hostname:
            return False
        if origin_parts.hostname.lower() != host_parts.hostname.lower():
            return False
        origin_port = origin_parts.port or (443 if origin_parts.scheme == "https" else 80)
        host_port = host_parts.port or origin_port
        return origin_port == host_port
    except ValueError:
        return False


def request_is_loopback(request: web.Request) -> bool:
    remote = request.remote
    if not remote:
        return False
    try:
        address = ipaddress.ip_address(remote.split("%", 1)[0])
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def global_mutation_denial(request: web.Request) -> web.Response | None:
    if not same_origin_browser_request(request):
        return web.json_response(
            {"success": False, "error": "Cross-origin mutation request rejected"},
            status=403,
        )
    try:
        from comfy.cli_args import args  # type: ignore

        multi_user = args.multi_user
    except (AttributeError, ImportError):
        multi_user = False
    if multi_user and not request_is_loopback(request):
        return web.json_response(
            {
                "success": False,
                "error": "Global model-loader mutations are limited to loopback clients in multi-user mode",
            },
            status=403,
        )
    return None


async def read_json_object_request(
    request: web.Request,
    max_bytes: int = MAX_JSON_REQUEST_BYTES,
) -> dict[str, Any]:
    def bad_request(message: str) -> web.HTTPBadRequest:
        return web.HTTPBadRequest(
            text=json.dumps({"success": False, "error": message}),
            content_type="application/json",
        )

    if request.content_type != "application/json":
        raise bad_request("Content-Type must be application/json")
    if request.content_length is not None and request.content_length > max_bytes:
        raise bad_request("Request body exceeds the size limit")
    raw = await request.read()
    if len(raw) > max_bytes:
        raise bad_request("Request body exceeds the size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise bad_request("Request body must be valid JSON") from error
    if not isinstance(payload, dict):
        raise bad_request("Request body must be a JSON object")
    return payload


def require_json_boolean(
    payload: dict[str, Any],
    key: str,
    *,
    default: bool = False,
) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be true or false")
    return value


def resolve_role_target(role: str, filename: str) -> tuple[Path, Path, str]:
    folder_role = _ROLE_ALIASES.get(role, role)
    if folder_role not in folder_paths.folder_names_and_paths:
        raise ValueError("Unknown model folder role")
    if not isinstance(filename, str) or not filename or "\0" in filename or Path(filename).is_absolute():
        raise ValueError("Invalid model filename")
    normalized = filename.replace("\\", "/")
    if any(part in {"", ".", ".."} for part in Path(normalized).parts):
        raise ValueError("Unsafe model filename")
    full_path = folder_paths.get_full_path(folder_role, normalized)
    if not full_path:
        raise FileNotFoundError("Model file was not found")
    lexical = Path(full_path).absolute()
    for configured_root in folder_paths.get_folder_paths(folder_role):
        root_lexical = Path(configured_root).expanduser().absolute()
        try:
            relative = lexical.relative_to(root_lexical)
        except ValueError:
            continue
        current = root_lexical
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise ValueError("Symlinked model targets are forbidden")
        root = root_lexical.resolve(strict=True)
        target = lexical.resolve(strict=True)
        try:
            relative_path = target.relative_to(root).as_posix()
        except ValueError as error:
            raise ValueError("Model target escapes its folder role") from error
        if not target.is_file():
            raise FileNotFoundError("Model target is not a regular file")
        return root, target, relative_path
    raise ValueError("Model target escapes its folder role")


def prepare_download_destination(
    root_dir: str | os.PathLike[str],
    *,
    requested_filename: str | None,
    resolved_filename: str,
    create_parents: bool = False,
) -> tuple[Path, Path, str]:
    root = Path(root_dir).expanduser().resolve(strict=True)
    selected = requested_filename if requested_filename else Path(resolved_filename).name
    if not isinstance(selected, str) or not selected or "\0" in selected:
        raise ValueError("Invalid download filename")
    normalized = selected.replace("\\", "/")
    raw_parts = normalized.split("/")
    relative = PurePosixPath(normalized)
    if (
        relative.is_absolute()
        or not raw_parts
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise ValueError("Unsafe download filename")

    current = root
    for part in raw_parts[:-1]:
        candidate = current / part
        if candidate.is_symlink():
            raise ValueError("Symlinked download directories are forbidden")
        if candidate.exists():
            if not candidate.is_dir():
                raise ValueError("Download directory must be a non-symlink directory")
        elif create_parents:
            try:
                candidate.mkdir()
            except FileExistsError:
                if candidate.is_symlink() or not candidate.is_dir():
                    raise ValueError(
                        "Download directory must be a non-symlink directory"
                    ) from None
        current = candidate

    if current.is_symlink():
        raise ValueError("Symlinked download directories are forbidden")
    destination = current / raw_parts[-1]
    if destination.is_symlink():
        raise ValueError("Symlinked download destinations are forbidden")
    resolved_destination = destination.resolve(strict=False)
    try:
        resolved_destination.relative_to(root)
    except ValueError as error:
        raise ValueError("Download destination escapes its folder role") from error
    return root, resolved_destination, relative.as_posix()


def promote_verified_replacement(
    *,
    role: str,
    original_filename: str,
    replacement_filename: str,
    expected_sha256: str,
    cleanup_filenames: list[str] | None = None,
    prompt_queue=None,
) -> list[str]:
    with maintenance_if_idle(prompt_queue) as acquired:
        if not acquired:
            raise BlockingIOError("Prompt queue is active")
        root, replacement, replacement_relative = resolve_role_target(
            role, replacement_filename
        )
        if (
            not isinstance(original_filename, str)
            or not original_filename
            or "\0" in original_filename
            or Path(original_filename).is_absolute()
        ):
            raise ValueError("Invalid promotion target")
        normalized_original = original_filename.replace("\\", "/")
        original_parts = Path(normalized_original).parts
        if any(part in {"", ".", ".."} for part in original_parts):
            raise ValueError("Unsafe promotion target")
        original = (root / normalized_original).absolute()
        if len(original_parts) == 1:
            original = replacement.parent / original_parts[0]
        if original.is_symlink() or original.parent.is_symlink():
            raise ValueError("Symlinked promotion targets are forbidden")
        original_resolved_parent = original.parent.resolve(strict=True)
        try:
            original_resolved_parent.relative_to(root)
        except ValueError as error:
            raise ValueError("Promotion target escapes its folder role") from error
        if original.parent != replacement.parent:
            raise ValueError("Replacement must share the original model directory") from None
        if original == replacement:
            raise ValueError("Replacement must differ from the original model")
        result = verify(replacement, expected_sha256, on_mismatch="error")
        if result["status"] != "ok":
            raise ValueError("Replacement file failed integrity verification")

        cleanup_targets: list[Path] = []
        deleted: list[str] = []
        retry_match = re.fullmatch(
            rf"(.+)_([1-9][0-9]*){re.escape(replacement.suffix)}",
            replacement.name,
        )
        retry_prefix = retry_match.group(1) if retry_match else None
        for name in cleanup_filenames or []:
            if not isinstance(name, str):
                continue
            try:
                _cleanup_root, candidate, _cleanup_relative = resolve_role_target(
                    role, name
                )
            except (FileNotFoundError, ValueError):
                continue
            if candidate.parent != replacement.parent or retry_prefix is None:
                continue
            if re.fullmatch(
                rf"{re.escape(retry_prefix)}_[1-9][0-9]*{re.escape(replacement.suffix)}",
                candidate.name,
            ) is None:
                continue
            if candidate in {original, replacement} or candidate in cleanup_targets:
                continue
            cleanup_targets.append(candidate)
            deleted.append(name.replace("\\", "/"))

        original_targets = [original, *[Path(f"{original}{suffix}") for suffix in (".sha256", ".eclipse.json")]]
        for candidate in cleanup_targets:
            original_targets.extend(
                [candidate, *[Path(f"{candidate}{suffix}") for suffix in (".sha256", ".eclipse.json")]]
            )
        replacement_pairs = [(replacement, original)]
        replacement_sidecars = [
            Path(f"{replacement}{suffix}")
            for suffix in (".sha256", ".eclipse.json")
        ]
        if any(path.is_symlink() for path in [*original_targets, *replacement_sidecars]):
            raise ValueError("Symlinked integrity sidecars are forbidden")
        replacement_pairs.extend(
            (source, Path(f"{original}{source.name.removeprefix(replacement.name)}"))
            for source in replacement_sidecars
            if source.is_file()
        )

        tombstoned: list[tuple[Path, Path]] = []
        promoted: list[tuple[Path, Path]] = []
        try:
            for target in original_targets:
                if not target.is_file() or target.is_symlink():
                    continue
                tombstone = target.with_name(
                    f".{target.name}.eclipse-tombstone-{uuid.uuid4().hex}"
                )
                os.replace(target, tombstone)
                tombstoned.append((target, tombstone))
            for source, destination in replacement_pairs:
                os.replace(source, destination)
                promoted.append((source, destination))
        except Exception:
            for source, destination in reversed(promoted):
                if destination.exists() and not source.exists():
                    os.replace(destination, source)
            for target, tombstone in reversed(tombstoned):
                if tombstone.exists() and not target.exists():
                    os.replace(tombstone, target)
            for directory in {target.parent for target, _tombstone in tombstoned}:
                _fsync_directory(directory)
            raise
        for directory in {destination.parent for _source, destination in promoted}:
            _fsync_directory(directory)
        for _target, tombstone in tombstoned:
            tombstone.unlink(missing_ok=True)
        for directory in {target.parent for target, _tombstone in tombstoned}:
            _fsync_directory(directory)
        invalidate_cache_entry(replacement)
        invalidate_cache_entry(original)
        log.msg(
            _LOG_PREFIX,
            f"Promoted verified replacement for {Path(replacement_relative).name}",
        )
        return deleted


def delete_template_transaction(
    name: str,
    *,
    delete_models: bool,
    prompt_queue=None,
) -> list[str]:
    with maintenance_if_idle(prompt_queue) as acquired:
        if not acquired:
            raise BlockingIOError("Prompt queue is active")
        config = load_template(name)
        if not config:
            raise FileNotFoundError("Template was not found")

        targets: list[Path] = []
        deleted_names: list[str] = []
        if delete_models:
            model_type = config.get("model_type", "Standard Checkpoint")
            selection = _MODEL_FIELDS.get(model_type)
            if selection:
                role, field, reference_type = selection
                filename = config.get(field)
                if isinstance(filename, str) and filename not in {"", "None"}:
                    target = resolve_model_file(
                        role,
                        filename,
                        reference_type=reference_type,
                    ).path
                    targets.append(target)
                    deleted_names.append(filename)
                    sidecars = {
                        Path(f"{target}.sha256"),
                        target.with_suffix(".sha256"),
                        Path(f"{target}.eclipse.json"),
                    }
                    for sidecar in sidecars:
                        if sidecar.is_file() and not sidecar.is_symlink():
                            targets.append(sidecar)

        template_target = Path(TEMPLATE_DIR) / f"{name}.json"
        if template_target.is_symlink() or not template_target.is_file():
            raise FileNotFoundError("Template was not found")
        targets.append(template_target)

        moved: list[tuple[Path, Path]] = []
        try:
            for target in targets:
                tombstone = target.with_name(f".{target.name}.eclipse-tombstone-{uuid.uuid4().hex}")
                os.replace(target, tombstone)
                moved.append((target, tombstone))
        except Exception:
            for target, tombstone in reversed(moved):
                if tombstone.exists() and not target.exists():
                    os.replace(tombstone, target)
            for directory in {target.parent for target, _tombstone in moved}:
                _fsync_directory(directory)
            raise

        for directory in {target.parent for target, _tombstone in moved}:
            _fsync_directory(directory)
        for _target, tombstone in moved:
            try:
                tombstone.unlink()
            except OSError as error:
                log.warning(_LOG_PREFIX, f"Could not remove tombstone: {type(error).__name__}")
        for directory in {target.parent for target, _tombstone in moved}:
            _fsync_directory(directory)
        return deleted_names
