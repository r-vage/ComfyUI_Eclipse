# CivitAI identity validation and verified transactional acquisition.

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import shutil
import socket
import stat
import threading
from pathlib import Path, PurePosixPath
from typing import Any, TypedDict
from urllib.parse import parse_qs, quote, urljoin, urlparse

import requests  # type: ignore

from ..logger import log
from .integrity import invalidate_cache_entry
from .lifecycle import maintenance_if_idle

_LOG_PREFIX = "CivitAI"
_BASE_URL = "https://civitai.com"
_SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_GIT_BLOB_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_AIR_RE = re.compile(
    r"^urn:air:([^:]+):([^:]+):civitai:(\d+)@(\d+)(?:\+(\d+))?$",
    re.IGNORECASE,
)
_CONTENT_RANGE_RE = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$")
_PRECISION_PLACEHOLDER_RE = re.compile(
    r"(?:bf16fp8|fp8bf16|bf16int8|int8bf16)",
    re.IGNORECASE,
)
_NEXT_DATA_RE = re.compile(
    rb"<script[^>]*id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
MAX_REDIRECTS = 5
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024 * 1024
MIN_FREE_RESERVE_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_MODEL_PAGE_BYTES = 8 * 1024 * 1024
MAX_MODEL_PAGE_REDIRECTS = 2


class CivitaiResolvedFile(TypedDict):
    air: str | None
    sha256: str
    filename: str
    download_url: str
    model_id: int
    model_version_id: int
    file_id: int
    expected_size: int | None
    file_type: str
    file_format: str
    precision: str | None


class CivitaiSelectionError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        available_files: list[dict[str, Any]] | None = None,
        available_precisions: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.available_files = available_files or []
        self.available_precisions = available_precisions or []


class DownloadCancelled(RuntimeError):
    pass


class DownloadDestinationBusy(RuntimeError):
    pass


class _ActiveTransfer:
    def __init__(self, response) -> None:
        self.cancel_requested = threading.Event()
        self.response = response

    def cancel(self) -> None:
        self.cancel_requested.set()
        try:
            self.response.close()
        except (AttributeError, OSError, requests.RequestException):
            pass


_DOWNLOAD_REGISTRY_LOCK = threading.Lock()
_RESERVED_DOWNLOAD_IDS: set[str] = set()
_ACTIVE_TRANSFERS: dict[str, _ActiveTransfer] = {}
_ACTIVE_DOWNLOAD_DESTINATIONS: set[str] = set()


def _claim_download_destination(destination: Path) -> str:
    identity = str(destination.expanduser().resolve(strict=False))
    with _DOWNLOAD_REGISTRY_LOCK:
        if identity in _ACTIVE_DOWNLOAD_DESTINATIONS:
            raise DownloadDestinationBusy("Download destination is already in use")
        _ACTIVE_DOWNLOAD_DESTINATIONS.add(identity)
    return identity


def _release_download_destination(identity: str) -> None:
    with _DOWNLOAD_REGISTRY_LOCK:
        _ACTIVE_DOWNLOAD_DESTINATIONS.discard(identity)


def _auth_headers(api_key: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "ComfyUI-Eclipse/4.3.2",
    }
    if api_key and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    return headers


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA_RE.fullmatch(value.strip()) is not None


def parse_air(air: str) -> dict[str, int | str] | None:
    if not isinstance(air, str):
        return None
    match = _AIR_RE.fullmatch(air.strip())
    if match is None:
        return None
    result: dict[str, int | str] = {
        "ecosystem": match.group(1).lower(),
        "resource_type": match.group(2).lower(),
        "model_id": int(match.group(3)),
        "version_id": int(match.group(4)),
    }
    if match.group(5):
        result["file_id"] = int(match.group(5))
    return result


def reserve_download_id(download_id: str) -> bool:
    with _DOWNLOAD_REGISTRY_LOCK:
        if download_id in _RESERVED_DOWNLOAD_IDS:
            return False
        _RESERVED_DOWNLOAD_IDS.add(download_id)
        return True


def release_download_id(download_id: str) -> None:
    with _DOWNLOAD_REGISTRY_LOCK:
        _ACTIVE_TRANSFERS.pop(download_id, None)
        _RESERVED_DOWNLOAD_IDS.discard(download_id)


def cancel_active_download(download_id: str) -> str:
    with _DOWNLOAD_REGISTRY_LOCK:
        transfer = _ACTIVE_TRANSFERS.get(download_id)
        reserved = download_id in _RESERVED_DOWNLOAD_IDS
        if transfer is None:
            return "not-transferring" if reserved else "not-found"
        transfer.cancel_requested.set()
        response = transfer.response
    try:
        response.close()
    except (AttributeError, OSError, requests.RequestException):
        pass
    return "cancelling"


def _activate_transfer(download_id: str | None, response) -> _ActiveTransfer | None:
    if not download_id:
        return None
    transfer = _ActiveTransfer(response)
    with _DOWNLOAD_REGISTRY_LOCK:
        if download_id not in _RESERVED_DOWNLOAD_IDS:
            raise ValueError("Download identity is not reserved")
        if download_id in _ACTIVE_TRANSFERS:
            raise ValueError("Download identity is already transferring")
        _ACTIVE_TRANSFERS[download_id] = transfer
    return transfer


def _deactivate_transfer(download_id: str | None, transfer: _ActiveTransfer | None) -> None:
    if not download_id or transfer is None:
        return
    with _DOWNLOAD_REGISTRY_LOCK:
        if _ACTIVE_TRANSFERS.get(download_id) is transfer:
            _ACTIVE_TRANSFERS.pop(download_id, None)


def _close_transfer_window(
    download_id: str | None,
    transfer: _ActiveTransfer | None,
) -> bool:
    if not download_id or transfer is None:
        return False
    with _DOWNLOAD_REGISTRY_LOCK:
        if _ACTIVE_TRANSFERS.get(download_id) is transfer:
            _ACTIVE_TRANSFERS.pop(download_id, None)
        return transfer.cancel_requested.is_set()


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP/HTTPS download URLs are supported")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Download URLs may not contain credentials")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError as error:
        raise ValueError("Download hostname could not be resolved") from error
    if not addresses:
        raise ValueError("Download hostname did not resolve")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0].split("%", 1)[0])
        if not ip.is_global:
            raise ValueError("Download hostname resolves to a non-public address")


