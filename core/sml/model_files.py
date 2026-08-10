# Smart Language Model File Handling
# Handles file scanning, model list generation, download utilities, and hash verification

import hashlib
import inspect
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlsplit

from .config_templates import (
    get_config_value,
    get_llm_models_path,
)
from .credentials import resolve_auth_token
from .json_store import JsonStoreError, read_json_object, update_json_object
from .logger import log

# Model list cache for get_llm_model_list and get_mmproj_list (avoids repeated directory scans)
_model_list_cache: List[str] = []
_model_list_cache_time: float = 0.0
_mmproj_list_cache: List[str] = []
_mmproj_list_cache_time: float = 0.0
_MODEL_LIST_CACHE_TTL: float = 30.0  # Cache for 30 seconds


@dataclass
class VerificationResult:
    # Result of model integrity verification.
    success: bool
    corrupted_files: List[Path]  # List of files that failed hash verification
    verified_count: int  # Number of files that passed verification
    skipped_count: int  # Number of files skipped (no reference hash)


@dataclass(frozen=True)
class HashSidecar:
    # Parsed hash sidecar state for one model artifact.
    expected_hash: str | None
    size_bytes: int | None
    mtime_ns: int | None
    reference: str
    state: str


_LOG_PREFIX = ""
_HASH_SIDECAR_VERSION = 2
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_FULL_HF_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
_MODELSCOPE_REPO_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$"
)
_SIDECAR_REFERENCES = {"upstream", "legacy", "transfer"}
_PROVENANCE_MANIFEST_NAME = ".sml-provenance.json"
_PROVENANCE_SCHEMA_VERSION = 1
_STANDARD_TRANSFORMERS_WEIGHT_PATTERN = re.compile(
    r"^(?:model|pytorch_model)(?:-\d+-of-\d+)?\.(?:safetensors|bin)$",
    re.IGNORECASE,
)


def _is_standard_transformers_weight(path_or_filename: object) -> bool:
    # Identify the canonical weight names consumed by Transformers.
    filename = Path(str(path_or_filename)).name
    return bool(_STANDARD_TRANSFORMERS_WEIGHT_PATTERN.fullmatch(filename))


def _is_optional_consolidated_weight(path_or_filename: object) -> bool:
    # Mistral repositories may publish a consolidated export beside the
    # canonical Transformers weights. It is an alternative packaging format,
    # not an additional shard required by from_pretrained().
    path = Path(str(path_or_filename))
    return "consolidated" in path.name.lower() and path.suffix.lower() in {
        ".safetensors",
        ".bin",
    }


def _without_optional_consolidated_weights(items: list[Any]) -> list[Any]:
    # Keep consolidated weights when they are the only available format.
    if not any(_is_standard_transformers_weight(item) for item in items):
        return items
    return [item for item in items if not _is_optional_consolidated_weight(item)]


def _validated_snapshot_file_selection(value: object) -> list[str] | None:
    # Validate an optional exact repository file selection used by backends
    # that consume only one representation from a multi-format repository.
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("Required snapshot files must be a non-empty list")

    normalized = []
    for filename in value:
        if not isinstance(filename, str):
            raise TypeError("Required snapshot filenames must be strings")
        candidate = _validate_repository_filename(filename).as_posix()
        if candidate in normalized:
            raise ValueError(f"Duplicate required snapshot file: {candidate}")
        normalized.append(candidate)
    return normalized


def _selected_huggingface_snapshot_files(
    repo_files: list[Any],
    required_files: list[str] | None = None,
) -> list[str]:
    # Normalize the exact immutable repository file set downloaded by Eclipse.
    selected_files = []
    for filename in repo_files:
        if not isinstance(filename, str):
            raise TypeError("Hugging Face repository filenames must be strings")
        if fnmatchcase(filename, "*.md") or fnmatchcase(filename, ".git*"):
            continue
        selected_files.append(_validate_repository_filename(filename).as_posix())
    selected_files = _without_optional_consolidated_weights(selected_files)
    if required_files is None:
        return selected_files

    available = set(selected_files)
    missing = [filename for filename in required_files if filename not in available]
    if missing:
        raise RuntimeError(
            "Required repository file(s) not found: " + ", ".join(missing)
        )
    return list(required_files)


def _normalize_sha256(value: object) -> str | None:
    # Return a canonical SHA-256 digest or None for non-SHA-256 metadata.
    candidate = str(value).strip().strip('"').lower()
    return candidate if _SHA256_PATTERN.fullmatch(candidate) else None


def resolve_huggingface_revision(
    repo_id: str,
    revision: str | None = None,
    token: str | None = None,
) -> str:
    # Resolve a branch/tag once and return the immutable full commit hash.
    requested_revision = revision.strip() if isinstance(revision, str) else None
    if revision is not None and not requested_revision:
        raise ValueError("Hugging Face revision must be a non-empty string")
    if requested_revision and _FULL_HF_COMMIT_PATTERN.fullmatch(requested_revision):
        return requested_revision.lower()

    from huggingface_hub import HfApi  # type: ignore

    hf_token = resolve_auth_token("huggingface", token)
    model_info = HfApi().model_info(
        repo_id,
        revision=requested_revision,
        token=hf_token,
    )
    resolved_revision = getattr(model_info, "sha", "")
    if not isinstance(resolved_revision, str) or not _FULL_HF_COMMIT_PATTERN.fullmatch(
        resolved_revision
    ):
        raise RuntimeError(
            f"Hugging Face did not return a full commit hash for {repo_id}"
        )
    return resolved_revision.lower()


def _validated_modelscope_revision(revision: str | None) -> str:
    # ModelScope revisions are Git branch/tag names or full commit hashes.
    if revision is None:
        return "master"
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("ModelScope revision must be a non-empty string")
    normalized = revision.strip()
    if (
        normalized.startswith(("/", "."))
        or normalized.endswith(("/", ".", ".lock"))
        or ".." in normalized
        or "@{" in normalized
        or "//" in normalized
        or any(char.isspace() or char in "~^:?*[\\" for char in normalized)
    ):
        raise ValueError(f"Invalid ModelScope revision: {revision!r}")
    return normalized


def resolve_modelscope_revision(
    repo_id: str,
    revision: str | None = None,
    token: str | None = None,
) -> str:
    # Resolve a public ModelScope branch/tag through its Git endpoint. Private
    # repositories must provide a full commit so credentials never enter a
    # subprocess command line or environment.
    if not isinstance(repo_id, str) or not _MODELSCOPE_REPO_PATTERN.fullmatch(repo_id):
        raise ValueError(
            "ModelScope repo_id must use the canonical 'owner/model' format"
        )
    requested_revision = _validated_modelscope_revision(revision)
    if _FULL_HF_COMMIT_PATTERN.fullmatch(requested_revision):
        return requested_revision.lower()

    git_executable = shutil.which("git")
    if git_executable is None:
        raise RuntimeError(
            "Resolving a mutable ModelScope revision requires Git; configure a "
            "full 40-character commit revision instead"
        )

    repository_url = f"https://www.modelscope.cn/{repo_id}.git"
    branch_ref = f"refs/heads/{requested_revision}"
    tag_ref = f"refs/tags/{requested_revision}"
    peeled_tag_ref = f"{tag_ref}^{{}}"
    inherited_environment = os.environ
    environment = {
        key: inherited_environment[key]
        for key in (
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTPS_PROXY",
            "https_proxy",
            "HTTP_PROXY",
            "http_proxy",
            "NO_PROXY",
            "no_proxy",
        )
        if key in inherited_environment
    }
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )
    try:
        result = subprocess.run(
            [
                git_executable,
                "ls-remote",
                repository_url,
                branch_ref,
                tag_ref,
                peeled_tag_ref,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=environment,
            cwd=os.path.abspath(os.sep),
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(
            f"Could not resolve ModelScope revision {requested_revision!r}: {e}"
        ) from e

    if result.returncode != 0:
        private_hint = (
            " For a private repository, configure its full 40-character commit "
            "revision; Eclipse does not expose ModelScope credentials to Git."
            if token
            else ""
        )
        detail = result.stderr.strip() or "Git revision lookup failed"
        raise RuntimeError(
            f"Could not resolve ModelScope revision {requested_revision!r} for "
            f"{repo_id}: {detail}.{private_hint}"
        )

    resolved_by_ref: dict[str, str] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        commit, reference = fields
        if _FULL_HF_COMMIT_PATTERN.fullmatch(commit):
            resolved_by_ref[reference] = commit.lower()

    branch_commit = resolved_by_ref.get(branch_ref)
    tag_commit = resolved_by_ref.get(peeled_tag_ref) or resolved_by_ref.get(tag_ref)
    if branch_commit and tag_commit and branch_commit != tag_commit:
        raise RuntimeError(
            f"ModelScope revision {requested_revision!r} is ambiguous: a branch "
            "and tag resolve to different commits"
        )
    resolved_revision = branch_commit or tag_commit
    if resolved_revision is None:
        raise RuntimeError(
            f"ModelScope revision {requested_revision!r} was not found for {repo_id}"
        )
    return resolved_revision


def get_modelscope_file_hash(
    repo_id: str,
    filename: str,
    token: str | None = None,
    revision: str | None = None,
) -> str:
    # Read the exact SHA-256 returned by ModelScope for one file at a pinned
    # commit. Prefer the current standalone Hub package, then the legacy SDK.
    normalized_filename = _validate_repository_filename(filename).as_posix()
    if not isinstance(revision, str) or not _FULL_HF_COMMIT_PATTERN.fullmatch(
        revision
    ):
        raise ValueError(
            "ModelScope file metadata requires a full 40-character commit revision"
        )
    resolved_token = resolve_auth_token("modelscope", token)

    try:
        from modelscope_hub import HubApi  # type: ignore

        api = HubApi(token=resolved_token)
        files = api.list_repo_files(
            repo_id,
            "model",
            revision=revision,
            recursive=True,
        )
    except ImportError:
        try:
            from modelscope.hub.api import HubApi  # type: ignore
        except ImportError:
            raise RuntimeError(
                "ModelScope support requires the 'modelscope' package. "
                "Install with: pip install modelscope"
            ) from None

        constructor_args = {}
        if resolved_token and "token" in inspect.signature(HubApi).parameters:
            constructor_args["token"] = resolved_token
        api = HubApi(**constructor_args)
        files = api.get_model_files(
            repo_id,
            revision=revision,
            recursive=True,
        )

    for file_info in files:
        if isinstance(file_info, dict):
            path = (
                file_info.get("Path")
                or file_info.get("path")
                or file_info.get("Name")
            )
            digest_value = file_info.get("Sha256") or file_info.get("sha256")
            lfs_info = file_info.get("Lfs") or file_info.get("lfs")
        else:
            path = getattr(file_info, "path", None)
            digest_value = getattr(file_info, "sha256", None)
            lfs_info = getattr(file_info, "lfs", None)
        if path != normalized_filename:
            continue
        normalized_hash = _normalize_sha256(digest_value)
        if normalized_hash is None and isinstance(lfs_info, dict):
            lfs_digest = lfs_info.get("sha256") or lfs_info.get("oid")
            if isinstance(lfs_digest, str) and lfs_digest.startswith("sha256:"):
                lfs_digest = lfs_digest.removeprefix("sha256:")
            normalized_hash = _normalize_sha256(lfs_digest)
        if normalized_hash is None:
            raise RuntimeError(
                f"ModelScope did not return a SHA-256 for {normalized_filename} "
                f"at revision {revision}"
            )
        return normalized_hash

    raise RuntimeError(
        f"ModelScope file {normalized_filename!r} was not found in {repo_id} "
        f"at revision {revision}"
    )


def _expected_sha256_for_file(entry: dict, filename: str) -> str | None:
    # Registry digests are keyed by exact repository-relative filename.
    normalized_filename = _validate_repository_filename(filename).as_posix()
    return _validated_expected_sha256_map(entry).get(normalized_filename)


def _validated_expected_sha256_map(entry: dict) -> dict[str, str]:
    # Validate every configured digest before any repository access begins.
    expected_hashes = entry.get("expected_sha256")
    if expected_hashes is None:
        return {}
    if not isinstance(expected_hashes, dict):
        raise TypeError("expected_sha256 must be an object keyed by filename")

    normalized_hashes = {}
    for filename, expected_hash in expected_hashes.items():
        if not isinstance(filename, str):
            raise TypeError("expected_sha256 filenames must be strings")
        relative_path = _validate_repository_filename(filename)
        normalized_filename = relative_path.as_posix()
        if normalized_filename != filename.replace("\\", "/"):
            raise ValueError(
                f"expected_sha256 filename must be repository-relative: {filename!r}"
            )
        normalized_hash = _normalize_sha256(expected_hash)
        if normalized_hash is None:
            raise ValueError(f"Invalid expected SHA-256 for {filename}")
        normalized_hashes[normalized_filename] = normalized_hash
    return normalized_hashes


def _record_artifact_provenance(
    manifest_root: Path,
    artifact_path: Path,
    *,
    source: str,
    repository: str,
    requested_revision: str | None,
    resolved_revision: str | None,
    filename: str,
    sha256: str,
    digest_source: str,
    artifact_stat: os.stat_result,
) -> None:
    # Persist one verified artifact without rewriting unrelated manifest entries.
    normalized_hash = _normalize_sha256(sha256)
    if normalized_hash is None:
        raise ValueError(f"Invalid provenance SHA-256 for {filename}")

    local_path = artifact_path.relative_to(manifest_root).as_posix()
    downloaded_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record = {
        "source": source,
        "repository": repository,
        "requested_revision": requested_revision,
        "resolved_revision": resolved_revision,
        "filename": filename,
        "local_path": local_path,
        "sha256": normalized_hash,
        "digest_source": digest_source,
        "size_bytes": artifact_stat.st_size,
        "downloaded_at": downloaded_at,
    }

    _record_provenance_records(manifest_root, {local_path: record})


def _artifact_provenance_matches(
    manifest_root: Path,
    artifact_path: Path,
    *,
    source: str,
    repository: str,
    requested_revision: str | None,
    resolved_revision: str,
    filename: str,
    sha256: str,
) -> bool:
    # Check a recorded artifact without hashing or rewriting its manifest.
    manifest_path = manifest_root / _PROVENANCE_MANIFEST_NAME
    if not manifest_path.is_file():
        return False
    try:
        manifest = read_json_object(manifest_path)
        local_path = artifact_path.relative_to(manifest_root).as_posix()
        record = manifest.get("artifacts", {}).get(local_path)
    except (JsonStoreError, OSError, TypeError, ValueError) as e:
        log.warning(_LOG_PREFIX, f"Could not read model provenance: {e}")
        return False
    if manifest.get("schema_version") != _PROVENANCE_SCHEMA_VERSION or not isinstance(
        record, dict
    ):
        return False

    artifact_stat = artifact_path.stat()
    return bool(
        record.get("source") == source
        and record.get("repository") == repository
        and record.get("requested_revision") == requested_revision
        and record.get("resolved_revision") == resolved_revision
        and record.get("filename") == filename
        and record.get("local_path") == local_path
        and _normalize_sha256(record.get("sha256")) == sha256
        and record.get("size_bytes") == artifact_stat.st_size
    )


def _record_provenance_records(
    manifest_root: Path,
    records: dict[str, dict[str, Any]],
) -> None:
    # Commit a group of verified artifacts in one locked manifest transaction.
    if not records:
        return

    manifest_path = manifest_root / _PROVENANCE_MANIFEST_NAME

    def update_manifest(manifest: dict[str, Any]) -> None:
        schema_version = manifest.get("schema_version")
        if schema_version not in (None, _PROVENANCE_SCHEMA_VERSION):
            raise ValueError(
                f"Unsupported provenance manifest schema: {schema_version}"
            )
        artifacts = manifest.setdefault("artifacts", {})
        if not isinstance(artifacts, dict):
            raise TypeError("Provenance manifest artifacts must be an object")
        manifest["schema_version"] = _PROVENANCE_SCHEMA_VERSION
        artifacts.update(records)

    update_json_object(manifest_path, update_manifest, default={})


def _record_snapshot_inventory(
    manifest_root: Path,
    repository: str,
    resolved_revision: str,
    filenames: list[str],
) -> None:
    # Record the complete selected file set for one immutable HF snapshot.
    if not _FULL_HF_COMMIT_PATTERN.fullmatch(resolved_revision):
        return
    normalized_files = sorted(
        {_validate_repository_filename(name).as_posix() for name in filenames}
    )
    if not normalized_files:
        raise ValueError("Snapshot inventory must contain at least one file")

    manifest_path = manifest_root / _PROVENANCE_MANIFEST_NAME

    def update_manifest(manifest: dict[str, Any]) -> None:
        schema_version = manifest.get("schema_version")
        if schema_version not in (None, _PROVENANCE_SCHEMA_VERSION):
            raise ValueError(
                f"Unsupported provenance manifest schema: {schema_version}"
            )
        manifest["schema_version"] = _PROVENANCE_SCHEMA_VERSION
        manifest.setdefault("artifacts", {})
        if not isinstance(manifest["artifacts"], dict):
            raise TypeError("Provenance manifest artifacts must be an object")
        manifest["snapshot"] = {
            "source": "huggingface",
            "repository": repository,
            "resolved_revision": resolved_revision.lower(),
            "files": normalized_files,
        }

    update_json_object(manifest_path, update_manifest, default={})


def _matching_snapshot_inventory_missing_files(
    model_path: Path,
    repository: str,
    resolved_revision: str | None,
    required_files: list[str] | None = None,
) -> list[str] | None:
    # Return local missing files for a matching immutable inventory. None means
    # that no authoritative inventory is available and remote listing remains
    # necessary.
    if (
        not model_path.is_dir()
        or not isinstance(resolved_revision, str)
        or not _FULL_HF_COMMIT_PATTERN.fullmatch(resolved_revision)
    ):
        return None
    manifest_path = model_path / _PROVENANCE_MANIFEST_NAME
    if not manifest_path.is_file():
        return None
    try:
        manifest = read_json_object(manifest_path)
        snapshot = manifest.get("snapshot")
    except (JsonStoreError, OSError) as e:
        log.warning(_LOG_PREFIX, f"Could not read model provenance: {e}")
        return None
    if (
        manifest.get("schema_version") != _PROVENANCE_SCHEMA_VERSION
        or not isinstance(snapshot, dict)
        or snapshot.get("source") != "huggingface"
        or snapshot.get("repository") != repository
        or snapshot.get("resolved_revision") != resolved_revision.lower()
    ):
        return None
    filenames = snapshot.get("files")
    if not isinstance(filenames, list) or not filenames:
        return None

    normalized_files = []
    try:
        for filename in filenames:
            if not isinstance(filename, str):
                return None
            normalized_files.append(
                _validate_repository_filename(filename).as_posix()
            )
    except ValueError:
        return None
    if len(normalized_files) != len(set(normalized_files)):
        return None
    if required_files is not None:
        recorded_files = set(normalized_files)
        if any(filename not in recorded_files for filename in required_files):
            return None
        normalized_files = list(required_files)
    return [
        filename
        for filename in normalized_files
        if not (model_path / filename).is_file()
    ]


def _hash_sidecar_path(file_path: Path) -> Path:
    return file_path.parent / f"{file_path.name}.sha256"


def _read_hash_sidecar(file_path: Path) -> HashSidecar:
    # Parse legacy one-token sidecars and the versioned metadata format.
    sidecar_path = _hash_sidecar_path(file_path)
    if not sidecar_path.exists():
        return HashSidecar(None, None, None, "", "missing")

    try:
        lines = [line.strip() for line in sidecar_path.read_text().splitlines()]
    except OSError:
        return HashSidecar(None, None, None, "", "malformed")

    if not lines or not lines[0]:
        return HashSidecar(None, None, None, "", "malformed")

    expected_hash = _normalize_sha256(lines[0].split()[0])
    if expected_hash is None:
        return HashSidecar(None, None, None, "", "malformed")

    if len(lines) == 1:
        return HashSidecar(expected_hash, None, None, "legacy", "legacy")

    metadata = {}
    for line in lines[1:]:
        if not line or "=" not in line:
            return HashSidecar(expected_hash, None, None, "legacy", "malformed")
        key, value = line.split("=", 1)
        if not key or key in metadata:
            return HashSidecar(expected_hash, None, None, "legacy", "malformed")
        metadata[key] = value

    try:
        version = int(metadata["sml_sidecar_version"])
        metadata_hash = _normalize_sha256(metadata["expected_sha256"])
        size_bytes = int(metadata["size_bytes"])
        mtime_ns = int(metadata["mtime_ns"])
        reference = metadata["reference"]
    except (KeyError, TypeError, ValueError):
        return HashSidecar(expected_hash, None, None, "legacy", "malformed")

    if (
        version != _HASH_SIDECAR_VERSION
        or metadata_hash != expected_hash
        or size_bytes < 0
        or mtime_ns < 0
        or reference not in _SIDECAR_REFERENCES
    ):
        return HashSidecar(expected_hash, None, None, "legacy", "malformed")

    return HashSidecar(expected_hash, size_bytes, mtime_ns, reference, "current")


def _write_hash_sidecar(
    file_path: Path,
    expected_hash: str,
    reference: str = "upstream",
    verified_stat: os.stat_result | None = None,
) -> None:
    # Atomically record a verified artifact's digest and current stat metadata.
    normalized_hash = _normalize_sha256(expected_hash)
    if normalized_hash is None:
        raise ValueError("Expected hash is not a valid SHA-256 digest")
    if reference not in _SIDECAR_REFERENCES:
        raise ValueError(f"Unsupported hash reference type: {reference}")

    file_stat = verified_stat if verified_stat is not None else file_path.stat()
    sidecar_path = _hash_sidecar_path(file_path)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        f"{normalized_hash}\n"
        f"sml_sidecar_version={_HASH_SIDECAR_VERSION}\n"
        f"expected_sha256={normalized_hash}\n"
        f"size_bytes={file_stat.st_size}\n"
        f"mtime_ns={file_stat.st_mtime_ns}\n"
        f"reference={reference}\n"
    )

    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{sidecar_path.name}.", suffix=".tmp", dir=sidecar_path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, sidecar_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _calculate_stable_file_hash(
    file_path: Path, show_progress: bool = True
) -> tuple[str, os.stat_result]:
    # Reject a verification result if the file changed while it was being read.
    before = file_path.stat()
    actual_hash = calculate_file_hash(file_path, show_progress=show_progress)
    after = file_path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"{file_path.name} changed during hash verification")
    return actual_hash, after


