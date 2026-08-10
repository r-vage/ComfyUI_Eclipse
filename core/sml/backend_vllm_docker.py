# vLLM/Docker integration for SML.
#
# This module handles ALL vLLM functionality:
# - Docker configuration loading/saving (docker_config.json in repo root)
# - Container lifecycle management (start, stop, reuse)
# - Model-container tracking for efficient reuse
# - vLLM model loading and generation (works with ANY model: Mistral, Qwen, Llama, etc.)
#
# The vLLM API is model-agnostic - same code works for all models served by vLLM.

import base64
import json
import platform
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import docker_error_handler
from .config_templates import get_llm_models_absolute_path
from .container_spec import (
    CONTAINER_SPEC_BACKEND_LABEL,
    DEFAULT_CONTAINER_SECURITY_OPTIONS,
    DEFAULT_PRIVATE_SHM_SIZE,
    qualified_container_hardening,
    ContainerMount,
    ContainerSpec,
    canonical_path_identity,
    container_mapping_spec_fields,
    inspect_container_reuse,
    mapping_matches_container_spec,
    stable_model_key,
)
from .device import detect_gpu_vendor, get_docker_gpu_args
from .docker_image_policy import (
    RELEASE_DOCKER_IMAGES,
    resolve_managed_docker_image,
)
from .json_store import update_json_object, write_json_object
from .logger import log

_LOG_PREFIX = "vLLM Docker"
VLLM_CONTAINER_PREFIX = "sml-vllm"
VLLM_CONTAINER_PORT = 8000


@dataclass(frozen=True)
class VllmContainerPlan:
    spec: ContainerSpec
    docker_command: tuple[str, ...]
    model_key: str
    model_name: str
    container_name: str
    port: int
    served_model_name: str

# Docker-based vLLM works on all platforms: Windows, Linux, macOS
# For native Linux vLLM (faster, no Docker overhead), use backend_vllm_native.py instead

# ==============================================================================
# DOCKER DAEMON MANAGEMENT (centralized in docker_utils)
# ==============================================================================

from .docker_utils import (
    IS_LINUX,
    IS_MACOS,
    IS_WINDOWS,
    ensure_docker_running,
    get_cached_daemon_status,
    get_docker_version,
    is_docker_daemon_running,
    is_docker_installed,
    start_docker_daemon,
)

# Module-level availability flags (used throughout this file and exported)
# Uses cached values from docker_utils — no extra subprocess calls at import time
DOCKER_AVAILABLE = is_docker_installed()
DOCKER_VERSION = get_docker_version()
DOCKER_DAEMON_RUNNING = get_cached_daemon_status()


# ==============================================================================
# GPU DETECTION AND MEMORY MANAGEMENT
# Import from device.py - single source of truth
# ==============================================================================

from .device import check_model_fits


def is_vllm_docker_available() -> bool:
    # Check if Docker-based vLLM is available (all platforms).
    #
    # Returns:
    #     bool: True if Docker vLLM can be used
    return DOCKER_AVAILABLE


# ==============================================================================
# CONFIGURATION MANAGEMENT
# ==============================================================================

# Config file in repo root (user visible/editable)
_CONFIG_PATH = Path(__file__).parent.parent.parent / "docker_config.json"
_cached_config: dict | None = None


def _get_default_config() -> dict:
    # Return default configuration if file doesn't exist
    return {
        "_comment": "Docker Configuration for SML - Supports vLLM, SGLang, Ollama, and llama.cpp backends",
        "backend": "vllm",
        "gpu_memory_utilization": 0.6,
        "dtype": "auto",
        "trust_remote_code": False,
        "docker_bind_host": "127.0.0.1",
        "allow_unpinned_docker_images": False,
        "vllm": {
            "docker_image": RELEASE_DOCKER_IMAGES["vllm"]["nvidia"],
            "docker_image_rocm": RELEASE_DOCKER_IMAGES["vllm"]["amd"],
            "url": "http://localhost:8000/v1",
            "port": 8000,
            "timeout": 2,
            "startup_timeout": 600,
            "request_timeout": 300,
            "tensor_parallel_size": 1,
            "allow_mistral_weight_conversion": False,
        },
        "ollama": {
            "docker_image": RELEASE_DOCKER_IMAGES["ollama"]["nvidia"],
            "docker_image_rocm": RELEASE_DOCKER_IMAGES["ollama"]["amd"],
            "port": 11434,
            "url": "http://localhost:11434/v1",
            "auto_pull": True,
        },
        "llamacpp": {
            "docker_image": RELEASE_DOCKER_IMAGES["llamacpp"]["nvidia"],
            "docker_image_rocm": RELEASE_DOCKER_IMAGES["llamacpp"]["amd"],
            "port": 8080,
            "url": "http://localhost:8080/v1",
            "n_gpu_layers": -1,
        },
        "paths": {"models_base": "", "docker_mount": "/models"},
        "active_model": {"name": "", "container_id": "", "last_started": ""},
        "model_containers": {},
    }


def load_docker_config(force_reload: bool = False) -> dict:
    # Load docker configuration from JSON file.
    #
    # Args:
    #     force_reload: Force reload from disk (ignore cache)
    #
    # Returns:
    #     Configuration dictionary
    global _cached_config

    if _cached_config is not None and not force_reload:
        return _cached_config

    if not _CONFIG_PATH.exists():
        # Seed from .example when available, but create through the shared lock.
        _example_path = _CONFIG_PATH.with_suffix(".json.example")
        initial_config = _get_default_config()
        if _example_path.exists():
            try:
                with open(_example_path, "r", encoding="utf-8") as example_file:
                    example_config = json.load(example_file)
                if not isinstance(example_config, dict):
                    raise TypeError("Docker example config must be a JSON object")
                initial_config = example_config
            except Exception as e:
                log.error(_LOG_PREFIX, f"Could not load Docker example config: {e}")
        else:
            log.debug(
                _LOG_PREFIX, f"Config file not found, creating defaults: {_CONFIG_PATH}"
            )
        try:
            _cached_config = update_json_object(
                _CONFIG_PATH,
                lambda _config: None,
                default=initial_config,
            )
            log.msg(_LOG_PREFIX, "Created docker_config.json atomically")
            return _cached_config
        except Exception as e:
            log.error(_LOG_PREFIX, f"Error creating config: {e}")
            _cached_config = initial_config
            return _cached_config

    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _cached_config = json.load(f)
        log.debug(_LOG_PREFIX, f"Loaded config from {_CONFIG_PATH}")
        return _cached_config
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error loading config: {e}")
        _cached_config = _get_default_config()
        return _cached_config


def save_docker_config(config: dict) -> bool:
    # Save configuration to JSON file.
    #
    # Args:
    #     config: Configuration dictionary to save
    #
    # Returns:
    #     bool: True if successful
    global _cached_config

    try:
        _cached_config = write_json_object(_CONFIG_PATH, config)
        return True
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error saving config: {e}")
        return False


def _update_docker_config(updater) -> bool:
    # Mutate the latest on-disk object while holding the shared JSON lock.
    global _cached_config
    try:
        _cached_config = update_json_object(
            _CONFIG_PATH,
            updater,
            default=_get_default_config(),
        )
        return True
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error updating config: {e}")
        return False


# ------------------------------------------------------------------------------
# Config Section Getters
# ------------------------------------------------------------------------------


def get_vllm_config() -> dict:
    # Get vLLM configuration section
    config = load_docker_config()
    return config.get("vllm", {})


def get_global_docker_options() -> dict:
    # Get global Docker options (gpu_memory_utilization, dtype, trust_remote_code).
    # NOTE: trust_remote_code defaults to False here (safe). The per-model registry
    # flag and the runtime "⚠ Trust Remote Code" chip are the source of truth —
    # callers pass an explicit value into start_vllm_container() which overrides this.
    config = load_docker_config()
    return {
        "gpu_memory_utilization": config.get("gpu_memory_utilization", 0.9),
        "dtype": config.get("dtype", "auto"),
        "trust_remote_code": config.get("trust_remote_code", False),
    }


def get_paths_config() -> dict:
    # Get paths configuration section
    config = load_docker_config()
    return config.get("paths", {})


def get_vllm_startup_timeout() -> int:
    # Get vLLM startup timeout from config (default 600s / 10 min)
    config = load_docker_config()
    return config.get("vllm", {}).get("startup_timeout", 600)


def get_vllm_request_timeout() -> int:
    # Get vLLM request timeout from config (default 300s / 5 min)
    config = load_docker_config()
    return config.get("vllm", {}).get("request_timeout", 300)


# ------------------------------------------------------------------------------
# Model-Container Tracking
# ------------------------------------------------------------------------------


def load_vllm_model_containers() -> dict[str, Any]:
    # Load all vLLM model-container mappings.
    config = load_docker_config()
    containers = config.get("model_containers", {})
    return containers if isinstance(containers, dict) else {}