def _request_json(url: str, api_key: str | None) -> dict[str, Any]:
    _validate_public_url(url)
    with requests.get(
        url,
        headers=_auth_headers(api_key),
        timeout=(10, 30),
        allow_redirects=False,
        stream=True,
    ) as response:
        response.raise_for_status()
        if response.is_redirect or response.is_permanent_redirect:
            raise ValueError("CivitAI metadata endpoint returned an unexpected redirect")
        content_length = response.headers.get("Content-Length")
        try:
            declared_length = int(content_length) if content_length else None
        except ValueError as error:
            raise ValueError("CivitAI metadata response has an invalid length") from error
        if declared_length is not None and declared_length > MAX_METADATA_BYTES:
            raise ValueError("CivitAI metadata response exceeds the size limit")
        content = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            content.extend(chunk)
            if len(content) > MAX_METADATA_BYTES:
                raise ValueError("CivitAI metadata response exceeds the size limit")
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("CivitAI metadata response is not valid JSON") from error
    if not isinstance(payload, dict):
        raise TypeError("CivitAI response must be a JSON object")
    return payload


def get_model_version(version_id: int, api_key: str | None) -> dict[str, Any]:
    if isinstance(version_id, bool) or not isinstance(version_id, int) or version_id <= 0:
        raise TypeError("Invalid CivitAI model-version identity")
    return _request_json(f"{_BASE_URL}/api/v1/model-versions/{version_id}", api_key)


def get_model_version_by_hash(sha256: str, api_key: str | None) -> dict[str, Any] | None:
    if not _valid_sha(sha256):
        return None
    url = f"{_BASE_URL}/api/v1/model-versions/by-hash/{sha256.strip().upper()}"
    try:
        return _request_json(url, api_key)
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 404:
            return None
        raise


def _request_model_page_payload(
    model_id: int,
    version_id: int,
    api_key: str | None,
) -> dict[str, Any]:
    current_url = f"{_BASE_URL}/models/{model_id}?modelVersionId={version_id}"
    headers = _auth_headers(api_key)
    headers["Accept"] = "text/html"
    content = None
    for redirect_count in range(MAX_MODEL_PAGE_REDIRECTS + 1):
        _validate_public_url(current_url)
        with requests.get(
            current_url,
            headers=headers,
            timeout=(10, 30),
            allow_redirects=False,
            stream=True,
        ) as response:
            response.raise_for_status()
            if response.is_redirect or response.is_permanent_redirect:
                if redirect_count >= MAX_MODEL_PAGE_REDIRECTS:
                    raise ValueError("CivitAI model page exceeded the redirect limit")
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("CivitAI model page redirect omitted Location")
                redirected_url = urljoin(current_url, location)
                parsed_redirect = urlparse(redirected_url)
                redirect_versions = parse_qs(parsed_redirect.query).get(
                    "modelVersionId"
                )
                if (
                    parsed_redirect.scheme != "https"
                    or parsed_redirect.hostname != "civitai.com"
                    or parsed_redirect.port not in {None, 443}
                    or not parsed_redirect.path.startswith(f"/models/{model_id}/")
                    or redirect_versions != [str(version_id)]
                    or parsed_redirect.fragment
                ):
                    raise ValueError("CivitAI model page redirect is not canonical")
                current_url = redirected_url
                continue
            content_length = response.headers.get("Content-Length")
            try:
                declared_length = int(content_length) if content_length else None
            except ValueError as error:
                raise ValueError("CivitAI model page has an invalid length") from error
            if declared_length is not None and declared_length > MAX_MODEL_PAGE_BYTES:
                raise ValueError("CivitAI model page exceeds the size limit")
            content = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                content.extend(chunk)
                if len(content) > MAX_MODEL_PAGE_BYTES:
                    raise ValueError("CivitAI model page exceeds the size limit")
            break
    if content is None:
        raise ValueError("CivitAI model page did not return content")
    match = _NEXT_DATA_RE.search(content)
    if match is None:
        raise ValueError("CivitAI model page omitted its data payload")
    try:
        payload = json.loads(match.group(1))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("CivitAI model page data is not valid JSON") from error
    if not isinstance(payload, dict):
        raise TypeError("CivitAI model page data must be a JSON object")
    return payload