def download_with_progress(url: str, path: str, name: str) -> None:
    # Generic HTTP fetching is intentionally disabled. Retain this compatibility
    # entry point only so older callers fail closed instead of regaining a direct
    # redirect-capable URL path. Supported artifacts use repository-native clients.
    del url, path, name
    raise RuntimeError(
        "Generic direct URL downloads are disabled; use a supported repository source"
    )


def is_same_drive(path1: Path, path2: Path) -> bool:
    # Check if two paths are on the same drive/mount point.
    #
    # On Windows: compares drive letters (e.g., C: vs D:)
    # On Unix: compares mount points using os.stat().st_dev
    #
    # Args:
    #     path1: First path
    #     path2: Second path
    #
    # Returns:
    #     True if both paths are on the same drive/filesystem
    import os
    import platform

    try:
        if platform.system() == "Windows":
            # On Windows, compare drive letters
            drive1 = os.path.splitdrive(str(path1.resolve()))[0].upper()
            drive2 = os.path.splitdrive(str(path2.resolve()))[0].upper()
            return drive1 == drive2
        else:
            # On Unix, compare device IDs (st_dev)
            # Need to use existing parent dirs for paths that don't exist yet
            check_path1 = path1
            while not check_path1.exists() and check_path1.parent != check_path1:
                check_path1 = check_path1.parent

            check_path2 = path2
            while not check_path2.exists() and check_path2.parent != check_path2:
                check_path2 = check_path2.parent

            return os.stat(check_path1).st_dev == os.stat(check_path2).st_dev
    except Exception:
        # If we can't determine, assume different drives (safer)
        return False


def download_file_via_temp(
    url: str,
    final_path: Path,
    filename: str,
    expected_hash: Optional[str] = None,
    max_verify_attempts: int = 3,
) -> bool:
    # Download a file to temp folder first, verify hash, then move to final location.
    #
    # This approach is more reliable for drives with issues because:
    # - Temp folder (usually on SSD) may be faster and more reliable
    # - If verification fails, we don't leave corrupted files in the model folder
    # - The move operation is atomic on the same filesystem
    #
    # If temp folder is on the same drive as target, downloads directly to target
    # (no benefit from temp folder in that case).
    #
    # Args:
    #     url: URL to download from
    #     final_path: Final destination path for the file
    #     filename: Display name for progress bar
    #     expected_hash: Optional SHA256 hash to verify against
    #     max_verify_attempts: Max attempts to download and verify (default 3)
    #
    # Returns:
    #     True if download and verification succeeded, False otherwise
    import tempfile
    import shutil

    # Check if temp folder is on the same drive as target
    temp_check_dir = Path(tempfile.gettempdir())
    use_temp_folder = not is_same_drive(temp_check_dir, final_path)

    if not use_temp_folder:
        log.debug(
            _LOG_PREFIX, f"Temp folder is on same drive as target, downloading directly"
        )

    for attempt in range(max_verify_attempts):
        temp_dir = None
        try:
            if use_temp_folder:
                # Create temp directory for download
                temp_dir = tempfile.mkdtemp(prefix="sml_download_")
                download_path = Path(temp_dir) / filename
            else:
                # Download directly to final location
                final_path.parent.mkdir(parents=True, exist_ok=True)
                download_path = final_path

            if attempt > 0:
                log.msg(
                    _LOG_PREFIX,
                    f"Retry attempt {attempt + 1}/{max_verify_attempts} for {filename}...",
                )

            # Download to target location (temp or final)
            download_with_progress(url, str(download_path), filename)

            if not download_path.exists():
                log.error(_LOG_PREFIX, f"Download failed: file not created")
                continue

            # Verify hash if provided
            verified_hash = None
            if expected_hash:
                location_desc = (
                    "temp location" if use_temp_folder else "download location"
                )
                log.msg(_LOG_PREFIX, f"Verifying {filename} in {location_desc}...")
                actual_hash = calculate_file_hash(download_path, show_progress=True)

                if actual_hash != expected_hash:
                    log.error(
                        _LOG_PREFIX,
                        f"✗ Hash verification failed for {filename} (attempt {attempt + 1}/{max_verify_attempts})",
                    )
                    log.error(_LOG_PREFIX, f"  Expected: {expected_hash}")
                    log.error(_LOG_PREFIX, f"  Got:      {actual_hash}")
                    # Clean up and retry download
                    if download_path.exists():
                        download_path.unlink()
                    continue

                log.msg(_LOG_PREFIX, f"✓ Hash verified in {location_desc}")
                verified_hash = actual_hash

            # If using temp folder, copy to final location (keep temp for retry if copy fails)
            if use_temp_folder:
                # Ensure target directory exists
                final_path.parent.mkdir(parents=True, exist_ok=True)

                # Retry loop for copy operation (don't need to re-download if copy fails)
                max_copy_attempts = 3
                copy_success = False
                corrupted_file_exists = (
                    False  # Track if we have a corrupted file occupying sectors
                )

                for copy_attempt in range(max_copy_attempts):
                    try:
                        # On first attempt or if no corrupted file, copy directly to final path
                        # On retry after corruption: copy to temp name to force different sectors
                        if corrupted_file_exists:
                            # Copy to temporary name (corrupted file still occupies original sectors)
                            temp_final_path = (
                                final_path.parent / f"{final_path.name}.new"
                            )
                            target_path = temp_final_path
                            log.msg(
                                _LOG_PREFIX,
                                f"Copying to alternate location to avoid bad sectors...",
                            )
                        else:
                            # Delete existing file if present (first attempt)
                            if final_path.exists():
                                final_path.unlink()
                            target_path = final_path

                        # Copy from temp to target location
                        shutil.copy2(str(download_path), str(target_path))

                        if copy_attempt > 0:
                            log.msg(
                                _LOG_PREFIX,
                                f"✓ Copied {filename} (attempt {copy_attempt + 1})",
                            )
                        else:
                            log.msg(
                                _LOG_PREFIX, f"✓ Copied {filename} to final location"
                            )

                        # Verify hash after copy to detect target drive issues
                        if expected_hash:
                            log.msg(_LOG_PREFIX, f"Verifying {filename} after copy...")
                            post_copy_hash = calculate_file_hash(
                                target_path, show_progress=False
                            )

                            if post_copy_hash != expected_hash:
                                log.error(
                                    _LOG_PREFIX,
                                    f"⚠ DRIVE ISSUE DETECTED: File corrupted after copy to target drive!",
                                )
                                log.error(
                                    _LOG_PREFIX,
                                    f"  File was verified correct in temp folder but corrupted after copying.",
                                )
                                log.error(
                                    _LOG_PREFIX,
                                    f"  This indicates your target drive may have bad sectors or write errors.",
                                )
                                log.error(_LOG_PREFIX, f"  Expected: {expected_hash}")
                                log.error(_LOG_PREFIX, f"  Got:      {post_copy_hash}")

                                if not corrupted_file_exists:
                                    # First corruption - keep the file to occupy bad sectors
                                    corrupted_file_exists = True
                                    log.msg(
                                        _LOG_PREFIX,
                                        f"Keeping corrupted file to force write to different sectors on retry...",
                                    )
                                else:
                                    # Retry with temp name also failed - delete it
                                    if target_path.exists():
                                        target_path.unlink()

                                if copy_attempt < max_copy_attempts - 1:
                                    log.msg(
                                        _LOG_PREFIX,
                                        f"Retrying copy (attempt {copy_attempt + 2}/{max_copy_attempts})...",
                                    )
                                continue  # Retry copy

                            # Use the post-copy hash (verified to match expected)
                            verified_hash = post_copy_hash

                        # If we used a temp name, rename to final name
                        if corrupted_file_exists:
                            # Delete the corrupted file and rename the good one
                            if final_path.exists():
                                final_path.unlink()
                            target_path.rename(final_path)
                            log.msg(
                                _LOG_PREFIX,
                                f"✓ Renamed to final filename after successful verification",
                            )

                        copy_success = True
                        break

                    except Exception as e:
                        log.error(
                            _LOG_PREFIX,
                            f"Copy error (attempt {copy_attempt + 1}/{max_copy_attempts}): {e}",
                        )
                        # Clean up temp file if it exists
                        if corrupted_file_exists:
                            temp_final_path = (
                                final_path.parent / f"{final_path.name}.new"
                            )
                            if temp_final_path.exists():
                                try:
                                    temp_final_path.unlink()
                                except Exception:
                                    pass

                # Clean up after copy attempts
                if copy_success:
                    # Clean up temp download file
                    try:
                        if download_path.exists():
                            download_path.unlink()
                    except Exception:
                        pass  # Not critical if temp cleanup fails
                else:
                    # All copy attempts failed - clean up and retry download
                    log.error(
                        _LOG_PREFIX,
                        f"All {max_copy_attempts} copy attempts failed for {filename}",
                    )
                    # Clean up any leftover files
                    if final_path.exists():
                        try:
                            final_path.unlink()
                        except Exception:
                            pass
                    temp_final_path = final_path.parent / f"{final_path.name}.new"
                    if temp_final_path.exists():
                        try:
                            temp_final_path.unlink()
                        except Exception:
                            pass
                    if download_path.exists():
                        download_path.unlink()
                    continue  # Retry download

            # Save hash file only if we have a verified hash
            if verified_hash:
                try:
                    _write_hash_sidecar(final_path, verified_hash)
                    log.debug(
                        _LOG_PREFIX,
                        f"Saved hash metadata: {_hash_sidecar_path(final_path).name}",
                    )
                except Exception as e:
                    log.warning(_LOG_PREFIX, f"Could not cache hash: {e}")

            return True

        except Exception as e:
            log.error(
                _LOG_PREFIX,
                f"Download error (attempt {attempt + 1}/{max_verify_attempts}): {e}",
            )
            # Clean up failed download if downloading directly
            if not use_temp_folder and final_path.exists():
                try:
                    final_path.unlink()
                except Exception:
                    pass
        finally:
            # Clean up temp directory
            if temp_dir and Path(temp_dir).exists():
                try:
                    shutil.rmtree(temp_dir)
                except Exception:
                    pass

    log.error(
        _LOG_PREFIX,
        f"✗ Failed to download {filename} after {max_verify_attempts} attempts",
    )
    return False


def get_hf_file_hash(
    repo_id: str,
    filename: str,
    token: str | None = None,
    revision: str | None = None,
) -> str | None:
    # Get SHA256 hash for a file from HuggingFace metadata.
    #
    # Args:
    #     repo_id: HuggingFace repo_id (user/repo format)
    #     filename: Filename to get hash for
    #     token: Optional HuggingFace token
    #
    # Returns:
    #     SHA256 hash string or None if not available
    try:
        from huggingface_hub import hf_hub_url, get_hf_file_metadata  # type: ignore
        hf_token = resolve_auth_token("huggingface", token)

        url = hf_hub_url(
            repo_id=repo_id,
            filename=filename,
            repo_type="model",
            revision=revision,
        )
        metadata = get_hf_file_metadata(url=url, token=hf_token)

        if hasattr(metadata, "etag") and metadata.etag:
            return _normalize_sha256(metadata.etag)
    except Exception as e:
        log.debug(_LOG_PREFIX, f"Could not get HF hash for {filename}: {e}")

    return None


def get_llm_model_list() -> List[str]:
    # Scan models/LLM folder and models/florence2 folder and return list of available models (cached).
    # First collects all model files, then filters to show:
    # - For shard files: show folder/ instead of individual files
    # - For single files: show full relative path to the file
    import time

    global _model_list_cache, _model_list_cache_time

    current_time = time.time()

    # Check if cache is valid
    if (
        current_time - _model_list_cache_time < _MODEL_LIST_CACHE_TTL
        and _model_list_cache
    ):
        return _model_list_cache.copy()

    try:
        import folder_paths  # type: ignore

        llm_dir = get_llm_models_path()
        florence2_dir = Path(folder_paths.models_dir) / "florence2"

        # Build list of folders to scan with their prefixes
        folders_to_scan = []
        if llm_dir.exists():
            folders_to_scan.append((llm_dir, ""))
        if florence2_dir.exists():
            folders_to_scan.append((florence2_dir, "florence2/"))

        if not folders_to_scan:
            return ["(No models/LLM folder found)"]

        model_extensions = {".safetensors", ".gguf", ".bin", ".pt"}
        all_model_files = []  # List of display paths
        folder_to_base = (
            {}
        )  # Map folder display path to actual base Path for config.json check

        # Step 1: Recursively scan and collect all model files
        def scan_files(
            base_path: Path,
            relative_path: str = "",
            display_prefix: str = "",
            base_for_config: Optional[Path] = None,
        ):
            # Recursively collect all model files
            try:
                for item in base_path.iterdir():
                    if item.is_file() and item.suffix in model_extensions:
                        # Build full relative path
                        if relative_path:
                            file_path = f"{relative_path}/{item.name}"
                        else:
                            file_path = item.name
                        display_path = f"{display_prefix}{file_path}"
                        all_model_files.append(display_path)
                        # Track folder -> base mapping for config check
                        if relative_path:
                            folder_display = f"{display_prefix}{relative_path}"
                            folder_to_base[folder_display] = (
                                base_for_config / relative_path
                                if base_for_config
                                else base_path
                            )
                    elif item.is_dir():
                        # Recurse into subdirectories (limit depth to avoid infinite loops)
                        item_rel_path = (
                            f"{relative_path}/{item.name}"
                            if relative_path
                            else item.name
                        )
                        if relative_path.count("/") < 4:  # Max 4 levels deep
                            scan_files(
                                item, item_rel_path, display_prefix, base_for_config
                            )
            except PermissionError:
                pass  # Skip directories we can't access

        for scan_path, prefix in folders_to_scan:
            scan_files(scan_path, "", prefix, scan_path)

        if not all_model_files:
            return ["(No models found in models/LLM)"]

        # Step 2: Group files by their parent folder (using display path)
        # Step 2: Group files by their parent folder (using display path)
        folder_files = defaultdict(list)

        for file_path in all_model_files:
            if "/" in file_path:
                folder = file_path.rsplit("/", 1)[0]
                filename = file_path.rsplit("/", 1)[1]
            else:
                folder = ""  # Root level
                filename = file_path

            folder_files[folder].append(filename)

        # Step 3: Check for config.json to identify model repositories
        # Use folder_to_base mapping to find correct path for config.json
        folders_with_config = set()
        for folder in folder_files.keys():
            if folder:  # Skip root level
                # Use the tracked base path if available, otherwise try llm_dir
                if folder in folder_to_base:
                    actual_folder = folder_to_base[folder]
                    config_path = actual_folder / "config.json"
                else:
                    # Fallback: try llm_dir (for models without prefix)
                    config_path = llm_dir / folder / "config.json"
                if config_path.exists():
                    folders_with_config.add(folder)

        # Step 4: Filter to create final model list
        models = []

        for folder, files in folder_files.items():
            # Separate mmproj files from model files
            model_files = [f for f in files if "mmproj" not in f.lower()]

            # Separate GGUF files from other model files
            gguf_files = [f for f in model_files if f.lower().endswith(".gguf")]
            non_gguf_files = [f for f in model_files if not f.lower().endswith(".gguf")]

            # Check if any non-GGUF file is a shard file
            has_shards = any(
                "-of-" in f or ".shard" in f.lower() for f in non_gguf_files
            )

            # Check if folder has config.json (indicates it's a model repository)
            has_config = folder in folders_with_config

            # For non-GGUF files: show folder/ if has shards or config.json
            if non_gguf_files and (has_shards or has_config):
                # Show folder/ for sharded models or model repositories with config.json
                if folder:
                    models.append(folder + "/")
                else:
                    # Shards in root - shouldn't happen but handle it
                    for f in non_gguf_files:
                        models.append(f)
            elif non_gguf_files:
                # List individual non-GGUF files
                for f in non_gguf_files:
                    if folder:
                        models.append(f"{folder}/{f}")
                    else:
                        models.append(f)

            # GGUF files: ALWAYS show individual files (even if folder has config.json)
            # This allows users to select specific quantization variants
            for f in gguf_files:
                if folder:
                    models.append(f"{folder}/{f}")
                else:
                    models.append(f)

        # Cache the result before returning
        result = sorted(models)
        _model_list_cache.clear()
        _model_list_cache.extend(result)
        _model_list_cache_time = current_time
        return result

    except Exception as e:
        log.error(_LOG_PREFIX, f"Error scanning models/LLM: {e}")
        return ["(Error scanning models folder)"]


