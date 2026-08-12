# Immutable provider inspection and destination compatibility policy.

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import folder_paths  # type: ignore

from ..civitai_client import (
    get_model_version,
    get_model_version_by_hash,
    parse_air,
    resolve_civitai_version_filenames,
)
from ..config_store import get_config_value
from ..sml.credentials import resolve_auth_token

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_GIT_OID_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_HF_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CIVITAI_VERSION_RE = re.compile(r"^/api/v1/model-versions/(\d+)/?$")
_CIVITAI_DOWNLOAD_RE = re.compile(r"^/api/download/models/(\d+)/?$")
_CIVITAI_MODEL_RE = re.compile(r"^/models/(\d+)(?:/[^/?#]+)?/?$")
_CIVITAI_DOWNLOAD_QUERY_KEYS = {"fileId", "type", "format", "size", "fp", "quantType"}

_CATEGORY_LABELS = {
    "checkpoints": "Checkpoints",
    "diffusion_models": "Diffusion Models",
    "diffusion_models_gguf": "Diffusion Models (GGUF)",
    "loras": "LoRAs",
    "text_encoders": "Text Encoders",
    "clip": "Text Encoders (CLIP)",
    "clip_vision": "CLIP Vision",
    "vae": "VAEs",
    "audio_encoders": "Audio Encoders",
    "embeddings": "Embeddings",
    "controlnet": "ControlNet",
    "upscale_models": "Upscale Models",
    "latent_upscale_models": "Latent Upscale Models",
    "style_models": "Style Models",
    "gligen": "GLIGEN",
    "hypernetworks": "Hypernetworks",
    "model_patches": "Model Patches",
    "detection": "Detection",
}
_EXCLUDED_CATEGORIES = {"custom_nodes", "configs", "diffusers"}
_SAFE_TENSOR_EXTENSIONS = {".safetensors", ".sft"}
_LEGACY_EXTENSIONS = {".ckpt", ".pkl", ".bin", ".pt", ".pth"}
_GGUF_CATEGORIES = {
    "clip",
    "clip_gguf",
    "diffusion_models",
    "diffusion_models_gguf",
    "text_encoders",
    "unet_gguf",
}
_STANDARD_MODEL_CATEGORIES = {
    "audio_encoders",
    "checkpoints",
    "clip",
    "clip_gguf",
    "clip_vision",
    "controlnet",
    "detection",
    "diffusion_models",
    "diffusion_models_gguf",
    "embeddings",
    "gligen",
    "hypernetworks",
    "latent_upscale_models",
    "loras",
    "model_patches",
    "style_models",
    "text_encoders",
    "upscale_models",
    "unet_gguf",
    "vae",
}
_MAX_INSPECTION_FILES = 50_000
_WEIGHT_HINTS = {
    "checkpoint": "checkpoints",
    "diffusionmodel": "diffusion_models",
    "diffusion model": "diffusion_models",
    "unet": "diffusion_models",
    "lora": "loras",
    "locon": "loras",
    "lycoris": "loras",
    "dora": "loras",
    "textencoder": "text_encoders",
    "text encoder": "text_encoders",
    "clipvision": "clip_vision",
    "clip vision": "clip_vision",
    "vae": "vae",
    "embedding": "embeddings",
    "textualinversion": "embeddings",
    "controlnet": "controlnet",
    "upscaler": "upscale_models",
    "hypernet": "hypernetworks",
}


def _root_id(category: str, index: int) -> str:
    return f"{category}:{index}"