def get_container_for_model(model_name: str) -> str | None:
    # Get saved container ID for a specific model.
    #
    # Args:
    #     model_name: Name of the model
    #
    # Returns:
    #     Container ID if saved, None otherwise
    config = load_docker_config()
    containers = config.get("model_containers", {})
    model_info = containers.get(model_name, {})

    if isinstance(model_info, dict):
        return model_info.get("container_id")
    elif isinstance(model_info, str):
        # Legacy format: direct container ID string
        return model_info

    return None


def save_container_for_model(
    model_name: str,
    container_id: str,
    model_path: str = "",
    display_name: str = "",
    legacy_key: str = "",
    spec: ContainerSpec | None = None,
) -> bool:
    # Save container ID for a specific model.
    #
    # Args:
    #     model_name: Name of the model
    #     container_id: Docker container ID
    #
    # Returns:
    #     bool: True if successful
    now = datetime.now().isoformat()
    mapping = {
        "container_id": container_id,
        "created": now,
        "last_used": now,
        "model_path": model_path,
        "display_name": display_name,
    }
    if spec is not None:
        mapping.update(container_mapping_spec_fields(spec))

    def update_mapping(config: dict[str, Any]) -> None:
        containers = config.setdefault("model_containers", {})
        if not isinstance(containers, dict):
            raise TypeError("model_containers must be a JSON object")
        containers[model_name] = mapping
        if legacy_key and legacy_key != model_name:
            containers.pop(legacy_key, None)

    log.debug(
        _LOG_PREFIX, f"Saved container {container_id[:12]} for model {model_name}"
    )
    return _update_docker_config(update_mapping)


def update_container_last_used(model_name: str) -> bool:
    # Update last_used timestamp for a model's container.
    #
    # Args:
    #     model_name: Name of the model
    #
    # Returns:
    #     bool: True if successful
    updated = False

    def update_last_used(config: dict[str, Any]) -> None:
        nonlocal updated
        containers = config.get("model_containers", {})
        if model_name in containers and isinstance(containers[model_name], dict):
            containers[model_name]["last_used"] = datetime.now().isoformat()
            updated = True

    return _update_docker_config(update_last_used) and updated


def remove_container_for_model(model_name: str) -> bool:
    # Remove saved container ID for a model (e.g., container was deleted).
    #
    # Args:
    #     model_name: Name of the model
    #
    # Returns:
    #     bool: True if successful
    removed = False

    def remove_mapping(config: dict[str, Any]) -> None:
        nonlocal removed
        containers = config.get("model_containers", {})
        if model_name in containers:
            del containers[model_name]
            removed = True

    success = _update_docker_config(remove_mapping)
    if success and removed:
        log.debug(_LOG_PREFIX, f"Removed container entry for model {model_name}")
    return success


def cleanup_stale_containers(max_age_hours: int = 24) -> int:
    # Remove container entries older than max_age_hours.
    now = datetime.now()
    max_age_seconds = max_age_hours * 3600
    removed = 0

    def remove_stale(config: dict[str, Any]) -> None:
        nonlocal removed
        containers = config.get("model_containers", {})
        for model_name in list(containers.keys()):
            container_info = containers[model_name]
            if not isinstance(container_info, dict):
                continue
            last_used_str = container_info.get("last_used")
            if not last_used_str:
                continue
            try:
                last_used = datetime.fromisoformat(last_used_str)
                if (now - last_used).total_seconds() > max_age_seconds:
                    del containers[model_name]
                    removed += 1
            except Exception:
                pass

    success = _update_docker_config(remove_stale)
    if success and removed > 0:
        log.debug(_LOG_PREFIX, f"Cleaned up {removed} stale container entries")
    return removed if success else 0


# ------------------------------------------------------------------------------
# Convenience Getters
# ------------------------------------------------------------------------------


def is_vllm_enabled() -> bool:
    # Check if vLLM is enabled in config
    return get_vllm_config().get("enabled", True)


def get_vllm_url() -> str:
    # Get vLLM server URL
    return get_vllm_config().get("url", "http://localhost:8000/v1")


def get_docker_image() -> str:
    # Resolve the configured image through the shared release-pin policy.
    from .device import detect_gpu_vendor

    full_config = load_docker_config()
    vllm_config = full_config.get("vllm", {})
    if not isinstance(vllm_config, dict):
        vllm_config = {}
    vendor = detect_gpu_vendor()
    image_key = "docker_image_rocm" if vendor == "amd" else "docker_image"
    configured_image = vllm_config.get(
        image_key,
        vllm_config.get("docker_image", RELEASE_DOCKER_IMAGES["vllm"]["nvidia"]),
    )
    return resolve_managed_docker_image(
        "vllm",
        configured_image,
        vendor,
        allow_unpinned=full_config.get("allow_unpinned_docker_images", False),
    )


def get_models_base_path() -> str:
    # Get absolute base path for models (for Docker mount).
    #
    # Uses llm_models_absolute_path from config.json.
    # Docker requires full absolute paths for volume mounts.
    try:
        return get_llm_models_absolute_path()
    except ValueError as e:
        log.warning(_LOG_PREFIX, str(e))
        return ""


def set_models_base_path(path: str) -> bool:
    # Set base path for models
    def update_path(config: dict[str, Any]) -> None:
        paths = config.setdefault("paths", {})
        if not isinstance(paths, dict):
            raise TypeError("paths must be a JSON object")
        paths["models_base"] = path

    return _update_docker_config(update_path)


# ==============================================================================
# DOCKER CONTAINER MANAGEMENT
# ==============================================================================


def _run_docker_cmd(args: list[str], timeout: int = 30) -> tuple[bool, str]:
    # Run one Docker command through the shared argument-vector boundary.
    if not DOCKER_AVAILABLE:
        return False, "Docker not available"

    try:
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
        output = result.stdout.strip() or result.stderr.strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as error:
        return False, str(error)


def is_docker_available() -> bool:
    # Check if Docker is available.
    # Uses cached startup check for fast fail.
    return DOCKER_AVAILABLE


def is_vllm_container_running() -> bool:
    # Check if any vLLM container is currently running
    return bool(get_running_vllm_containers())


def get_running_vllm_containers() -> list[str]:
    # Get list of running vLLM container IDs
    if not DOCKER_AVAILABLE:
        return []

    container_ids = []
    filters = [
        f"label={CONTAINER_SPEC_BACKEND_LABEL}=vllm",
        f"ancestor={get_docker_image()}",
    ]
    for container_filter in filters:
        success, output = _run_docker_cmd(
            [
                "ps",
                "--filter",
                container_filter,
                "--format",
                "{{.ID}}",
            ],
            timeout=5,
        )
        if not success:
            continue
        for container_id in output.splitlines():
            container_id = container_id.strip()
            if container_id and container_id not in container_ids:
                container_ids.append(container_id)
    return container_ids


def stop_vllm_container(container_id: str | None = None) -> bool:
    # Stop vLLM Docker container.
    #
    # Args:
    #     container_id: Specific container ID to stop, or None to stop all vLLM containers
    #
    # Returns:
    #     bool: True if successful
    containers = [container_id] if container_id else get_running_vllm_containers()
    for cid in containers:
        log.debug(_LOG_PREFIX, f"Stopping container {cid[:12]}...")
        success, output = _run_docker_cmd(["stop", cid], timeout=30)
        if not success:
            log.error(_LOG_PREFIX, f"Error stopping container {cid[:12]}: {output}")
            return False
    return True


def is_container_running(container_id: str) -> bool:
    # Check if a specific container is running.
    #
    # Args:
    #     container_id: Docker container ID
    #
    # Returns:
    #     bool: True if container is running
    success, output = _run_docker_cmd(
        ["ps", "-q", "--filter", f"id={container_id}"],
        timeout=5,
    )
    if success and output:
        return True
    success, output = _run_docker_cmd(
        ["ps", "-q", "--filter", f"name={container_id}"],
        timeout=5,
    )
    return success and bool(output)


def is_container_exists(container_id: str) -> bool:
    # Check if a container exists (running or stopped).
    #
    # Args:
    #     container_id: Docker container ID
    #
    # Returns:
    #     bool: True if container exists
    success, output = _run_docker_cmd(
        ["ps", "-aq", "--filter", f"id={container_id}"],
        timeout=5,
    )
    if success and output:
        return True
    success, output = _run_docker_cmd(
        ["ps", "-aq", "--filter", f"name={container_id}"],
        timeout=5,
    )
    return success and bool(output)