def _preferred_filename_from_page_payload(
    payload: dict[str, Any],
    *,
    model_id: int,
    version_id: int,
    file_id: int,
    expected_sha256: str,
    source_filename: str,
) -> str | None:
    props = payload.get("props")
    page_props = props.get("pageProps") if isinstance(props, dict) else None
    if not isinstance(page_props, dict) or str(page_props.get("id")) != str(model_id):
        return None
    trpc_state = page_props.get("trpcState")
    trpc_json = trpc_state.get("json") if isinstance(trpc_state, dict) else None
    queries = trpc_json.get("queries") if isinstance(trpc_json, dict) else None
    if not isinstance(queries, list):
        return None
    source_suffix = PurePosixPath(source_filename).suffix.casefold()
    for query in queries:
        if not isinstance(query, dict):
            continue
        query_key = query.get("queryKey")
        if (
            not isinstance(query_key, list)
            or not query_key
            or query_key[0] != ["model", "getById"]
        ):
            continue
        state = query.get("state")
        model = state.get("data") if isinstance(state, dict) else None
        if not isinstance(model, dict) or str(model.get("id")) != str(model_id):
            continue
        versions = model.get("modelVersions")
        if not isinstance(versions, list):
            continue
        for version in versions:
            if not isinstance(version, dict) or str(version.get("id")) != str(version_id):
                continue
            files = version.get("files")
            if not isinstance(files, list):
                continue
            for file_data in files:
                if (
                    not isinstance(file_data, dict)
                    or str(file_data.get("id")) != str(file_id)
                    or str(file_data.get("modelVersionId")) != str(version_id)
                ):
                    continue
                hashes = file_data.get("hashes")
                page_sha = next(
                    (
                        item.get("hash")
                        for item in hashes
                        if isinstance(item, dict)
                        and str(item.get("type") or "").casefold() == "sha256"
                        and _valid_sha(item.get("hash"))
                    ),
                    None,
                ) if isinstance(hashes, list) else None
                if not isinstance(page_sha, str) or not hmac.compare_digest(
                    page_sha.casefold(),
                    expected_sha256.casefold(),
                ):
                    continue
                name = file_data.get("name")
                if not isinstance(name, str):
                    continue
                preferred = name.strip()
                if (
                    not preferred
                    or preferred in {".", ".."}
                    or "\0" in preferred
                    or "/" in preferred
                    or "\\" in preferred
                    or len(preferred.encode("utf-8")) > 240
                    or PurePosixPath(preferred).suffix.casefold() != source_suffix
                ):
                    continue
                duplicate_name = any(
                    isinstance(other, dict)
                    and str(other.get("id")) != str(file_id)
                    and isinstance(other.get("name"), str)
                    and other["name"].strip().casefold() == preferred.casefold()
                    for other in files
                )
                if duplicate_name:
                    return _append_file_identity(preferred, file_id)
                return preferred
    return None


def _preferred_filename_for_file(
    *,
    model_id: int,
    version_id: int,
    file_id: int,
    expected_sha256: str,
    source_filename: str,
    api_key: str | None,
) -> str | None:
    payload = _request_model_page_payload(model_id, version_id, api_key)
    return _preferred_filename_from_page_payload(
        payload,
        model_id=model_id,
        version_id=version_id,
        file_id=file_id,
        expected_sha256=expected_sha256,
        source_filename=source_filename,
    )


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"Invalid CivitAI {name}")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid CivitAI {name}") from error
    if result <= 0:
        raise ValueError(f"Invalid CivitAI {name}")
    return result


def _version_model_id(version: dict[str, Any]) -> int:
    direct = version.get("modelId")
    if direct is not None:
        return _integer(direct, "model identity")
    model = version.get("model")
    if isinstance(model, dict):
        return _integer(model.get("id"), "model identity")
    canonical_air = version.get("air")
    parsed = parse_air(canonical_air) if isinstance(canonical_air, str) else None
    if parsed:
        return parsed["model_id"]
    raise ValueError("CivitAI response omitted the model identity")


def _file_sha(file_data: dict[str, Any]) -> str:
    hashes = file_data.get("hashes")
    sha = hashes.get("SHA256") if isinstance(hashes, dict) else None
    if not _valid_sha(sha):
        raise ValueError("CivitAI file metadata does not include a valid SHA-256")
    return sha.strip().lower()


def _file_metadata(file_data: dict[str, Any]) -> dict[str, Any]:
    metadata = file_data.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _file_type(file_data: dict[str, Any]) -> str:
    return str(file_data.get("type") or "").strip()


def _file_format(file_data: dict[str, Any]) -> str:
    return str(_file_metadata(file_data).get("format") or "").strip()


def _file_precision_label(file_data: dict[str, Any]) -> str | None:
    metadata = _file_metadata(file_data)
    value = metadata.get("quantType") or metadata.get("fp")
    if not isinstance(value, str) or not value.strip():
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
    return safe[:64].casefold() or None


def _has_duplicate_filename(
    filename: str,
    all_files: list[dict[str, Any]],
) -> bool:
    return sum(
        1
        for item in all_files
        if PurePosixPath(str(item.get("name") or "").replace("\\", "/")).name.casefold()
        == filename.casefold()
    ) > 1


def _append_file_identity(filename: str, file_id: Any) -> str:
    identity = _integer(file_id, "file identity")
    path = PurePosixPath(filename)
    marker = f".civitai-{identity}"
    if path.stem.casefold().endswith(marker.casefold()):
        return filename
    return f"{path.stem}{marker}{path.suffix}"


def _precision_aware_filename(
    filename: str,
    selected: dict[str, Any],
    all_files: list[dict[str, Any]],
) -> str:
    precision = _file_precision_label(selected)
    path = PurePosixPath(filename)
    stem = path.stem
    normalized_filename = filename
    if precision is not None:
        normalized_stem, replacements = _PRECISION_PLACEHOLDER_RE.subn(
            precision,
            stem,
            count=1,
        )
        if replacements:
            normalized_filename = f"{normalized_stem}{path.suffix}"

    duplicate_name = _has_duplicate_filename(filename, all_files)
    selected_id = selected.get("id")
    same_precision_collision = duplicate_name and any(
        item is not selected
        and str(item.get("id")) != str(selected_id)
        and PurePosixPath(
            str(item.get("name") or "").replace("\\", "/")
        ).name.casefold()
        == filename.casefold()
        and _file_precision_label(item) == precision
        for item in all_files
    )
    if same_precision_collision:
        return _append_file_identity(normalized_filename, selected_id)

    normalized_path = PurePosixPath(normalized_filename)
    if (
        duplicate_name
        and precision is not None
        and precision not in normalized_path.stem.casefold()
    ):
        return f"{normalized_path.stem}.{precision}{normalized_path.suffix}"
    return normalized_filename