def get_mmproj_list() -> List[str]:
    # Scan models/LLM folder for mmproj files for GGUF QwenVL models.
    # Returns only individual .mmproj files and .gguf files containing 'mmproj' in the name.
    # Never shows folders, only file paths.
    # Results are cached for 30 seconds to avoid repeated filesystem scans.
    import time

    global _mmproj_list_cache, _mmproj_list_cache_time

    current_time = time.time()
    if (
        current_time - _mmproj_list_cache_time < _MODEL_LIST_CACHE_TTL
        and _mmproj_list_cache
    ):
        return _mmproj_list_cache.copy()

    try:
        llm_dir = get_llm_models_path()

        if not llm_dir.exists():
            return ["None", "(No models/LLM folder found)"]

        mmproj_files = ["None"]  # Add None option for when mmproj is not needed

        def scan_for_mmproj(base_path: Path, relative_path: str = ""):
            # Recursively scan for mmproj files
            try:
                for item in base_path.iterdir():
                    if item.is_file():
                        # Match .mmproj files or .gguf files with 'mmproj' in name
                        if item.suffix == ".mmproj" or (
                            item.suffix == ".gguf" and "mmproj" in item.name.lower()
                        ):
                            if relative_path:
                                mmproj_files.append(f"{relative_path}/{item.name}")
                            else:
                                mmproj_files.append(item.name)
                    elif item.is_dir():
                        # Recurse into subdirectories (limit depth to avoid infinite loops)
                        item_rel_path = (
                            f"{relative_path}/{item.name}"
                            if relative_path
                            else item.name
                        )
                        if relative_path.count("/") < 4:  # Max 4 levels deep
                            scan_for_mmproj(item, item_rel_path)
            except PermissionError:
                pass  # Skip directories we can't access

        # Start recursive scan from LLM root
        scan_for_mmproj(llm_dir)

        if len(mmproj_files) == 1:  # Only "None" option
            mmproj_files.append("(No mmproj files found)")

        # Cache the result before returning
        result = sorted(mmproj_files)
        _mmproj_list_cache.clear()
        _mmproj_list_cache.extend(result)
        _mmproj_list_cache_time = current_time
        return result

    except Exception as e:
        log.error(_LOG_PREFIX, f"Error scanning for mmproj files: {e}")
        return ["None", "(Error scanning mmproj files)"]


def search_model_file(filename: str, llm_base: Path) -> Path | None:
    # Search recursively for a model file in the LLM folder.
    # Used to find legacy model files when template paths are outdated.
    # Returns Path object if found, None otherwise.
    try:
        if not llm_base.exists():
            return None

        # Search recursively (limit depth implicitly by rglob)
        for path in llm_base.rglob(filename):
            if path.is_file():
                return path

        return None
    except Exception as e:
        log.warning(_LOG_PREFIX, f"Error searching for {filename}: {e}")
        return None


def calculate_model_size(target_path: Path) -> float:
    # Calculate total model size in GB from a file or directory.
    # Handles sharded models, single files, and directories with multiple model files.
    # Returns size in GB, or 0.0 if calculation fails.
    try:
        total_size_gb = 0.0

        if target_path.is_file():
            # Single file (GGUF, safetensors, etc.)
            total_size_gb = target_path.stat().st_size / (1024**3)
        elif target_path.is_dir():
            # Model folder - check for sharded models first, then single files
            # Priority: .safetensors (preferred) > .bin > .pt > .gguf
            all_files = list(target_path.rglob("*"))
            model_files = [f for f in all_files if f.is_file()]

            # Check for shard files (e.g., model-00001-of-00005.safetensors)
            safetensors_files = _without_optional_consolidated_weights(
                [f for f in model_files if f.suffix == ".safetensors"]
            )
            bin_files = _without_optional_consolidated_weights(
                [f for f in model_files if f.suffix == ".bin"]
            )
            pt_files = [f for f in model_files if f.suffix == ".pt"]
            gguf_files = [f for f in model_files if f.suffix == ".gguf"]

            # Check if we have shards (files with -of- pattern)
            has_shards = lambda files: any("-of-" in f.name for f in files)

            # Priority: safetensors shards > single safetensors > bin shards > single bin > pt > gguf
            if has_shards(safetensors_files):
                # Use safetensors shards
                for file in safetensors_files:
                    if "-of-" in file.name:
                        total_size_gb += file.stat().st_size / (1024**3)
            elif safetensors_files:
                # Single safetensors file (no shards)
                for file in safetensors_files:
                    total_size_gb += file.stat().st_size / (1024**3)
            elif has_shards(bin_files):
                # Use bin shards
                for file in bin_files:
                    if "-of-" in file.name:
                        total_size_gb += file.stat().st_size / (1024**3)
            elif bin_files:
                # Single bin file
                for file in bin_files:
                    total_size_gb += file.stat().st_size / (1024**3)
            elif pt_files:
                # PT files
                for file in pt_files:
                    total_size_gb += file.stat().st_size / (1024**3)
            elif gguf_files:
                # GGUF files
                for file in gguf_files:
                    total_size_gb += file.stat().st_size / (1024**3)

        return total_size_gb

    except Exception as e:
        log.warning(_LOG_PREFIX, f"Error calculating model size: {e}")
        return 0.0


def calculate_file_hash(file_path: Path, show_progress: bool = True) -> str:
    # Calculate SHA256 hash of a file with optional progress display.
    #
    # Args:
    #     file_path: Path to the file to hash
    #     show_progress: Whether to display progress for large files (>100MB)
    #
    # Returns:
    #     Hexadecimal SHA256 hash string
    import sys

    sha256_hash = hashlib.sha256()
    file_size = file_path.stat().st_size
    bytes_processed = 0
    last_progress = -1

    # Show initial message with file size for large files
    size_mb = file_size / (1024 * 1024)
    if show_progress and file_size > 100 * 1024 * 1024:
        log.msg(
            _LOG_PREFIX, f"Calculating hash for {file_path.name} ({size_mb:.1f} MB)..."
        )
    elif show_progress:
        log.msg(_LOG_PREFIX, f"Calculating hash for {file_path.name}...")

    with open(file_path, "rb") as f:
        while chunk := f.read(8192 * 1024):  # 8MB chunks for speed
            sha256_hash.update(chunk)
            bytes_processed += len(chunk)
            # Show progress for large files (> 100MB)
            if show_progress and file_size > 100 * 1024 * 1024:
                progress = int((bytes_processed / file_size) * 100)
                # Update every 1% to keep progress smooth
                if progress != last_progress:
                    # Use carriage return to overwrite the same line
                    sys.stdout.write(
                        f"\rSML: [SmartLM]   Hashing: {progress}% ({bytes_processed / (1024*1024):.0f}/{size_mb:.0f} MB)"
                    )
                    sys.stdout.flush()
                    last_progress = progress

    # Print newline after progress is complete to preserve the final line
    if show_progress and file_size > 100 * 1024 * 1024:
        print()  # Move to next line

    return sha256_hash.hexdigest()


def get_or_create_local_file_hash(
    file_path: Path,
    *,
    show_progress: bool = True,
) -> str:
    # Reuse a stat-bound sidecar when possible. A legacy digest is migrated
    # under the same trusted-local-file policy used by integrity verification.
    artifact_path = Path(file_path)
    artifact_stat = artifact_path.stat()
    sidecar = _read_hash_sidecar(artifact_path)
    if sidecar.expected_hash is not None:
        if sidecar.state == "current":
            if (
                sidecar.size_bytes == artifact_stat.st_size
                and sidecar.mtime_ns == artifact_stat.st_mtime_ns
            ):
                return sidecar.expected_hash
        elif sidecar.state == "legacy":
            _write_hash_sidecar(
                artifact_path,
                sidecar.expected_hash,
                reference="legacy",
                verified_stat=artifact_stat,
            )
            return sidecar.expected_hash

    digest, verified_stat = _calculate_stable_file_hash(
        artifact_path,
        show_progress=show_progress,
    )
    _write_hash_sidecar(
        artifact_path,
        digest,
        reference="transfer",
        verified_stat=verified_stat,
    )
    return digest


def _critical_model_files(model_path: Path) -> list[Path]:
    # Return the load-bearing artifacts covered by integrity/provenance checks.
    if model_path.is_dir():
        wd14_onnx = model_path / "model.onnx"
        wd14_tags = model_path / "selected_tags.csv"
        if wd14_onnx.is_file() and wd14_tags.is_file():
            # WD14 repositories publish several equivalent framework formats.
            # The runtime consumes ONNX plus this exact tag dictionary, so do
            # not let an unused Safetensors export hide either load-bearing file.
            return [wd14_onnx, wd14_tags]
        safetensors = _without_optional_consolidated_weights(
            sorted(model_path.rglob("*.safetensors"))
        )
        bin_files = sorted(model_path.rglob("pytorch_model*.bin"))
        onnx_files = sorted(model_path.rglob("*.onnx"))
        return safetensors if safetensors else bin_files if bin_files else onnx_files
    if model_path.suffix in [".gguf", ".safetensors", ".bin", ".onnx", ".pt"]:
        return [model_path]
    return []


def _record_snapshot_provenance(
    model_path: Path,
    repository: str,
    requested_revision: str | None,
    resolved_revision: str,
    expected_sha256: dict[str, str],
) -> None:
    # Record all critical snapshot artifacts after final-path verification.
    records = {}
    downloaded_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for artifact_path in _critical_model_files(model_path):
        filename = artifact_path.relative_to(model_path).as_posix()
        sidecar = _read_hash_sidecar(artifact_path)
        if sidecar.state != "current" or sidecar.expected_hash is None:
            transfer_hash, verified_stat = _calculate_stable_file_hash(
                artifact_path,
                show_progress=True,
            )
            _write_hash_sidecar(
                artifact_path,
                transfer_hash,
                reference="transfer",
                verified_stat=verified_stat,
            )
            sidecar = _read_hash_sidecar(artifact_path)

        artifact_stat = artifact_path.stat()
        if (
            artifact_stat.st_size != sidecar.size_bytes
            or artifact_stat.st_mtime_ns != sidecar.mtime_ns
        ):
            raise RuntimeError(
                f"Verified artifact changed before provenance recording: {filename}"
            )

        digest_source = (
            "registry"
            if filename in expected_sha256
            else sidecar.reference
            if sidecar.reference in {"upstream", "transfer", "legacy"}
            else "transfer"
        )
        records[filename] = {
            "source": "huggingface",
            "repository": repository,
            "requested_revision": requested_revision,
            "resolved_revision": resolved_revision,
            "filename": filename,
            "local_path": filename,
            "sha256": sidecar.expected_hash,
            "digest_source": digest_source,
            "size_bytes": artifact_stat.st_size,
            "downloaded_at": downloaded_at,
        }

    _record_provenance_records(model_path, records)


def _matching_snapshot_provenance_revision(
    model_path: Path,
    repository: str,
    requested_revision: str | None,
    expected_sha256: dict[str, str],
) -> str | None:
    # Return the recorded immutable commit only when every critical artifact,
    # sidecar, stat tuple, registry digest, and repository identity still match.
    if not model_path.is_dir() or requested_revision is None:
        return None

    manifest_path = model_path / _PROVENANCE_MANIFEST_NAME
    if not manifest_path.is_file():
        return None
    try:
        manifest = read_json_object(manifest_path)
    except (JsonStoreError, OSError) as e:
        log.warning(_LOG_PREFIX, f"Could not read model provenance: {e}")
        return None

    if manifest.get("schema_version") != _PROVENANCE_SCHEMA_VERSION:
        return None
    records = manifest.get("artifacts")
    if not isinstance(records, dict):
        return None

    resolved_revision = None
    critical_files = _critical_model_files(model_path)
    if not critical_files:
        return None

    for artifact_path in critical_files:
        filename = artifact_path.relative_to(model_path).as_posix()
        record = records.get(filename)
        if not isinstance(record, dict):
            return None
        if (
            record.get("source") != "huggingface"
            or record.get("repository") != repository
            or record.get("requested_revision") != requested_revision
            or record.get("filename") != filename
            or record.get("local_path") != filename
        ):
            return None

        record_revision = record.get("resolved_revision")
        if not isinstance(record_revision, str) or not _FULL_HF_COMMIT_PATTERN.fullmatch(
            record_revision
        ):
            return None
        if resolved_revision is None:
            resolved_revision = record_revision.lower()
        elif resolved_revision != record_revision.lower():
            return None

        record_hash = _normalize_sha256(record.get("sha256"))
        configured_hash = expected_sha256.get(filename)
        sidecar = _read_hash_sidecar(artifact_path)
        if (
            record_hash is None
            or (configured_hash is not None and record_hash != configured_hash)
            or sidecar.state != "current"
            or sidecar.expected_hash != record_hash
        ):
            return None
        artifact_stat = artifact_path.stat()
        if (
            artifact_stat.st_size != sidecar.size_bytes
            or artifact_stat.st_mtime_ns != sidecar.mtime_ns
            or artifact_stat.st_size != record.get("size_bytes")
        ):
            return None

    return resolved_revision


def verify_model_integrity(
    model_path: Path,
    repo_id: str | None = None,
    hf_filename: str | None = None,
    return_details: bool = False,
    force_verification: bool = False,
    revision: str | None = None,
    expected_sha256: str | None = None,
    expected_sha256_map: dict[str, str] | None = None,
):
    # Verify model artifacts using upstream/legacy hashes and versioned sidecars.
    # Matching size and mtime metadata is the ordinary constant-time fast path.
    #
    # Args:
    #     model_path: Path to model file or directory
    #     repo_id: HuggingFace repo_id (user/repo format or full URL)
    #     hf_filename: Optional filename to use for HuggingFace lookup (for renamed files)
    #     return_details: If True, return VerificationResult with details; otherwise return bool
    #     force_verification: Rehash even when sidecar metadata matches
    #     revision: Immutable Hugging Face revision used for metadata lookup
    #     expected_sha256: Optional authoritative digest already resolved by caller
    #     expected_sha256_map: Optional exact repository filename to digest mapping
    #
    # Returns:
    #     If return_details=False: True if verification passes, False if corruption detected
    #     If return_details=True: VerificationResult with success status and list of corrupted files
    corrupted_files = []
    normalized_expected_hashes = {}
    if expected_sha256_map is not None:
        normalized_expected_hashes = _validated_expected_sha256_map(
            {"expected_sha256": expected_sha256_map}
        )

    try:
        # Look for model.safetensors, pytorch_model.bin, or model.onnx
        critical_files = _critical_model_files(model_path)

        if not critical_files:
            log.warning(_LOG_PREFIX, f"No model files found to verify at {model_path}")
            if return_details:
                return VerificationResult(
                    success=True, corrupted_files=[], verified_count=0, skipped_count=0
                )
            return True  # Skip verification

        verified_count = 0
        failed_count = 0
        calculated_count = 0

        for file_path in critical_files:
            if hf_filename:
                lookup_filename = hf_filename
            elif model_path.is_dir():
                lookup_filename = file_path.relative_to(model_path).as_posix()
            else:
                lookup_filename = file_path.name
            sidecar = _read_hash_sidecar(file_path)
            configured_hash = normalized_expected_hashes.get(lookup_filename)
            provided_hash = configured_hash or (
                _normalize_sha256(expected_sha256) if expected_sha256 else None
            )
            if expected_sha256 and provided_hash is None:
                raise ValueError("Expected SHA-256 is not a valid digest")
            expected_hash = provided_hash or sidecar.expected_hash
            reference = "upstream" if provided_hash else sidecar.reference or "upstream"

            sidecar_matches_reference = (
                provided_hash is None or sidecar.expected_hash == provided_hash
            )

            if (
                sidecar.state == "current"
                and not force_verification
                and sidecar_matches_reference
            ):
                file_stat = file_path.stat()
                if (
                    file_stat.st_size == sidecar.size_bytes
                    and file_stat.st_mtime_ns == sidecar.mtime_ns
                ):
                    if reference == "transfer":
                        calculated_count += 1
                        log.warning(
                            _LOG_PREFIX,
                            f"{file_path.name} matches its transfer baseline but has no upstream SHA-256 reference",
                        )
                    else:
                        verified_count += 1
                    continue

            if (
                sidecar.state == "legacy"
                and not force_verification
                and sidecar_matches_reference
            ):
                try:
                    _write_hash_sidecar(
                        file_path, expected_hash, reference="legacy"
                    )
                    log.debug(
                        _LOG_PREFIX,
                        f"Upgraded legacy hash metadata for {file_path.name}",
                    )
                except (OSError, TypeError, ValueError) as e:
                    log.warning(
                        _LOG_PREFIX,
                        f"Could not upgrade legacy hash metadata for {file_path.name}: {e}",
                    )
                verified_count += 1
                continue

            if sidecar.state == "malformed":
                log.warning(
                    _LOG_PREFIX,
                    f"Malformed hash metadata for {file_path.name}; performing full verification",
                )

            # If no cached hash, try to get it from HuggingFace
            if not expected_hash and repo_id:
                try:
                    from huggingface_hub import hf_hub_url, get_hf_file_metadata  # type: ignore

                    log.msg(
                        _LOG_PREFIX,
                        f"Fetching hash from HuggingFace for {lookup_filename}...",
                    )

                    # Use the shared credential precedence for authenticated metadata.
                    hf_token = resolve_auth_token("huggingface")

                    # Construct URL and get metadata
                    url = hf_hub_url(
                        repo_id=repo_id,
                        filename=lookup_filename,
                        repo_type="model",
                        revision=revision,
                    )
                    metadata = get_hf_file_metadata(url=url, token=hf_token)

                    # ETag is the SHA256 hash for git-lfs files (per HuggingFace docs)
                    if hasattr(metadata, "etag") and metadata.etag:
                        expected_hash = _normalize_sha256(metadata.etag)
                    if expected_hash:
                        log.msg(_LOG_PREFIX, f"Retrieved hash from HuggingFace")
                    else:
                        log.warning(
                            _LOG_PREFIX,
                            f"No SHA-256 hash available in HuggingFace metadata for {lookup_filename}",
                        )
                except Exception as e:
                    log.warning(
                        _LOG_PREFIX,
                        f"Could not retrieve hash from HuggingFace ({repo_id}/{lookup_filename}): {e}",
                    )

            # If we still don't have a reference hash, skip verification
            if not expected_hash:
                log.warning(
                    _LOG_PREFIX,
                    f"No reference hash available for {file_path.name}, skipping verification",
                )
                calculated_count += 1
                continue

            # Calculate actual hash and reject files modified during verification.
            actual_hash, verified_stat = _calculate_stable_file_hash(
                file_path, show_progress=True
            )

            # Verify against HuggingFace hash
            if actual_hash == expected_hash:
                if reference == "transfer":
                    calculated_count += 1
                    log.warning(
                        _LOG_PREFIX,
                        f"✓ {file_path.name} matches its transfer baseline; upstream SHA-256 remains unavailable",
                    )
                else:
                    verified_count += 1
                    log.msg(_LOG_PREFIX, f"✓ {file_path.name} integrity verified")

                # Save versioned metadata for future stat-only verification.
                try:
                    _write_hash_sidecar(
                        file_path,
                        expected_hash,
                        reference=reference,
                        verified_stat=verified_stat,
                    )
                    log.msg(
                        _LOG_PREFIX,
                        f"Cached hash metadata to {_hash_sidecar_path(file_path).name}",
                    )
                except Exception as e:
                    log.warning(_LOG_PREFIX, f"Could not cache hash: {e}")
            else:
                log.error(_LOG_PREFIX, f"✗ {file_path.name} CORRUPTED! Hash mismatch.")
                log.error(_LOG_PREFIX, f"  Expected: {expected_hash}")
                log.error(_LOG_PREFIX, f"  Got:      {actual_hash}")
                failed_count += 1
                corrupted_files.append(file_path)
                # Don't save hash file on failure - user needs to redownload

        if failed_count > 0:
            log.error(
                _LOG_PREFIX,
                f"⚠ Model verification FAILED! {failed_count} corrupted file(s) detected.",
            )
            if return_details:
                return VerificationResult(
                    success=False,
                    corrupted_files=corrupted_files,
                    verified_count=verified_count,
                    skipped_count=calculated_count,
                )
            return False
        elif verified_count > 0:
            log.msg(
                _LOG_PREFIX, f"✓ Model integrity verified ({verified_count} file(s))"
            )
        elif calculated_count > 0:
            log.warning(
                _LOG_PREFIX,
                f"⚠ No reference hash available, skipping verification for {calculated_count} file(s)",
            )

        if return_details:
            return VerificationResult(
                success=True,
                corrupted_files=[],
                verified_count=verified_count,
                skipped_count=calculated_count,
            )
        return True

    except Exception as e:
        log.error(_LOG_PREFIX, f"Model verification error: {e}")
        if return_details:
            return VerificationResult(
                success=False,
                corrupted_files=[model_path] if model_path.exists() else [],
                verified_count=0,
                skipped_count=0,
            )
        return False