def get_destination_categories() -> list[dict[str, Any]]:
    categories: list[dict[str, Any]] = []
    for category, value in sorted(folder_paths.folder_names_and_paths.items()):
        if category in _EXCLUDED_CATEGORIES or not isinstance(value, tuple) or not value:
            continue
        roots = value[0]
        if not isinstance(roots, (list, tuple)) or not roots:
            continue
        root_entries: list[tuple[int, str]] = []
        seen_roots: set[Path] = set()
        for index, root_value in enumerate(roots):
            root = Path(root_value).expanduser().absolute()
            if root in seen_roots:
                continue
            seen_roots.add(root)
            root_entries.append((index, root.name or str(root)))
        label_counts: dict[str, int] = {}
        for _index, label in root_entries:
            label_counts[label] = label_counts.get(label, 0) + 1
        label_ordinals: dict[str, int] = {}
        public_roots = []
        for index, label in root_entries:
            if label_counts[label] > 1:
                label_ordinals[label] = label_ordinals.get(label, 0) + 1
                label = f"{label} ({label_ordinals[label]})"
            public_roots.append(
                {
                    "id": _root_id(category, index),
                    "index": index,
                    "label": label,
                }
            )
        categories.append(
            {
                "id": category,
                "label": _CATEGORY_LABELS.get(category, category.replace("_", " ").title()),
                "roots": public_roots,
            }
        )
    return categories


def resolve_destination_root(category: str, root_id: str) -> tuple[Path, int]:
    if category in _EXCLUDED_CATEGORIES or category not in folder_paths.folder_names_and_paths:
        raise ValueError("Unknown destination category")
    prefix = f"{category}:"
    if not isinstance(root_id, str) or not root_id.startswith(prefix):
        raise ValueError("Destination root does not belong to the category")
    try:
        index = int(root_id[len(prefix) :])
    except ValueError as error:
        raise ValueError("Invalid destination root") from error
    roots = folder_paths.get_folder_paths(category)
    if index < 0 or index >= len(roots):
        raise ValueError("Destination root is no longer registered")
    lexical = Path(roots[index]).expanduser().absolute()
    if lexical.is_symlink():
        raise ValueError("Symlinked destination roots are forbidden")
    lexical.mkdir(parents=True, exist_ok=True)
    return lexical.resolve(strict=True), index


def _category_registers_safe_tensor(category: str) -> bool:
    value = folder_paths.folder_names_and_paths.get(category)
    if not isinstance(value, tuple) or len(value) < 2:
        return False
    extensions = value[1]
    if not isinstance(extensions, (set, frozenset, list, tuple)):
        return False
    normalized = {
        extension.casefold()
        for extension in extensions
        if isinstance(extension, str)
    }
    return bool(normalized & _SAFE_TENSOR_EXTENSIONS)


def extension_compatible(extension: str, category: str) -> bool:
    extension = extension.casefold()
    if extension in _SAFE_TENSOR_EXTENSIONS:
        return (
            category not in _EXCLUDED_CATEGORIES
            and (
                category in _STANDARD_MODEL_CATEGORIES
                or _category_registers_safe_tensor(category)
            )
        )
    if extension == ".gguf":
        return category in _GGUF_CATEGORIES
    if extension == ".pth" and category == "upscale_models":
        return True
    if extension == ".pt" and category == "embeddings":
        return True
    if extension == ".onnx" and category in {"detection", "audio_encoders"}:
        return True
    if extension in _LEGACY_EXTENSIONS:
        return get_config_value("allow_legacy_model_formats", False) is True
    return False


def compatible_categories(filename: str) -> list[str]:
    extension = Path(filename).suffix.casefold()
    return [
        item["id"]
        for item in get_destination_categories()
        if extension_compatible(extension, item["id"])
    ]


def infer_category(filename: str, provider_type: str, format_name: str) -> tuple[str | None, bool]:
    haystack = f"{provider_type} {format_name} {filename}".casefold().replace("_", " ")
    for hint, category in _WEIGHT_HINTS.items():
        if hint in haystack:
            return category, False
    if re.search(r"(?:^|[/_.-])(t5|clip|umt5|llm|text.encoder)(?:[/_.-]|$)", haystack):
        return "text_encoders", False
    if "audio" in haystack and ("vae" in haystack or "encoder" in haystack):
        return "audio_encoders", False
    extension = Path(filename).suffix.casefold()
    if extension == ".gguf":
        return None, True
    if extension in _SAFE_TENSOR_EXTENSIONS | _LEGACY_EXTENSIONS:
        return None, True
    return None, False