def resolve_civitai_version_filenames(
    version: dict[str, Any],
    api_key: str | None,
) -> dict[int, str]:
    # Resolve all local filename suggestions with one bounded model-page request.
    version_id = _integer(version.get("id"), "model-version identity")
    model_id = _version_model_id(version)
    files = version.get("files")
    if not isinstance(files, list):
        raise TypeError("CivitAI response omitted its file list")
    all_files = [item for item in files if isinstance(item, dict)]
    page_payload = None
    try:
        page_payload = _request_model_page_payload(model_id, version_id, api_key)
    except (OSError, TypeError, ValueError, requests.RequestException) as error:
        log.debug(
            _LOG_PREFIX,
            f"Preferred filename lookup unavailable: {type(error).__name__}",
        )

    resolved: dict[int, str] = {}
    for file_data in all_files:
        try:
            file_id = _integer(file_data.get("id"), "file identity")
        except (TypeError, ValueError):
            continue
        source_filename = PurePosixPath(
            str(file_data.get("name") or "").replace("\\", "/")
        ).name
        if not source_filename or source_filename in {".", ".."}:
            continue
        preferred = None
        if page_payload is not None:
            try:
                preferred = _preferred_filename_from_page_payload(
                    page_payload,
                    model_id=model_id,
                    version_id=version_id,
                    file_id=file_id,
                    expected_sha256=_file_sha(file_data),
                    source_filename=source_filename,
                )
            except (TypeError, ValueError):
                preferred = None
        resolved[file_id] = preferred or _precision_aware_filename(
            source_filename,
            file_data,
            all_files,
        )
    return resolved


def _role_compatible(
    file_data: dict[str, Any],
    *,
    target_role: str,
    air_resource_type: str | None,
) -> bool:
    role = "diffusion_models" if target_role == "unet" else target_role
    file_type = _file_type(file_data).casefold()
    file_format = _file_format(file_data).casefold()
    air_type = (air_resource_type or "").casefold()
    ordinary_model = file_type in {"model", "pruned model"}

    if role == "checkpoints":
        return air_type == "checkpoint" and ordinary_model
    if role == "diffusion_models":
        return file_type in {"unet", "diffusion model"} or (
            air_type in {"checkpoint", "unet", "diffusionmodel"}
            and ordinary_model
        )
    if role == "diffusion_models_gguf":
        model_weight = file_type in {"unet", "diffusion model"} or (
            air_type in {"checkpoint", "unet", "diffusionmodel"}
            and ordinary_model
        )
        return model_weight and file_format == "gguf"
    if role == "vae":
        return file_type == "vae" or (air_type == "vae" and ordinary_model)
    if role in {"text_encoders", "clip"}:
        return file_type == "text encoder" or (
            air_type == "textencoder" and ordinary_model
        )
    if role == "clip_vision":
        return file_type in {"clipvision", "vision encoder"} or (
            air_type == "clipvision" and ordinary_model
        )
    if role == "loras":
        return file_type == "enhancement lora" or (
            air_type in {"lora", "lycoris", "dora"} and ordinary_model
        )
    if role == "embeddings":
        return file_type == "negative" or (
            air_type == "embedding" and ordinary_model
        )
    return False


def _public_file_summary(file_data: dict[str, Any]) -> dict[str, Any]:
    metadata = _file_metadata(file_data)
    return {
        "id": file_data.get("id"),
        "name": PurePosixPath(str(file_data.get("name") or "").replace("\\", "/")).name,
        "type": _file_type(file_data),
        "format": _file_format(file_data),
        "precision": metadata.get("fp"),
        "quantization": metadata.get("quantType"),
        "primary": file_data.get("primary") is True,
    }


def _available_precisions(candidates: list[dict[str, Any]]) -> list[str]:
    available: set[str] = set()
    for item in candidates:
        metadata = _file_metadata(item)
        fp = metadata.get("fp")
        quantization = metadata.get("quantType")
        if isinstance(fp, str) and fp.strip():
            available.add(fp.strip())
        if isinstance(quantization, str) and quantization.strip():
            available.add(quantization.strip())
        elif _file_format(item).casefold() == "gguf":
            available.add("gguf_unquantized")
    return sorted(available, key=str.casefold)


def _selection_error(
    message: str,
    candidates: list[dict[str, Any]],
) -> CivitaiSelectionError:
    return CivitaiSelectionError(
        message,
        available_files=[_public_file_summary(item) for item in candidates],
        available_precisions=_available_precisions(candidates),
    )


def _select_unambiguous(
    candidates: list[dict[str, Any]],
    *,
    message: str,
) -> dict[str, Any]:
    primary = [item for item in candidates if item.get("primary") is True]
    if len(primary) == 1:
        return primary[0]
    if not primary and len(candidates) == 1:
        return candidates[0]
    raise _selection_error(message, candidates)


