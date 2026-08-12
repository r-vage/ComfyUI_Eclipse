# Persistent single-transfer queue for the standalone Download Manager.

from __future__ import annotations

import copy
import hashlib
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from huggingface_hub import HfApi, hf_hub_url  # type: ignore

from ..civitai_client import (
    DownloadCancelled,
    cancel_active_download,
    download_file,
    get_model_version,
    release_download_id,
    reserve_download_id,
)
from ..config_store import get_config_value
from ..json_store import JsonStoreError, read_json_object, write_json_object
from ..logger import log
from ..model_loader.acquisition import discard_partial_download
from ..model_loader.endpoints import prepare_download_destination
from ..model_loader.integrity import sha256_for, write_expected
from ..sml.credentials import resolve_auth_token
from .providers import extension_compatible, resolve_destination_root

_LOG_PREFIX = "DownloadManager"
_QUEUE_SCHEMA_VERSION = 1
_BUNDLE_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_GIT_OID_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_ACTIVE_STATES = {"preparing", "transferring", "hashing", "verifying", "locking", "promoting"}
_NON_ABORTABLE_STATES = {"hashing", "verifying", "locking", "promoting"}
_FINAL_STATES = {"completed", "failed", "cancelled"}
_STARTABLE_STATES = {"ready", "failed", "cancelled"}
_REMOVABLE_STATES = {"ready", "completed", "failed", "cancelled"}
_CONFLICT_POLICIES = {"skip", "overwrite", "rename"}
_ROOT = Path(__file__).resolve().parents[2]
_STATE_DIR = _ROOT / "download_manager"
_QUEUE_PATH = _STATE_DIR / "queue.json"
_SENSITIVE_KEYS = {
    "authorization",
    "headers",
    "token",
    "api_key",
    "download_url",
    "signed_url",
    "url",
}


def _ensure_state_directory() -> None:
    if _STATE_DIR.is_symlink():
        raise JsonStoreError("Download Manager state directory may not be a symlink")
    try:
        _STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as error:
        raise JsonStoreError("Could not create the Download Manager state directory") from error
    if _STATE_DIR.is_symlink() or not _STATE_DIR.is_dir():
        raise JsonStoreError("Download Manager state path must be a real directory")
    try:
        _STATE_DIR.chmod(0o700)
    except OSError as error:
        raise JsonStoreError("Could not secure the Download Manager state directory") from error


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_error(error: BaseException) -> str:
    if isinstance(error, DownloadCancelled):
        return "Transfer cancelled"
    if isinstance(error, BlockingIOError):
        return "Promotion is waiting for the ComfyUI prompt queue to become idle"
    if isinstance(error, FileExistsError):
        return "Destination already exists"
    if isinstance(error, PermissionError):
        return "Destination permission denied"
    if isinstance(error, OSError):
        return "Local storage operation failed"
    if isinstance(error, (TypeError, ValueError)):
        message = str(error)
        if len(message) <= 180 and not any(marker in message for marker in ("/tmp/", "\\", "Bearer ")):
            return message
    return f"{type(error).__name__}: operation failed"


def _without_sensitive_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_sensitive_keys(item)
            for key, item in value.items()
            if isinstance(key, str) and key.casefold() not in _SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [_without_sensitive_keys(item) for item in value]
    return value


def _safe_relative_path(subfolder: str, filename: str) -> str:
    if not isinstance(subfolder, str) or not isinstance(filename, str):
        raise TypeError("Destination path fields must be strings")
    if not filename or len(filename.encode("utf-8")) > 240 or "\x00" in filename:
        raise ValueError("Invalid local filename")
    if "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise ValueError("Local filename must be a basename")
    normalized_folder = subfolder.strip().replace("\\", "/").strip("/")
    if normalized_folder:
        folder = PurePosixPath(normalized_folder)
        if folder.is_absolute() or any(part in {"", ".", ".."} for part in folder.parts):
            raise ValueError("Unsafe destination subfolder")
        return f"{folder.as_posix()}/{filename}"
    return filename