def _decorate_row(row: dict[str, Any]) -> dict[str, Any]:
    categories = compatible_categories(row["remote_path"])
    suggestion, ambiguous = infer_category(
        row["remote_path"], row.get("provider_type", ""), row.get("format", "")
    )
    if suggestion not in categories:
        suggestion = categories[0] if len(categories) == 1 else None
        ambiguous = len(categories) > 1
    digest = row.get("expected_digest")
    disabled_reason = None
    if not isinstance(digest, dict) or not digest.get("value"):
        disabled_reason = "The provider did not publish verifiable file identity metadata."
    elif not categories:
        disabled_reason = "This file format is not compatible with a registered model category."
    row.update(
        {
            "key": uuid.uuid4().hex,
            "compatible_categories": categories,
            "suggested_category": suggestion,
            "category_ambiguous": ambiguous,
            "supported": disabled_reason is None,
            "disabled_reason": disabled_reason,
        }
    )
    return row


def _parse_civitai_locator(locator: str) -> tuple[dict[str, Any], dict[str, int | str] | None]:
    locator = locator.strip()
    api_key = get_config_value("civitai_api_key", "") or None
    parsed_air = parse_air(locator)
    if parsed_air:
        return get_model_version(int(parsed_air["version_id"]), api_key), parsed_air
    if _SHA256_RE.fullmatch(locator):
        version = get_model_version_by_hash(locator, api_key)
        if version is None:
            raise ValueError("CivitAI did not find that SHA-256")
        return version, {"sha256": locator.casefold()}
    parsed = urlparse(locator)
    if parsed.scheme != "https" or parsed.hostname not in {"civitai.com", "www.civitai.com"}:
        raise ValueError("Enter a CivitAI AIR, SHA-256, model-version URL, or download URL")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("CivitAI URL contains unsupported components")
    query = parse_qs(parsed.query)
    version_id = None
    selected: dict[str, int | str] = {}
    match = _CIVITAI_VERSION_RE.fullmatch(parsed.path) or _CIVITAI_DOWNLOAD_RE.fullmatch(parsed.path)
    if match:
        version_id = int(match.group(1))
        allowed_query = (
            _CIVITAI_DOWNLOAD_QUERY_KEYS
            if _CIVITAI_DOWNLOAD_RE.fullmatch(parsed.path)
            else set()
        )
        if set(query) - allowed_query:
            raise ValueError("CivitAI URL contains unsupported query parameters")
    else:
        model_match = _CIVITAI_MODEL_RE.fullmatch(parsed.path)
        if set(query) - {"modelVersionId"}:
            raise ValueError("CivitAI model URL contains unsupported query parameters")
        values = query.get("modelVersionId", [])
        if model_match and len(values) == 1 and values[0].isdigit():
            version_id = int(values[0])
            selected["model_id"] = int(model_match.group(1))
    file_values = query.get("fileId", [])
    if len(file_values) == 1 and file_values[0].isdigit() and int(file_values[0]) > 0:
        selected["file_id"] = int(file_values[0])
    elif file_values:
        raise ValueError("CivitAI fileId must be one positive integer")
    for key in _CIVITAI_DOWNLOAD_QUERY_KEYS - {"fileId"}:
        values = query.get(key, [])
        if len(values) > 1 or any(not value or len(value) > 64 for value in values):
            raise ValueError(f"CivitAI {key} selector is invalid")
        if values:
            selected[key] = values[0]
    if version_id is None:
        raise ValueError("CivitAI URL must identify one model version")
    selected["version_id"] = version_id
    return get_model_version(version_id, api_key), selected


