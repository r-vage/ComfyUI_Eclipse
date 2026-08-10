# Shared Smart LM registry model resolution and acquisition.

from pathlib import Path
from typing import Any

from .config_templates import get_llm_models_path
from .credentials import resolve_auth_token
from .logger import log
from .model_files import (
    check_model_completeness,
    ensure_model_path,
    ensure_targeted_gguf_files,
)
from .model_registry import load_defaults

_LOG_PREFIX = "Model Acquisition"

GGUF_BACKENDS = {"gguf", "llamacpp"}

BACKEND_TO_METHOD = {
    "transformers": "Transformers",
    "gguf": "GGUF (llama-cpp-python)",
    "ollama": "Ollama (Docker)",
    "vllm": "vLLM (Docker)",
    "vllm_native": "vLLM (Native)",
    "sglang": "SGLang (Docker)",
    "llamacpp": "llama.cpp (Docker)",
    "wd14": "Transformers",
}

FAMILY_TO_EXEC = {
    "Qwen": "Qwen",
    "Mistral": "Mistral",
    "Florence": "Florence",
    "LLaVA": "LLaVA",
    "VLM": "VLM",
    "LLM_TEXT": "LLM (Text-Only)",
    "WD14": "WD14",
}


def _safe_local_registry_path(relative_path: object) -> Path:
    # Resolve a local-only registry path strictly below the configured LLM root.
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("local_only entries require a relative local_path")
    raw_path = relative_path.strip().replace("\\", "/")
    candidate_path = Path(raw_path)
    if candidate_path.is_absolute() or "\x00" in raw_path:
        raise ValueError("local_path must be relative to the configured LLM folder")
    if any(part in {"", ".", ".."} for part in raw_path.split("/")):
        raise ValueError("local_path contains an unsafe path component")
    if any(part in {"", ".", ".."} for part in candidate_path.parts):
        raise ValueError("local_path contains an unsafe path component")

    llm_root = get_llm_models_path().resolve()
    resolved_path = (llm_root / candidate_path).resolve()
    try:
        resolved_path.relative_to(llm_root)
    except ValueError:
        raise ValueError("local_path escapes the configured LLM folder") from None
    return resolved_path


def validate_local_registry_path(relative_path: object, *, require_exists: bool) -> str:
    # Validate and normalize a local-only path for registry persistence.
    resolved_path = _safe_local_registry_path(relative_path)
    if require_exists and not resolved_path.exists():
        raise FileNotFoundError(
            "local_path does not exist below the configured LLM folder"
        )
    llm_root = get_llm_models_path().resolve()
    return resolved_path.relative_to(llm_root).as_posix()


def _entry_source(entry: dict[str, Any]) -> str:
    source = entry.get("source") or load_defaults().get(
        "model_source", "huggingface"
    )
    if not isinstance(source, str):
        raise TypeError("Model source must be a string")
    normalized_source = source.lower().strip()
    if normalized_source not in {"huggingface", "modelscope"}:
        raise ValueError(f"Unsupported model download source: {normalized_source!r}")
    return normalized_source


def _candidate_gguf_names(entry: dict[str, Any], quantization: str | None) -> list[str]:
    names: list[str] = []
    file_pattern = entry.get("file_pattern", "")
    model_name = entry.get("name", "")
    if isinstance(file_pattern, str) and file_pattern and quantization:
        names.append(file_pattern.replace("{quant}", quantization))
    if isinstance(model_name, str) and model_name and quantization:
        for variant in (
            f"{model_name}-{quantization}.gguf",
            f"{model_name}.{quantization}.gguf",
        ):
            if variant not in names:
                names.append(variant)
    return names