def _select_unique_largest(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    sized: list[tuple[float, dict[str, Any]]] = []
    max_size_kb = MAX_DOWNLOAD_BYTES / 1024
    for item in candidates:
        size_kb = item.get("sizeKB")
        if (
            isinstance(size_kb, bool)
            or not isinstance(size_kb, (int, float))
            or size_kb <= 0
            or size_kb > max_size_kb
        ):
            return None
        sized.append((float(size_kb), item))
    largest_size = max(size for size, _item in sized)
    largest = [item for size, item in sized if size == largest_size]
    return largest[0] if len(largest) == 1 else None


def _pick_file(
    version: dict[str, Any],
    *,
    wanted_sha: str | None,
    wanted_file_id: int | None,
    download_preference: str | None,
    target_role: str,
    air_resource_type: str | None,
) -> dict[str, Any]:
    files = version.get("files")
    if not isinstance(files, list):
        raise TypeError("CivitAI response omitted its file list")
    all_files = [item for item in files if isinstance(item, dict)]
    candidates = [
        item
        for item in all_files
        if _role_compatible(
            item,
            target_role=target_role,
            air_resource_type=air_resource_type,
        )
    ]
    if not candidates:
        raise _selection_error(
            "No CivitAI files are compatible with the selected target role",
            [],
        )
    if wanted_file_id is not None:
        selected = next(
            (
                item
                for item in candidates
                if not isinstance(item.get("id"), bool)
                and str(item.get("id")) == str(wanted_file_id)
            ),
            None,
        )
        if selected is None:
            if any(str(item.get("id")) == str(wanted_file_id) for item in all_files):
                raise _selection_error(
                    "AIR file identity is incompatible with the selected target role",
                    candidates,
                )
            raise _selection_error(
                "AIR file identity is not part of the selected model version",
                candidates,
            )
        return selected
    if wanted_sha:
        normalized = wanted_sha.strip().lower()
        selected = next(
            (
                item
                for item in candidates
                if isinstance(item.get("hashes"), dict)
                and _valid_sha(item["hashes"].get("SHA256"))
                and item["hashes"]["SHA256"].lower() == normalized
            ),
            None,
        )
        if selected is None:
            matching_any = any(
                isinstance(item.get("hashes"), dict)
                and _valid_sha(item["hashes"].get("SHA256"))
                and item["hashes"]["SHA256"].lower() == normalized
                for item in all_files
            )
            message = (
                "Expected SHA-256 is incompatible with the selected target role"
                if matching_any
                else "Expected SHA-256 is not part of the selected model version"
            )
            raise _selection_error(message, candidates)
        return selected
    preference = (download_preference or "default").strip().casefold()
    if preference not in {"", "default"}:
        if preference == "fp8_e4m3fn":
            preference = "fp8"

        def matches_preference(item: dict[str, Any]) -> bool:
            metadata = _file_metadata(item)
            fp = str(metadata.get("fp") or "").casefold()
            quantization = metadata.get("quantType")
            quant = str(quantization or "").casefold()
            file_format = _file_format(item).casefold()
            if preference == "gguf":
                return file_format == "gguf"
            if preference == "gguf_unquantized":
                return file_format == "gguf" and (
                    quantization is None or quant in {"", "none"}
                )
            return preference in {fp, quant}

        preferred = [item for item in candidates if matches_preference(item)]
        if not preferred:
            raise _selection_error(
                "Requested precision or quantization is unavailable for the selected target role",
                candidates,
            )
        if len(preferred) == 1:
            return preferred[0]
        largest = _select_unique_largest(preferred)
        if largest is not None:
            return largest
        raise _selection_error(
            "Requested precision or quantization matches multiple CivitAI files without a unique largest artifact",
            preferred,
        )
    return _select_unambiguous(
        candidates,
        message="CivitAI model version has no unambiguous compatible file",
    )


def resolve_file_for_download(
    *,
    air: str | None,
    sha256: str | None,
    api_key: str | None,
    download_preference: str | None = None,
    target_role: str = "diffusion_models",
) -> CivitaiResolvedFile | None:
    parsed_air = parse_air(air) if air else None
    if air and parsed_air is None:
        raise ValueError("Malformed AIR identity")
    if sha256 and not _valid_sha(sha256):
        raise ValueError("Malformed expected SHA-256")
    if parsed_air is None and not sha256:
        return None

    version = (
        get_model_version(_integer(parsed_air["version_id"], "model-version identity"), api_key)
        if parsed_air
        else get_model_version_by_hash(sha256, api_key)
    )
    if version is None:
        return None
    version_id = _integer(version.get("id"), "model-version identity")
    model_id = _version_model_id(version)
    if parsed_air and version_id != _integer(parsed_air["version_id"], "model-version identity"):
        raise ValueError("CivitAI response model-version identity does not match AIR")
    if parsed_air and model_id != _integer(parsed_air["model_id"], "model identity"):
        raise ValueError("CivitAI response model identity does not match AIR")

    canonical_air = version.get("air") if isinstance(version.get("air"), str) else air
    canonical_identity = parse_air(canonical_air) if canonical_air else None
    if canonical_identity and (
        _integer(canonical_identity["model_id"], "model identity") != model_id
        or _integer(canonical_identity["version_id"], "model-version identity")
        != version_id
    ):
        raise ValueError("CivitAI canonical AIR conflicts with response identities")
    air_resource_type = None
    if canonical_identity:
        air_resource_type = str(canonical_identity["resource_type"])
    elif parsed_air:
        air_resource_type = str(parsed_air["resource_type"])

    selected = _pick_file(
        version,
        wanted_sha=sha256,
        wanted_file_id=(
            _integer(parsed_air["file_id"], "file identity")
            if parsed_air and "file_id" in parsed_air
            else None
        ),
        download_preference=download_preference,
        target_role=target_role,
        air_resource_type=air_resource_type,
    )
    file_id = _integer(selected.get("id"), "file identity")
    resolved_air = canonical_air if canonical_identity else air
    if resolved_air and parse_air(resolved_air):
        resolved_air = f"{re.sub(r'\+\d+$', '', resolved_air)}+{file_id}"
    resolved_sha = _file_sha(selected)
    if sha256 and not hmac.compare_digest(resolved_sha, sha256.strip().lower()):
        raise ValueError("CivitAI SHA-256 does not match the requested identity")
    filename = PurePosixPath(
        str(selected.get("name") or "").replace("\\", "/")
    ).name
    if not filename or filename in {".", ".."}:
        raise ValueError("CivitAI response omitted a safe filename")
    version_files = version.get("files")
    selection_files = (
        [item for item in version_files if isinstance(item, dict)]
        if isinstance(version_files, list)
        else []
    )
    preferred_filename = None
    try:
        preferred_filename = _preferred_filename_for_file(
            model_id=model_id,
            version_id=version_id,
            file_id=file_id,
            expected_sha256=resolved_sha,
            source_filename=filename,
            api_key=api_key,
        )
    except (OSError, TypeError, ValueError, requests.RequestException) as error:
        log.debug(
            _LOG_PREFIX,
            f"Preferred filename lookup unavailable: {type(error).__name__}",
        )
    filename = preferred_filename or _precision_aware_filename(
        filename,
        selected,
        selection_files,
    )

    size_kb = selected.get("sizeKB")
    expected_size = None
    if isinstance(size_kb, (int, float)) and not isinstance(size_kb, bool) and size_kb > 0:
        expected_size = int(size_kb * 1024)
    metadata = _file_metadata(selected)
    file_type = _file_type(selected)
    file_format = _file_format(selected)
    precision_value = metadata.get("quantType") or metadata.get("fp")
    precision = str(precision_value) if precision_value not in (None, "") else None
    download_url = f"{_BASE_URL}/api/download/models/{version_id}?fileId={quote(str(file_id))}"
    log.msg(
        _LOG_PREFIX,
        "Resolved "
        f"role={target_role} AIR={resolved_air or 'hash-only'} "
        f"version={version_id} file={file_id} name={filename} type={file_type or 'unknown'} "
        f"format={file_format or 'unknown'} precision={precision or 'default'} "
        f"sha256={resolved_sha[:12]}…",
    )
    return {
        "air": resolved_air,
        "sha256": resolved_sha,
        "filename": filename,
        "download_url": download_url,
        "model_id": model_id,
        "model_version_id": version_id,
        "file_id": file_id,
        "expected_size": expected_size,
        "file_type": file_type,
        "file_format": file_format,
        "precision": precision,
    }


def _open_download(url: str, headers: dict[str, str]):
    current = url
    credential_host = urlparse(url).hostname
    for _redirect in range(MAX_REDIRECTS + 1):
        _validate_public_url(current)
        request_headers = dict(headers)
        if urlparse(current).hostname != credential_host:
            request_headers.pop("Authorization", None)
        response = requests.get(
            current,
            headers=request_headers,
            timeout=(10, 120),
            stream=True,
            allow_redirects=False,
        )
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        location = response.headers.get("Location")
        response.close()
        if not location:
            raise ValueError("Download redirect omitted Location")
        current = urljoin(current, location)
    raise ValueError("Download exceeded the redirect limit")


def _digest_file(path: Path, progress_cb=None, *, algorithm: str = "sha256") -> str:
    total = path.stat().st_size
    if algorithm == "sha256":
        digest = hashlib.sha256()
    elif algorithm == "git-sha1":
        digest = hashlib.sha1(usedforsecurity=False)
        digest.update(f"blob {total}\0".encode())
    else:
        raise ValueError("Unsupported provider digest algorithm")
    processed = 0
    with path.open("rb", buffering=0) as file_handle:
        while chunk := file_handle.read(8 * 1024 * 1024):
            digest.update(chunk)
            processed += len(chunk)
            if progress_cb is not None:
                try:
                    progress_cb(processed, total)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    log.debug(_LOG_PREFIX, "Hash progress callback failed")
    return digest.hexdigest()


def _provider_digest_file(path: Path, progress_cb=None, *, algorithm: str) -> str:
    if algorithm == "sha256":
        return _digest_file(path, progress_cb=progress_cb)
    return _digest_file(path, progress_cb=progress_cb, algorithm=algorithm)


def _open_staging_file(path: Path, *, append: bool, expected_size: int):
    if path.is_symlink():
        raise ValueError("Symlinked download staging files are forbidden")
    if path.exists() and not path.is_file():
        raise ValueError("Download staging path must be a regular file")
    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_APPEND if append else os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("Download staging path must be a regular file")
        if append and file_stat.st_size != expected_size:
            raise OSError("Download staging file changed before resume")
        return os.fdopen(descriptor, "ab" if append else "wb")
    except Exception:
        os.close(descriptor)
        raise


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


def _promote_verified_staging(
    staging: Path,
    destination: Path,
    expected_digest: str,
    *,
    digest_algorithm: str,
    allow_replace_existing: bool,
) -> None:
    if destination.is_symlink() or destination.parent.is_symlink():
        raise ValueError("Symlinked download destinations are forbidden")
    if destination.exists() and not allow_replace_existing:
        if destination.is_file() and hmac.compare_digest(
            _provider_digest_file(destination, algorithm=digest_algorithm),
            expected_digest,
        ):
            staging.unlink(missing_ok=True)
            return
        raise FileExistsError("Download destination appeared during acquisition")
    os.replace(staging, destination)


def _notify_phase(phase_cb, phase: str, downloaded: int = 0, total: int = 0) -> None:
    if phase_cb is None:
        return
    try:
        phase_cb(phase, downloaded, total)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        log.debug(_LOG_PREFIX, "Download phase callback failed")


def _download_file_locked(
    *,
    url: str,
    destination: Path,
    api_key: str | None,
    expected_sha256: str | None = None,
    expected_git_blob: str | None = None,
    expected_size: int | None = None,
    progress_cb=None,
    phase_cb=None,
    download_id: str | None = None,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    require_idle_promotion: bool = False,
    allow_replace_existing: bool = False,
    keep_partial_on_cancel: bool = False,
    provider_name: str = "CivitAI",
) -> bool:
    valid_sha256 = _valid_sha(expected_sha256)
    valid_git_blob = (
        isinstance(expected_git_blob, str)
        and _GIT_BLOB_RE.fullmatch(expected_git_blob.strip()) is not None
    )
    if valid_sha256 == valid_git_blob:
        log.error(
            _LOG_PREFIX,
            "Download rejected because exactly one valid provider digest is required",
        )
        return False
    destination = Path(destination)
    if destination.is_symlink() or destination.parent.is_symlink():
        log.error(_LOG_PREFIX, "Download destination may not be a symlink")
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(f"{destination}.part")
    expected = (
        expected_sha256.strip().lower()
        if valid_sha256 and isinstance(expected_sha256, str)
        else expected_git_blob.strip().lower()
        if isinstance(expected_git_blob, str)
        else ""
    )
    digest_algorithm = "sha256" if valid_sha256 else "git-sha1"

    if staging.is_symlink() or (staging.exists() and not staging.is_file()):
        log.error(_LOG_PREFIX, "Download staging path must be a non-symlink regular file")
        return False

    if destination.exists():
        destination_size = destination.stat().st_size if destination.is_file() else 0
        _notify_phase(phase_cb, "verifying", 0, destination_size)
        if destination.is_file() and hmac.compare_digest(
            _provider_digest_file(
                destination,
                progress_cb=lambda processed, total: _notify_phase(
                    phase_cb, "verifying", processed, total
                ),
                algorithm=digest_algorithm,
            ),
            expected,
        ):
            return True
        if not destination.is_file():
            return False

    partial_size = staging.stat().st_size if staging.is_file() and not staging.is_symlink() else 0
    headers = _auth_headers(api_key)
    headers["Accept"] = "application/octet-stream"
    if partial_size:
        headers["Range"] = f"bytes={partial_size}-"

    transfer: _ActiveTransfer | None = None
    try:
        response = _open_download(url, headers)
        with response:
            if partial_size:
                if response.status_code == 416:
                    _notify_phase(phase_cb, "hashing", 0, partial_size)
                    if hmac.compare_digest(
                        _provider_digest_file(
                            staging,
                            progress_cb=lambda processed, total: _notify_phase(
                                phase_cb, "hashing", processed, total
                            ),
                            algorithm=digest_algorithm,
                        ),
                        expected,
                    ):
                        if require_idle_promotion:
                            _notify_phase(phase_cb, "locking", partial_size, partial_size)
                            with maintenance_if_idle() as acquired:
                                if not acquired:
                                    raise BlockingIOError("Prompt queue is active")
                                _notify_phase(phase_cb, "promoting", partial_size, partial_size)
                                _promote_verified_staging(
                                    staging,
                                    destination,
                                    expected,
                                    digest_algorithm=digest_algorithm,
                                    allow_replace_existing=allow_replace_existing,
                                )
                        else:
                            _notify_phase(phase_cb, "promoting", partial_size, partial_size)
                            _promote_verified_staging(
                                staging,
                                destination,
                                expected,
                                digest_algorithm=digest_algorithm,
                                allow_replace_existing=allow_replace_existing,
                            )
                        _fsync_directory(destination.parent)
                        invalidate_cache_entry(destination)
                        return True
                    staging.unlink(missing_ok=True)
                    return False
                if response.status_code != 206:
                    raise ValueError("Server did not honor the requested download range")
                content_range = response.headers.get("Content-Range", "")
                match = _CONTENT_RANGE_RE.fullmatch(content_range)
                if match is None or int(match.group(1)) != partial_size:
                    raise ValueError("Server returned an invalid Content-Range")
                range_end = int(match.group(2))
                total_size = int(match.group(3)) if match.group(3) != "*" else None
                if range_end < partial_size or (total_size is not None and range_end >= total_size):
                    raise ValueError("Server returned an inconsistent Content-Range")
            else:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError("Unexpected download response")
                total_size = None

            content_length_header = response.headers.get("Content-Length")
            content_length = int(content_length_header) if content_length_header else None
            if content_length is not None and content_length < 0:
                raise ValueError("Invalid Content-Length")
            if (
                partial_size
                and content_length is not None
                and partial_size + content_length != range_end + 1
            ):
                raise ValueError("Content-Length conflicts with Content-Range")
            predicted_total = (
                total_size
                if partial_size and total_size is not None
                else partial_size + content_length
                if content_length is not None
                else None
            )
            if predicted_total is not None and predicted_total > max_bytes:
                raise ValueError("Download exceeds the configured size limit")
            if expected_size is not None and predicted_total is not None:
                tolerance = max(4096, int(expected_size * 0.01))
                if abs(predicted_total - expected_size) > tolerance:
                    raise ValueError(
                        f"Download size conflicts with {provider_name} metadata"
                    )
            required = (content_length or max((expected_size or 0) - partial_size, 0)) + MIN_FREE_RESERVE_BYTES
            if shutil.disk_usage(destination.parent).free < required:
                raise OSError("Insufficient disk space for verified download")

            downloaded = partial_size
            transfer = _activate_transfer(download_id, response)
            _notify_phase(phase_cb, "transferring", downloaded, predicted_total or 0)
            try:
                with _open_staging_file(
                    staging,
                    append=bool(partial_size),
                    expected_size=partial_size,
                ) as staging_file:
                    for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                        if transfer is not None and transfer.cancel_requested.is_set():
                            raise DownloadCancelled("Download transfer was cancelled")
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            raise ValueError("Download exceeds the configured size limit")
                        staging_file.write(chunk)
                        if transfer is not None and transfer.cancel_requested.is_set():
                            raise DownloadCancelled("Download transfer was cancelled")
                        if progress_cb is not None:
                            try:
                                progress_cb(downloaded, predicted_total or 0)
                            except (AttributeError, RuntimeError, TypeError, ValueError):
                                log.debug(_LOG_PREFIX, "Download progress callback failed")
                    if _close_transfer_window(download_id, transfer):
                        raise DownloadCancelled("Download transfer was cancelled")
                    staging_file.flush()
                    os.fsync(staging_file.fileno())
            finally:
                _deactivate_transfer(download_id, transfer)
            if predicted_total is not None and downloaded != predicted_total:
                raise OSError("Download was truncated")

        _notify_phase(phase_cb, "hashing", 0, downloaded)
        actual = _provider_digest_file(
            staging,
            progress_cb=lambda processed, total: _notify_phase(
                phase_cb, "hashing", processed, total
            ),
            algorithm=digest_algorithm,
        )
        if not hmac.compare_digest(actual, expected):
            staging.unlink(missing_ok=True)
            _notify_phase(phase_cb, "failed", downloaded, downloaded)
            log.error(
                _LOG_PREFIX,
                f"Downloaded bytes did not match the {provider_name} provider digest",
            )
            return False
        if require_idle_promotion:
            _notify_phase(phase_cb, "locking", downloaded, predicted_total or downloaded)
            with maintenance_if_idle() as acquired:
                if not acquired:
                    raise BlockingIOError("Prompt queue is active")
                _notify_phase(phase_cb, "promoting", downloaded, predicted_total or downloaded)
                _promote_verified_staging(
                    staging,
                    destination,
                    expected,
                    digest_algorithm=digest_algorithm,
                    allow_replace_existing=allow_replace_existing,
                )
        else:
            _notify_phase(phase_cb, "promoting", downloaded, predicted_total or downloaded)
            _promote_verified_staging(
                staging,
                destination,
                expected,
                digest_algorithm=digest_algorithm,
                allow_replace_existing=allow_replace_existing,
            )
        _fsync_directory(destination.parent)
        invalidate_cache_entry(destination)
        return True
    except DownloadCancelled:
        _deactivate_transfer(download_id, transfer)
        if not keep_partial_on_cancel:
            staging.unlink(missing_ok=True)
        _fsync_directory(destination.parent)
        _notify_phase(phase_cb, "aborted")
        log.msg(_LOG_PREFIX, f"Download {download_id or ''} cancelled during transfer")
        raise
    except BlockingIOError:
        raise
    except (OSError, requests.RequestException, ValueError) as error:
        _deactivate_transfer(download_id, transfer)
        if transfer is not None and transfer.cancel_requested.is_set():
            if not keep_partial_on_cancel:
                staging.unlink(missing_ok=True)
            _fsync_directory(destination.parent)
            _notify_phase(phase_cb, "aborted")
            log.msg(_LOG_PREFIX, f"Download {download_id or ''} cancelled during transfer")
            raise DownloadCancelled("Download transfer was cancelled") from error
        _notify_phase(phase_cb, "failed")
        log.error(_LOG_PREFIX, f"Download failed: {type(error).__name__}: {error}")
        return False


def download_file(
    *,
    url: str,
    destination: Path,
    api_key: str | None,
    expected_sha256: str | None = None,
    expected_git_blob: str | None = None,
    expected_size: int | None = None,
    progress_cb=None,
    phase_cb=None,
    download_id: str | None = None,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    require_idle_promotion: bool = False,
    allow_replace_existing: bool = False,
    keep_partial_on_cancel: bool = False,
    provider_name: str = "CivitAI",
) -> bool:
    destination = Path(destination)
    destination_identity = _claim_download_destination(destination)
    try:
        return _download_file_locked(
            url=url,
            destination=destination,
            api_key=api_key,
            expected_sha256=expected_sha256,
            expected_git_blob=expected_git_blob,
            expected_size=expected_size,
            progress_cb=progress_cb,
            phase_cb=phase_cb,
            download_id=download_id,
            max_bytes=max_bytes,
            require_idle_promotion=require_idle_promotion,
            allow_replace_existing=allow_replace_existing,
            keep_partial_on_cancel=keep_partial_on_cancel,
            provider_name=provider_name,
        )
    finally:
        _release_download_destination(destination_identity)


def discard_partial_download(destination: Path) -> int:
    destination = Path(destination)
    destination_identity = _claim_download_destination(destination)
    try:
        if destination.is_symlink() or destination.parent.is_symlink():
            raise ValueError("Symlinked download destinations are forbidden")
        staging = Path(f"{destination}.part")
        if staging.is_symlink():
            raise ValueError("Symlinked download staging files are forbidden")
        if not staging.exists():
            return 0
        if not staging.is_file():
            raise ValueError("Download staging path must be a regular file")
        removed_bytes = staging.stat().st_size
        staging.unlink()
        _fsync_directory(destination.parent)
        return removed_bytes
    finally:
        _release_download_destination(destination_identity)