def _expected_digest(identity: dict[str, Any]) -> dict[str, str]:
    provider = identity.get("provider")
    if provider == "civitai":
        value = identity.get("sha256")
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            raise ValueError("CivitAI identity requires an authoritative SHA-256")
        return {"algorithm": "sha256", "value": value.casefold(), "source": "civitai"}
    if provider == "huggingface":
        algorithm = identity.get("digest_algorithm")
        value = identity.get("digest")
        valid = (
            algorithm == "sha256"
            and isinstance(value, str)
            and _SHA256_RE.fullmatch(value)
        ) or (
            algorithm == "git-sha1"
            and isinstance(value, str)
            and _GIT_OID_RE.fullmatch(value)
        )
        if not valid:
            raise ValueError("Hugging Face identity requires a provider digest")
        source = "huggingface-lfs" if algorithm == "sha256" else "huggingface-git"
        return {"algorithm": algorithm, "value": value.casefold(), "source": source}
    raise ValueError("Unknown provider identity")


def _clean_provider_identity(identity: dict[str, Any]) -> dict[str, Any]:
    provider = identity.get("provider")
    if provider == "civitai":
        for key in ("model_id", "version_id", "file_id"):
            value = identity.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("CivitAI provider identity is incomplete")
        filename = identity.get("filename")
        if not isinstance(filename, str) or not filename or len(filename) > 512:
            raise ValueError("CivitAI provider filename is invalid")
        cleaned = {
            "provider": "civitai",
            "model_id": identity["model_id"],
            "version_id": identity["version_id"],
            "file_id": identity["file_id"],
            "sha256": identity.get("sha256"),
            "size": identity.get("size"),
            "filename": filename,
            "air": identity.get("air"),
        }
    elif provider == "huggingface":
        repo_id = identity.get("repo_id")
        commit = identity.get("commit")
        path = identity.get("path")
        if (
            not isinstance(repo_id, str)
            or len(repo_id) > 256
            or repo_id.count("/") != 1
            or not isinstance(commit, str)
            or not _GIT_OID_RE.fullmatch(commit)
            or not isinstance(path, str)
            or not path
            or len(path) > 1024
            or any(part in {"", ".", ".."} for part in PurePosixPath(path).parts)
        ):
            raise ValueError("Hugging Face provider identity is incomplete")
        cleaned = {
            "provider": "huggingface",
            "repo_id": repo_id,
            "commit": commit.casefold(),
            "path": path,
            "digest_algorithm": identity.get("digest_algorithm"),
            "digest": identity.get("digest"),
            "size": identity.get("size"),
        }
    else:
        raise ValueError("Unknown provider identity")
    size = cleaned.get("size")
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
        raise ValueError("Provider file size is invalid")
    _expected_digest(cleaned)
    return cleaned