def resolve_registered_model_path(
    entry: dict[str, Any],
    quantization: str | None = None,
    *,
    log_prefix: str = _LOG_PREFIX,
) -> tuple[str, bool]:
    # Resolve one registry entry to a local path and whether acquisition is needed.
    backend = entry.get("backend")
    repo_id = entry.get("repo_id", "")
    name = entry.get("name", "")
    if not isinstance(backend, str) or not backend:
        raise ValueError("Registry entry is missing its backend")
    if not isinstance(repo_id, str) or not isinstance(name, str) or not name:
        raise ValueError("Registry entry has invalid model identity fields")

    if entry.get("local_only", False):
        local_path = _safe_local_registry_path(entry.get("local_path"))
        if not local_path.exists():
            raise FileNotFoundError(
                f"Local-only model path is unavailable: {entry.get('local_path', '')}"
            )
        return str(local_path), False

    if backend == "ollama":
        if not repo_id:
            raise ValueError("Ollama registry entries require repo_id")
        return repo_id, False

    llm_base = get_llm_models_path()
    explicit_local_path = entry.get("local_path")
    if isinstance(explicit_local_path, str) and explicit_local_path.strip():
        explicit_path = _safe_local_registry_path(explicit_local_path)
        if explicit_path.exists():
            return str(explicit_path), False

    if backend in GGUF_BACKENDS:
        repo_folder = repo_id.rsplit("/", 1)[-1] if repo_id else name
        filenames = _candidate_gguf_names(entry, quantization)
        folder_names: list[str] = []
        for folder_name in (
            repo_folder,
            name,
            f"{name}-{quantization}" if quantization else "",
        ):
            if folder_name and folder_name not in folder_names:
                folder_names.append(folder_name)

        for folder_name in folder_names:
            candidate_dir = llm_base / folder_name
            if not candidate_dir.is_dir():
                continue
            for filename in filenames:
                candidate_file = candidate_dir / filename
                if candidate_file.is_file():
                    return str(candidate_file), False

        for folder_name in folder_names:
            candidate_dir = llm_base / folder_name
            if not candidate_dir.is_dir():
                continue
            model_files = [
                path
                for path in candidate_dir.glob("*.gguf")
                if "mmproj" not in path.name.lower()
            ]
            if len(model_files) == 1:
                return str(model_files[0]), False

        for filename in filenames:
            candidate_file = llm_base / filename
            if candidate_file.is_file():
                return str(candidate_file), False
        return str(llm_base / repo_folder), True

    if backend == "wd14":
        wd14_name = repo_id.rsplit("/", 1)[-1] if repo_id else name
        model_dir = llm_base / wd14_name
        runtime_files = (model_dir / "model.onnx", model_dir / "selected_tags.csv")
        if all(path.is_file() for path in runtime_files):
            needs_adoption = any(
                not path.with_name(f"{path.name}.sha256").is_file()
                for path in runtime_files
            )
            return str(model_dir), needs_adoption
        return str(model_dir), True

    model_dir = llm_base / name
    candidates = [model_dir]
    if repo_id and "/" in repo_id:
        alternative = llm_base / repo_id.rsplit("/", 1)[-1]
        if alternative not in candidates:
            candidates.append(alternative)

    if entry.get("family") == "Florence":
        import folder_paths  # type: ignore

        candidates.append(Path(folder_paths.models_dir) / "florence2" / name)

    for candidate in candidates:
        if not candidate.exists():
            continue
        is_complete, _ = check_model_completeness(
            candidate,
            repo_id=repo_id or None,
            revision=entry.get("revision"),
        )
        if is_complete:
            return str(candidate), False
        log.warning(log_prefix, f"Model folder '{candidate}' exists but is incomplete.")
    return str(model_dir), True


def resolve_registered_mmproj_path(
    entry: dict[str, Any],
    model_path: str,
) -> str | None:
    # Resolve a projector beside the selected GGUF, with a legacy repo-folder fallback.
    mmproj = entry.get("mmproj")
    if not isinstance(mmproj, str) or not mmproj:
        return None
    normalized_mmproj = mmproj.replace("\\", "/")
    if any(part in {"", ".", ".."} for part in normalized_mmproj.split("/")):
        raise ValueError("mmproj contains an unsafe path component")

    llm_root = get_llm_models_path().resolve()
    selected_path = Path(model_path).resolve()
    folders = [selected_path if selected_path.is_dir() else selected_path.parent]
    repo_id = entry.get("repo_id", "")
    name = entry.get("name", "")
    repo_folder = repo_id.rsplit("/", 1)[-1] if isinstance(repo_id, str) and repo_id else name
    if isinstance(repo_folder, str) and repo_folder:
        legacy_folder = (llm_root / repo_folder).resolve()
        if legacy_folder not in folders:
            folders.append(legacy_folder)

    for folder in folders:
        candidate = (folder / normalized_mmproj).resolve()
        try:
            candidate.relative_to(llm_root)
        except ValueError:
            raise ValueError("mmproj escapes the configured LLM folder") from None
        if candidate.is_file():
            return str(candidate)
    return None