def _download_repository_file(
    repo_id: str,
    filename: str,
    local_dir: Path,
    source: str,
    token: str | None,
    log_prefix: str,
    force_download: bool,
    revision: str | None = None,
    log_transfer_mode: bool = True,
) -> Path:
    # Download one repository file into the selected local directory.
    from ..common import make_comfy_tqdm_class

    progress_class = make_comfy_tqdm_class(filename, log_prefix=log_prefix)
    if source == "modelscope":
        try:
            from modelscope.hub.file_download import model_file_download  # type: ignore
        except ImportError:
            raise RuntimeError(
                "ModelScope support requires the 'modelscope' package. "
                "Install with: pip install modelscope"
            ) from None

        download_args = {
            "model_id": repo_id,
            "file_path": filename,
            "local_dir": str(local_dir),
        }
        if revision:
            download_args["revision"] = revision
        if token:
            download_args["token"] = token
        downloaded = model_file_download(**download_args)
    else:
        from huggingface_hub import hf_hub_download  # type: ignore

        if log_transfer_mode and os.environ.get("HF_XET_HIGH_PERFORMANCE") == "1":
            log.msg(
                log_prefix,
                "Using Xet high-performance transfer for accelerated download",
            )

        download_args = {
            "repo_id": repo_id,
            "filename": filename,
            "local_dir": str(local_dir),
            "tqdm_class": progress_class,
            "force_download": force_download,
            "revision": revision,
        }
        if "local_dir_use_symlinks" in inspect.signature(
            hf_hub_download
        ).parameters:
            download_args["local_dir_use_symlinks"] = False
        if token:
            download_args["token"] = token
        downloaded = hf_hub_download(**download_args)

    downloaded_path = Path(downloaded) if downloaded else local_dir / filename
    if not downloaded_path.is_file():
        fallback_path = local_dir / filename
        if fallback_path.is_file():
            downloaded_path = fallback_path
        else:
            raise RuntimeError(f"Download did not create {filename}")
    return downloaded_path


def _download_huggingface_repository_files(
    repo_id: str,
    target_path: Path,
    token: str | None,
    revision: str,
    required_files: list[str] | None = None,
) -> None:
    # Download the same repository file set as the old snapshot path, but
    # sequentially so each file gets an independent byte progress display.
    from huggingface_hub import list_repo_files  # type: ignore

    repo_files = list_repo_files(repo_id, revision=revision, token=token)
    complete_selection = _selected_huggingface_snapshot_files(repo_files)
    selected_files = _selected_huggingface_snapshot_files(
        repo_files,
        required_files=required_files,
    )
    selected_before_weight_filter = sum(
        not fnmatchcase(filename, "*.md")
        and not fnmatchcase(filename, ".git*")
        for filename in repo_files
    )
    skipped_count = selected_before_weight_filter - len(complete_selection)
    if skipped_count:
        log.msg(
            _LOG_PREFIX,
            "Skipping optional consolidated weight export because canonical "
            "Transformers weights are available",
        )
    if required_files is not None:
        log.msg(
            _LOG_PREFIX,
            f"Restricting snapshot to {len(selected_files)} runtime file(s)",
        )

    if not selected_files:
        raise RuntimeError(f"No downloadable model files found in {repo_id}")

    target_path.mkdir(parents=True, exist_ok=True)
    log.msg(
        _LOG_PREFIX,
        f"Downloading {len(selected_files)} Hugging Face file(s) sequentially",
    )
    if os.environ.get("HF_XET_HIGH_PERFORMANCE") == "1":
        log.msg(
            _LOG_PREFIX,
            "Using Xet high-performance transfer for accelerated download",
        )

    for file_index, filename in enumerate(selected_files, start=1):
        log.msg(
            _LOG_PREFIX,
            f"[{file_index}/{len(selected_files)}] Preparing {filename}",
        )
        downloaded_path = _download_repository_file(
            repo_id,
            filename,
            target_path,
            "huggingface",
            token,
            _LOG_PREFIX,
            force_download=False,
            revision=revision,
            log_transfer_mode=False,
        )
        size_gib = downloaded_path.stat().st_size / (1024**3)
        size_text = (
            f"{size_gib:.2f} GiB"
            if size_gib >= 1
            else f"{downloaded_path.stat().st_size / (1024**2):.1f} MiB"
        )
        log.msg(
            _LOG_PREFIX,
            f"[{file_index}/{len(selected_files)}] ✓ {filename} ready ({size_text})",
        )

    _record_snapshot_inventory(
        target_path,
        repo_id,
        revision,
        selected_files,
    )


def _download_verified_local_file(
    repo_id: str,
    remote_filename: str,
    model_root: Path,
    token: str | None,
    max_attempts: int,
    revision: str | None,
    requested_revision: str | None,
    expected_sha256: str | None,
) -> None:
    # Download a missing snapshot file atomically on its destination drive.
    # Corruption repair keeps using the isolated temp/copy path below.
    relative_path = _validate_repository_filename(remote_filename)
    target_file = model_root / relative_path
    expected_hash = expected_sha256 or get_hf_file_hash(
        repo_id,
        remote_filename,
        token,
        revision=revision,
    )
    if expected_hash is None:
        log.warning(
            _LOG_PREFIX,
            f"No upstream SHA-256 is available for {remote_filename}; using a transfer-only baseline",
        )

    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            if attempt > 0:
                log.msg(
                    _LOG_PREFIX,
                    f"Retry attempt {attempt + 1}/{max_attempts} for {remote_filename}",
                )
            downloaded_path = _download_repository_file(
                repo_id,
                remote_filename,
                model_root,
                "huggingface",
                token,
                _LOG_PREFIX,
                force_download=attempt > 0,
                revision=revision,
            )
            if downloaded_path.resolve() != target_file.resolve():
                raise RuntimeError(
                    f"Download returned an unexpected path for {remote_filename}"
                )

            actual_hash, verified_stat = _calculate_stable_file_hash(
                target_file,
                show_progress=True,
            )
            transfer_hash = expected_hash or actual_hash
            if actual_hash != transfer_hash:
                raise RuntimeError(
                    f"Downloaded {remote_filename} hash mismatch: "
                    f"expected {transfer_hash}, got {actual_hash}"
                )

            _write_hash_sidecar(
                target_file,
                transfer_hash,
                reference="upstream" if expected_hash else "transfer",
                verified_stat=verified_stat,
            )
            if revision:
                _record_artifact_provenance(
                    model_root,
                    target_file,
                    source="huggingface",
                    repository=repo_id,
                    requested_revision=requested_revision,
                    resolved_revision=revision,
                    filename=remote_filename,
                    sha256=transfer_hash,
                    digest_source=(
                        "registry"
                        if expected_sha256
                        else "upstream"
                        if expected_hash
                        else "transfer"
                    ),
                    artifact_stat=verified_stat,
                )
            return
        except Exception as e:  # noqa: BLE001 -- bounded download retry boundary
            last_error = e
            target_file.unlink(missing_ok=True)
            _hash_sidecar_path(target_file).unlink(missing_ok=True)
            log.warning(
                _LOG_PREFIX,
                f"Download verification failed for {remote_filename} "
                f"(attempt {attempt + 1}/{max_attempts}): {e}",
            )

    raise RuntimeError(
        f"Failed to download and verify {remote_filename} after "
        f"{max_attempts} attempt(s): {last_error}"
    )