def _git_blob_digest(path: Path) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb", buffering=0) as file_handle:
        while chunk := file_handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class DownloadQueueManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._jobs: list[dict[str, Any]] = []
        self._load_error: str | None = None
        self._worker: threading.Thread | None = None
        self._last_progress_write: dict[str, tuple[float, int, str]] = {}
        self._load()

    def _load(self) -> None:
        try:
            _ensure_state_directory()
            payload = read_json_object(_QUEUE_PATH, default={"version": _QUEUE_SCHEMA_VERSION, "jobs": []})
        except (JsonStoreError, OSError) as error:
            self._load_error = f"Queue state is unreadable ({type(error).__name__})"
            log.error(_LOG_PREFIX, self._load_error)
            return
        jobs = payload.get("jobs", [])
        if payload.get("version") not in {None, _QUEUE_SCHEMA_VERSION} or not isinstance(jobs, list):
            self._load_error = "Queue state uses an unsupported schema"
            log.error(_LOG_PREFIX, self._load_error)
            return
        changed = False
        for item in jobs:
            if not isinstance(item, dict) or not isinstance(item.get("uuid"), str):
                continue
            job = _without_sensitive_keys(copy.deepcopy(item))
            identity = job.get("provider_identity")
            if not isinstance(identity, dict):
                continue
            try:
                job["provider_identity"] = _clean_provider_identity(identity)
            except (TypeError, ValueError):
                job["state"] = "failed"
                job["error"] = "Persisted provider identity is invalid"
                job["completed_at"] = _utc_now()
                changed = True
            if job.get("state") in _ACTIVE_STATES:
                job["state"] = "queued"
                job["resume_pending"] = True
                job["interrupted_at"] = _utc_now()
                job["error"] = None
                changed = True
            self._jobs.append(job)
        if changed:
            self._persist_locked()

    def start(self) -> None:
        with self._lock:
            if self._worker is not None or self._load_error:
                return
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="eclipse-download-manager",
                daemon=True,
            )
            self._worker.start()
            self._wake.set()

    def _ensure_available(self) -> None:
        if self._load_error:
            raise RuntimeError(self._load_error)

    def _persist_locked(self) -> None:
        self._ensure_available()
        _ensure_state_directory()
        write_json_object(
            _QUEUE_PATH,
            {"version": _QUEUE_SCHEMA_VERSION, "jobs": self._jobs},
            private=True,
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "success": self._load_error is None,
                "schema_version": _QUEUE_SCHEMA_VERSION,
                "error": self._load_error,
                "jobs": [self._public_job(job) for job in self._jobs],
            }

    def _public_job(self, job: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(job)
        if result.get("state") in {"failed", "cancelled"}:
            try:
                has_partial, partial_bytes = self._partial_status(result)
            except (KeyError, OSError, TypeError, ValueError):
                has_partial, partial_bytes = False, 0
            result["has_partial"] = has_partial
            result["partial_bytes"] = partial_bytes
        return result

    def enqueue(
        self,
        row: dict[str, Any],
        assignment: dict[str, Any],
        *,
        persist: bool = True,
        publish: bool = True,
    ) -> dict[str, Any]:
        self._ensure_available()
        if row.get("supported") is not True:
            raise ValueError("Unsupported or unverifiable files cannot be queued")
        category = assignment.get("category")
        root_id = assignment.get("root_id")
        if not isinstance(category, str) or category not in row.get("compatible_categories", []):
            raise ValueError("Destination category is incompatible with the file format")
        if row.get("category_ambiguous") and assignment.get("confirm_ambiguous") is not True:
            raise ValueError("Ambiguous destination category requires confirmation")
        root, root_index = resolve_destination_root(category, root_id)
        remote_path = row.get("remote_path")
        if not isinstance(remote_path, str):
            raise TypeError("Remote filename is invalid")
        filename = (
            assignment.get("filename")
            or row.get("suggested_filename")
            or Path(remote_path).name
        )
        subfolder = assignment.get("subfolder") or ""
        relative_path = _safe_relative_path(subfolder, filename)
        if Path(filename).suffix.casefold() != Path(remote_path).suffix.casefold():
            raise ValueError("Local filename must preserve the provider file extension")
        if not extension_compatible(Path(filename).suffix, category):
            raise ValueError("Local filename is incompatible with the destination category")
        prepare_download_destination(
            root,
            requested_filename=relative_path,
            resolved_filename=Path(remote_path).name,
            create_parents=True,
        )
        conflict_policy = assignment.get("conflict_policy", "skip")
        if conflict_policy not in _CONFLICT_POLICIES:
            raise ValueError("Invalid conflict policy")
        raw_identity = row.get("identity")
        if not isinstance(raw_identity, dict):
            raise TypeError("Provider identity is invalid")
        identity = _clean_provider_identity(raw_identity)
        digest = _expected_digest(identity)
        inspected_digest = row.get("expected_digest")
        if (
            isinstance(inspected_digest, dict)
            and inspected_digest.get("algorithm") == digest["algorithm"]
            and inspected_digest.get("value") == digest["value"]
            and isinstance(inspected_digest.get("source"), str)
        ):
            digest["source"] = inspected_digest["source"]
        now = _utc_now()
        job = {
            "uuid": str(uuid.uuid4()),
            "schema_version": _QUEUE_SCHEMA_VERSION,
            "state": "ready",
            "provider_identity": identity,
            "provider_type": row.get("provider_type"),
            "format": row.get("format"),
            "precision": row.get("precision"),
            "air": row.get("air"),
            "destination": {
                "category": category,
                "root_id": root_id,
                "root_index": root_index,
                "subfolder": subfolder,
                "filename": filename,
                "relative_path": relative_path,
            },
            "expected_digest": digest,
            "local_sha256": None,
            "provider_verified": False,
            "size": identity.get("size"),
            "conflict_policy": conflict_policy,
            "progress": {"phase": "ready", "bytes": 0, "total": identity.get("size") or 0, "percent": 0},
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
            "resume_pending": False,
            "error": None,
        }
        with self._lock:
            self._jobs.append(job)
            if persist:
                self._persist_locked()
        if publish:
            self._emit(job)
        return copy.deepcopy(job)

    def enqueue_many(
        self,
        pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        jobs = []
        with self._lock:
            original_count = len(self._jobs)
            try:
                for row, assignment in pairs:
                    jobs.append(
                        self.enqueue(
                            row,
                            assignment,
                            persist=False,
                            publish=False,
                        )
                    )
                self._persist_locked()
            except Exception:
                del self._jobs[original_count:]
                raise
        for job in jobs:
            self._emit(job)
        return jobs

    def bundle_item_pair(
        self, item: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        identity = item.get("provider_identity")
        destination = item.get("destination")
        if not isinstance(identity, dict) or not isinstance(destination, dict):
            raise TypeError("Bundle item is missing identity or destination")
        digest = _expected_digest(identity)
        row = {
            "supported": True,
            "identity": identity,
            "remote_path": identity.get("filename") or identity.get("path"),
            "provider_type": item.get("provider_type"),
            "format": item.get("format"),
            "precision": item.get("precision"),
            "air": item.get("air"),
            "expected_digest": digest,
            "compatible_categories": [destination.get("category")],
            "category_ambiguous": False,
        }
        assignment = {
            "category": destination.get("category"),
            "root_id": destination.get("root_id")
            or f"{destination.get('category')}:{destination.get('root_index', 0)}",
            "subfolder": destination.get("subfolder", ""),
            "filename": destination.get("filename"),
            "conflict_policy": item.get("conflict_policy", "skip"),
            "confirm_ambiguous": True,
        }
        return row, assignment

    def enqueue_bundle_item(self, item: dict[str, Any]) -> dict[str, Any]:
        row, assignment = self.bundle_item_pair(item)
        return self.enqueue(row, assignment)

    def start_jobs(self, job_ids: list[str]) -> list[dict[str, Any]]:
        with self._lock:
            jobs = self._selected_jobs_locked(job_ids)
            invalid = [job for job in jobs if job.get("state") not in _STARTABLE_STATES]
            if invalid:
                raise ValueError("Only ready, failed, or cancelled jobs can be started")
            now = _utc_now()
            started = []
            for job in jobs:
                has_partial, partial_bytes = self._partial_status(job)
                total = job.get("size") or 0
                job["state"] = "queued"
                job["error"] = None
                job["completed_at"] = None
                job["updated_at"] = now
                job["resume_pending"] = has_partial
                job["progress"] = {
                    "phase": "queued",
                    "bytes": partial_bytes,
                    "total": total,
                    "percent": int(partial_bytes * 100 / total) if total else 0,
                }
                started.append(copy.deepcopy(job))
            self._persist_locked()
        for job in started:
            self._emit(job)
        self._wake.set()
        return started

    def remove_jobs(self, job_ids: list[str]) -> list[str]:
        with self._lock:
            jobs = self._selected_jobs_locked(job_ids)
            invalid = [job for job in jobs if job.get("state") not in _REMOVABLE_STATES]
            if invalid:
                raise ValueError("Queued or active jobs cannot be removed")
            if any(
                job.get("state") in {"failed", "cancelled"}
                and self._partial_status(job)[0]
                for job in jobs
            ):
                raise ValueError("Delete retained partial files before removing their queue entries")
            removed = [job["uuid"] for job in jobs]
            removed_set = set(removed)
            self._jobs = [
                job for job in self._jobs if job.get("uuid") not in removed_set
            ]
            for job_uuid in removed:
                self._last_progress_write.pop(job_uuid, None)
            self._persist_locked()
        return removed

    def cancel(self, job_uuid: str) -> str:
        with self._lock:
            job = self._job_locked(job_uuid)
            state = job.get("state")
            if state == "queued":
                job["state"] = "cancelled"
                job["error"] = "Cancelled before transfer"
                job["updated_at"] = _utc_now()
                job["completed_at"] = job["updated_at"]
                self._persist_locked()
                self._emit(job)
                return "cancelled"
            if state == "transferring":
                result = cancel_active_download(job_uuid)
                return "cancelling" if result == "cancelling" else result
            if state in _NON_ABORTABLE_STATES:
                raise BlockingIOError("Hashing, verification, locking, and promotion cannot be cancelled")
            raise ValueError("Job is not cancellable")

    def retry(self, job_uuid: str) -> dict[str, Any]:
        with self._lock:
            job = self._job_locked(job_uuid)
            if job.get("state") not in {"failed", "cancelled"}:
                raise ValueError("Only failed or cancelled jobs can be retried")
            has_partial, partial_bytes = self._partial_status(job)
            total = job.get("size") or 0
            job["state"] = "queued"
            job["error"] = None
            job["completed_at"] = None
            job["updated_at"] = _utc_now()
            job["resume_pending"] = has_partial
            job["progress"] = {
                "phase": "queued",
                "bytes": partial_bytes,
                "total": total,
                "percent": int(partial_bytes * 100 / total) if total else 0,
            }
            self._persist_locked()
            result = copy.deepcopy(job)
        self._emit(result)
        self._wake.set()
        return result

    def discard_partial(self, job_uuid: str) -> int:
        with self._lock:
            job = self._job_locked(job_uuid)
            if job.get("state") not in {"failed", "cancelled"}:
                raise ValueError("Only failed or cancelled jobs can discard partial data")
            target, _relative_path = self._destination(job)
            removed_bytes = discard_partial_download(target)
            total = job.get("size") or 0
            job["resume_pending"] = False
            job["progress"] = {
                "phase": job["state"],
                "bytes": 0,
                "total": total,
                "percent": 0,
            }
            job["updated_at"] = _utc_now()
            self._persist_locked()
            result = copy.deepcopy(job)
        self._emit(result)
        return removed_bytes

    def _job_locked(self, job_uuid: str) -> dict[str, Any]:
        if not isinstance(job_uuid, str):
            raise TypeError("Job UUID must be a string")
        for job in self._jobs:
            if job.get("uuid") == job_uuid:
                return job
        raise KeyError("Download job was not found")

    def _selected_jobs_locked(self, job_ids: list[str]) -> list[dict[str, Any]]:
        if not isinstance(job_ids, list) or not job_ids:
            raise ValueError("Select at least one download job")
        if not all(isinstance(job_uuid, str) for job_uuid in job_ids):
            raise TypeError("Job UUIDs must be strings")
        unique_ids = list(dict.fromkeys(job_ids))
        jobs = [self._job_locked(job_uuid) for job_uuid in unique_ids]
        return jobs

    def _next_queued(self) -> str | None:
        with self._lock:
            for job in self._jobs:
                if job.get("state") == "queued":
                    return job["uuid"]
        return None

    def _worker_loop(self) -> None:
        while True:
            job_uuid = self._next_queued()
            if job_uuid is None:
                self._wake.wait(30)
                self._wake.clear()
                continue
            try:
                self._run_job(job_uuid)
            except Exception as error:  # noqa: BLE001 - worker must survive one failed job
                log.error(_LOG_PREFIX, f"Worker recovered from {type(error).__name__}")
                self._finish_failed(job_uuid, error)

    def _update(self, job_uuid: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            job = self._job_locked(job_uuid)
            job.update(changes)
            job["updated_at"] = _utc_now()
            self._persist_locked()
            result = copy.deepcopy(job)
        self._emit(result)
        return result

    def _phase(self, job_uuid: str, phase: str, processed: int = 0, total: int = 0) -> None:
        percent = int(processed * 100 / total) if total > 0 else 0
        now_mono = time.monotonic()
        with self._lock:
            job = self._job_locked(job_uuid)
            job["state"] = phase
            job["progress"] = {"phase": phase, "bytes": processed, "total": total, "percent": percent}
            job["updated_at"] = _utc_now()
            last_time, last_percent, last_phase = self._last_progress_write.get(job_uuid, (0.0, -1, ""))
            should_write = phase != last_phase or percent != last_percent or now_mono - last_time >= 0.5
            if should_write:
                self._persist_locked()
                self._last_progress_write[job_uuid] = (now_mono, percent, phase)
            result = copy.deepcopy(job)
        if should_write:
            self._emit(result)

    def _progress(self, job_uuid: str, processed: int, total: int) -> None:
        self._phase(job_uuid, "transferring", processed, total)

    def _validate_provider_identity(self, identity: dict[str, Any]) -> None:
        digest = _expected_digest(identity)
        if identity.get("provider") == "civitai":
            token = get_config_value("civitai_api_key", "") or None
            version = get_model_version(int(identity["version_id"]), token)
            files = version.get("files")
            match = next(
                (
                    item
                    for item in files
                    if isinstance(item, dict) and item.get("id") == identity.get("file_id")
                ),
                None,
            ) if isinstance(files, list) else None
            hashes = match.get("hashes") if isinstance(match, dict) else None
            actual = hashes.get("SHA256") if isinstance(hashes, dict) else None
            if not isinstance(actual, str) or actual.casefold() != digest["value"]:
                raise ValueError("CivitAI file identity changed or is unavailable")
            return
        token = resolve_auth_token("huggingface")
        repo_id = identity.get("repo_id")
        commit = identity.get("commit")
        path = identity.get("path")
        if not all(isinstance(value, str) for value in (repo_id, commit, path)):
            raise TypeError("Hugging Face identity is incomplete")
        entries = HfApi(token=token).get_paths_info(repo_id, path, revision=commit, token=token)
        if len(entries) != 1 or getattr(entries[0], "path", None) != path:
            raise ValueError("Hugging Face file is unavailable at the immutable commit")
        entry = entries[0]
        actual = entry.lfs.sha256 if digest["algorithm"] == "sha256" and entry.lfs else entry.blob_id
        if not isinstance(actual, str) or actual.casefold() != digest["value"]:
            raise ValueError("Hugging Face provider identity did not match")

    def _destination(self, job: dict[str, Any]) -> tuple[Path, str]:
        destination = job["destination"]
        category = destination["category"]
        root, _index = resolve_destination_root(category, destination["root_id"])
        relative_path = destination["relative_path"]
        _root, target, normalized = prepare_download_destination(
            root,
            requested_filename=relative_path,
            resolved_filename=destination["filename"],
            create_parents=True,
        )
        return target, normalized

    def _partial_status(self, job: dict[str, Any]) -> tuple[bool, int]:
        target, _relative_path = self._destination(job)
        staging = Path(f"{target}.part")
        if staging.is_symlink():
            raise ValueError("Symlinked download staging files are forbidden")
        if not staging.exists():
            return False, 0
        if not staging.is_file():
            raise ValueError("Download staging path must be a regular file")
        return True, staging.stat().st_size

    def _rename_destination(self, job_uuid: str, job: dict[str, Any], target: Path) -> tuple[Path, str]:
        if not target.exists():
            return target, job["destination"]["relative_path"]
        stem, suffix = target.stem, target.suffix
        for index in range(1, 10000):
            candidate = target.with_name(f"{stem} ({index}){suffix}")
            if candidate.exists() or candidate.is_symlink():
                continue
            relative = str(PurePosixPath(job["destination"]["relative_path"]).with_name(candidate.name))
            with self._lock:
                current = self._job_locked(job_uuid)
                current["destination"]["filename"] = candidate.name
                current["destination"]["relative_path"] = relative
                self._persist_locked()
            return candidate, relative
        raise FileExistsError("No collision-free local filename is available")

    def _run_job(self, job_uuid: str) -> None:
        with self._lock:
            job = copy.deepcopy(self._job_locked(job_uuid))
            if job.get("state") != "queued":
                return
        self._update(job_uuid, state="preparing", started_at=job.get("started_at") or _utc_now(), error=None)
        identity = job["provider_identity"]
        self._validate_provider_identity(identity)
        target, relative_path = self._destination(job)
        conflict = job["conflict_policy"]
        if conflict == "rename":
            target, relative_path = self._rename_destination(job_uuid, job, target)
        elif target.exists() and conflict == "skip":
            local_sha = sha256_for(
                target,
                write_sidecar=True,
                reference_type="download-manager-local",
                folder_role=job["destination"]["category"],
                relative_path=relative_path,
            )
            provider_digest = (
                local_sha
                if job["expected_digest"]["algorithm"] == "sha256"
                else _git_blob_digest(target)
            )
            self._update(
                job_uuid,
                state="completed",
                local_sha256=local_sha,
                provider_verified=provider_digest == job["expected_digest"]["value"],
                conflict_result="skipped-existing",
                completed_at=_utc_now(),
                progress={"phase": "completed", "bytes": target.stat().st_size, "total": target.stat().st_size, "percent": 100},
            )
            return
        provider = identity["provider"]
        if provider == "civitai":
            url = f"https://civitai.com/api/download/models/{identity['version_id']}?fileId={identity['file_id']}"
            token = get_config_value("civitai_api_key", "") or None
        else:
            url = hf_hub_url(identity["repo_id"], identity["path"], revision=identity["commit"])
            token = resolve_auth_token("huggingface")
        if not reserve_download_id(job_uuid):
            raise RuntimeError("Download job identity is already reserved")
        digest = job["expected_digest"]
        try:
            success = download_file(
                url=url,
                destination=target,
                api_key=token,
                expected_sha256=digest["value"] if digest["algorithm"] == "sha256" else None,
                expected_git_blob=digest["value"] if digest["algorithm"] == "git-sha1" else None,
                expected_size=job.get("size"),
                progress_cb=lambda processed, total: self._progress(job_uuid, processed, total),
                phase_cb=lambda phase, processed, total: self._phase(job_uuid, phase, processed, total),
                download_id=job_uuid,
                require_idle_promotion=True,
                allow_replace_existing=conflict == "overwrite",
                keep_partial_on_cancel=True,
                provider_name="CivitAI" if provider == "civitai" else "Hugging Face",
            )
        finally:
            release_download_id(job_uuid)
        if not success:
            raise ValueError("Verified provider download failed")
        self._phase(job_uuid, "verifying", 0, target.stat().st_size)
        local_sha = sha256_for(
            target,
            use_sidecar=False,
            write_sidecar=True,
            progress_cb=lambda processed, total: self._phase(job_uuid, "verifying", processed, total),
            reference_type=f"download-manager-{provider}",
            folder_role=job["destination"]["category"],
            relative_path=relative_path,
        )
        if not local_sha:
            raise OSError("Local SHA-256 recording failed")
        if provider == "civitai":
            write_expected(
                target,
                air=job.get("air"),
                sha256=digest["value"],
                precision=job.get("precision"),
                reference_type="civitai",
                folder_role=job["destination"]["category"],
                relative_path=relative_path,
            )
        self._update(
            job_uuid,
            state="completed",
            local_sha256=local_sha,
            provider_verified=True,
            completed_at=_utc_now(),
            resume_pending=False,
            progress={"phase": "completed", "bytes": target.stat().st_size, "total": target.stat().st_size, "percent": 100},
        )

    def _finish_failed(self, job_uuid: str, error: BaseException) -> None:
        try:
            state = "cancelled" if isinstance(error, DownloadCancelled) else "failed"
            self._update(
                job_uuid,
                state=state,
                error=_safe_error(error),
                completed_at=_utc_now(),
            )
        except (JsonStoreError, KeyError, OSError, RuntimeError):
            log.error(_LOG_PREFIX, "Could not persist a failed queue job")

    def _emit(self, job: dict[str, Any]) -> None:
        try:
            from server import PromptServer  # type: ignore

            PromptServer.instance.send_sync(
                "eclipse.download_manager.progress",
                {"job_uuid": job.get("uuid"), "job": self._public_job(job)},
            )
        except (AttributeError, ImportError, RuntimeError):
            log.debug(_LOG_PREFIX, "Progress event is unavailable")

    def export_bundle(self, job_ids: list[str]) -> dict[str, Any]:
        with self._lock:
            selected = [copy.deepcopy(job) for job in self._jobs if job.get("uuid") in job_ids]
        items = []
        for job in selected:
            items.append(
                {
                    "provider_identity": job["provider_identity"],
                    "provider_type": job.get("provider_type"),
                    "format": job.get("format"),
                    "precision": job.get("precision"),
                    "air": job.get("air"),
                    "destination": job["destination"],
                    "expected_digest": job["expected_digest"],
                    "local_sha256": job.get("local_sha256"),
                    "conflict_policy": job["conflict_policy"],
                }
            )
        return {"schema_version": _BUNDLE_SCHEMA_VERSION, "kind": "eclipse-download-bundle", "items": items}

_MANAGER: DownloadQueueManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_manager() -> DownloadQueueManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = DownloadQueueManager()
        return _MANAGER