def start_existing_container(container_id: str) -> bool:
    # Start an existing stopped container.
    #
    # Args:
    #     container_id: Docker container ID
    #
    # Returns:
    #     bool: True if started successfully
    try:
        log.debug(_LOG_PREFIX, f"Starting existing container {container_id[:12]}...")
        result = subprocess.run(
            ["docker", "start", container_id],
            capture_output=True,
            timeout=30,
            text=True,
            encoding="utf-8",
            errors="replace",  # Handle non-UTF8 bytes gracefully on Windows
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
        return result.returncode == 0
    except Exception as e:
        log.error(_LOG_PREFIX, f"Failed to start container: {e}")
        return False


def _get_local_vllm_image_id(image_reference: str) -> str:
    # Resolve the immutable local image ID, preserving Docker's cold-pull behavior.
    success, output = _run_docker_cmd(
        ["image", "inspect", image_reference, "--format", "{{.Id}}"],
        timeout=10,
    )
    if not success or not output.strip():
        log.msg(_LOG_PREFIX, f"Pulling Docker image: {image_reference}...")
        pull_success, pull_output = _run_docker_cmd(
            ["pull", image_reference],
            timeout=600,
        )
        if not pull_success:
            raise RuntimeError(
                f"Could not pull vLLM image {image_reference}: {pull_output}"
            )
        success, output = _run_docker_cmd(
            ["image", "inspect", image_reference, "--format", "{{.Id}}"],
            timeout=10,
        )
    image_id = output.strip()
    if not success or not image_id:
        raise RuntimeError(
            f"Could not resolve immutable image ID for {image_reference}: {output}"
        )
    return image_id


def _get_vllm_container_name(model_name: str, model_key: str) -> str:
    display_stem = Path(model_name).stem
    safe_name = "".join(
        character
        if character.isascii()
        and (character.isalnum() or character in {"-", "_"})
        else "-"
        for character in display_stem
    ).strip("-_")
    safe_name = safe_name[:32] or "model"
    return f"{VLLM_CONTAINER_PREFIX}-{safe_name}-{model_key[:12]}"


def _get_vllm_gpu_environment(gpu_vendor: str) -> tuple[tuple[str, str], ...]:
    # NVIDIA's forward-compatibility libcuda can be older than an upgraded host
    # driver and fail with CUDA error 803. Prefer the driver library mounted by
    # NVIDIA Container Toolkit on direct Linux GPU hosts.
    if not IS_LINUX or gpu_vendor != "nvidia":
        return ()

    architecture = platform.machine().lower()
    library_architecture = {
        "amd64": "x86_64-linux-gnu",
        "arm64": "aarch64-linux-gnu",
        "aarch64": "aarch64-linux-gnu",
        "x86_64": "x86_64-linux-gnu",
    }.get(architecture)
    if not library_architecture:
        return ()

    library_path = ":".join(
        (
            f"/usr/lib/{library_architecture}",
            "/usr/local/nvidia/lib64",
            "/usr/local/cuda/lib64",
        )
    )
    return (("LD_LIBRARY_PATH", library_path),)


def _infer_vllm_gguf_tokenizer(filename: str) -> str | None:
    base_name_parts = filename.removesuffix(".gguf").split("-")
    while base_name_parts and base_name_parts[-1].startswith(
        ("Q", "F", "IQ", "BF")
    ):
        base_name_parts.pop()
    if not base_name_parts:
        return None

    base_name = "-".join(base_name_parts)
    base_name_lower = base_name.lower()
    if "ministral" in base_name_lower or "mistral" in base_name_lower:
        return f"mistralai/{base_name}"
    if "qwen" in base_name_lower:
        return f"Qwen/{base_name}"
    if "llama" in base_name_lower:
        return f"meta-llama/{base_name}"
    if "phi" in base_name_lower:
        return f"microsoft/{base_name}"
    if "gemma" in base_name_lower:
        return f"google/{base_name}"
    return None


def _is_vllm_mistral3_model(model_path: Path) -> bool:
    try:
        from .model_types import is_mistral3_vision_model

        return is_mistral3_vision_model(str(model_path))
    except ImportError:
        name_lower = model_path.name.lower()
        return "ministral" in name_lower or "pixtral" in name_lower


def _ensure_vllm_model_format(
    model_path: Path,
    *,
    allow_conversion: bool = False,
) -> bool:
    # Mistral3/Pixtral HF weights require an explicitly enabled transaction.
    if model_path.name.lower().endswith(".gguf"):
        return True
    if not _is_vllm_mistral3_model(model_path):
        return True
    from .mistral_weight_converter import has_complete_mistral_weights

    if has_complete_mistral_weights(model_path):
        return True

    has_hf_format = (model_path / "model.safetensors").exists() or any(
        model_path.glob("model-*.safetensors")
    )
    if not has_hf_format:
        return True

    log.msg(
        _LOG_PREFIX,
        "Detected Mistral3/Pixtral Hugging Face weights without a complete "
        "Mistral-native conversion",
    )
    try:
        from .mistral_weight_converter import convert_weights_to_mistral
    except ImportError as error:
        log.error(_LOG_PREFIX, f"Weight converter not available: {error}")
        return False

    success, message = convert_weights_to_mistral(
        str(model_path),
        allow_conversion=allow_conversion,
    )
    if not success:
        log.error(_LOG_PREFIX, f"Auto-conversion failed: {message}")
        return False
    if not has_complete_mistral_weights(model_path):
        log.error(
            _LOG_PREFIX,
            "Conversion reported success without a validated transaction",
        )
        return False
    log.msg(_LOG_PREFIX, f"✓ {message}")
    return True


def _build_vllm_container_plan(
    model_path: str,
    docker_image: str,
    port: int,
    max_model_len: int,
    quantization: str | None,
    gpu_memory_utilization: float,
    trust_remote_code: bool,
    tensor_parallel_size: int,
    dtype: str,
    use_torch_compile: bool = False,
    model_provenance: str = "",
) -> VllmContainerPlan:
    # Build the exact fingerprint and Docker argv from one normalized request.
    from .docker_utils import (
        get_docker_bind_host,
        host_path_for_docker,
        validate_docker_image,
    )

    model_path_obj = Path(model_path).expanduser().resolve(strict=False)
    model_name = model_path_obj.name
    model_identity = canonical_path_identity(model_path_obj)
    model_key = stable_model_key("vllm", model_identity)
    container_name = _get_vllm_container_name(model_name, model_key)
    mount_path = model_path_obj.parent
    mount_posix = host_path_for_docker(mount_path)
    image_reference = validate_docker_image(docker_image)
    image_id = _get_local_vllm_image_id(image_reference)
    bind_host = get_docker_bind_host()
    gpu_arguments = tuple(get_docker_gpu_args())
    gpu_vendor = detect_gpu_vendor() or "none"
    hardening = qualified_container_hardening("vllm", gpu_vendor)
    environment = _get_vllm_gpu_environment(gpu_vendor)
    is_gguf_model = model_name.lower().endswith(".gguf")
    docker_model_path = f"/models/{model_name}"
    tokenizer_hint = (
        _infer_vllm_gguf_tokenizer(model_name) if is_gguf_model else None
    )
    is_mistral3_model = (
        not is_gguf_model and _is_vllm_mistral3_model(model_path_obj)
    )
    if is_mistral3_model:
        from .mistral_weight_converter import has_complete_mistral_weights

        mistral_load_format = has_complete_mistral_weights(model_path_obj)
    else:
        mistral_load_format = False
    enforce_eager = mistral_load_format or not use_torch_compile
    model_mount = ContainerMount(
        source=mount_posix,
        target="/models",
        read_only=True,
    )

    effective_quantization = ""
    if (
        not is_gguf_model
        and quantization
        and quantization.lower() not in {"none", "auto", "bf16", "fp16"}
    ):
        if is_mistral3_model and quantization.lower() == "bitsandbytes":
            log.warning(
                _LOG_PREFIX,
                "Mistral3/Pixtral does not support BitsAndBytes in vLLM; "
                "running without quantization",
            )
        else:
            effective_quantization = quantization.lower()

    spec = ContainerSpec(
        backend="vllm",
        image_reference=image_reference,
        image_id=image_id,
        bind_host=bind_host,
        host_port=port,
        container_port=VLLM_CONTAINER_PORT,
        mounts=(model_mount,),
        gpu_arguments=gpu_arguments,
        environment=environment,
        security_options=DEFAULT_CONTAINER_SECURITY_OPTIONS,
        capability_drops=hardening.capability_drops,
        read_only_rootfs=hardening.read_only_rootfs,
        tmpfs_mounts=hardening.tmpfs_mounts,
        ipc_mode="private",
        shm_size=DEFAULT_PRIVATE_SHM_SIZE,
        model_identity=model_identity,
        settings=(
            ("container_name", container_name),
            ("dtype", dtype),
            ("enforce_eager", enforce_eager),
            ("gpu_memory_utilization", gpu_memory_utilization),
            ("gpu_vendor", gpu_vendor),
            ("is_gguf_model", is_gguf_model),
            ("load_format", "mistral" if mistral_load_format else ""),
            ("max_model_len", max_model_len),
            ("model_provenance", model_provenance),
            ("quantization", effective_quantization),
            ("served_model_name", docker_model_path),
            ("tensor_parallel_size", tensor_parallel_size),
            ("tokenizer", tokenizer_hint or ""),
            ("trust_remote_code", trust_remote_code),
        ),
    )

    docker_command = ["run", "--name", container_name]
    for label_name, label_value in sorted(spec.docker_labels.items()):
        docker_command.extend(["--label", f"{label_name}={label_value}"])
    docker_command.extend(spec.docker_isolation_arguments)
    for variable_name, variable_value in environment:
        docker_command.extend(["-e", f"{variable_name}={variable_value}"])
    docker_command.extend(
        [
            *gpu_arguments,
            "-v",
            model_mount.docker_volume_argument,
            "-p",
            f"{bind_host}:{port}:{VLLM_CONTAINER_PORT}",
            "-d",
            image_reference,
            "--model",
            docker_model_path,
            "--dtype",
            dtype,
            "--max-model-len",
            str(max_model_len),
            "--gpu-memory-utilization",
            str(gpu_memory_utilization),
        ]
    )
    if tensor_parallel_size > 1:
        docker_command.extend(["--tensor-parallel-size", str(tensor_parallel_size)])
    if tokenizer_hint:
        docker_command.extend(["--tokenizer", tokenizer_hint])
    if trust_remote_code:
        docker_command.append("--trust-remote-code")
    if mistral_load_format:
        docker_command.extend(["--load-format", "mistral"])
    if enforce_eager:
        docker_command.append("--enforce-eager")
    if effective_quantization:
        docker_command.extend(["--quantization", effective_quantization])

    return VllmContainerPlan(
        spec=spec,
        docker_command=tuple(docker_command),
        model_key=model_key,
        model_name=model_name,
        container_name=container_name,
        port=port,
        served_model_name=docker_model_path,
    )


def _mapping_container_id(mapping: Any) -> str | None:
    if isinstance(mapping, str):
        return mapping or None
    if isinstance(mapping, dict):
        container_id = mapping.get("container_id")
        return container_id if isinstance(container_id, str) and container_id else None
    return None


def _save_vllm_plan_mapping(plan: VllmContainerPlan, container_id: str) -> bool:
    return save_container_for_model(
        plan.model_key,
        container_id,
        plan.spec.model_identity,
        plan.model_name,
        plan.model_name,
        plan.spec,
    )


def _remove_vllm_container(container_id_or_name: str) -> bool:
    success, output = _run_docker_cmd(["rm", "-f", container_id_or_name])
    if success or not is_container_exists(container_id_or_name):
        return True
    log.error(
        _LOG_PREFIX,
        f"Cannot remove incompatible container '{container_id_or_name}': {output}",
    )
    return False


def _reuse_vllm_container(
    plan: VllmContainerPlan,
    wait_for_ready: bool,
) -> bool | None:
    mappings = load_vllm_model_containers()
    current_mapping_container = _mapping_container_id(mappings.get(plan.model_key))
    candidates = []
    for mapping_key in (plan.model_key, plan.model_name):
        candidate = _mapping_container_id(mappings.get(mapping_key))
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    if plan.container_name not in candidates:
        candidates.append(plan.container_name)

    for candidate in candidates:
        if not is_container_exists(candidate):
            continue

        reuse_check = inspect_container_reuse(candidate, plan.spec, _run_docker_cmd)
        if not reuse_check.reusable:
            log.msg(
                _LOG_PREFIX,
                "Recreating vLLM container "
                f"({reuse_check.reason.replace('_', ' ')})...",
            )
            if not _remove_vllm_container(candidate):
                return False
            continue

        _set_last_vllm_container(candidate)
        if is_container_running(candidate):
            log.msg(_LOG_PREFIX, f"✓ Reusing exact container for {plan.model_name}")
            if current_mapping_container != candidate or not mapping_matches_container_spec(
                mappings.get(plan.model_key), plan.spec
            ):
                _save_vllm_plan_mapping(plan, candidate)
            else:
                update_container_last_used(plan.model_key)
            if wait_for_ready:
                return wait_for_vllm_ready(
                    timeout=30,
                    container_id=candidate,
                    port=plan.port,
                )
            return True

        for running_id in get_running_vllm_containers():
            if running_id != candidate:
                stop_vllm_container(running_id)

        log.msg(_LOG_PREFIX, f"Restarting exact container for {plan.model_name}...")
        started, output = _run_docker_cmd(["start", candidate])
        if not started:
            log.warning(_LOG_PREFIX, f"Failed to restart container: {output}")
            if not _remove_vllm_container(candidate):
                return False
            continue

        if current_mapping_container != candidate or not mapping_matches_container_spec(
            mappings.get(plan.model_key), plan.spec
        ):
            _save_vllm_plan_mapping(plan, candidate)
        else:
            update_container_last_used(plan.model_key)
        if wait_for_ready:
            return wait_for_vllm_ready(
                timeout=get_vllm_startup_timeout(),
                container_id=candidate,
                port=plan.port,
            )
        return True

    return None


def start_vllm_container(
    model_path: str,
    models_base_path: str | None = None,
    docker_image: str | None = None,
    port: int | None = None,
    max_model_len: int | None = None,
    wait_for_ready: bool = True,
    quantization: str | None = None,
    gpu_memory_utilization: float | None = None,
    trust_remote_code: bool | None = None,
    use_torch_compile: bool = False,
    model_provenance: str = "",
    tensor_parallel_size: int | None = None,
) -> bool:
    # Start vLLM Docker container with specified model.
    # Reuses existing container if available.
    #
    # Args:
    #     model_path: Full path to model folder (e.g., D:/AI/.../LLM/Ministral-3-3B-Instruct-2512)
    #     models_base_path: Base LLM models directory (defaults to docker_config value)
    #     docker_image: Docker image to use (defaults to docker_config value)
    #     port: Port to expose (defaults to docker_config value)
    #     max_model_len: Maximum model length/context size (defaults to docker_config value)
    #     wait_for_ready: Wait for server to be ready before returning
    #     quantization: Quantization method (bitsandbytes, awq, gptq, etc.) or None for no quantization
    #     gpu_memory_utilization: Override GPU memory utilization (0.0-1.0)
    #     use_torch_compile: Allow vLLM's torch.compile integration when true
    #
    # Returns:
    #     bool: True if container started successfully
    # Load defaults from docker_config if not provided
    docker_cfg = get_vllm_config()
    global_cfg = get_global_docker_options()
    paths_cfg = get_paths_config()

    if models_base_path is None:
        models_base_path = paths_cfg.get("models_base", "")
    if docker_image is None:
        docker_image = get_docker_image()
    if port is None:
        port = docker_cfg.get("port", 8000)
    if max_model_len is None:
        max_model_len = (
            8192  # Default, normally overridden by context_size from template
        )

    try:
        model_name = Path(model_path).name
        model_dir = Path(model_path)

        if not _ensure_vllm_model_format(
            model_dir,
            allow_conversion=bool(
                docker_cfg.get("allow_mistral_weight_conversion", False)
            ),
        ):
            return False

        # ==============================================================================
        # PATH CALCULATION FOR DOCKER VOLUME MOUNT
        # ==============================================================================
        # Model could be in subfolders like: models/LLM/Qwen-VL/Qwen3-VL-2B-Instruct-FP8
        # We need to mount the model's parent folder and calculate the relative path.
        #
        # Option 1 (simple): Mount model's direct parent -> /models/model_name
        # Option 2 (complex): Mount root LLM folder -> /models/relative/path/to/model
        #
        # We use Option 1 for simplicity - mount the model's parent folder directly.
        # This works for all folder structures and avoids path calculation issues.
        # ==============================================================================
        model_path_obj = Path(model_path)
        model_name = model_path_obj.name

        # Always use the model's direct parent as the volume mount source
        # This ensures /models/{model_name} always works regardless of folder depth
        actual_models_base = model_path_obj.parent
        models_base = actual_models_base.as_posix()

        log.debug(_LOG_PREFIX, f"Model path: {model_path}")
        log.debug(_LOG_PREFIX, f"Mounting: {models_base} -> /models")

        log.msg(_LOG_PREFIX, f"Starting container for: {model_name}")

        # Log GPU vendor detection for visibility
        gpu_vendor = detect_gpu_vendor()
        if gpu_vendor == "amd":
            log.msg(_LOG_PREFIX, "GPU: AMD/ROCm detected - using ROCm Docker flags")
        elif gpu_vendor == "nvidia":
            log.debug(_LOG_PREFIX, "GPU: NVIDIA detected")
        else:
            log.warning(_LOG_PREFIX, "No GPU detected - container may run on CPU only")

        log.msg(_LOG_PREFIX, "This may take 1-2 minutes on first run...")

        # Get additional docker settings (global config)
        dtype = global_cfg.get("dtype", "auto")
        # trust_remote_code: caller (registry flag OR chip) overrides the global config.
        # Default False = safe. Caller passes True only when the model entry explicitly
        # whitelists it or the runtime "⚠ Trust Remote Code" chip is on.
        if trust_remote_code is None:
            trust_remote_code = bool(global_cfg.get("trust_remote_code", False))
        else:
            trust_remote_code = trust_remote_code

        # Get GPU memory utilization - use parameter if provided, else global config
        if gpu_memory_utilization is None:
            gpu_memory_utilization = global_cfg.get("gpu_memory_utilization", 0.9)

        # Get tensor parallel size from config (default 1 = single GPU)
        if tensor_parallel_size is None:
            tensor_parallel_size = docker_cfg.get("tensor_parallel_size", 1)

        # ==============================================================================
        # GPU VRAM CHECK - Detect if model will fit before attempting to load
        # ==============================================================================
        fit_check = check_model_fits(
            model_path, gpu_memory_utilization, tensor_parallel_size
        )
        gpu_info = fit_check["gpu_info"]

        if gpu_info["gpu_count"] > 0:
            # Log GPU info
            for gpu in gpu_info["gpus"]:
                log.debug(
                    _LOG_PREFIX,
                    f"GPU {gpu['index']}: {gpu['name']} ({gpu['vram_gb']:.1f}GB)",
                )

            if fit_check["model_size_gb"] > 0:
                log.msg(
                    _LOG_PREFIX,
                    f"Model size: ~{fit_check['model_size_gb']:.1f}GB (needs ~{fit_check['estimated_required_gb']:.1f}GB with overhead)",
                )

            if not fit_check["fits"]:
                # Model doesn't fit with current settings
                log.warning(_LOG_PREFIX, f"⚠ {fit_check['message']}")

                # Check if we can use tensor parallelism to make it fit
                if (
                    fit_check["suggested_tensor_parallel"] > 1
                    and gpu_info["gpu_count"] >= fit_check["suggested_tensor_parallel"]
                ):
                    tensor_parallel_size = fit_check["suggested_tensor_parallel"]
                    log.msg(
                        _LOG_PREFIX,
                        f"Auto-enabling tensor parallelism: using {tensor_parallel_size} GPUs",
                    )
                    # Re-check with new tensor parallel size
                    fit_check = check_model_fits(
                        model_path, gpu_memory_utilization, tensor_parallel_size
                    )

                # If still doesn't fit, we should warn but still try (user might have other memory free)
                if not fit_check["fits"]:
                    log.error(_LOG_PREFIX, f"⚠ Model may not fit in VRAM!")
                    log.error(
                        _LOG_PREFIX,
                        f"  Model needs ~{fit_check['estimated_required_gb']:.1f}GB, available: {fit_check['available_vram_gb']:.1f}GB",
                    )
                    log.error(
                        _LOG_PREFIX,
                        f"  Consider using a GGUF quantized version or smaller model",
                    )
                    # Don't return False - let vLLM try and fail with a clearer error
            else:
                log.debug(_LOG_PREFIX, f"✓ {fit_check['message']}")

        # Get quantization setting - parameter overrides (no config fallback, comes from template)
        if quantization is None:
            quantization = (
                None  # No config fallback — quantization comes from template/node
            )

        log.debug(_LOG_PREFIX, f"GPU memory utilization: {gpu_memory_utilization}")
        if quantization:
            log.debug(_LOG_PREFIX, f"Quantization: {quantization}")

        # ==============================================================================
        # GGUF SUPPORT
        # vLLM supports GGUF files (experimental) but needs a tokenizer.
        # GGUF is self-contained (weights + metadata in one file, no separate tokenizer).
        # vLLM will download the tokenizer from HuggingFace based on model name inference.
        # See: https://docs.vllm.ai/en/latest/features/quantization/gguf/
        # ==============================================================================
        is_gguf_model = model_name.lower().endswith(".gguf")

        # Validate image string + resolve bind host (defense-in-depth before passing to subprocess)
        from .docker_utils import get_docker_bind_host, validate_docker_image

        docker_image = resolve_managed_docker_image(
            "vllm",
            docker_image,
            gpu_vendor,
            allow_unpinned=load_docker_config().get(
                "allow_unpinned_docker_images", False
            ),
        )
        docker_image = validate_docker_image(docker_image)
        bind_host = get_docker_bind_host()

        if is_gguf_model:
            log.msg(_LOG_PREFIX, "⚠ GGUF model detected (experimental vLLM support)")
            # For GGUF, the model_name is the .gguf file
            # Mount parent folder and point to the file
            gguf_file_path = Path(model_path)
            from .docker_utils import host_path_for_docker

            gguf_parent_posix = host_path_for_docker(gguf_file_path.parent)
            gguf_filename = gguf_file_path.name
            docker_model_path = f"/models/{gguf_filename}"

            # Try to infer base model repo for tokenizer from GGUF filename
            # E.g., "Ministral-3B-Instruct-2512-Q4_K_M.gguf" -> "mistralai/Ministral-3B-Instruct-2512"
            tokenizer_hint = None
            base_name_parts = gguf_filename.replace(".gguf", "").split("-")
            # Remove common quantization suffixes (Q4_K_M, Q5_K_S, IQ4_XS, BF16, etc.)
            while base_name_parts and base_name_parts[-1].startswith(
                ("Q", "F", "IQ", "BF")
            ):
                base_name_parts.pop()
            if base_name_parts:
                base_name = "-".join(base_name_parts)
                # Common HuggingFace repo patterns
                if "ministral" in base_name.lower() or "mistral" in base_name.lower():
                    tokenizer_hint = f"mistralai/{base_name}"
                elif "qwen" in base_name.lower():
                    tokenizer_hint = f"Qwen/{base_name}"
                elif "llama" in base_name.lower():
                    tokenizer_hint = f"meta-llama/{base_name}"
                elif "phi" in base_name.lower():
                    tokenizer_hint = f"microsoft/{base_name}"
                elif "gemma" in base_name.lower():
                    tokenizer_hint = f"google/{base_name}"

            docker_cmd = [
                "docker",
                "run",
                *get_docker_gpu_args(),  # GPU flags: NVIDIA "--gpus all" or AMD "/dev/kfd, /dev/dri"
                "-v",
                f"{gguf_parent_posix}:/models",
                "-p",
                f"{bind_host}:{port}:8000",
                "--ipc=host",
                "-d",  # Detached mode
                docker_image,
                "--model",
                docker_model_path,
                "--dtype",
                dtype,
                "--max-model-len",
                str(max_model_len),
                "--gpu-memory-utilization",
                str(gpu_memory_utilization),
            ]

            # Add tensor parallelism for multi-GPU
            if tensor_parallel_size > 1:
                docker_cmd.extend(["--tensor-parallel-size", str(tensor_parallel_size)])
                log.msg(
                    _LOG_PREFIX,
                    f"  Using tensor parallelism: {tensor_parallel_size} GPUs",
                )

            # Add tokenizer from HuggingFace (vLLM will download it)
            if tokenizer_hint:
                docker_cmd.extend(["--tokenizer", tokenizer_hint])
                log.msg(
                    _LOG_PREFIX,
                    f"  Tokenizer: {tokenizer_hint} (will download from HuggingFace)",
                )
            else:
                log.warning(
                    _LOG_PREFIX,
                    "  ⚠ Could not infer tokenizer - vLLM will convert from GGUF metadata (slow)",
                )
        else:
            # Standard model folder
            from .docker_utils import host_path_for_docker

            docker_cmd = [
                "docker",
                "run",
                *get_docker_gpu_args(),  # GPU flags: NVIDIA "--gpus all" or AMD "/dev/kfd, /dev/dri"
                "-v",
                f"{host_path_for_docker(models_base)}:/models",
                "-p",
                f"{bind_host}:{port}:8000",
                "--ipc=host",
                "-d",  # Detached mode
                docker_image,
                "--model",
                f"/models/{model_name}",
                "--dtype",
                dtype,
                "--max-model-len",
                str(max_model_len),
                "--gpu-memory-utilization",
                str(gpu_memory_utilization),
            ]

            # Add tensor parallelism for multi-GPU
            if tensor_parallel_size > 1:
                docker_cmd.extend(["--tensor-parallel-size", str(tensor_parallel_size)])
                log.msg(
                    _LOG_PREFIX,
                    f"  Using tensor parallelism: {tensor_parallel_size} GPUs",
                )

        if trust_remote_code:
            docker_cmd.append("--trust-remote-code")

        # Detect Mistral3/Pixtral vision models - they need special handling
        # These models have both consolidated.safetensors (Mistral format) and model.safetensors (HF format)
        # vLLM defaults to HF format which causes weight loading issues for Mistral-native models
        # Mistral3/Pixtral models come in two formats:
        # 1. Mistral-native: consolidated.safetensors with 'layers.*' weight keys
        # 2. HuggingFace: model.safetensors or sharded files with 'language_model.model.*' keys
        # Only use --load-format mistral for Mistral-native format models
        is_mistral3_model = False
        has_consolidated = False
        has_hf_format = False

        if not is_gguf_model:
            model_dir = Path(model_path)

            # Check for weight file formats
            from .mistral_weight_converter import has_complete_mistral_weights

            has_consolidated = has_complete_mistral_weights(model_dir)
            has_hf_format = (model_dir / "model.safetensors").exists() or any(
                model_dir.glob("model-*.safetensors")
            )  # Sharded HF format

            # Use shared detection function from model_types
            try:
                from .model_types import is_mistral3_vision_model

                is_mistral3_model = is_mistral3_vision_model(str(model_dir))
            except ImportError:
                # Fallback if import fails
                model_name_lower = model_name.lower()
                is_mistral3_model = (
                    "ministral" in model_name_lower or "pixtral" in model_name_lower
                )

        # Only use mistral load format if we have consolidated.safetensors (Mistral-native format)
        # HuggingFace format Mistral3 models need conversion - vLLM can't load them directly
        # (vLLM expects Mistral-native weight keys like 'layers.*' not HF keys like 'language_model.model.*')
        if is_mistral3_model:
            if has_consolidated:
                log.msg(
                    _LOG_PREFIX,
                    "  Detected Mistral3/Pixtral with Mistral-native format - using mistral load format",
                )
                docker_cmd.extend(["--load-format", "mistral"])
                # Enforce eager mode to avoid CUDA graph issues with Pixtral
                docker_cmd.append("--enforce-eager")
            elif has_hf_format and not has_consolidated:
                # HuggingFace format Mistral3 - need to convert to Mistral-native format
                # vLLM's default loader can't handle HF-format Mistral3 models (weight key mismatch)
                log.msg(
                    _LOG_PREFIX,
                    "  Detected Mistral3/Pixtral with HuggingFace format - attempting auto-conversion...",
                )
                try:
                    from .mistral_weight_converter import convert_weights_to_mistral

                    success, message = convert_weights_to_mistral(
                        model_path,
                        allow_conversion=bool(
                            docker_cfg.get("allow_mistral_weight_conversion", False)
                        ),
                    )
                    if success:
                        log.msg(_LOG_PREFIX, f"  ✓ {message}")
                        log.msg(
                            _LOG_PREFIX,
                            "  Using mistral load format with converted weights",
                        )
                        docker_cmd.extend(["--load-format", "mistral"])
                        docker_cmd.append("--enforce-eager")
                    else:
                        log.error(_LOG_PREFIX, f"  Auto-conversion failed: {message}")
                        log.error(
                            _LOG_PREFIX,
                            "  This HuggingFace-format Mistral3/Pixtral model cannot be used with vLLM Docker.",
                        )
                        log.error(_LOG_PREFIX, "  Solutions:")
                        log.error(
                            _LOG_PREFIX,
                            "    1. Use 'Transformers' backend instead (supports HF format directly)",
                        )
                        log.error(
                            _LOG_PREFIX,
                            "    2. Download the original Mistral-native model (with consolidated.safetensors)",
                        )
                        log.error(
                            _LOG_PREFIX,
                            "    3. For FP8 models: use 'vLLM' (local) or 'Transformers' backend",
                        )
                        return False
                except ImportError as e:
                    log.error(_LOG_PREFIX, f"⚠️  Weight converter not available: {e}")
                    log.error(
                        _LOG_PREFIX,
                        "    This Mistral3/Pixtral model uses HuggingFace weight format.",
                    )
                    log.error(_LOG_PREFIX, "    Solutions:")
                    log.error(
                        _LOG_PREFIX,
                        "      1. Use 'Transformers' backend instead (supports HF format)",
                    )
                    log.error(
                        _LOG_PREFIX,
                        "      2. Download the original Mistral-native model (with consolidated.safetensors)",
                    )
                    return False

        # Add quantization if specified (not for GGUF - they're already quantized)
        # Valid options: awq, gptq, squeezellm, bitsandbytes, fp8
        # NOTE: Mistral3/Pixtral vision models do NOT support BitsAndBytes quantization in vLLM
        if (
            not is_gguf_model
            and quantization
            and quantization.lower() not in ["none", "auto", "bf16", "fp16"]
        ):
            if is_mistral3_model and quantization.lower() == "bitsandbytes":
                log.warning(
                    _LOG_PREFIX,
                    "⚠ Mistral3/Pixtral models don't support BitsAndBytes quantization in vLLM",
                )
                log.warning(
                    _LOG_PREFIX, "  Running without quantization (requires more VRAM)"
                )
            else:
                docker_cmd.extend(["--quantization", quantization.lower()])

        plan = _build_vllm_container_plan(
            model_path=model_path,
            docker_image=docker_image,
            port=port,
            max_model_len=max_model_len,
            quantization=quantization,
            gpu_memory_utilization=gpu_memory_utilization,
            trust_remote_code=trust_remote_code,
            tensor_parallel_size=tensor_parallel_size,
            dtype=dtype,
            use_torch_compile=use_torch_compile,
            model_provenance=model_provenance,
        )
        reuse_result = _reuse_vllm_container(plan, wait_for_ready)
        if reuse_result is not None:
            return reuse_result

        for running_id in get_running_vllm_containers():
            log.warning(_LOG_PREFIX, f"Stopping other container {running_id[:12]}...")
            if not stop_vllm_container(running_id):
                return False

        docker_cmd = list(plan.docker_command)
        success, output = _run_docker_cmd(docker_cmd, timeout=30)
        if not success:
            log.error(_LOG_PREFIX, f"Failed to start container: {output}")
            return False

        container_id = output.strip()
        log.msg(_LOG_PREFIX, f"✓ Container created: {container_id[:12]}")

        # Track container for error diagnosis
        _set_last_vllm_container(container_id)

        creation_check = inspect_container_reuse(
            plan.container_name,
            plan.spec,
            _run_docker_cmd,
        )
        if not creation_check.reusable:
            log.error(
                _LOG_PREFIX,
                "New vLLM container identity could not be verified: "
                f"{creation_check.reason}",
            )
            _remove_vllm_container(plan.container_name)
            return False

        if _save_vllm_plan_mapping(plan, container_id):
            log.msg(_LOG_PREFIX, f"✓ Saved container ID for {plan.model_name}")

        if wait_for_ready:
            startup_timeout = get_vllm_startup_timeout()
            return wait_for_vllm_ready(
                timeout=startup_timeout,
                container_id=container_id,
                port=plan.port,
            )

        return True

    except Exception as e:
        log.error(_LOG_PREFIX, f"Error starting container: {e}")
        return False


# Module-level variable to track last container ID for error diagnosis
_last_vllm_container_id = None


def _set_last_vllm_container(container_id: str):
    global _last_vllm_container_id
    _last_vllm_container_id = container_id


def wait_for_vllm_ready(
    timeout: int = 600,
    container_id: str | None = None,
    port: int | None = None,
) -> bool:
    # Wait for vLLM server to be ready to accept requests.
    #
    # Args:
    #     timeout: Maximum seconds to wait (default 600s / 10 min for large models)
    #     container_id: Container ID for error diagnosis
    #
    # Returns:
    #     bool: True if server is ready, False if timeout
    import requests

    global _last_vllm_container_id

    # Use provided container_id or the last tracked one
    diag_container = container_id or _last_vllm_container_id
    health_port = port or get_vllm_config().get("port", VLLM_CONTAINER_PORT)

    log.msg(_LOG_PREFIX, f"Waiting for vLLM to be ready (timeout: {timeout}s)...")

    start_time = time.time()
    poll_interval = 5

    while time.time() - start_time < timeout:
        # Check if container is still running
        container_running = (
            is_container_running(diag_container)
            if diag_container
            else is_vllm_container_running()
        )
        if not container_running:
            log.warning(_LOG_PREFIX, "vLLM container stopped unexpectedly")
            # Use centralized error handler to diagnose
            if diag_container:
                error = docker_error_handler.diagnose_vllm_error(
                    diag_container, timeout_occurred=False
                )
                log.error(_LOG_PREFIX, docker_error_handler.format_error_message(error))
            return False

        try:
            response = requests.get(
                f"http://localhost:{health_port}/health",
                timeout=2,
            )
            if response.status_code == 200:
                elapsed = time.time() - start_time
                log.msg(_LOG_PREFIX, f"✓ vLLM ready in {elapsed:.1f}s")
                return True
        except Exception:
            pass

        elapsed = int(time.time() - start_time)
        if elapsed % 15 == 0 and elapsed > 0:
            log.msg(_LOG_PREFIX, f"Still waiting for vLLM... ({elapsed}s)")

        time.sleep(poll_interval)

    # Timeout occurred - use centralized error handler to diagnose
    log.warning(_LOG_PREFIX, f"⚠ vLLM did not become ready within {timeout}s")
    if diag_container:
        error = docker_error_handler.diagnose_vllm_error(
            diag_container, timeout_occurred=True
        )
        log.error(_LOG_PREFIX, docker_error_handler.format_error_message(error))
        if error.raw_log:
            log.debug(_LOG_PREFIX, f"Container log excerpt: {error.raw_log[:300]}")
    return False


def auto_start_vllm_for_model(
    model_path: str,
    quantization: str | None = None,
    context_size: int | None = None,
    trust_remote_code: bool = False,
    use_torch_compile: bool = False,
    model_provenance: str = "",
    tensor_parallel_size: int | None = None,
) -> bool:
    # Start vLLM container for the specified model.
    #
    # Args:
    #     model_path: Full path to model folder
    #     quantization: Quantization method (bitsandbytes, awq, gptq, fp8, or None)
    #     context_size: Maximum context window size (max_model_len in vLLM)
    #
    # Returns:
    #     bool: True if container started successfully or already running with correct model
    models_base = get_models_base_path()
    docker_image = get_docker_image()

    if not is_docker_available():
        log.warning(_LOG_PREFIX, "Docker not available, cannot start container")
        return False

    # Auto-detect models_base from model_path if not configured
    if not models_base:
        # model_path is like "D:/AI/.../models/LLM/Ministral-3-3B" (folder)
        # or "D:/AI/.../models/LLM/model.gguf" (GGUF file)
        # We need the parent folder (models/LLM/)
        model_path_obj = Path(model_path)
        if model_path_obj.exists():
            # For GGUF files, parent is already the models folder
            # For folders, parent is also the models folder
            models_base = str(model_path_obj.parent)
            log.msg(_LOG_PREFIX, f"Auto-detected models_base: {models_base}")
            # Save for future use
            set_models_base_path(models_base)
        else:
            log.error(_LOG_PREFIX, "models_path not configured and cannot auto-detect")
            return False

    log.debug(_LOG_PREFIX, "Starting vLLM container...")

    return start_vllm_container(
        model_path=model_path,
        models_base_path=models_base,
        docker_image=docker_image,
        wait_for_ready=True,
        quantization=quantization,
        max_model_len=context_size,  # Pass context_size as max_model_len
        trust_remote_code=trust_remote_code,
        use_torch_compile=use_torch_compile,
        model_provenance=model_provenance,
        tensor_parallel_size=tensor_parallel_size,
    )


# ==============================================================================
# vLLM MODEL LOADING & GENERATION (Model-Agnostic)
# ==============================================================================


def is_vllm_available() -> bool:
    # Check if vLLM server is running and accessible
    try:
        if not is_vllm_enabled():
            return False

        url = get_vllm_url()
        timeout = get_vllm_config().get("timeout", 2)

        import requests

        # Check if server is running
        response = requests.get(f"{url.rstrip('/v1')}/health", timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


def is_vllm_serving_model(model_path: str) -> str | None:
    # Check if vLLM server is serving the specified model.
    #
    # Works with ANY model type (Mistral, Qwen, Llama, etc.)
    #
    # Args:
    #     model_path: Path to model folder or model name
    #
    # Returns:
    #     str: The model ID if found in vLLM server
    #     None: If server not running or model not found
    try:
        from openai import OpenAI  # type: ignore

        if not is_vllm_enabled():
            return None

        url = get_vllm_url()
        timeout = get_vllm_config().get("timeout", 2)
        request_timeout = get_vllm_request_timeout()

        # Quick health check first
        import requests

        response = requests.get(f"{url.rstrip('/v1')}/health", timeout=timeout)
        if response.status_code != 200:
            return None

        # Check which models are loaded
        client = OpenAI(base_url=url, api_key="not-needed", timeout=request_timeout)
        models = client.models.list()
        available_models = [m.id for m in models.data]

        # Extract model name from path
        model_name = Path(model_path).name

        # Try to find matching model
        for available in available_models:
            # Match by name similarity
            if model_name in available or available in model_name:
                return available

        return None

    except Exception as e:
        return None


def load_vllm(
    model_path: str,
    quantization: str | None = None,
    context_size: int | None = None,
    trust_remote_code: bool = False,
    use_torch_compile: bool = False,
    model_provenance: str = "",
    tensor_parallel_size: int | None = None,
) -> dict[str, Any] | None:
    # Load ANY model via vLLM (native on Linux, Docker on Windows).
    #
    # This function is model-agnostic - works with Mistral, Qwen, Llama, etc.
    # vLLM handles all model-specific details internally.
    #
    # Args:
    #     model_path: Full path to model folder
    #     quantization: Quantization method (bitsandbytes, awq, gptq, fp8, or None)
    #     context_size: Maximum context window size (max_model_len in vLLM)
    #
    # Returns:
    #     Dict with vLLM client info, or None if vLLM unavailable/wrong model
    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        log.warning(_LOG_PREFIX, "Requires openai package: pip install openai")
        return None

    # Check Docker availability (works on all platforms: Windows, Linux, macOS)
    if not is_vllm_docker_available():
        log.warning(_LOG_PREFIX, "Not available (Docker not found)")
        return None

    # Ensure Docker is running before proceeding
    if not ensure_docker_running():
        log.warning(_LOG_PREFIX, "Docker is not running and could not be started")
        return None

    vllm_config = get_vllm_config()
    url = get_vllm_url()

    model_name = Path(model_path).name
    log.msg(_LOG_PREFIX, f"Validating container for {model_name}...")
    try:
        if not auto_start_vllm_for_model(
            model_path,
            quantization=quantization,
            context_size=context_size,
            trust_remote_code=trust_remote_code,
            use_torch_compile=use_torch_compile,
            model_provenance=model_provenance,
            tensor_parallel_size=tensor_parallel_size,
        ):
            log.warning(_LOG_PREFIX, "⚠ Failed to start or validate container")
            return None
    except Exception as e:
        log.warning(_LOG_PREFIX, f"⚠ Container start error: {e}")
        return None

    matched_model = is_vllm_serving_model(model_path)
    if not matched_model:
        log.warning(_LOG_PREFIX, "⚠ Container ready but model not detected")
        return None

    # Model found! Use vLLM
    request_timeout = get_vllm_request_timeout()
    client = OpenAI(base_url=url, api_key="not-needed", timeout=request_timeout)

    # Update last used timestamp
    model_identity = canonical_path_identity(model_path)
    update_container_last_used(stable_model_key("vllm", model_identity))

    # Store vLLM client info
    # Check if this is a GGUF model
    is_gguf_model = model_name.lower().endswith(".gguf")

    log.debug(_LOG_PREFIX, "Using vLLM (Docker) backend")
    log.debug(_LOG_PREFIX, f"Model: {matched_model}")
    if is_gguf_model:
        log.debug(_LOG_PREFIX, "GGUF format (experimental vLLM support)")
    log.debug(_LOG_PREFIX, "Optimized inference enabled")

    return {"mode": "vllm", "client": client, "model_name": matched_model}


def generate_vllm(
    smart_lm_instance,
    prompt: str,
    image_paths: list | None = None,
    max_tokens: int = 512,
    temperature: float = 0.7,
    top_p: float = 0.9,
    top_k: int = 50,
    seed: int | None = None,
    llm_mode: str | None = None,
    instruction_template: str = "",
    repetition_penalty: float = 1.0,
    vision_task: str | None = None,
    use_few_shot: bool = True,
    **kwargs,
) -> str | tuple[str, str]:
    # Generate text using vLLM API (OpenAI-compatible).
    #
    # This function is model-agnostic - works with ANY model served by vLLM.
    # The OpenAI-compatible API handles all model differences internally.
    #
    # Supports both:
    # - Vision models (QwenVL, Mistral Vision) with image_paths
    # - Text-only LLM with llm_mode for few-shot examples
    #
    # Args:
    #     smart_lm_instance: The SmartLM instance with vllm_client
    #     prompt: Text prompt
    #     image_paths: Optional list of image paths for vision models
    #     max_tokens: Maximum tokens to generate
    #     temperature: Sampling temperature
    #     top_p: Nucleus sampling parameter
    #     top_k: Top-k sampling parameter (passed to vLLM via extra_body)
    #     seed: Random seed for reproducibility
    #     llm_mode: LLM mode key for few-shot examples (text-only models)
    #     instruction_template: Custom instruction template (text-only models)
    #     repetition_penalty: Repetition penalty (passed to vLLM via extra_body)
    #
    # Returns:
    #     Generated text (or tuple (cleaned, raw) for LLM mode)
    log.debug(
        _LOG_PREFIX,
        f"generate_vllm: model={getattr(smart_lm_instance, 'vllm_model_name', 'unknown')}",
    )
    log.debug(_LOG_PREFIX, f"  prompt={prompt[:100] if prompt else 'None'}...")
    log.debug(_LOG_PREFIX, f"  image_paths={image_paths}")
    log.debug(_LOG_PREFIX, f"  llm_mode={llm_mode}")

    client = smart_lm_instance.vllm_client
    model_name = smart_lm_instance.vllm_model_name

    # Build messages
    messages = []

    if image_paths and len(image_paths) > 0:
        # Vision + text (multimodal)
        # Eclipse 3.5+ passes system + user separately via system_prompt kwarg.
        # Legacy callers may still send a combined "system\n\nuser" string.
        system_prompt = kwargs.get("system_prompt")
        user_message = ""

        if system_prompt is not None:
            user_message = (prompt or "").strip()
        elif "\n\n" in prompt:
            parts = prompt.split("\n\n", 1)  # Split only on first \n\n
            system_prompt = parts[0].strip()
            if len(parts) > 1:
                remaining = parts[1].strip()
                if remaining.startswith("Additional context:"):
                    user_message = remaining.replace("Additional context:", "").strip()
                elif remaining:
                    user_message = remaining
            log.debug(
                _LOG_PREFIX,
                f"  Parsed - System: {system_prompt[:50] if system_prompt else 'None'}..., User: {user_message[:50] if user_message else 'empty'}...",
            )
        else:
            # No separator - use entire prompt as user message (Custom task)
            user_message = prompt

        # Build image data
        image_data = []
        for img_path in image_paths:
            with open(img_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")
                image_data.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                    }
                )

        # Add system message if we have one
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Inject text-only few-shot examples to guide output style (no prefixes, uncensored)
        if vision_task and use_few_shot:
            from .config_templates import get_vision_few_shot_messages

            few_shot = get_vision_few_shot_messages(vision_task)
            if few_shot:
                messages.extend(few_shot)

        # Build multimodal content - images + optional user text
        content = image_data.copy()
        user_content = (
            user_message
            if user_message
            else "Please follow the instructions above for this image."
        )
        content.append({"type": "text", "text": user_content})
        messages.append({"role": "user", "content": content})
    elif llm_mode:
        # Text-only LLM with few-shot examples
        from .config_templates import get_llm_few_shot_examples
        from .tasks import get_system_prompt

        LLM_FEW_SHOT_EXAMPLES = get_llm_few_shot_examples()

        config = LLM_FEW_SHOT_EXAMPLES.get(llm_mode)
        if config:
            display_name = config.get("display_name", llm_mode)
        else:
            # No few-shot entry — derive display name for correct system prompt lookup
            display_name = llm_mode.replace("_", " ").title()
            config = {
                "display_name": display_name,
                "instruction_template": "",
                "examples": [],
            }
            log.debug(
                _LOG_PREFIX,
                f"No few-shot config for '{llm_mode}', using task system prompt for '{display_name}'",
            )

        # Get system_prompt from prompt_defaults (authoritative source)
        system_prompt = get_system_prompt(display_name)
        if not system_prompt:
            system_prompt = "You are a helpful assistant."

        examples_val = config.get("examples", []) if use_few_shot else []
        examples = examples_val if isinstance(examples_val, list) else []
        template_val = (
            instruction_template
            if instruction_template
            else config.get("instruction_template", "")
        )
        if isinstance(template_val, list):
            template = "\n".join(str(item) for item in template_val)
        else:
            template = str(template_val or "")

        if isinstance(prompt, list):
            prompt = "\n".join(str(item) for item in prompt)
        elif not isinstance(prompt, str):
            prompt = str(prompt or "")

        log.debug(
            _LOG_PREFIX,
            f"  LLM mode: display_name={display_name}, {len(examples)} examples (use_few_shot={use_few_shot})",
        )

        # Build messages: system + (optional examples) + user request
        messages = [{"role": "system", "content": system_prompt}]

        # Add few-shot examples only if available for this task
        if examples:
            messages.extend(examples)

        # Build user request
        if llm_mode != "direct_chat" and template:
            req = (
                template.replace("{prompt}", prompt)
                if "{prompt}" in template
                else f"{template} {prompt}"
            )
            messages.append({"role": "user", "content": req})
        else:
            messages.append({"role": "user", "content": prompt})
    else:
        # Simple text only (no llm_mode)
        messages.append({"role": "user", "content": prompt})

    # Call vLLM API
    try:
        gen_start = time.time()
        log.msg(_LOG_PREFIX, "Starting generation...")

        # vLLM accepts top_k / repetition_penalty / min_p via extra_body on its
        # OpenAI-compat endpoint (not standard OpenAI fields).
        extra_body: dict[str, Any] = {}
        if top_k and top_k > 0:
            extra_body["top_k"] = top_k
        if repetition_penalty and repetition_penalty != 1.0:
            extra_body["repetition_penalty"] = repetition_penalty
        min_p = kwargs.get("min_p", 0.0)
        if min_p and min_p > 0.0:
            extra_body["min_p"] = min_p
        stop_sequences = kwargs.get("stop_sequences")

        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            seed=seed,
            extra_body=extra_body if extra_body else None,
            stop=stop_sequences if stop_sequences else None,
        )

        gen_elapsed = time.time() - gen_start
        result = response.choices[0].message.content

        # Calculate tokens/sec if we have usage info
        usage_info = ""
        if hasattr(response, "usage") and response.usage:
            tokens = response.usage.completion_tokens
            if tokens and gen_elapsed > 0:
                tok_per_sec = tokens / gen_elapsed
                usage_info = f" ({tokens} tokens, {tok_per_sec:.1f} tok/s)"

        log.msg(
            _LOG_PREFIX, f"✓ Generation completed in {gen_elapsed:.1f}s{usage_info}"
        )

        from .common import clean_model_output

        cleaned_result, raw_result = clean_model_output(result)

        # For LLM mode, return tuple (cleaned, raw) for compatibility
        if llm_mode:
            return cleaned_result, raw_result

        return cleaned_result

    except Exception as e:
        error_msg = str(e)

        # Provide helpful error messages for common issues
        if "is not a multimodal model" in error_msg:
            model_name_short = (
                Path(model_name).name if "/" in model_name else model_name
            )
            log.error(
                _LOG_PREFIX,
                f"Model '{model_name_short}' is text-only, not a vision model",
            )
            log.error(_LOG_PREFIX, "Solutions:")
            log.error(
                _LOG_PREFIX,
                "  1. Use 'LLM (Text-Only)' as model_family (no image input)",
            )
            log.error(
                _LOG_PREFIX,
                "  2. Or use a multimodal model like Ministral-3B-Instruct or Mistral-Small-3.1",
            )
            raise RuntimeError(
                f"Model '{model_name_short}' is a text-only LLM, not a vision model.\n\n"
                "You're trying to analyze an image with a non-multimodal model.\n\n"
                "Solutions:\n"
                "  1. Change 'model_family' to 'LLM (Text-Only)' and remove image input\n"
                "  2. Or use a multimodal Mistral model:\n"
                "     - Ministral-3B-Instruct (3B, vision)\n"
                "     - Mistral-Small-3.1-24B (24B, vision)\n"
                "     - Mistral-Small-3.2-24B (24B, vision)"
            ) from e

        log.error(_LOG_PREFIX, f"Generation failed ({type(e).__name__})")
        log.debug(_LOG_PREFIX, f"Generation error: {e}")
        if _last_vllm_container_id and not is_container_running(
            _last_vllm_container_id
        ):
            error = docker_error_handler.diagnose_vllm_error(
                _last_vllm_container_id, timeout_occurred=False
            )
            log.error(_LOG_PREFIX, docker_error_handler.format_error_message(error))
        raise


# ==============================================================================
# MODULE EXPORTS
# ==============================================================================

__all__ = [
    # Docker availability (re-exported from docker_utils)
    "IS_WINDOWS",
    "IS_LINUX",
    "IS_MACOS",
    "DOCKER_AVAILABLE",
    "DOCKER_VERSION",
    "is_docker_available",
    "is_docker_daemon_running",
    "is_vllm_docker_available",
    "start_docker_daemon",
    "ensure_docker_running",
    # Configuration
    "load_docker_config",
    "save_docker_config",
    "get_vllm_config",
    "get_paths_config",
    "get_vllm_url",
    "get_docker_image",
    "get_models_base_path",
    "set_models_base_path",
    "get_global_docker_options",
    # Container tracking
    "get_container_for_model",
    "save_container_for_model",
    "update_container_last_used",
    "cleanup_stale_containers",
    # Container management
    "is_vllm_container_running",
    "get_running_vllm_containers",
    "is_container_exists",
    "is_container_running",
    "start_existing_container",
    "stop_vllm_container",
    "start_vllm_container",
    "auto_start_vllm_for_model",
    # vLLM API
    "is_vllm_serving_model",
    "wait_for_vllm_ready",
    "load_vllm",
    "generate_vllm",
]