def _validate_repository_filename(filename: str) -> Path:
    # Preserve repository subdirectories while preventing writes outside the target.
    normalized = filename.replace("\\", "/")
    relative_path = Path(normalized)
    if (
        not filename
        or relative_path.is_absolute()
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        raise ValueError(f"Unsafe repository filename: {filename!r}")
    return relative_path


def _download_verified_target_file(
    repo_id: str,
    remote_filename: str,
    target_file: Path,
    source: str,
    token: str | None,
    log_prefix: str,
    label: str,
    max_attempts: int,
    force_download: bool,
    revision: str | None = None,
    requested_revision: str | None = None,
    expected_sha256: str | None = None,
    manifest_root: Path | None = None,
    digest_source: str | None = None,
) -> None:
    # Verify the download before placement, then read back the final destination.
    expected_hash = expected_sha256
    if expected_hash is None and source != "modelscope":
        expected_hash = get_hf_file_hash(
            repo_id,
            remote_filename,
            token,
            revision=revision,
        )
    if expected_hash is None:
        log.warning(
            log_prefix,
            f"No upstream SHA-256 is available for {remote_filename}; using a transfer-only baseline",
        )

    last_error: Exception | None = None
    target_file.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(max_attempts):
        staging_path = (
            target_file.parent / f".{target_file.name}.{uuid.uuid4().hex}.part"
        )
        try:
            if attempt > 0:
                log.msg(
                    log_prefix,
                    f"Retry attempt {attempt + 1}/{max_attempts} for {label}",
                )

            with tempfile.TemporaryDirectory(prefix="sml_targeted_download_") as temp_dir:
                downloaded_path = _download_repository_file(
                    repo_id,
                    remote_filename,
                    Path(temp_dir),
                    source,
                    token,
                    log_prefix,
                    force_download=force_download or attempt > 0,
                    revision=revision,
                )
                downloaded_hash, _ = _calculate_stable_file_hash(
                    downloaded_path, show_progress=True
                )
                transfer_hash = expected_hash or downloaded_hash
                if downloaded_hash != transfer_hash:
                    raise RuntimeError(
                        f"Downloaded {label} hash mismatch: expected {transfer_hash}, got {downloaded_hash}"
                    )

                shutil.copy2(downloaded_path, staging_path)

            staging_hash, _ = _calculate_stable_file_hash(
                staging_path, show_progress=False
            )
            if staging_hash != transfer_hash:
                raise RuntimeError(
                    f"Target-drive copy of {label} failed verification: "
                    f"expected {transfer_hash}, got {staging_hash}"
                )

            os.replace(staging_path, target_file)
            final_hash, final_stat = _calculate_stable_file_hash(
                target_file, show_progress=False
            )
            if final_hash != transfer_hash:
                raise RuntimeError(
                    f"Final {label} failed verification: expected {transfer_hash}, got {final_hash}"
                )

            reference = "upstream" if expected_hash else "transfer"
            _write_hash_sidecar(
                target_file,
                transfer_hash,
                reference=reference,
                verified_stat=final_stat,
            )
            if revision:
                _record_artifact_provenance(
                    manifest_root or target_file.parent,
                    target_file,
                    source=source,
                    repository=repo_id,
                    requested_revision=requested_revision,
                    resolved_revision=revision,
                    filename=remote_filename,
                    sha256=transfer_hash,
                    digest_source=digest_source
                    or (
                        "registry"
                        if expected_sha256
                        else "upstream"
                        if expected_hash
                        else "transfer"
                    ),
                    artifact_stat=final_stat,
                )
            return
        except Exception as e:  # noqa: BLE001 -- bounded download retry boundary
            last_error = e
            log.error(
                log_prefix,
                f"{label} download/verification failed "
                f"(attempt {attempt + 1}/{max_attempts}): {e}",
            )
        finally:
            staging_path.unlink(missing_ok=True)

    target_file.unlink(missing_ok=True)
    _hash_sidecar_path(target_file).unlink(missing_ok=True)
    raise RuntimeError(
        f"{label} failed download verification after {max_attempts} attempts: {last_error}"
    ) from last_error


def ensure_targeted_hf_file(
    entry: dict,
    target_dir: Path,
    *,
    filename: str | None = None,
    local_model_path: str | None = None,
    token: str | None = None,
    log_prefix: str = _LOG_PREFIX,
    label: str = "model artifact",
    require_immutable: bool = False,
) -> str:
    # Ensure one Hugging Face artifact through the shared verified download path.
    repo_id = entry.get("repo_id", "")
    remote_filename = filename or entry.get("filename", "")
    if not isinstance(repo_id, str) or not repo_id:
        raise ValueError(f"Targeted {label} download requires repo_id")
    if not isinstance(remote_filename, str) or not remote_filename:
        raise ValueError(f"Targeted {label} download requires filename")

    relative_filename = _validate_repository_filename(remote_filename)
    target_root = Path(target_dir)
    target_file = target_root / relative_filename
    if local_model_path:
        local_target = Path(local_model_path)
        if not local_target.is_file():
            raise ValueError(f"Existing {label} path is not a file: {local_target}")
        target_file = local_target
        target_root = local_target.parent

    requested_revision = entry.get("revision")
    if require_immutable and (
        not isinstance(requested_revision, str)
        or not _FULL_HF_COMMIT_PATTERN.fullmatch(requested_revision)
    ):
        raise ValueError(
            f"Targeted {label} requires a full 40-character Hugging Face commit revision"
        )
    token = resolve_auth_token("huggingface", token)
    resolved_revision = resolve_huggingface_revision(
        repo_id,
        requested_revision,
        token,
    )
    expected_hash = _expected_sha256_for_file(entry, remote_filename)
    if require_immutable and expected_hash is None:
        raise ValueError(
            f"Targeted {label} requires expected_sha256 for {remote_filename}"
        )

    retry_value = get_config_value("retry_download_attempts", 2)
    try:
        max_attempts = max(1, int(retry_value) + 1)
    except (TypeError, ValueError):
        max_attempts = 3

    if target_file.exists():
        result = verify_model_integrity(
            target_file,
            repo_id,
            hf_filename=remote_filename,
            return_details=True,
            revision=resolved_revision,
            expected_sha256=expected_hash,
        )
        if not result.success:
            raise RuntimeError(
                f"Existing {label} does not match its pinned digest: {target_file}. "
                "Remove or rename the file to allow a verified replacement download."
            )

        sidecar = _read_hash_sidecar(target_file)
        if sidecar.expected_hash and not _artifact_provenance_matches(
                target_root,
                target_file,
                source="huggingface",
                repository=repo_id,
                requested_revision=requested_revision,
                resolved_revision=resolved_revision,
                filename=remote_filename,
                sha256=sidecar.expected_hash,
            ):
            _record_artifact_provenance(
                target_root,
                target_file,
                source="huggingface",
                repository=repo_id,
                requested_revision=requested_revision,
                resolved_revision=resolved_revision,
                filename=remote_filename,
                sha256=sidecar.expected_hash,
                digest_source="registry" if expected_hash else sidecar.reference,
                artifact_stat=target_file.stat(),
            )
        return str(target_file)

    _download_verified_target_file(
        repo_id,
        remote_filename,
        target_file,
        "huggingface",
        token,
        log_prefix,
        label,
        max_attempts,
        False,
        revision=resolved_revision,
        requested_revision=requested_revision,
        expected_sha256=expected_hash,
        manifest_root=target_root,
    )
    return str(target_file)


def ensure_targeted_gguf_files(
    entry: dict,
    quantization: str,
    source: str = "huggingface",
    token: str | None = None,
    log_prefix: str = _LOG_PREFIX,
    local_model_path: str | None = None,
) -> str:
    # Ensure one quantized GGUF and its optional mmproj using fail-closed retries.
    source = source.strip().lower() if isinstance(source, str) else ""
    if source not in {"huggingface", "modelscope"}:
        raise ValueError(f"Unsupported targeted model source: {source!r}")
    repo_id = entry.get("repo_id", "")
    file_pattern = entry.get("file_pattern", "")
    if not repo_id or not file_pattern:
        raise ValueError("Targeted GGUF download requires repo_id and file_pattern")

    filename = file_pattern.replace("{quant}", quantization)
    relative_filename = _validate_repository_filename(filename)
    repo_folder = repo_id.rstrip("/").split("/")[-1] or entry.get("name", "model")
    relative_repo_folder = _validate_repository_filename(repo_folder)
    target_dir = get_llm_models_path() / relative_repo_folder
    target_file = target_dir / relative_filename
    if local_model_path:
        local_target = Path(local_model_path)
        if not local_target.is_file():
            raise ValueError(f"Existing GGUF path is not a file: {local_target}")
        target_file = local_target
        target_dir = local_target.parent

    retry_value = get_config_value("retry_download_attempts", 2)
    try:
        max_attempts = max(1, int(retry_value) + 1)
    except (TypeError, ValueError):
        max_attempts = 3

    required_files = [
        (
            filename,
            target_file,
            "GGUF model",
            _expected_sha256_for_file(entry, filename),
        )
    ]
    mmproj = entry.get("mmproj")
    if mmproj:
        relative_mmproj = _validate_repository_filename(mmproj)
        required_files.append(
            (
                mmproj,
                target_dir / relative_mmproj,
                "mmproj",
                _expected_sha256_for_file(entry, mmproj),
            )
        )

    requested_revision = entry.get("revision")
    token = resolve_auth_token(source, token)
    if source == "modelscope":
        resolved_revision = resolve_modelscope_revision(
            repo_id,
            requested_revision,
            token,
        )
        log.debug(
            log_prefix,
            f"Resolved ModelScope revision for {repo_id}: {resolved_revision}",
        )
    else:
        resolved_revision = resolve_huggingface_revision(
            repo_id,
            requested_revision,
            token,
        )
        log.debug(
            log_prefix,
            f"Resolved Hugging Face revision for {repo_id}: {resolved_revision}",
        )

    for remote_filename, local_file, label, registry_expected_hash in required_files:
        resolved_expected_hash = registry_expected_hash
        if resolved_expected_hash is None:
            if source == "modelscope":
                resolved_expected_hash = get_modelscope_file_hash(
                    repo_id,
                    remote_filename,
                    token,
                    revision=resolved_revision,
                )
            else:
                resolved_expected_hash = get_hf_file_hash(
                    repo_id,
                    remote_filename,
                    token,
                    revision=resolved_revision,
                )
        if resolved_expected_hash is None:
            raise RuntimeError(
                f"No upstream SHA-256 is available for {remote_filename} at "
                f"the resolved {source} revision"
            )

        force_download = False
        if local_file.exists():
            sidecar = _read_hash_sidecar(local_file)
            reference_changed = bool(
                resolved_expected_hash
                and sidecar.expected_hash
                and sidecar.expected_hash != resolved_expected_hash
            )
            if not reference_changed:
                result = verify_model_integrity(
                    local_file,
                    repo_id if source == "huggingface" else None,
                    hf_filename=remote_filename,
                    return_details=True,
                    revision=resolved_revision,
                    expected_sha256=resolved_expected_hash,
                )
                if result.success:
                    if result.skipped_count:
                        log.warning(
                            log_prefix,
                            f"Using {label} without an upstream SHA-256 verification: {local_file}",
                        )
                    sidecar = _read_hash_sidecar(local_file)
                    if resolved_revision and sidecar.expected_hash:
                        digest_source = (
                            "registry"
                            if registry_expected_hash
                            else "upstream"
                            if resolved_expected_hash
                            else sidecar.reference
                        )
                        if not _artifact_provenance_matches(
                            target_dir,
                            local_file,
                            source=source,
                            repository=repo_id,
                            requested_revision=requested_revision,
                            resolved_revision=resolved_revision,
                            filename=remote_filename,
                            sha256=sidecar.expected_hash,
                        ):
                            _record_artifact_provenance(
                                target_dir,
                                local_file,
                                source=source,
                                repository=repo_id,
                                requested_revision=requested_revision,
                                resolved_revision=resolved_revision,
                                filename=remote_filename,
                                sha256=sidecar.expected_hash,
                                digest_source=digest_source,
                                artifact_stat=local_file.stat(),
                            )
                    continue
            force_download = True
            if reference_changed:
                log.warning(
                    log_prefix,
                    f"Existing {label} does not match the requested revision/digest; downloading a replacement",
                )
            else:
                log.warning(
                    log_prefix,
                    f"Existing {label} failed verification; downloading a replacement",
                )

        _download_verified_target_file(
            repo_id,
            remote_filename,
            local_file,
            source,
            token,
            log_prefix,
            label,
            max_attempts,
            force_download,
            revision=resolved_revision,
            requested_revision=requested_revision,
            expected_sha256=resolved_expected_hash,
            manifest_root=target_dir,
            digest_source="registry" if registry_expected_hash else "upstream",
        )

    log.msg(log_prefix, f"Targeted GGUF files ready at {target_dir}")
    return str(target_file)


def check_model_completeness(
    model_path: Path,
    repo_id: str | None = None,
    hf_token: str | None = None,
    revision: str | None = None,
    required_files: list[str] | None = None,
) -> tuple[bool, list[str]]:
    # Check if all required model files are present by reading the model index file.
    #
    # For sharded models, reads model.safetensors.index.json or pytorch_model.bin.index.json
    # to get the list of required weight files and checks if they all exist.
    #
    # Note: Consolidated files are intentionally ignored - users may have deleted them
    # to save space since they're only needed for specific loading methods.
    #
    # Args:
    #     model_path: Path to model directory
    #     repo_id: HuggingFace repo_id for re-downloading missing files
    #     hf_token: Optional HuggingFace token for authenticated downloads
    #
    # Returns:
    #     Tuple of (is_complete, missing_files_list)
    import json

    if not model_path.is_dir():
        # Single file model (e.g., GGUF) - just check if file exists
        if model_path.exists():
            return (True, [])
        return (False, [model_path.name])

    if repo_id:
        inventory_missing = _matching_snapshot_inventory_missing_files(
            model_path,
            repo_id,
            revision,
            required_files=required_files,
        )
        if inventory_missing is not None:
            if inventory_missing:
                log.warning(
                    _LOG_PREFIX,
                    f"Model incomplete (snapshot inventory): "
                    f"{len(inventory_missing)} file(s) missing",
                )
                return (False, inventory_missing)
            return (True, [])

    missing_files = []

    # Check for safetensors index file first (preferred), then pytorch
    index_files = [
        model_path / "model.safetensors.index.json",
        model_path / "pytorch_model.bin.index.json",
    ]

    index_file = None
    for idx_file in index_files:
        if idx_file.exists():
            index_file = idx_file
            break

    if not index_file:
        # Retrieve the effective HF credential for repository metadata.
        token = resolve_auth_token("huggingface", hf_token)

        # No local index file - check if we can query HuggingFace repo to list files
        if repo_id:
            try:
                from huggingface_hub import list_repo_files  # type: ignore

                repo_files = list_repo_files(
                    repo_id,
                    revision=revision,
                    token=token,
                )
                selected_files = _selected_huggingface_snapshot_files(
                    repo_files,
                    required_files=required_files,
                )
                missing_files.extend(
                    filename
                    for filename in selected_files
                    if not (model_path / filename).is_file()
                )
                if not missing_files and revision:
                    try:
                        _record_snapshot_inventory(
                            model_path,
                            repo_id,
                            revision,
                            selected_files,
                        )
                    except (JsonStoreError, OSError, TypeError, ValueError) as e:
                        log.warning(
                            _LOG_PREFIX,
                            f"Could not cache model snapshot inventory: {e}",
                        )
            except Exception as e:  # noqa: BLE001 -- optional remote metadata boundary
                log.warning(
                    _LOG_PREFIX,
                    f"Could not list repo files for completeness check: {e}",
                )

        # If we couldn't list files or found nothing, check basic config and weight heuristics locally
        if not missing_files:
            config_file = model_path / "config.json"
            if not config_file.exists():
                return (False, ["config.json"])

            # Local fallback: ensure there is at least one weight file in the directory
            weight_extensions = (
                ".safetensors",
                ".bin",
                ".gguf",
                ".pt",
                ".pth",
                ".ckpt",
            )
            has_weights = any(
                f.name.endswith(weight_extensions)
                for f in model_path.iterdir()
                if f.is_file()
            )
            if not has_weights:
                log.warning(
                    _LOG_PREFIX,
                    f"Model folder {model_path} has config.json but no weights found.",
                )
                return (
                    False,
                    ["model.safetensors"],
                )  # request at least one weights file

            return (True, [])
        else:
            log.warning(
                _LOG_PREFIX,
                f"Model incomplete (repo scan): {len(missing_files)} file(s) missing",
            )
            return (False, missing_files)

    try:
        with open(index_file, "r", encoding="utf-8") as f:
            index_data = json.load(f)

        # Get unique weight files from the weight_map
        weight_map = index_data.get("weight_map", {})
        required_files = set(weight_map.values())

        # Check each required file (but ignore consolidated files - user may have deleted them intentionally)
        for filename in required_files:
            # Skip consolidated files - these are optional alternative formats
            # Users often delete them to save disk space
            if "consolidated" in filename.lower():
                continue

            file_path = model_path / filename
            if not file_path.exists():
                missing_files.append(filename)
                log.debug(_LOG_PREFIX, f"Missing model file: {filename}")

        # Also check essential config files
        essential_files = ["config.json"]
        for filename in essential_files:
            file_path = model_path / filename
            if not file_path.exists():
                missing_files.append(filename)

        if missing_files:
            log.warning(
                _LOG_PREFIX, f"Model incomplete: {len(missing_files)} file(s) missing"
            )
            return (False, missing_files)

        return (True, [])

    except (AttributeError, OSError, TypeError, ValueError) as e:
        log.warning(_LOG_PREFIX, f"Could not read model index file: {e}")
        return (True, [])  # Assume complete if we can't check


def download_missing_files(
    model_path: Path,
    missing_files: list[str],
    repo_id: str,
    hf_token: str | None = None,
    revision: str | None = None,
    requested_revision: str | None = None,
    expected_sha256: dict[str, str] | None = None,
) -> bool:
    # Download missing files sequentially with per-file byte progress. Hugging
    # Face places each completed file atomically in the local model directory.
    if not missing_files:
        return True
    if not repo_id:
        log.error(_LOG_PREFIX, "Cannot download missing files: no repo_id provided")
        return False

    clean_repo_id = extract_repo_id_from_url(repo_id)
    if not clean_repo_id:
        log.error(_LOG_PREFIX, f"Cannot extract repo_id from: {repo_id}")
        return False

    normalized_hashes = _validated_expected_sha256_map(
        {"expected_sha256": expected_sha256}
    )
    validated_files = []
    try:
        for filename in missing_files:
            validated_files.append(_validate_repository_filename(filename).as_posix())
    except ValueError as e:
        log.error(_LOG_PREFIX, f"Cannot download missing files: {e}")
        return False

    retry_value = get_config_value("retry_download_attempts", 2)
    try:
        max_attempts = max(1, int(retry_value) + 1)
    except (TypeError, ValueError):
        max_attempts = 3

    failed_files = []
    for file_index, filename in enumerate(validated_files, start=1):
        try:
            log.msg(
                _LOG_PREFIX,
                f"[{file_index}/{len(validated_files)}] Preparing {filename}",
            )
            _download_verified_local_file(
                clean_repo_id,
                filename,
                model_path,
                hf_token,
                max_attempts,
                revision,
                requested_revision,
                normalized_hashes.get(filename),
            )
            log.msg(
                _LOG_PREFIX,
                f"✓ Missing file ready: {filename}",
            )
        except Exception as e:  # noqa: BLE001 -- per-file recovery boundary
            log.error(_LOG_PREFIX, f"Failed to download missing file {filename}: {e}")
            failed_files.append(filename)

    if failed_files:
        log.error(
            _LOG_PREFIX,
            f"Failed to download {len(failed_files)} file(s): {', '.join(failed_files)}",
        )
        return False

    log.msg(_LOG_PREFIX, f"\u2713 Successfully downloaded {len(validated_files)} missing file(s)")
    return True


def redownload_corrupted_files(
    corrupted_files: list[Path],
    repo_id: str,
    local_dir: Path,
    hf_token: str | None = None,
    revision: str | None = None,
    requested_revision: str | None = None,
    expected_sha256: dict[str, str] | None = None,
) -> bool:
    # Replace only corrupted files through verified atomic placement.
    if not corrupted_files:
        return True
    if not repo_id:
        log.warning(_LOG_PREFIX, "Cannot re-download files: no repo_id provided")
        return False

    clean_repo_id = extract_repo_id_from_url(repo_id)
    if not clean_repo_id:
        log.warning(_LOG_PREFIX, f"Cannot extract repo_id from: {repo_id}")
        return False

    normalized_hashes = _validated_expected_sha256_map(
        {"expected_sha256": expected_sha256}
    )
    validated_files = []
    try:
        resolved_local_dir = local_dir.resolve()
        for file_path in corrupted_files:
            relative_path = file_path.resolve().relative_to(resolved_local_dir)
            validated_files.append(
                (file_path, _validate_repository_filename(relative_path.as_posix()).as_posix())
            )
    except ValueError as e:
        log.error(_LOG_PREFIX, f"Cannot re-download corrupted file: {e}")
        return False

    retry_value = get_config_value("retry_download_attempts", 2)
    try:
        max_attempts = max(1, int(retry_value) + 1)
    except (TypeError, ValueError):
        max_attempts = 3

    failed_files = []
    for file_path, filename in validated_files:
        final_path = local_dir / filename
        try:
            _download_verified_target_file(
                clean_repo_id,
                filename,
                final_path,
                "huggingface",
                hf_token,
                _LOG_PREFIX,
                filename,
                max_attempts,
                force_download=True,
                revision=revision,
                requested_revision=requested_revision,
                expected_sha256=normalized_hashes.get(filename),
                manifest_root=local_dir,
            )
        except Exception as e:
            log.error(_LOG_PREFIX, f"Failed to re-download {filename}: {e}")
            failed_files.append(filename)

    if failed_files:
        log.error(
            _LOG_PREFIX,
            f"Failed to re-download {len(failed_files)} file(s): {', '.join(failed_files)}",
        )
        return False

    log.msg(_LOG_PREFIX, f"✓ Successfully re-downloaded {len(validated_files)} corrupted file(s)")
    return True


def extract_repo_id_from_url(repo_id: str) -> str:
    # Extract actual repo_id (namespace/repo_name) from a HuggingFace URL.
    #
    # Args:
    #     repo_id: Either a direct repo_id like "user/repo" or a full HuggingFace URL
    #
    # Returns:
    #     Extracted repo_id in format "user/repo", or original string if not a URL
    #
    # Examples:
    #     "bartowski/model" -> "bartowski/model"
    #     "https://huggingface.co/user/repo/resolve/main/file.gguf" -> "user/repo"
    if not repo_id:
        return ""

    # If it is a URL, accept only the canonical Hugging Face resolve form.
    if repo_id.startswith(("http://", "https://")):
        try:
            repository, _, _ = _parse_huggingface_resolve_url(repo_id)
            return repository
        except ValueError:
            return ""

    # Already in correct format or not a URL
    return repo_id


def _parse_huggingface_resolve_url(url: str) -> tuple[str, str, str]:
    # Convert one canonical Hugging Face HTTPS resolve URL into repository,
    # requested revision, and repository-relative filename components.
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as e:
        raise ValueError(f"Invalid Hugging Face resolve URL: {url!r}") from e

    if parsed.scheme != "https":
        raise ValueError("Hugging Face direct URLs must use HTTPS")
    if parsed.hostname is None or parsed.hostname.lower() != "huggingface.co":
        raise ValueError("Direct model URLs must use the huggingface.co host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Hugging Face direct URLs must not contain user information")
    if port not in {None, 443}:
        raise ValueError("Hugging Face direct URLs must use the default HTTPS port")
    if parsed.fragment:
        raise ValueError("Hugging Face direct URLs must not contain a fragment")
    if parsed.query not in {"", "download=true"}:
        raise ValueError("Unsupported Hugging Face direct URL query")

    raw_parts = parsed.path.split("/")
    if not raw_parts or raw_parts[0] != "":
        raise ValueError("Invalid Hugging Face resolve URL path")
    try:
        parts = [unquote(part, errors="strict") for part in raw_parts[1:]]
    except UnicodeDecodeError as e:
        raise ValueError("Invalid percent encoding in Hugging Face resolve URL") from e
    if len(parts) < 5 or any(not part for part in parts):
        raise ValueError("Hugging Face resolve URL is missing required path components")

    owner, model_name, action, revision, *filename_parts = parts
    if action != "resolve":
        raise ValueError("Hugging Face direct URL must use the /resolve/ path")
    if any("/" in part or "\\" in part for part in (owner, model_name)):
        raise ValueError("Invalid Hugging Face repository identifier")
    _validate_repository_filename(f"{owner}/{model_name}")
    if any(
        ord(character) < 32 or ord(character) == 127
        for character in "".join(parts)
    ):
        raise ValueError("Hugging Face direct URL contains control characters")

    _validate_repository_filename(revision)
    remote_filename = "/".join(filename_parts)
    _validate_repository_filename(remote_filename)
    return f"{owner}/{model_name}", revision, remote_filename


# ============================================================================
# Model Discovery Functions (v2 workflow)
# ============================================================================


def _verify_fp8_tensors(model_path: Path) -> bool:
    # Verify a model folder actually contains FP8 quantized tensors.
    #
    # Checks safetensors files for float8_e4m3fn dtype or FP8 scale patterns.
    # This is used to distinguish true FP8 models from models that were
    # converted/dequantized to BF16 but still have FP8 in their config.
    #
    # Args:
    #     model_path: Path to model folder
    #
    # Returns:
    #     True if actual FP8 tensors are found
    try:
        from safetensors import safe_open  # type: ignore

        sf_files = list(model_path.glob("*.safetensors"))
        if not sf_files:
            return False

        # Check first safetensors file
        with safe_open(str(sf_files[0]), framework="pt") as f:
            # Check a few weight tensors for FP8 dtype
            for key in list(f.keys())[:20]:
                if "weight" in key.lower() and "scale" not in key.lower():
                    tensor = f.get_tensor(key)
                    dtype_str = str(tensor.dtype).lower()
                    if (
                        "float8" in dtype_str
                        or "e4m3" in dtype_str
                        or "e5m2" in dtype_str
                    ):
                        return True

            # Also check for FP8 scale tensors (weight_scale_inv, activation_scale)
            tensor_keys = list(f.keys())
            has_fp8_scales = any(
                "weight_scale" in k.lower() or "scale_inv" in k.lower()
                for k in tensor_keys
            )
            if has_fp8_scales:
                return True

    except Exception:
        pass

    return False


def detect_prequantized_model(model_path: Path) -> tuple[bool, str]:
    # Check if a model is pre-quantized by inspecting config.json and safetensors.
    #
    # Detects: AWQ, GPTQ, BitsAndBytes (BNB), FP8, GGML/GGUF markers.
    # Also checks actual tensor dtypes in safetensors to verify FP8 vs BF16.
    #
    # Args:
    #     model_path: Path to model folder or GGUF file
    #
    # Returns:
    #     Tuple of (is_quantized: bool, quant_type: str)
    #     quant_type is one of: "awq", "gptq", "bnb", "fp8", "gguf", "unknown", ""
    import json

    p = Path(model_path)

    # GGUF files are always pre-quantized
    if p.is_file() and p.suffix.lower() == ".gguf":
        return True, "gguf"

    if not p.is_dir():
        return False, ""

    # Check config.json for quantization_config
    config_file = p / "config.json"
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text())
            if "quantization_config" in config:
                quant_config = config["quantization_config"]
                quant_method = quant_config.get("quant_method", "").lower()

                # AWQ
                if quant_method == "awq":
                    return True, "awq"

                # GPTQ
                if quant_method == "gptq":
                    return True, "gptq"

                # BitsAndBytes
                if (
                    quant_method in ["bitsandbytes", "bnb"]
                    or quant_config.get("load_in_4bit")
                    or quant_config.get("load_in_8bit")
                ):
                    return True, "bnb"

                # FP8 (various indicators in config)
                if quant_method == "fp8" or "activation_scheme" in quant_config:
                    # Double-check: verify safetensors actually contain FP8 tensors
                    # Some models may have config from FP8 but weights converted to BF16
                    if _verify_fp8_tensors(p):
                        return True, "fp8"
                    # Config says FP8 but no FP8 tensors found - not actually quantized
                    # (model was likely converted/dequantized to BF16)
                # Unknown quantization config present
                if quant_method:
                    return True, quant_method

        except Exception:
            pass

    # Check params.json (Mistral native format)
    params_file = p / "params.json"
    if params_file.exists():
        try:
            params = json.loads(params_file.read_text())
            if "quantization" in params:
                qformat = params["quantization"].get("qformat_weight", "")
                if "fp8" in qformat.lower():
                    return True, "fp8"
                if qformat:
                    return True, qformat.lower()
        except Exception:
            pass

    # Fallback: check filename markers (less reliable but catches edge cases)
    model_name_lower = p.name.lower()

    # Standard quantization format markers
    quant_markers = {
        "awq": ["-awq", "_awq", ".awq"],
        "gptq": ["-gptq", "_gptq", ".gptq"],
        "bnb": ["-bnb", "_bnb"],
        "fp8": ["-fp8", "_fp8"],
        "int8": ["-int8", "_int8"],
        "int4": ["-int4", "_int4"],
    }
    for quant_type, markers in quant_markers.items():
        if any(m in model_name_lower for m in markers):
            return True, quant_type

    # GGUF quantization markers (Q4_K_M, Q5_K_S, Q8_0, etc.)
    # These appear in GGUF filenames and folder names
    gguf_quant_markers = [
        "_q4_",
        "_q5_",
        "_q6_",
        "_q8_",  # underscore style
        "-q4-",
        "-q5-",
        "-q6-",
        "-q8-",  # dash style
        "_k_m",
        "_k_s",
        "_k_l",  # quality suffixes (K_M, K_S, K_L)
        "q4_k",
        "q5_k",
        "q6_k",
        "q8_0",  # full quant names
        ".q4_",
        ".q5_",
        ".q6_",
        ".q8_",  # dot prefix style
        "_iq4",
        "_iq3",
        "_iq2",  # imatrix quants
    ]
    if any(m in model_name_lower for m in gguf_quant_markers):
        return True, "gguf"

    return False, ""