def acquire_registered_model(
    entry: dict[str, Any],
    quantization: str | None = None,
    *,
    local_model_path: str | None = None,
    log_prefix: str = _LOG_PREFIX,
) -> str:
    # Acquire one registry selection through its already-hardened backend path.
    backend = entry.get("backend")
    repo_id = entry.get("repo_id", "")
    if entry.get("local_only", False):
        model_path, _ = resolve_registered_model_path(
            entry, quantization, log_prefix=log_prefix
        )
        return model_path

    if backend == "ollama":
        if not isinstance(repo_id, str) or not repo_id:
            raise ValueError("Ollama registry entries require repo_id")
        from .backend_ollama_docker import (
            ensure_ollama_running,
            get_last_pull_error,
            is_ollama_container_running,
            pull_ollama_model,
            stop_ollama_container,
        )

        was_running = is_ollama_container_running()
        if not ensure_ollama_running():
            raise RuntimeError("Ollama container is not available")
        try:
            if not pull_ollama_model(repo_id):
                detail = get_last_pull_error() or "Ollama pull failed"
                raise RuntimeError(detail)
        finally:
            if not was_running:
                stop_ollama_container()
        return repo_id

    if not isinstance(repo_id, str) or not repo_id:
        raise ValueError("Downloadable registry entries require repo_id")
    source = _entry_source(entry)

    if backend in GGUF_BACKENDS and quantization:
        file_pattern = entry.get("file_pattern", "")
        if isinstance(file_pattern, str) and file_pattern:
            token = resolve_auth_token(source)
            source_label = "ModelScope" if source == "modelscope" else "HuggingFace"
            log.msg(log_prefix, f"Preparing targeted GGUF files from {source_label}")
            existing_model_file = (
                local_model_path
                if local_model_path and Path(local_model_path).is_file()
                else None
            )
            try:
                return ensure_targeted_gguf_files(
                    entry,
                    quantization,
                    source=source,
                    token=token,
                    log_prefix=log_prefix,
                    local_model_path=existing_model_file,
                )
            except Exception as error:  # noqa: BLE001 -- provider boundary
                error_name = type(error).__name__
                if (
                    "NotExist" in error_name
                    or "EntryNotFound" in error_name
                    or "404" in str(error)
                ):
                    raise RuntimeError(
                        f"Requested GGUF quantization {quantization} was not found "
                        f"in repository '{repo_id}'"
                    ) from None
                if "RepositoryNotFound" in error_name or "401" in str(error):
                    raise RuntimeError(
                        f"Repository '{repo_id}' was not found or access was denied"
                    ) from None
                raise RuntimeError(
                    f"Failed to prepare targeted GGUF files from '{repo_id}': "
                    f"{error_name}"
                ) from None

    if source == "modelscope":
        raise RuntimeError(
            "ModelScope full-repository downloads are not supported; "
            "use a targeted GGUF entry"
        )

    family = entry.get("family", "")
    template_info: dict[str, Any] = {
        "repo_id": repo_id,
        "local_path": local_model_path or entry.get("local_path", ""),
        "model_family": FAMILY_TO_EXEC.get(family, family),
        "loading_method": BACKEND_TO_METHOD.get(backend, "Transformers"),
    }
    for key in ("revision", "expected_sha256"):
        if key in entry:
            template_info[key] = entry[key]
    if backend == "wd14":
        template_info["required_snapshot_files"] = [
            "model.onnx",
            "selected_tags.csv",
        ]
    if backend in GGUF_BACKENDS and entry.get("mmproj"):
        template_info["mmproj_url"] = ""
        template_info["mmproj_path"] = ""

    model_path, _, _ = ensure_model_path(template_info)
    return str(model_path)


def download_registered_model(
    entry: dict[str, Any],
    quantization: str | None = None,
    *,
    log_prefix: str = _LOG_PREFIX,
) -> dict[str, Any]:
    # Resolve and acquire a model for the registry-manager endpoint.
    model_path, needs_download = resolve_registered_model_path(
        entry, quantization, log_prefix=log_prefix
    )
    if entry.get("local_only", False):
        return {
            "success": True,
            "downloaded": False,
            "local_only": True,
        }
    if (
        not needs_download
        and entry.get("backend") != "ollama"
        and not entry.get("revision")
        and not entry.get("expected_sha256")
    ):
        return {"success": True, "downloaded": False}
    acquire_registered_model(
        entry,
        quantization,
        local_model_path=model_path,
        log_prefix=log_prefix,
    )
    return {"success": True, "downloaded": needs_download}