def inspect_civitai(locator: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    version, selected = _parse_civitai_locator(locator)
    version_id = int(version.get("id") or 0)
    if version_id <= 0:
        raise ValueError("CivitAI response omitted the model-version identity")
    canonical_air = version.get("air")
    parsed_canonical = parse_air(canonical_air) if isinstance(canonical_air, str) else None
    model_id = int(version.get("modelId") or (parsed_canonical or {}).get("model_id") or 0)
    if model_id <= 0:
        raise ValueError("CivitAI response omitted the model identity")
    if selected and selected.get("version_id") not in {None, version_id}:
        raise ValueError("CivitAI locator does not match the returned version")
    if selected and selected.get("model_id") not in {None, model_id}:
        raise ValueError("CivitAI model URL does not match the returned version")
    base_air = canonical_air if isinstance(canonical_air, str) and parse_air(canonical_air) else None
    if base_air and "+" in base_air:
        base_air = base_air.rsplit("+", 1)[0]
    files = version.get("files")
    if not isinstance(files, list):
        raise TypeError("CivitAI response omitted its file list")
    suggested_filenames = resolve_civitai_version_filenames(
        version,
        get_config_value("civitai_api_key", "") or None,
    )
    rows: list[dict[str, Any]] = []
    matched_hash = selected.get("sha256") if selected else None
    for file_data in files:
        if not isinstance(file_data, dict):
            continue
        file_id = file_data.get("id")
        name = file_data.get("name")
        hashes = file_data.get("hashes")
        sha256 = hashes.get("SHA256") if isinstance(hashes, dict) else None
        metadata = file_data.get("metadata") if isinstance(file_data.get("metadata"), dict) else {}
        if not isinstance(file_id, int) or not isinstance(name, str) or not name.strip():
            continue
        size_kb = file_data.get("sizeKB")
        size = int(float(size_kb) * 1024) if isinstance(size_kb, (int, float)) and size_kb >= 0 else None
        exact_air = f"{base_air}+{file_id}" if base_air else None
        file_sha = sha256.casefold() if isinstance(sha256, str) and _SHA256_RE.fullmatch(sha256) else None
        precision = metadata.get("quantType") or metadata.get("fp")
        rows.append(
            _decorate_row(
                {
                    "remote_path": name.strip(),
                    "suggested_filename": suggested_filenames.get(
                        file_id, name.strip()
                    ),
                    "provider": "civitai",
                    "provider_type": str(file_data.get("type") or "Model"),
                    "format": str(metadata.get("format") or Path(name).suffix.lstrip(".")),
                    "precision": str(precision) if precision else None,
                    "size": size,
                    "primary": file_data.get("primary") is True,
                    "air": exact_air,
                    "expected_digest": {
                        "algorithm": "sha256",
                        "value": file_sha,
                        "source": "civitai",
                    },
                    "identity": {
                        "provider": "civitai",
                        "model_id": model_id,
                        "version_id": version_id,
                        "file_id": file_id,
                        "sha256": file_sha,
                        "size": size,
                        "filename": name.strip(),
                        "air": exact_air,
                    },
                    "locator_match": (
                        selected is not None
                        and (selected.get("file_id") == file_id or matched_hash == file_sha)
                    ),
                }
            )
        )
    if selected and "file_id" in selected and not any(
        row["identity"]["file_id"] == selected["file_id"] for row in rows
    ):
        raise ValueError("CivitAI exact file locator was not found in the version")
    if matched_hash and not any(
        row["expected_digest"]["value"] == matched_hash for row in rows
    ):
        raise ValueError("CivitAI SHA-256 locator did not match the returned version")
    return {
        "provider": "civitai",
        "model_id": model_id,
        "revision": str(version_id),
        "label": str(version.get("name") or f"CivitAI version {version_id}"),
    }, rows


def _parse_hf_locator(locator: str, revision: str | None) -> tuple[str, str | None, str | None]:
    locator = locator.strip()
    selected_path = None
    locator_revision = None
    if "://" not in locator:
        parts = locator.split("/")
        if len(parts) != 2 or not all(_HF_COMPONENT_RE.fullmatch(part) for part in parts):
            raise ValueError("Hugging Face repository IDs must use owner/repository")
        repo_id = locator
    else:
        parsed = urlparse(locator)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"huggingface.co", "www.huggingface.co"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("Enter a canonical Hugging Face repository or file URL")
        query = parse_qs(parsed.query, keep_blank_values=True)
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if len(parts) < 2 or not all(_HF_COMPONENT_RE.fullmatch(part) for part in parts[:2]):
            raise ValueError("Hugging Face URL does not identify a repository")
        repo_id = "/".join(parts[:2])
        if len(parts) > 2:
            if len(parts) < 5 or parts[2] not in {"blob", "resolve"}:
                raise ValueError("Hugging Face file URLs must use /blob/ or /resolve/")
            if set(query) - {"download"} or query.get("download", []) not in ([], ["true"]):
                raise ValueError("Hugging Face file URL contains unsupported query parameters")
            locator_revision = parts[3]
            selected_parts = parts[4:]
            if any(part in {"", ".", ".."} or "\x00" in part for part in selected_parts):
                raise ValueError("Hugging Face file URL contains an unsafe path")
            selected_path = "/".join(selected_parts)
        elif query:
            raise ValueError("Hugging Face repository URL contains unsupported query parameters")
    requested_revision = revision.strip() if isinstance(revision, str) and revision.strip() else locator_revision
    return repo_id, requested_revision, selected_path


def _hf_precision(filename: str) -> str | None:
    match = re.search(
        r"(?:^|[._-])((?:u?int|fp|bf|nf|nvfp)\d+(?:_[a-z0-9]+)?|q\d(?:_[a-z0-9]+)+)(?:[._-]|$)",
        filename.casefold(),
    )
    return match.group(1) if match else None


def inspect_huggingface(locator: str, revision: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from huggingface_hub import HfApi  # type: ignore
    from huggingface_hub.hf_api import RepoFile  # type: ignore

    repo_id, requested_revision, selected_path = _parse_hf_locator(locator, revision)
    token = resolve_auth_token("huggingface")
    api = HfApi(token=token)
    info = api.model_info(repo_id, revision=requested_revision, token=token)
    commit = info.sha
    if not isinstance(commit, str) or not _GIT_OID_RE.fullmatch(commit):
        raise ValueError("Hugging Face did not resolve an immutable commit")
    if selected_path:
        entries = api.get_paths_info(repo_id, selected_path, revision=commit, token=token)
    else:
        entries = api.list_repo_tree(repo_id, recursive=True, expand=False, revision=commit, token=token)
    rows: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, RepoFile):
            continue
        if len(rows) >= _MAX_INSPECTION_FILES:
            raise ValueError("Hugging Face repository exceeds the inspection file limit")
        lfs_sha = entry.lfs.sha256 if entry.lfs is not None else None
        if isinstance(lfs_sha, str) and _SHA256_RE.fullmatch(lfs_sha):
            algorithm = "sha256"
            value = lfs_sha.casefold()
            source = "huggingface-xet" if entry.xet_hash else "huggingface-lfs"
            provider_type = "Xet" if entry.xet_hash else "LFS"
        else:
            algorithm = "git-sha1"
            value = entry.blob_id.casefold() if isinstance(entry.blob_id, str) and _GIT_OID_RE.fullmatch(entry.blob_id) else None
            source = "huggingface-git"
            provider_type = "Git blob"
        rows.append(
            _decorate_row(
                {
                    "remote_path": entry.path,
                    "provider": "huggingface",
                    "provider_type": provider_type,
                    "format": Path(entry.path).suffix.lstrip(".") or "file",
                    "precision": _hf_precision(entry.path),
                    "size": entry.size,
                    "primary": False,
                    "air": None,
                    "expected_digest": {
                        "algorithm": algorithm,
                        "value": value,
                        "source": source,
                    },
                    "identity": {
                        "provider": "huggingface",
                        "repo_id": repo_id,
                        "commit": commit.casefold(),
                        "path": entry.path,
                        "digest_algorithm": algorithm,
                        "digest": value,
                        "size": entry.size,
                    },
                    "locator_match": bool(selected_path),
                }
            )
        )
    if selected_path and not rows:
        raise ValueError("Hugging Face file was not found at the resolved commit")
    return {
        "provider": "huggingface",
        "repo_id": repo_id,
        "revision": commit.casefold(),
        "requested_revision": requested_revision,
        "label": repo_id,
    }, rows


def inspect_provider(provider: str, locator: str, revision: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = provider.strip().casefold() if isinstance(provider, str) else ""
    if not isinstance(locator, str) or not locator.strip() or len(locator) > 2048:
        raise ValueError("A bounded provider locator is required")
    if normalized == "civitai":
        return inspect_civitai(locator)
    if normalized == "huggingface":
        return inspect_huggingface(locator, revision)
    raise ValueError("Provider must be CivitAI or Hugging Face")