def detect_fp8_model(model_path: Path) -> bool:
    # Check if a model folder contains FP8 quantized weights.
    #
    # Checks:
    # 1. params.json for quantization.qformat_weight containing "fp8"
    # 2. config.json for quantization_config with FP8 indicators
    # 3. Safetensors files for float8_e4m3fn dtype tensors
    #
    # Args:
    #     model_path: Path to model folder
    #
    # Returns:
    #     True if model uses FP8 quantization
    import json

    # Check params.json (Mistral native format)
    params_file = model_path / "params.json"
    if params_file.exists():
        try:
            params = json.loads(params_file.read_text())
            if "quantization" in params:
                qformat = params["quantization"].get("qformat_weight", "")
                if "fp8" in qformat.lower():
                    return True
        except Exception:
            pass

    # Check config.json for quantization_config
    config_file = model_path / "config.json"
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text())
            if "quantization_config" in config:
                quant_config = config["quantization_config"]
                quant_method = quant_config.get("quant_method", "")
                # FP8 indicators
                if quant_method == "fp8" or "activation_scheme" in quant_config:
                    return True
        except Exception:
            pass

    # Check safetensors metadata for tensor dtype info
    # Use metadata check instead of loading tensors (much faster)
    try:
        import safetensors  # type: ignore

        sf_files = list(model_path.glob("*.safetensors"))
        if sf_files:
            # Read file metadata without loading tensors
            with safetensors.safe_open(str(sf_files[0]), framework="pt") as f:
                metadata = f.metadata()
                # Some models include dtype info in metadata
                if metadata:
                    format_str = str(metadata).lower()
                    if (
                        "float8" in format_str
                        or "fp8" in format_str
                        or "e4m3" in format_str
                    ):
                        return True

                # Fallback: check tensor names for FP8 scale patterns
                # FP8 models typically have weight_scale or scale_inv tensors
                tensor_keys = list(f.keys())
                has_fp8_scales = any("scale" in k.lower() for k in tensor_keys[:50])
                if has_fp8_scales and any(
                    "weight" in k.lower() and "scale" in k.lower()
                    for k in tensor_keys[:50]
                ):
                    return True
    except Exception:
        pass

    return False


def discover_models_in_folder(folder_path: Optional[Path] = None) -> List[dict]:
    # Scan LLM folder and other model folders (florence2) and discover all models with their detected families.
    #
    # Args:
    #     folder_path: Optional path to scan (defaults to models/LLM + models/florence2)
    #
    # Returns:
    #     List of dicts with keys: name, path, family, is_gguf, is_folder, is_fp8
    try:
        import folder_paths  # type: ignore
        from .model_types import get_model_family_from_name

        models = []
        model_extensions = {".safetensors", ".gguf", ".bin", ".pt", ".onnx"}

        # Determine which folders to scan
        folders_to_scan = []
        if folder_path is None:
            # Scan default folders: models/LLM and models/florence2
            llm_path = get_llm_models_path()
            if llm_path.exists():
                folders_to_scan.append(
                    (llm_path, "")
                )  # (path, prefix for display name)

            florence2_path = Path(folder_paths.models_dir) / "florence2"
            if florence2_path.exists():
                folders_to_scan.append(
                    (florence2_path, "florence2/")
                )  # prefix with "florence2/"
        else:
            if folder_path.exists():
                folders_to_scan.append((folder_path, ""))

        if not folders_to_scan:
            return []

        def scan_dir(
            base_path: Path, relative_path: str = "", display_prefix: str = ""
        ):
            # Recursively scan for models.
            try:
                for item in base_path.iterdir():
                    if item.is_file() and item.suffix in model_extensions:
                        # Skip mmproj files
                        if "mmproj" in item.name.lower():
                            continue

                        file_path = (
                            f"{relative_path}/{item.name}"
                            if relative_path
                            else item.name
                        )
                        display_name = f"{display_prefix}{file_path}"
                        family = get_model_family_from_name(item.name)

                        models.append(
                            {
                                "name": display_name,
                                "path": str(item),
                                "family": family.value,
                                "is_gguf": item.suffix == ".gguf",
                                "is_folder": False,
                                "is_fp8": False,  # Single files are not FP8 (GGUF has own quant)
                            }
                        )
                    elif item.is_dir():
                        # Check if this is a model folder (has config.json or model files)
                        has_config = (item / "config.json").exists()
                        has_safetensors = any(item.glob("*.safetensors"))
                        has_gguf = list(item.glob("*.gguf"))
                        has_model_files = (
                            any(
                                (item / f).exists()
                                for f in [
                                    "model.safetensors",
                                    "pytorch_model.bin",
                                    "model.onnx",
                                ]
                            )
                            or has_safetensors
                            or has_gguf
                        )

                        if has_config or has_model_files:
                            folder_name = (
                                f"{relative_path}/{item.name}/"
                                if relative_path
                                else f"{item.name}/"
                            )
                            display_name = f"{display_prefix}{folder_name}"
                            # Pass full path so config.json can be read for family detection
                            family = get_model_family_from_name(str(item))

                            # Check for FP8 quantization
                            is_fp8 = detect_fp8_model(item)

                            # Add folder entry for non-GGUF models (safetensors, bin, etc.)
                            # Only add folder if there are non-GGUF model files
                            non_gguf_models = has_safetensors or any(
                                (item / f).exists()
                                for f in [
                                    "model.safetensors",
                                    "pytorch_model.bin",
                                    "model.onnx",
                                ]
                            )
                            if non_gguf_models:
                                models.append(
                                    {
                                        "name": display_name,
                                        "path": str(item),
                                        "family": family.value,
                                        "is_gguf": False,
                                        "is_folder": True,
                                        "is_fp8": is_fp8,
                                    }
                                )

                            # ALWAYS list individual GGUF files (even if folder has config.json)
                            # This allows users to select specific quantization variants
                            for gguf_file in has_gguf:
                                # Skip mmproj files
                                if "mmproj" in gguf_file.name.lower():
                                    continue
                                gguf_path = (
                                    f"{relative_path}/{item.name}/{gguf_file.name}"
                                    if relative_path
                                    else f"{item.name}/{gguf_file.name}"
                                )
                                gguf_display = f"{display_prefix}{gguf_path}"
                                gguf_family = get_model_family_from_name(gguf_file.name)
                                models.append(
                                    {
                                        "name": gguf_display,
                                        "path": str(gguf_file),
                                        "family": gguf_family.value,
                                        "is_gguf": True,
                                        "is_folder": False,
                                        "is_fp8": False,
                                    }
                                )
                        else:
                            # Recurse into subdirectory
                            item_rel = (
                                f"{relative_path}/{item.name}"
                                if relative_path
                                else item.name
                            )
                            if relative_path.count("/") < 4:
                                scan_dir(item, item_rel, display_prefix)
            except PermissionError:
                pass

        # Scan all folders
        for scan_path, prefix in folders_to_scan:
            scan_dir(scan_path, "", prefix)

        return sorted(models, key=lambda x: x["name"])

    except Exception as e:
        log.error(_LOG_PREFIX, f"Error discovering models: {e}")
        return []


# ============================================================================
# Model Download Functions
# ============================================================================


def ensure_mmproj_path(
    template_info: dict,
    model_folder: str,
) -> str | None:
    # Ensure mmproj file exists locally, downloading if needed.
    #
    # This function handles the separation between:
    # - mmproj_path: Local file path (relative to LLM folder) - for loading and dropdown selection
    # - mmproj_url: URL for downloading - used when local file doesn't exist
    #
    # Search order when mmproj_path is empty:
    # 1. Check if expected target file exists (derived from URL)
    # 2. Search for any .mmproj.gguf file in model folder
    # 3. Download from URL if not found
    #
    # Args:
    #     template_info: Template dict with mmproj_path (local) and mmproj_url (for download)
    #     model_folder: Folder to download mmproj into (usually model folder)
    #
    # Returns:
    #     Absolute path to mmproj file, or None if not available
    mmproj_path = template_info.get("mmproj_path", "")
    mmproj_url = template_info.get("mmproj_url", "")
    direct_source: tuple[str, str, str] | None = None
    direct_source_error: ValueError | None = None
    if mmproj_url:
        try:
            direct_source = _parse_huggingface_resolve_url(mmproj_url)
        except ValueError as e:
            direct_source_error = e
    remote_repo_id = direct_source[0] if direct_source else None
    original_filename = direct_source[2] if direct_source else ""
    direct_token: str | None = None
    direct_resolved_revision: str | None = None
    direct_expected_hash = (
        _expected_sha256_for_file(template_info, original_filename)
        if direct_source
        else None
    )

    def _get_direct_revision() -> str | None:
        nonlocal direct_token, direct_resolved_revision
        if direct_source is None:
            return None
        if direct_resolved_revision is None:
            direct_token = resolve_auth_token("huggingface")
            direct_resolved_revision = resolve_huggingface_revision(
                direct_source[0],
                direct_source[1],
                direct_token,
            )
        return direct_resolved_revision

    # Skip if neither path nor URL is provided
    if not mmproj_path and not mmproj_url:
        return None

    llm_dir = get_llm_models_path()
    model_folder_path = Path(model_folder)

    # Case 1: mmproj_path is a local path (not URL) - check if it exists
    if mmproj_path and not mmproj_path.startswith("http"):
        # Resolve to absolute path
        if os.path.sep in mmproj_path or (
            os.path.altsep and os.path.altsep in mmproj_path
        ):
            local_file = llm_dir / mmproj_path
        else:
            local_file = model_folder_path / mmproj_path

        if local_file.exists():
            return str(local_file)
        # Local path specified but file doesn't exist - fall through to search/download

    # Case 2: mmproj_path is empty or file not found - search for existing mmproj files
    # Search in model folder for any file with 'mmproj' in the name (broader pattern)
    if model_folder_path.exists():
        # Broader search: any .gguf file with 'mmproj' in the name
        # This catches patterns like: model.mmproj-Q8_0.gguf, model.mmproj.gguf, mmproj-fp16.gguf
        all_gguf = sorted(model_folder_path.glob("*.gguf"))
        mmproj_files = [f for f in all_gguf if "mmproj" in f.name.lower()]
        for found_file in mmproj_files:
            if remote_repo_id and not verify_model_integrity(
                found_file,
                remote_repo_id,
                hf_filename=original_filename,
                revision=_get_direct_revision(),
                expected_sha256=direct_expected_hash,
            ):
                found_file.unlink(missing_ok=True)
                _hash_sidecar_path(found_file).unlink(missing_ok=True)
                continue
            log.msg(_LOG_PREFIX, f"✓ Found existing mmproj: {found_file.name}")
            return str(found_file)

    # Case 3: Need to download from URL
    if not mmproj_url:
        if mmproj_path:
            log.warning(
                _LOG_PREFIX,
                f"mmproj_path specified but file not found and no mmproj_url: {mmproj_path}",
            )
        return None
    if direct_source_error is not None:
        raise direct_source_error
    if direct_source is None:
        raise ValueError("MMProj URL is not a supported Hugging Face resolve URL")

    # Determine target filename and path from URL
    # Preserve precision info (fp16, bf16, f16, etc.) when renaming
    precision_match = re.search(r"(fp16|bf16|f16|f32)", original_filename.lower())
    precision_suffix = f"-{precision_match.group(1)}" if precision_match else ""

    model_base = model_folder_path.name
    target_filename = f"{model_base}{precision_suffix}.mmproj.gguf"
    target = model_folder_path / target_filename
    # Check if target already exists (may have been downloaded previously with expected name)
    if target.exists():
        if not remote_repo_id or verify_model_integrity(
            target,
            remote_repo_id,
            hf_filename=original_filename,
            revision=_get_direct_revision(),
            expected_sha256=direct_expected_hash,
        ):
            return str(target)
        target.unlink(missing_ok=True)
        _hash_sidecar_path(target).unlink(missing_ok=True)

    # Also check for original filename (user might have downloaded manually)
    original_target = model_folder_path / original_filename
    if original_target.exists():
        if not remote_repo_id or verify_model_integrity(
            original_target,
            remote_repo_id,
            hf_filename=original_filename,
            revision=_get_direct_revision(),
            expected_sha256=direct_expected_hash,
        ):
            log.msg(
                _LOG_PREFIX,
                f"✓ Found mmproj with original filename: {original_filename}",
            )
            return str(original_target)
        original_target.unlink(missing_ok=True)
        _hash_sidecar_path(original_target).unlink(missing_ok=True)

    # Download from URL
    log.msg(_LOG_PREFIX, f"Downloading MMProj from {mmproj_url}")
    target.parent.mkdir(parents=True, exist_ok=True)

    if remote_repo_id:
        retry_value = get_config_value("retry_download_attempts", 2)
        try:
            max_attempts = max(1, int(retry_value) + 1)
        except (TypeError, ValueError):
            max_attempts = 3
        requested_revision = direct_source[1]
        resolved_revision = _get_direct_revision()
        if resolved_revision is None:
            raise RuntimeError("Could not resolve an immutable MMProj revision")
        _download_verified_target_file(
            remote_repo_id,
            original_filename,
            target,
            "huggingface",
            direct_token,
            _LOG_PREFIX,
            "mmproj",
            max_attempts,
            force_download=False,
            revision=resolved_revision,
            requested_revision=requested_revision,
            expected_sha256=direct_expected_hash,
            manifest_root=model_folder_path,
            digest_source="registry" if direct_expected_hash else None,
        )
        log.msg(_LOG_PREFIX, f"✓ MMProj downloaded as {target_filename}")
        return str(target)

    return None


def ensure_model_path(
    template_info: dict,
) -> tuple:
    # Download model if needed and return (model_path, model_folder_path, repo_id).
    #
    # Unified model download function with hash verification and automatic retry.
    # Supports automatic retry on hash verification failure (configurable via retry_download_attempts).
    #
    # Args:
    #     template_info: Template dict with local_path, repo_id, model_type, and
    #         optional Hugging Face revision/expected_sha256 provenance fields
    #
    # Returns:
    #     Tuple of (model_path, model_folder_path, repo_id)
    #
    # Raises:
    #     ValueError: If template is invalid or path not found
    #     RuntimeError: If model verification fails after all retries
    import shutil
    from .model_types import detect_model_type, ModelType

    local_path = template_info.get("local_path")
    repo_id = template_info.get("repo_id")
    requested_revision = template_info.get("revision")
    expected_sha256 = _validated_expected_sha256_map(template_info)
    required_snapshot_files = _validated_snapshot_file_selection(
        template_info.get("required_snapshot_files")
    )

    if repo_id is not None and not isinstance(repo_id, str):
        raise ValueError("Template repo_id must be a string")
    is_direct_url = bool(
        repo_id
        and repo_id.startswith(("http://", "https://"))
    )
    direct_source = _parse_huggingface_resolve_url(repo_id) if is_direct_url else None
    if direct_source and not direct_source[2].lower().endswith(".gguf"):
        raise ValueError("Direct model URLs are supported only for GGUF artifacts")

    if not repo_id and not local_path:
        raise ValueError("Template missing repo_id or local_path")

    model_type = detect_model_type(template_info)
    models_base = get_llm_models_path()

    # Get retry attempts from config (default 2)
    max_retries = get_config_value("retry_download_attempts", 2)

    # For Florence2 models, also check the models/florence2/ folder (used by comfyui-florence2 node)
    import folder_paths  # type: ignore

    florence2_base = Path(folder_paths.models_dir) / "florence2"
    # For QwenVL models, also check the models/llm/Qwen-VL/ folder (used by other ComfyUI nodes)
    qwenvl_base = models_base / "Qwen-VL"

    target = None

    # Construct target path
    if local_path:
        if local_path.lower().endswith(".gguf"):
            if os.path.sep in local_path or (
                os.path.altsep and os.path.altsep in local_path
            ):
                target = models_base / local_path
            else:
                model_name = Path(local_path).stem
                # Download GGUF files directly to models/llm/model_name/ (not Qwen-VL subfolder)
                target = models_base / model_name / Path(local_path).name

                # But also check Qwen-VL folder for existing models (backward compatibility)
                if not target.exists() and (
                    model_type == ModelType.QWENVL or "qwen" in local_path.lower()
                ):
                    qwenvl_target = qwenvl_base / model_name / Path(local_path).name
                    if qwenvl_target.exists():
                        target = qwenvl_target
                        log.msg(
                            _LOG_PREFIX,
                            f"✓ Found QwenVL model in Qwen-VL folder: {model_name}",
                        )
        else:
            # Check if local_path starts with a known subfolder of models_dir (e.g., "florence2/")
            # This handles paths like "florence2/base-PromptGen-v1.5/"
            from .common import to_posix_path

            local_path_parts = to_posix_path(local_path).split("/")
            models_dir = Path(folder_paths.models_dir)

            # Get the configured LLM folder name to exclude it from models_dir subfolder detection
            configured_llm_path = get_config_value("llm_models_path", "LLM")
            llm_folder_name = Path(configured_llm_path).name

            first_part = local_path_parts[0] if local_path_parts else ""
            first_part_is_models_subfolder = (
                first_part and (models_dir / first_part).exists()
            )
            first_part_is_llm_folder = first_part == llm_folder_name

            if first_part_is_models_subfolder and not first_part_is_llm_folder:
                # Path is relative to models_dir (e.g., "florence2/model_name/")
                target = models_dir / local_path
            else:
                # Path is relative to LLM folder (models_base)
                target = models_base / local_path

            # For Florence2 models, check alternative locations if not found at local_path
            if not target.exists() and model_type == ModelType.FLORENCE2:
                # Extract just the model name from local_path (remove any folder prefix)
                from .common import to_posix_path

                local_path_clean = to_posix_path(local_path).rstrip("/")
                model_folder_name = local_path_clean.split("/")[
                    -1
                ]  # Get last component

                # Also derive the repository's default local folder name.
                repo_model_name = repo_id.split("/")[-1] if repo_id else None

                # Build list of candidate names to search for
                candidate_names = [model_folder_name]
                if repo_model_name and repo_model_name != model_folder_name:
                    candidate_names.append(repo_model_name)
                # Also try alt names with common Florence prefixes removed/added
                for name in list(candidate_names):
                    if name.startswith("Florence-2-"):
                        candidate_names.append(name[len("Florence-2-") :])
                    if name.startswith("Florence-2.1-"):
                        candidate_names.append(name[len("Florence-2.1-") :])

                # Check LLM folder (models_base) first — this is where both SML and
                # ComfyUI-Florence2 download to
                for name in candidate_names:
                    llm_target = models_base / name
                    if llm_target.exists():
                        target = llm_target
                        log.msg(
                            _LOG_PREFIX,
                            f"✓ Found Florence2 model in LLM folder: {name}",
                        )
                        break

                # Then check models/florence2/ folder (backward compat with older setups)
                if not target.exists() and florence2_base.exists():
                    for name in candidate_names:
                        f2_target = florence2_base / name
                        if f2_target.exists():
                            target = f2_target
                            log.msg(
                                _LOG_PREFIX,
                                f"✓ Found Florence2 model in models/florence2/: {name}",
                            )
                            break

        # GGUF: try recursive filename search before giving up
        if (
            target is not None
            and not target.exists()
            and local_path.lower().endswith(".gguf")
        ):
            filename = Path(local_path).name
            log.msg(_LOG_PREFIX, f"Searching for GGUF file: {filename}...")
            found_path = search_model_file(filename, models_base)
            if found_path:
                target = found_path
                log.msg(_LOG_PREFIX, f"✓ Found at {target}")

        # If target still doesn't exist after all searches, local_path is stale — reset
        # and fall through to repo_id branch so the download goes to the correct location.
        if target is not None and not target.exists() and repo_id:
            log.warning(
                _LOG_PREFIX,
                f"local_path '{local_path}' not found — resetting to repo_id-based download",
            )
            target = None  # Triggers repo_id branch below

    if target is None:
        if not repo_id:
            raise ValueError(
                "Model path not found and no repo_id specified for download"
            )
        model_name = repo_id.split("/")[-1]
        if direct_source and direct_source[2].lower().endswith(".gguf"):
            filename = Path(direct_source[2]).name
            folder_name = Path(filename).stem
            # Download GGUF files directly to models/llm/folder_name/ (not Qwen-VL subfolder)
            target = models_base / folder_name / filename

            # Search for existing file (including in Qwen-VL subfolder for backward compatibility)
            if not target.exists():
                log.msg(_LOG_PREFIX, f"Searching for GGUF file: {filename}...")
                found_path = search_model_file(filename, models_base)
                if found_path:
                    target = found_path
                    log.msg(_LOG_PREFIX, f"✓ Found at {target}")
        elif model_type == ModelType.QWENVL:
            # Download new QwenVL models directly to models/llm/ (not Qwen-VL subfolder)
            target = models_base / model_name
            # But check Qwen-VL folder for existing models (backward compatibility with other ComfyUI nodes)
            if not target.exists() and qwenvl_base.exists():
                qwenvl_target = qwenvl_base / model_name
                if qwenvl_target.exists():
                    target = qwenvl_target
                    log.msg(
                        _LOG_PREFIX,
                        f"✓ Found QwenVL model in Qwen-VL folder: {model_name}",
                    )
        elif model_type == ModelType.FLORENCE2:
            # Florence2: check LLM folder first, then models/florence2/ with alt-name matching
            target = models_base / model_name
            if not target.exists():
                # Build candidate names to search for
                candidate_names = [model_name]
                if model_name.startswith("Florence-2-"):
                    candidate_names.append(model_name[len("Florence-2-") :])
                if model_name.startswith("Florence-2.1-"):
                    candidate_names.append(model_name[len("Florence-2.1-") :])

                # Check LLM folder with alt names
                for name in candidate_names[1:]:  # Skip first (already checked above)
                    llm_target = models_base / name
                    if llm_target.exists():
                        target = llm_target
                        log.msg(
                            _LOG_PREFIX,
                            f"✓ Found Florence2 model in LLM folder: {name}",
                        )
                        break

                # Check models/florence2/ folder (backward compat)
                if not target.exists() and florence2_base.exists():
                    for name in candidate_names:
                        f2_target = florence2_base / name
                        if f2_target.exists():
                            target = f2_target
                            log.msg(
                                _LOG_PREFIX,
                                f"✓ Found Florence2 model in models/florence2/: {name}",
                            )
                            break
        else:
            target = models_base / model_name

    resolved_revision: str | None = None
    revision_is_resolved = False

    def _get_resolved_revision(hf_token: str | None) -> str | None:
        # Resolve at most once and only when a Hugging Face network operation needs it.
        nonlocal resolved_revision, revision_is_resolved
        if is_direct_url or not repo_id:
            return None
        if not revision_is_resolved:
            clean_repo_id = extract_repo_id_from_url(repo_id)
            resolved_revision = resolve_huggingface_revision(
                clean_repo_id,
                requested_revision,
                hf_token,
            )
            revision_is_resolved = True
            log.debug(
                _LOG_PREFIX,
                f"Resolved Hugging Face revision for {clean_repo_id}: {resolved_revision}",
            )
        return resolved_revision

    def _check_completeness(
        path: Path,
        hf_token: str | None,
    ) -> tuple[bool, list[str]]:
        clean_repo_id = extract_repo_id_from_url(repo_id) if repo_id else None
        revision = None
        local_index_exists = path.is_dir() and any(
            (path / index_name).exists()
            for index_name in (
                "model.safetensors.index.json",
                "pytorch_model.bin.index.json",
            )
        )
        if clean_repo_id and not is_direct_url and not local_index_exists:
            try:
                revision = _get_resolved_revision(hf_token)
            except Exception as e:  # noqa: BLE001 -- optional remote metadata boundary
                log.warning(
                    _LOG_PREFIX,
                    f"Could not resolve repository revision for completeness check: {e}",
                )
                clean_repo_id = None
        return check_model_completeness(
            path,
            clean_repo_id,
            hf_token,
            revision=revision,
            required_files=required_snapshot_files,
        )

    def _delete_corrupted_files(
        path: Path, specific_files: Optional[List[Path]] = None
    ):
        # Delete corrupted model files for re-download.
        #
        # Args:
        #     path: Model directory or file path (used as fallback)
        #     specific_files: If provided, only delete these specific files instead of entire folder
        try:
            if specific_files:
                # Delete only the specific corrupted files
                for file_path in specific_files:
                    if file_path.exists():
                        file_path.unlink()
                        log.msg(
                            _LOG_PREFIX, f"Deleted corrupted file: {file_path.name}"
                        )
                    # Also delete any .sha256 hash file
                    sha_file = file_path.parent / f"{file_path.name}.sha256"
                    if sha_file.exists():
                        sha_file.unlink()
            elif path.is_dir():
                # Delete the entire model folder (fallback for full re-download)
                shutil.rmtree(path)
                log.msg(_LOG_PREFIX, f"Deleted corrupted folder: {path}")
            elif path.is_file():
                # Delete the single file
                path.unlink()
                log.msg(_LOG_PREFIX, f"Deleted corrupted file: {path}")
                # Also delete any .sha256 hash file
                sha_file = path.parent / f"{path.name}.sha256"
                if sha_file.exists():
                    sha_file.unlink()
        except Exception as e:
            log.error(_LOG_PREFIX, f"Failed to delete corrupted files: {e}")

    def _download_model(target_path: Path) -> bool:
        # Perform the actual download. Returns True if downloaded.
        # Use one credential policy for metadata and artifact downloads.
        hf_token = resolve_auth_token("huggingface")

        if is_direct_url:
            assert repo_id is not None
            assert direct_source is not None
            log.msg(_LOG_PREFIX, f"Downloading from {repo_id}")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            hf_repo_id, url_revision, remote_filename = direct_source
            selected_revision = requested_revision or url_revision
            resolved_revision = resolve_huggingface_revision(
                hf_repo_id,
                selected_revision,
                hf_token,
            )
            expected_hash = expected_sha256.get(remote_filename)
            try:
                max_attempts = max(1, int(max_retries) + 1)
            except (TypeError, ValueError):
                max_attempts = 3
            _download_verified_target_file(
                hf_repo_id,
                remote_filename,
                target_path,
                "huggingface",
                hf_token,
                _LOG_PREFIX,
                Path(remote_filename).name,
                max_attempts,
                force_download=False,
                revision=resolved_revision,
                requested_revision=selected_revision,
                expected_sha256=expected_hash,
                manifest_root=target_path.parent,
                digest_source="registry" if expected_hash else None,
            )
            log.msg(_LOG_PREFIX, f"✓ Downloaded to {target_path}")
            return True
        elif repo_id:
            log.msg(_LOG_PREFIX, f"Downloading {repo_id} to {target_path}")
            revision = _get_resolved_revision(hf_token)
            if hf_token:
                log.debug(_LOG_PREFIX, "Using HF token for authenticated download")
            if revision is None:
                raise RuntimeError(
                    f"Could not resolve an immutable revision for {repo_id}"
                )
            _download_huggingface_repository_files(
                extract_repo_id_from_url(repo_id),
                target_path,
                hf_token,
                revision,
                required_files=required_snapshot_files,
            )
            return True
        else:
            raise ValueError(f"Model path not found: {target_path}")

    def _get_hf_token() -> Optional[str]:
        # Get the effective Hugging Face credential.
        return resolve_auth_token("huggingface")

    recorded_provenance_revision = None
    if target.exists() and repo_id and not is_direct_url:
        recorded_provenance_revision = _matching_snapshot_provenance_revision(
            target,
            extract_repo_id_from_url(repo_id),
            requested_revision,
            expected_sha256,
        )
        if recorded_provenance_revision:
            log.debug(
                _LOG_PREFIX,
                f"Using recorded immutable revision for local model: "
                f"{recorded_provenance_revision}",
            )

    # Download with retry logic
    downloaded = False
    repository_downloaded = False
    verification_failed = False
    last_corrupted_files = []  # Track corrupted files for selective re-download

    for attempt in range(max_retries + 1):  # +1 because first attempt is not a "retry"
        # Download if target doesn't exist
        if not target.exists():
            try:
                downloaded = _download_model(target)
                repository_downloaded = bool(downloaded and not is_direct_url)
            except Exception as e:
                # Download threw an exception - check if partial files were created
                if target.exists():
                    log.warning(
                        _LOG_PREFIX,
                        f"Download failed (attempt {attempt + 1}/{max_retries + 1}): {e}",
                    )
                    # Keep completed files so a retry only fetches what is missing.
                    hf_token = _get_hf_token()
                    is_complete, missing = _check_completeness(target, hf_token)
                    if not is_complete:
                        log.warning(
                            _LOG_PREFIX,
                            f"Incomplete download detected ({len(missing)} files missing); "
                            "keeping completed files for retry",
                        )
                    if attempt < max_retries:
                        continue
                    raise
                else:
                    if attempt < max_retries:
                        log.warning(
                            _LOG_PREFIX,
                            f"Download failed (attempt {attempt + 1}/{max_retries + 1}): {e}",
                        )
                        continue
                    raise
        elif target.exists() and not downloaded:
            # Target exists but wasn't downloaded in this session - verify completeness
            hf_token = _get_hf_token()
            is_complete, missing = _check_completeness(target, hf_token)
            if not is_complete and missing:
                log.msg(
                    _LOG_PREFIX,
                    f"Existing model folder is incomplete: {len(missing)} file(s) missing",
                )
                clean_repo_id = extract_repo_id_from_url(repo_id) if repo_id else None
                revision = (
                    _get_resolved_revision(hf_token) if not is_direct_url else None
                )
                if clean_repo_id and not is_direct_url and download_missing_files(
                    target,
                    missing,
                    clean_repo_id,
                    hf_token,
                    revision=revision,
                    requested_revision=requested_revision,
                    expected_sha256=expected_sha256,
                ):
                    downloaded = True  # Mark as downloaded for verification
                    repository_downloaded = True
                else:
                    log.warning(_LOG_PREFIX, "Could not download missing files")
                    if attempt < max_retries:
                        continue
                    raise RuntimeError(
                        f"Model incomplete and could not download missing files: {', '.join(missing)}"
                    )
            else:
                # Model is complete, no need to download
                downloaded = True  # Trigger verification

        # Verify integrity after download
        if downloaded and target.exists():
            critical_files = _critical_model_files(target)
            needs_artifact_provenance_refresh = any(
                sidecar.state != "current" or sidecar.expected_hash is None
                for sidecar in (
                    _read_hash_sidecar(artifact_path)
                    for artifact_path in critical_files
                )
            )
            verification_token = _get_hf_token()
            verification_revision = recorded_provenance_revision
            if (
                verification_revision is None
                and repo_id
                and not is_direct_url
                and (
                    repository_downloaded
                    or revision_is_resolved
                    or requested_revision is not None
                )
            ):
                verification_revision = _get_resolved_revision(verification_token)
            verification_hashes = dict(expected_sha256)
            if verification_revision and (
                repository_downloaded
                or recorded_provenance_revision is None
                or needs_artifact_provenance_refresh
            ):
                clean_repo_id = extract_repo_id_from_url(repo_id or "")
                for artifact_path in critical_files:
                    filename = (
                        artifact_path.relative_to(target).as_posix()
                        if target.is_dir()
                        else artifact_path.name
                    )
                    if filename not in verification_hashes:
                        upstream_hash = get_hf_file_hash(
                            clean_repo_id,
                            filename,
                            verification_token,
                            revision=verification_revision,
                        )
                        if upstream_hash:
                            verification_hashes[filename] = upstream_hash
            # Use return_details=True to get list of corrupted files
            verification_result = verify_model_integrity(
                target,
                extract_repo_id_from_url(repo_id or ""),
                return_details=True,
                revision=verification_revision,
                expected_sha256_map=verification_hashes,
            )

            # Unpack verification_result (either VerificationResult or bool)
            if isinstance(verification_result, VerificationResult):
                success = verification_result.success
                corrupted_files = verification_result.corrupted_files
            else:
                success = verification_result
                corrupted_files = []

            if success:
                # Verification passed
                if verification_revision and (
                    repository_downloaded
                    or recorded_provenance_revision is None
                    or needs_artifact_provenance_refresh
                ):
                    _record_snapshot_provenance(
                        target,
                        extract_repo_id_from_url(repo_id or ""),
                        requested_revision,
                        verification_revision,
                        expected_sha256,
                    )
                verification_failed = False
                break
            else:
                # Verification failed
                verification_failed = True
                last_corrupted_files = corrupted_files

                if attempt < max_retries:
                    # Try selective re-download of only corrupted files (if we have a valid repo_id)
                    clean_repo_id = extract_repo_id_from_url(repo_id or "")

                    if last_corrupted_files and clean_repo_id and not is_direct_url:
                        # Selective re-download: only re-download corrupted files
                        log.msg(
                            _LOG_PREFIX,
                            f"⚠ {len(last_corrupted_files)} file(s) failed verification (attempt {attempt + 1}/{max_retries + 1})",
                        )
                        log.msg(
                            _LOG_PREFIX,
                            f"Attempting selective re-download of corrupted files only...",
                        )

                        hf_token = _get_hf_token()
                        if redownload_corrupted_files(
                            last_corrupted_files,
                            clean_repo_id,
                            target,
                            hf_token,
                            revision=verification_revision,
                            requested_revision=requested_revision,
                            expected_sha256=expected_sha256,
                        ):
                            # Files were re-downloaded, continue to next verification attempt
                            # Don't set downloaded=False since the folder still exists
                            continue
                        else:
                            # Selective re-download failed, fall back to full re-download
                            log.warning(
                                _LOG_PREFIX,
                                "Selective re-download failed, falling back to full re-download...",
                            )
                            _delete_corrupted_files(target)
                            downloaded = False
                    else:
                        # No repo_id or direct URL - delete and re-download everything
                        log.warning(
                            _LOG_PREFIX,
                            f"⚠ Hash verification failed (attempt {attempt + 1}/{max_retries + 1}), will retry download...",
                        )
                        _delete_corrupted_files(target)
                        downloaded = False  # Reset to trigger re-download
                else:
                    log.error(
                        _LOG_PREFIX,
                        f"✗ Hash verification failed after {max_retries + 1} attempts",
                    )
                    # Delete corrupted files so next restart will trigger fresh download
                    if last_corrupted_files:
                        log.msg(
                            _LOG_PREFIX,
                            "Deleting corrupted files to allow fresh download on restart...",
                        )
                        _delete_corrupted_files(target, last_corrupted_files)
        else:
            # downloaded is False and target doesn't exist - shouldn't happen, but break to avoid infinite loop
            if not target.exists():
                raise RuntimeError(
                    f"Model download failed: target does not exist after download attempt: {target}"
                )
            # Target exists but downloaded is False - this means completeness check didn't trigger download
            # This is a valid state for pre-existing complete models that don't need verification
            break

    # Final check - raise error if verification still failed
    if verification_failed:
        corrupted_names = (
            [f.name for f in last_corrupted_files] if last_corrupted_files else []
        )
        raise RuntimeError(
            f"Model verification failed for {target} after {max_retries + 1} attempts.\n"
            f"Corrupted files: {', '.join(corrupted_names) if corrupted_names else 'unknown'}\n"
            f"The download may be corrupted. Please check your network connection and try again."
        )

    # Update template with local path
    # Prefer relative path to LLM folder, but also support models in other locations (e.g., models/florence2/)
    current_local_path = None

    # First try: relative to LLM folder (models_base)
    try:
        relative_path = target.relative_to(models_base)
        current_local_path = relative_path.as_posix()
        if target.is_dir() and not current_local_path.endswith("/"):
            current_local_path += "/"
    except ValueError:
        pass

    # Second try: relative to ComfyUI models folder (e.g., "florence2/model_name/")
    if current_local_path is None:
        try:
            relative_path = target.relative_to(Path(folder_paths.models_dir))
            current_local_path = relative_path.as_posix()
            if target.is_dir() and not current_local_path.endswith("/"):
                current_local_path += "/"
        except ValueError:
            # Model is not under models_dir - don't update local_path
            pass

    return (str(target), str(target.parent), repo_id or "")


# ============================================================================
# Model Maintenance
# ============================================================================


def _registered_model_verification_paths(
    entry: dict[str, Any],
    quantization: str | None = None,
) -> list[Path]:
    # Resolve only already-local artifacts selected by one registry entry.
    backend = entry.get("backend", "")
    repo_id = entry.get("repo_id", "")
    name = entry.get("name", "")
    llm_base = get_llm_models_path().resolve()

    if backend == "ollama":
        raise ValueError(
            "Ollama manages its own content-addressed model store; "
            "forced file verification is unavailable for this backend"
        )

    if backend == "yolo":
        from .backend_yolo import resolve_yolo_model_path

        filename = entry.get("filename", f"{name}.pt")
        if not isinstance(filename, str) or not filename:
            raise ValueError("YOLO registry entry has no valid filename")
        model_path = resolve_yolo_model_path(filename)
        if not model_path:
            raise FileNotFoundError(f"YOLO model file not found: {filename}")
        return [Path(model_path).resolve()]

    def safe_child(base: Path, child_name: object) -> Path | None:
        if not isinstance(child_name, str) or not child_name:
            return None
        candidate = (base / child_name).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            raise ValueError(
                "Registry model path escapes the configured model folder"
            ) from None
        return candidate

    explicit_local_path = entry.get("local_path")
    if isinstance(explicit_local_path, str) and explicit_local_path.strip():
        explicit_path = safe_child(llm_base, explicit_local_path)
        if explicit_path is None or not explicit_path.exists():
            raise FileNotFoundError(
                "The registry entry's explicit local_path was not found"
            )
        if backend in ("gguf", "llamacpp") and explicit_path.is_file():
            paths = [explicit_path]
            mmproj = entry.get("mmproj")
            if isinstance(mmproj, str) and mmproj:
                mmproj_path = safe_child(explicit_path.parent, mmproj)
                if mmproj_path is None or not mmproj_path.is_file():
                    raise FileNotFoundError(
                        f"GGUF vision projector not found: {mmproj}"
                    )
                paths.append(mmproj_path)
            return paths
        if explicit_path.is_dir() and _critical_model_files(explicit_path):
            return [explicit_path]
        raise FileNotFoundError(
            "The registry entry's explicit local_path has no verifiable model artifacts"
        )

    repo_folder = repo_id.rsplit("/", 1)[-1] if repo_id else ""
    folder_names = []
    for folder_name in (repo_folder, name):
        if folder_name and folder_name not in folder_names:
            folder_names.append(folder_name)

    if backend in ("gguf", "llamacpp"):
        quantizations = entry.get("quantizations", [])
        if not isinstance(quantizations, list):
            quantizations = []
        selected_quantization = (
            quantization.strip() if isinstance(quantization, str) else ""
        )
        if not selected_quantization:
            if len(quantizations) == 1:
                selected_quantization = quantizations[0]
            elif len(quantizations) > 1:
                raise ValueError("quantization is required for this GGUF model")
        if selected_quantization and not isinstance(selected_quantization, str):
            raise TypeError("GGUF quantization names must be strings")

        file_pattern = entry.get("file_pattern", "")
        filenames = []
        if isinstance(file_pattern, str) and file_pattern and selected_quantization:
            filenames.append(file_pattern.replace("{quant}", selected_quantization))
        if selected_quantization:
            filenames.extend(
                [
                    f"{name}-{selected_quantization}.gguf",
                    f"{name}.{selected_quantization}.gguf",
                ]
            )

        model_file = None
        model_folder = None
        for folder_name in folder_names:
            folder = safe_child(llm_base, folder_name)
            if folder is None or not folder.is_dir():
                continue
            for filename in filenames:
                candidate = safe_child(folder, filename)
                if candidate is not None and candidate.is_file():
                    model_file = candidate
                    model_folder = folder
                    break
            if model_file is None:
                candidates = sorted(folder.glob("*.gguf"))
                candidates = [
                    candidate
                    for candidate in candidates
                    if "mmproj" not in candidate.name.lower()
                    and (
                        not selected_quantization
                        or selected_quantization.lower() in candidate.name.lower()
                    )
                ]
                if len(candidates) == 1:
                    model_file = candidates[0].resolve()
                    model_folder = folder
            if model_file is not None:
                break
        if model_file is None or model_folder is None:
            raise FileNotFoundError("Selected GGUF model file was not found locally")

        paths = [model_file]
        mmproj = entry.get("mmproj")
        if isinstance(mmproj, str) and mmproj:
            mmproj_path = safe_child(model_folder, mmproj)
            if mmproj_path is None or not mmproj_path.is_file():
                raise FileNotFoundError(f"GGUF vision projector not found: {mmproj}")
            paths.append(mmproj_path)
        return paths

    candidates = []
    for folder_name in folder_names:
        candidate = safe_child(llm_base, folder_name)
        if candidate is not None:
            candidates.append(candidate)
    if entry.get("family") == "Florence":
        import folder_paths  # type: ignore

        florence_base = (Path(folder_paths.models_dir) / "florence2").resolve()
        candidate = safe_child(florence_base, name)
        if candidate is not None:
            candidates.append(candidate)

    for candidate in candidates:
        if candidate.is_dir() and _critical_model_files(candidate):
            return [candidate]
    raise FileNotFoundError("Model files were not found in the configured local folders")


def force_verify_registered_model(
    display_name: str,
    quantization: str | None = None,
) -> dict[str, Any]:
    # Rehash every load-bearing local artifact for a registry selection.
    from .model_registry import get_model_entry

    entry = get_model_entry(display_name)
    if entry is None:
        return {
            "success": False,
            "error": f"Model not found in registry: {display_name}",
        }

    try:
        paths = _registered_model_verification_paths(entry, quantization)
        repo_id = entry.get("repo_id")
        revision = entry.get("revision")
        expected_hashes = _validated_expected_sha256_map(entry)
        verified_files = []
        verified_count = 0
        baseline_count = 0

        for path in paths:
            if path.is_dir():
                critical_files = _critical_model_files(path)
                result = verify_model_integrity(
                    path,
                    repo_id=repo_id,
                    return_details=True,
                    force_verification=True,
                    revision=revision,
                    expected_sha256_map=expected_hashes,
                )
            else:
                critical_files = _critical_model_files(path)
                matching_filenames = [
                    filename
                    for filename in expected_hashes
                    if filename.rsplit("/", 1)[-1] == path.name
                ]
                if len(matching_filenames) > 1:
                    raise ValueError(
                        f"Multiple registry digests match local file {path.name}"
                    )
                hf_filename = (
                    matching_filenames[0] if matching_filenames else path.name
                )
                expected_hash = expected_hashes.get(hf_filename)
                result = verify_model_integrity(
                    path,
                    repo_id=repo_id,
                    hf_filename=hf_filename,
                    return_details=True,
                    force_verification=True,
                    revision=revision,
                    expected_sha256=expected_hash,
                )

            if not isinstance(result, VerificationResult) or not result.success:
                corrupted = (
                    [str(item) for item in result.corrupted_files]
                    if isinstance(result, VerificationResult)
                    else []
                )
                return {
                    "success": False,
                    "error": "Forced model verification failed",
                    "corrupted_files": corrupted,
                }

            missing_baselines = [
                str(file_path)
                for file_path in critical_files
                if _read_hash_sidecar(file_path).state != "current"
            ]
            if missing_baselines:
                return {
                    "success": False,
                    "error": (
                        "No authoritative or previously recorded SHA-256 baseline "
                        "is available for every model artifact"
                    ),
                    "unverified_files": missing_baselines,
                }

            verified_files.extend(str(file_path) for file_path in critical_files)
            verified_count += result.verified_count
            baseline_count += result.skipped_count

        return {
            "success": True,
            "display_name": display_name,
            "verified_files": verified_files,
            "verified_count": verified_count,
            "baseline_count": baseline_count,
        }
    except (OSError, TypeError, ValueError) as error:
        return {"success": False, "error": str(error)}


def delete_model(display_name: str) -> Dict[str, Any]:
    # Delete a model from disk given its registry display name.
    # Returns {"success": bool, "error": str (if failed), "deleted": str (path/id)}.
    #
    # Backend-specific deletion:
    #   transformers/wd14: shutil.rmtree on model folder
    #   gguf/llamacpp: unlink .gguf file + .sha256 sidecar
    #   yolo: unlink .pt file
    #   ollama: docker exec sml-ollama ollama rm <repo_id>
    #   vllm/sglang: local Transformers-compatible model folder
    from .model_registry import (
        get_model_entry,
        invalidate_cache,
        invalidate_yolo_cache,
        sync_yolo_registry,
    )
    from .backend_yolo import resolve_yolo_model_path

    entry = get_model_entry(display_name)
    if entry is None:
        return {
            "success": False,
            "error": f"Model not found in registry: {display_name}",
        }

    backend = entry.get("backend", "")
    repo_id = entry.get("repo_id", "")
    name = entry.get("name", "")

    if backend == "ollama":
        return _delete_ollama_model(repo_id or name)

    # ── YOLO models ───────────────────────────────────────────────────
    if backend == "yolo":
        filename = entry.get("filename", f"{name}.pt")
        full_path = resolve_yolo_model_path(filename)
        if not full_path:
            return {
                "success": False,
                "error": f"YOLO model file not found on disk: {filename}",
            }
        try:
            Path(full_path).unlink()
            log.msg(_LOG_PREFIX, f"Deleted YOLO model: {full_path}")
            sync_yolo_registry()
            invalidate_yolo_cache()
            return {"success": True, "deleted": full_path}
        except OSError as e:
            return {"success": False, "error": f"Failed to delete {full_path}: {e}"}

    # ── Local backends (transformers, gguf/llamacpp, wd14) ────────────
    llm_base = get_llm_models_path()
    import folder_paths  # type: ignore

    explicit_local_path = entry.get("local_path")
    if isinstance(explicit_local_path, str) and explicit_local_path.strip():
        candidate = (llm_base / explicit_local_path).resolve()
        try:
            candidate.relative_to(llm_base.resolve())
        except ValueError:
            return {
                "success": False,
                "error": f"Path traversal blocked: {explicit_local_path}",
            }
        try:
            if candidate.is_dir():
                shutil.rmtree(candidate)
            elif candidate.is_file():
                candidate.unlink()
                sidecar = candidate.with_name(f"{candidate.name}.sha256")
                if sidecar.is_file():
                    sidecar.unlink()
            else:
                candidate = None
        except OSError as error:
            return {
                "success": False,
                "error": f"Failed to delete {explicit_local_path}: {error}",
            }
        if candidate is not None:
            log.msg(_LOG_PREFIX, f"Deleted local registry model: {candidate}")
            _invalidate_model_list_cache()
            invalidate_cache()
            return {"success": True, "deleted": str(candidate)}

    if backend in ("gguf", "llamacpp"):
        deleted = _delete_gguf_model(entry, llm_base)
        if deleted:
            _invalidate_model_list_cache()
            invalidate_cache()
            return {"success": True, "deleted": deleted}
        return {
            "success": False,
            "error": f"GGUF model file not found on disk for: {display_name}",
        }

    # Transformers-compatible or WD14 — delete the model folder
    candidates = []
    if backend == "wd14":
        wd14_name = repo_id.split("/")[-1] if "/" in repo_id else name
        candidates.append(llm_base / wd14_name)
    else:
        # Transformers: check name, repo_id last component, and florence2/
        candidates.append(llm_base / name)
        if "/" in repo_id:
            candidates.append(llm_base / repo_id.split("/")[-1])
        if entry.get("family") == "Florence":
            candidates.append(Path(folder_paths.models_dir) / "florence2" / name)

    for folder in candidates:
        if folder.exists() and folder.is_dir():
            # Security: ensure folder is within expected base directories
            try:
                folder.resolve().relative_to(llm_base.resolve())
            except ValueError:
                try:
                    folder.resolve().relative_to(
                        Path(folder_paths.models_dir).resolve()
                    )
                except ValueError:
                    return {
                        "success": False,
                        "error": f"Path traversal blocked: {folder}",
                    }
            try:
                shutil.rmtree(folder)
                log.msg(_LOG_PREFIX, f"Deleted model folder: {folder}")
                _invalidate_model_list_cache()
                invalidate_cache()
                return {"success": True, "deleted": str(folder)}
            except OSError as e:
                return {"success": False, "error": f"Failed to delete {folder}: {e}"}

    return {
        "success": False,
        "error": f"Model folder not found on disk for: {display_name}",
    }


def _delete_gguf_model(entry: Dict[str, Any], llm_base: Path) -> Optional[str]:
    # Find and delete a GGUF model file. Returns deleted path or None.
    name = entry.get("name", "")
    repo_id = entry.get("repo_id", "")
    file_pattern = entry.get("file_pattern", "")
    repo_folder = repo_id.split("/")[-1] if "/" in repo_id else name

    # Collect possible filenames (all quantizations)
    quantizations = entry.get("quantizations", [])

    # Build candidate folder names
    seen = set()
    folders = []
    for c in [repo_folder, name]:
        if c and c not in seen:
            seen.add(c)
            folders.append(c)

    # Check if the entire folder is a single-model GGUF repo
    for folder_name in folders:
        candidate_dir = llm_base / folder_name
        if not candidate_dir.exists():
            continue
        gguf_files = list(candidate_dir.glob("*.gguf"))
        other_files = [
            f
            for f in candidate_dir.iterdir()
            if f.is_file()
            and not f.name.endswith(".gguf")
            and not f.name.endswith(".sha256")
            and f.name
            not in {
                _PROVENANCE_MANIFEST_NAME,
                f".{_PROVENANCE_MANIFEST_NAME.lstrip('.')}.lock",
                f".{_PROVENANCE_MANIFEST_NAME}.lock",
            }
        ]
        # If folder contains only GGUF/SHA files, delete the whole folder
        if gguf_files and not other_files:
            try:
                shutil.rmtree(candidate_dir)
                log.msg(_LOG_PREFIX, f"Deleted GGUF model folder: {candidate_dir}")
                return str(candidate_dir)
            except OSError:
                pass

    # Fallback: delete individual GGUF files matching this model
    for folder_name in folders:
        candidate_dir = llm_base / folder_name
        if not candidate_dir.exists():
            continue
        for f in candidate_dir.glob("*.gguf"):
            if _gguf_file_matches_model(f.name, name, file_pattern, quantizations):
                sidecar = f.with_suffix(f.suffix + ".sha256")
                try:
                    f.unlink()
                    if sidecar.exists():
                        sidecar.unlink()
                    log.msg(_LOG_PREFIX, f"Deleted GGUF file: {f}")
                    return str(f)
                except OSError:
                    pass

    # Flat file in LLM base
    for f in llm_base.glob("*.gguf"):
        if _gguf_file_matches_model(f.name, name, file_pattern, quantizations):
            sidecar = f.with_suffix(f.suffix + ".sha256")
            try:
                f.unlink()
                if sidecar.exists():
                    sidecar.unlink()
                log.msg(_LOG_PREFIX, f"Deleted GGUF file: {f}")
                return str(f)
            except OSError:
                pass

    return None


def _gguf_file_matches_model(
    filename: str, model_name: str, file_pattern: str, quantizations: list
) -> bool:
    # Check if a GGUF filename belongs to this model entry.
    lower = filename.lower()
    name_lower = model_name.lower()
    if lower.startswith(name_lower):
        return True
    if file_pattern:
        pattern_name = Path(file_pattern).name.lower()
        if "{quant}" in pattern_name:
            prefix, suffix = pattern_name.split("{quant}", 1)
            if lower.startswith(prefix) and lower.endswith(suffix):
                quant_end = len(lower) - len(suffix) if suffix else len(lower)
                candidate_quantization = lower[len(prefix) : quant_end]
                if not quantizations or any(
                    isinstance(value, str)
                    and value.lower() == candidate_quantization
                    for value in quantizations
                ):
                    return True
        elif lower == pattern_name:
            return True
    return False


def _delete_ollama_model(model_id: str) -> Dict[str, Any]:
    # Delete an Ollama model via docker exec (primary) or local filesystem (fallback).
    #
    # Priority:
    #   1. docker exec ollama rm — auto-starts container if needed (handles root-owned files)
    #   2. Local filesystem — parse manifest, delete exclusive blobs (when Docker unavailable)
    import subprocess

    try:
        from .backend_ollama_docker import (
            OLLAMA_CONTAINER_NAME,
            ensure_ollama_running,
            delete_ollama_model_local,
            is_ollama_container_running,
            stop_ollama_container,
        )
    except ImportError:
        return {"success": False, "error": "Ollama backend not available"}

    # Primary — docker exec (auto-start container, no model loading needed)
    was_running = is_ollama_container_running()
    if ensure_ollama_running():
        try:
            proc = subprocess.run(
                ["docker", "exec", OLLAMA_CONTAINER_NAME, "ollama", "rm", model_id],
                capture_output=True,
                timeout=30,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode == 0:
                log.msg(_LOG_PREFIX, f"Deleted Ollama model via container: {model_id}")
                result = {"success": True, "deleted": model_id}
            else:
                error_msg = (
                    proc.stderr.strip() or proc.stdout.strip() or "Unknown error"
                )
                result = {"success": False, "error": f"ollama rm failed: {error_msg}"}
        except subprocess.TimeoutExpired:
            result = {"success": False, "error": "ollama rm timed out (30s)"}
        except Exception as e:
            result = {"success": False, "error": f"Failed to run ollama rm: {e}"}

        # Stop container if we started it just for deletion
        if not was_running:
            stop_ollama_container()

        return result

    # Fallback — local filesystem deletion (Docker unavailable)
    return delete_ollama_model_local(model_id)


def _invalidate_model_list_cache():
    # Clear the model list cache so next call rescans disk.
    global _model_list_cache, _model_list_cache_time
    global _mmproj_list_cache, _mmproj_list_cache_time
    _model_list_cache.clear()
    _model_list_cache_time = 0.0
    _mmproj_list_cache.clear()
    _mmproj_list_cache_time = 0.0
