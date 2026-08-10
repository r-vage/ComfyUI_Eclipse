# MIT License
#
# Copyright (c) 2025 RenderVoid
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# SGLang Docker Integration for SML
# =============================================
#
# Alternative to vLLM for high-performance LLM inference via Docker.
# SGLang (from LMSYS team) uses RadixAttention for efficient KV cache reuse.
#
# Features:
# - OpenAI-compatible API (same interface as vLLM)
# - Better throughput for batch processing
# - RadixAttention for KV cache reuse
# - FlashInfer attention backend
# - Supports FP8 quantized models natively
#
# Docker Image: lmsysorg/sglang:latest
# Default Port: 30000
# API Endpoint: http://localhost:30000/v1

import base64
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import docker_error_handler
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

_LOG_PREFIX = "SGLang Docker"

_last_sglang_container_name = None  # Track container for error diagnosis


@dataclass(frozen=True)
class SglangContainerPlan:
    spec: ContainerSpec
    docker_command: tuple[str, ...]
    model_key: str
    model_name: str
    container_name: str
    port: int
    served_model_name: str
    gpu_ids: tuple[int, ...]


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


def is_docker_available() -> bool:
    # Quick check if Docker is installed.
    return DOCKER_AVAILABLE


def is_sglang_docker_available() -> bool:
    # Check if SGLang can run via Docker (requires Docker + GPU support).
    return DOCKER_AVAILABLE


def get_gpu_memory(gpu_idx: int = 0) -> Tuple[int, int]:
    # Get (used_mb, total_mb) for a GPU.
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                f"--id={gpu_idx}",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            if len(parts) == 2:
                return int(parts[0].strip()), int(parts[1].strip())
    except Exception:
        pass
    return 0, 0


def get_free_gpu_memory_mb(gpu_idx: int = 0) -> int:
    # Get free GPU memory in MB.
    used, total = get_gpu_memory(gpu_idx)
    return total - used if total > 0 else 0


# ==============================================================================
# CONFIGURATION MANAGEMENT
# ==============================================================================

# Configuration file path
CONFIG_DIR = Path(__file__).parent.parent.parent
CONFIG_FILE = CONFIG_DIR / "docker_config.json"

# Default SGLang configuration
DEFAULT_SGLANG_CONFIG = {
    "allow_unpinned_docker_images": False,
    "sglang": {
        "docker_image": RELEASE_DOCKER_IMAGES["sglang"]["nvidia"],
        "docker_image_rocm": RELEASE_DOCKER_IMAGES["sglang"]["amd"],
        "url": "http://localhost:30000/v1",
        "timeout": 5,
        "request_timeout": 600,
        "startup_timeout": 300,
        "container_name_prefix": "sml_sglang",
        "tp_size": 1,  # Tensor parallelism
        "dp_size": 1,  # Data parallelism
        "port": 30000,
    },
    "paths": {
        "models_base": "",
    },
    "model_containers": {},
}


def load_docker_config() -> Dict[str, Any]:
    # Load Docker config from file, or create default if not exists.
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
            # Ensure sglang section exists
            if "sglang" not in config:
                def ensure_sglang_section(latest: Dict[str, Any]) -> None:
                    latest.setdefault(
                        "sglang", DEFAULT_SGLANG_CONFIG["sglang"].copy()
                    )

                config = update_json_object(
                    CONFIG_FILE,
                    ensure_sglang_section,
                    default=DEFAULT_SGLANG_CONFIG,
                )
            return config
        except Exception as e:
            log.warning(_LOG_PREFIX, f"Error loading docker_config.json: {e}")

    # Create default config
    try:
        return update_json_object(
            CONFIG_FILE,
            lambda _config: None,
            default=DEFAULT_SGLANG_CONFIG,
        )
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error creating docker_config.json: {e}")
        return DEFAULT_SGLANG_CONFIG.copy()


def save_docker_config(config: Dict[str, Any]) -> bool:
    # Save Docker config to file.
    try:
        write_json_object(CONFIG_FILE, config)
        return True
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error saving docker_config.json: {e}")
        return False


def _update_docker_config(updater) -> bool:
    # Mutate the latest on-disk object while holding the shared JSON lock.
    try:
        update_json_object(
            CONFIG_FILE,
            updater,
            default=DEFAULT_SGLANG_CONFIG,
        )
        return True
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error updating docker_config.json: {e}")
        return False


def get_sglang_config() -> Dict[str, Any]:
    # Get SGLang-specific configuration.
    config = load_docker_config()
    return config.get("sglang", DEFAULT_SGLANG_CONFIG["sglang"].copy())


def get_paths_config() -> Dict[str, Any]:
    # Get paths configuration.
    config = load_docker_config()
    return config.get("paths", {})


def is_sglang_enabled() -> bool:
    # Check if SGLang backend is enabled.
    return get_sglang_config().get("enabled", True)


def get_sglang_url() -> str:
    # Get SGLang API URL.
    return get_sglang_config().get("url", "http://localhost:30000/v1")


def get_sglang_docker_image() -> str:
    # Resolve the configured image through the shared release-pin policy.
    from .device import detect_gpu_vendor

    full_config = load_docker_config()
    sglang_config = full_config.get("sglang", {})
    if not isinstance(sglang_config, dict):
        sglang_config = {}
    vendor = detect_gpu_vendor()
    image_key = "docker_image_rocm" if vendor == "amd" else "docker_image"
    configured_image = sglang_config.get(
        image_key,
        sglang_config.get(
            "docker_image", RELEASE_DOCKER_IMAGES["sglang"]["nvidia"]
        ),
    )
    return resolve_managed_docker_image(
        "sglang",
        configured_image,
        vendor,
        allow_unpinned=full_config.get("allow_unpinned_docker_images", False),
    )


def get_sglang_port() -> int:
    # Get SGLang port.
    return get_sglang_config().get("port", 30000)


def get_sglang_request_timeout() -> int:
    # Get request timeout for SGLang API calls.
    return get_sglang_config().get("request_timeout", 600)


def get_models_base_path() -> str:
    # Get base path for models.
    config = load_docker_config()
    return config.get("paths", {}).get("models_base", "")


def set_models_base_path(path: str) -> bool:
    # Set base path for models.
    def update_path(config: Dict[str, Any]) -> None:
        paths = config.setdefault("paths", {})
        if not isinstance(paths, dict):
            raise TypeError("paths must be a JSON object")
        paths["models_base"] = path

    return _update_docker_config(update_path)


# ==============================================================================
# MODEL-CONTAINER TRACKING
# ==============================================================================


def load_sglang_model_containers() -> Dict[str, Any]:
    # Load all SGLang model-container mappings.
    config = load_docker_config()
    containers = config.get("sglang_model_containers", {})
    return containers if isinstance(containers, dict) else {}


def get_container_for_model(model_name: str) -> Optional[Dict[str, Any]]:
    # Get container info for a model (for reuse).
    return load_sglang_model_containers().get(model_name)


def save_container_for_model(
    model_name: str,
    container_info: Dict[str, Any],
    legacy_key: str = "",
) -> bool:
    # Save container info for a model.
    def update_mapping(config: Dict[str, Any]) -> None:
        containers = config.setdefault("sglang_model_containers", {})
        if not isinstance(containers, dict):
            raise TypeError("sglang_model_containers must be a JSON object")
        containers[model_name] = container_info
        if legacy_key and legacy_key != model_name:
            containers.pop(legacy_key, None)

    return _update_docker_config(update_mapping)


def update_container_last_used(model_name: str) -> bool:
    # Update last_used timestamp for a container.
    updated = False

    def update_last_used(config: Dict[str, Any]) -> None:
        nonlocal updated
        containers = config.get("sglang_model_containers", {})
        if model_name in containers and isinstance(containers[model_name], dict):
            containers[model_name]["last_used"] = time.time()
            updated = True

    return _update_docker_config(update_last_used) and updated


def remove_container_for_model(model_name: str) -> bool:
    # Remove saved container entry for a model (e.g., container was deleted).
    removed = False

    def remove_mapping(config: Dict[str, Any]) -> None:
        nonlocal removed
        containers = config.get("sglang_model_containers", {})
        if model_name in containers:
            del containers[model_name]
            removed = True

    success = _update_docker_config(remove_mapping)
    if success and removed:
        log.debug(_LOG_PREFIX, f"Removed container entry for model {model_name}")
    return success


def cleanup_stale_containers(max_age_hours: int = 24) -> int:
    # Remove container entries older than max_age_hours.
    now = time.time()
    max_age_seconds = max_age_hours * 3600
    removed = 0

    def remove_stale(config: Dict[str, Any]) -> None:
        nonlocal removed
        containers = config.get("sglang_model_containers", {})
        for model_name in list(containers.keys()):
            mapping = containers[model_name]
            if not isinstance(mapping, dict):
                continue
            last_used = mapping.get("last_used", 0)
            if now - last_used > max_age_seconds:
                del containers[model_name]
                removed += 1

    success = _update_docker_config(remove_stale)
    return removed if success else 0


# ==============================================================================
# CONTAINER MANAGEMENT
# ==============================================================================


def _run_docker_cmd(args: List[str], timeout: int = 30) -> Tuple[bool, str]:
    # Run a Docker CLI command and preserve its diagnostic output.
    try:
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
    except Exception as error:
        return False, str(error)

    output = result.stdout.strip() if result.returncode == 0 else result.stderr.strip()
    return result.returncode == 0, output


def is_sglang_container_running() -> bool:
    # Check if any SGLang container is running.
    containers = get_running_sglang_containers()
    return len(containers) > 0


def get_running_sglang_containers() -> List[Dict[str, str]]:
    # Get list of running SGLang containers.
    if not DOCKER_AVAILABLE:
        return []

    outputs = []
    legacy_prefix = get_sglang_config().get(
        "container_name_prefix",
        "sml_sglang",
    )
    filters = [
        f"label={CONTAINER_SPEC_BACKEND_LABEL}=sglang",
        f"name={legacy_prefix}",
    ]
    for docker_filter in filters:
        success, output = _run_docker_cmd(
            [
                "ps",
                "--filter",
                docker_filter,
                "--format",
                "{{.ID}}\t{{.Names}}\t{{.Status}}",
            ],
            timeout=10,
        )
        if success and output:
            outputs.extend(output.splitlines())

    containers = []
    seen_ids = set()
    for line in outputs:
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0] not in seen_ids:
            seen_ids.add(parts[0])
            containers.append(
                {"id": parts[0], "name": parts[1], "status": parts[2]}
            )
    return containers


def is_container_exists(container_name: str) -> bool:
    # Check if a container exists (running or stopped).
    success, output = _run_docker_cmd(
        ["ps", "-aq", "--filter", f"id={container_name}"],
        timeout=5,
    )
    if success and output:
        return True
    success, output = _run_docker_cmd(
        ["ps", "-aq", "--filter", f"name={container_name}"],
        timeout=5,
    )
    return success and bool(output)


def is_container_running(container_name: str) -> bool:
    # Check if a specific container is running.
    success, output = _run_docker_cmd(
        ["ps", "-q", "--filter", f"id={container_name}"],
        timeout=5,
    )
    if success and output:
        return True
    success, output = _run_docker_cmd(
        ["ps", "-q", "--filter", f"name={container_name}"],
        timeout=5,
    )
    return success and bool(output)


def start_existing_container(container_name: str) -> bool:
    # Start an existing stopped container.
    log.msg(_LOG_PREFIX, f"Starting existing container: {container_name}")
    success, output = _run_docker_cmd(["start", container_name])
    if success:
        log.msg(_LOG_PREFIX, f"Container {container_name} started")
        return True
    log.warning(_LOG_PREFIX, f"Failed to start container: {output}")
    return False


def stop_sglang_container(container_name: Optional[str] = None) -> bool:
    # Stop SGLang container(s).
    try:
        if container_name:
            containers = [{"name": container_name}]
        else:
            containers = get_running_sglang_containers()

        if not containers:
            log.debug(_LOG_PREFIX, "No SGLang containers to stop")
            return True

        for container in containers:
            name = container.get("name", container_name)
            if not name:
                continue
            log.msg(_LOG_PREFIX, f"Stopping container: {name}")
            success, output = _run_docker_cmd(["stop", name], timeout=60)
            if success:
                log.msg(_LOG_PREFIX, f"Container {name} stopped")
            else:
                log.warning(_LOG_PREFIX, f"Failed to stop {name}: {output}")
                return False

        return True
    except Exception as e:
        log.warning(_LOG_PREFIX, f"Error stopping containers: {e}")
        return False


def remove_sglang_container(container_name: str) -> bool:
    # Remove an SGLang container.
    log.msg(_LOG_PREFIX, f"Removing container: {container_name}")
    success, output = _run_docker_cmd(["rm", "-f", container_name])
    if not success:
        log.warning(_LOG_PREFIX, f"Error removing container: {output}")
    return success


def wait_for_sglang_ready(
    url: str,
    timeout: int = 300,
    poll_interval: int = 5,
    container_name: Optional[str] = None,
) -> bool:
    # Wait for SGLang server to be ready.
    import requests  # type: ignore

    start_time = time.time()
    health_url = url.removesuffix("/v1") + "/health"

    log.msg(_LOG_PREFIX, f"Waiting for SGLang to be ready (timeout: {timeout}s)...")

    while time.time() - start_time < timeout:
        # Check if container is still running before checking health
        container_running = (
            is_container_running(container_name)
            if container_name
            else is_sglang_container_running()
        )
        if not container_running:
            # Use centralized error handler to diagnose
            if container_name:
                error = docker_error_handler.diagnose_sglang_error(
                    container_name, timeout_occurred=False
                )
                log.error(_LOG_PREFIX, docker_error_handler.format_error_message(error))
            else:
                log.error(
                    _LOG_PREFIX,
                    "Container stopped unexpectedly! Check 'docker logs sml_sglang_*' for details.",
                )
            return False

        try:
            response = requests.get(health_url, timeout=5)
            if response.status_code == 200:
                elapsed = time.time() - start_time
                log.msg(_LOG_PREFIX, f"✓ SGLang ready in {elapsed:.1f}s")
                return True
        except requests.exceptions.ConnectionError:
            pass
        except Exception as e:
            log.debug(_LOG_PREFIX, f"Health check error: {e}")

        elapsed = int(time.time() - start_time)
        if elapsed % 15 == 0 and elapsed > 0:
            log.msg(_LOG_PREFIX, f"Still waiting for SGLang... ({elapsed}s)")

        time.sleep(poll_interval)

    # Timeout occurred - use centralized error handler to diagnose
    log.warning(_LOG_PREFIX, f"SGLang did not become ready within {timeout}s")
    if container_name:
        error = docker_error_handler.diagnose_sglang_error(
            container_name, timeout_occurred=True
        )
        log.error(_LOG_PREFIX, docker_error_handler.format_error_message(error))
        if error.raw_log:
            log.debug(_LOG_PREFIX, f"Container log excerpt: {error.raw_log[:300]}")
    return False


# ==============================================================================
# SGLANG CONTAINER STARTUP
# ==============================================================================


def _get_local_sglang_image_id(image_reference: str) -> str:
    # Resolve the immutable image ID, pulling once when the image is absent.
    success, output = _run_docker_cmd(
        ["image", "inspect", image_reference, "--format", "{{.Id}}"],
        timeout=10,
    )
    if not success or not output.strip():
        log.msg(_LOG_PREFIX, f"Pulling SGLang image: {image_reference}...")
        pull_success, pull_output = _run_docker_cmd(
            ["pull", image_reference],
            timeout=1800,
        )
        if not pull_success:
            raise RuntimeError(
                f"Could not pull SGLang image {image_reference}: {pull_output}"
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


def _get_sglang_container_name(
    container_name_prefix: str,
    model_name: str,
    model_key: str,
) -> str:
    safe_prefix = "".join(
        character
        if character.isascii()
        and (character.isalnum() or character in {"-", "_", "."})
        else "-"
        for character in container_name_prefix
    ).strip("-_.")
    safe_model = "".join(
        character
        if character.isascii()
        and (character.isalnum() or character in {"-", "_"})
        else "-"
        for character in Path(model_name).stem
    ).strip("-_")
    return f"{(safe_prefix or 'sml_sglang')[:24]}-{(safe_model or 'model')[:24]}-{model_key[:12]}"


def _normalize_sglang_gpu_ids(gpu_ids: Optional[List[int]]) -> tuple[int, ...]:
    if not gpu_ids:
        return ()

    normalized = []
    for gpu_id in gpu_ids:
        if isinstance(gpu_id, bool) or not isinstance(gpu_id, int) or gpu_id < 0:
            raise ValueError("SGLang GPU IDs must be non-negative integers")
        if gpu_id not in normalized:
            normalized.append(gpu_id)
    return tuple(normalized)


def _get_sglang_gpu_environment(
    gpu_vendor: str,
    gpu_ids: tuple[int, ...],
) -> tuple[tuple[str, str], ...]:
    if not gpu_ids:
        return ()

    visible_devices = ",".join(str(gpu_id) for gpu_id in gpu_ids)
    if gpu_vendor == "amd":
        return (
            ("HIP_VISIBLE_DEVICES", visible_devices),
            ("ROCR_VISIBLE_DEVICES", visible_devices),
        )
    if gpu_vendor == "nvidia":
        return (("CUDA_VISIBLE_DEVICES", visible_devices),)
    raise ValueError("SGLang GPU IDs require a detected NVIDIA or AMD GPU")


def _effective_sglang_quantization(quantization: Optional[str]) -> str:
    if not quantization:
        return ""
    normalized = quantization.lower()
    return normalized if normalized in {"fp8", "awq", "gptq"} else ""


def _build_sglang_container_plan(
    model_path: str,
    docker_image: str,
    port: int,
    quantization: Optional[str],
    context_size: Optional[int],
    gpu_ids: Optional[List[int]],
    tp_size: int,
    dp_size: int,
    gpu_memory_utilization: float,
    container_name_prefix: str,
    model_provenance: str = "",
) -> SglangContainerPlan:
    # Build the exact fingerprint and Docker argv from one normalized request.
    from .docker_utils import (
        get_docker_bind_host,
        host_path_for_docker,
        validate_docker_image,
    )

    if (
        isinstance(tp_size, bool)
        or not isinstance(tp_size, int)
        or tp_size < 1
        or isinstance(dp_size, bool)
        or not isinstance(dp_size, int)
        or dp_size < 1
    ):
        raise ValueError("SGLang TP and DP sizes must be positive")
    if context_size is not None and (
        isinstance(context_size, bool)
        or not isinstance(context_size, int)
        or context_size < 1
    ):
        raise ValueError("SGLang context size must be a positive integer")
    if (
        isinstance(gpu_memory_utilization, bool)
        or not isinstance(gpu_memory_utilization, (int, float))
        or not 0 < gpu_memory_utilization <= 1
    ):
        raise ValueError("SGLang GPU memory utilization must be between 0 and 1")
    if not model_path:
        raise ValueError("SGLang model path must not be empty")

    model_path_obj = Path(model_path).expanduser().resolve(strict=False)
    model_name = model_path_obj.name
    model_identity = canonical_path_identity(model_path_obj)
    model_key = stable_model_key("sglang", model_identity)
    normalized_gpu_ids = _normalize_sglang_gpu_ids(gpu_ids)
    if normalized_gpu_ids and tp_size * dp_size > len(normalized_gpu_ids):
        raise ValueError(
            "SGLang TP × DP requires more GPUs than the selected GPU IDs"
        )
    container_name = _get_sglang_container_name(
        container_name_prefix,
        model_name,
        model_key,
    )

    models_base = get_models_base_path()
    mount_path = model_path_obj.parent
    container_model_path = f"/models/{model_name}"
    if models_base:
        models_base_path = Path(models_base).expanduser().resolve(strict=False)
        try:
            relative_path = model_path_obj.relative_to(models_base_path)
        except ValueError:
            pass
        else:
            mount_path = models_base_path
            container_model_path = f"/models/{relative_path.as_posix()}"

    mount_posix = host_path_for_docker(mount_path)
    image_reference = validate_docker_image(docker_image)
    image_id = _get_local_sglang_image_id(image_reference)
    bind_host = get_docker_bind_host()
    gpu_arguments = tuple(get_docker_gpu_args())
    gpu_vendor = detect_gpu_vendor() or "none"
    hardening = qualified_container_hardening("sglang", gpu_vendor)
    environment = _get_sglang_gpu_environment(gpu_vendor, normalized_gpu_ids)
    effective_quantization = _effective_sglang_quantization(quantization)
    normalized_context_size = context_size if context_size else 0
    model_mount = ContainerMount(
        source=mount_posix,
        target="/models",
        read_only=True,
    )

    spec = ContainerSpec(
        backend="sglang",
        image_reference=image_reference,
        image_id=image_id,
        bind_host=bind_host,
        host_port=port,
        container_port=port,
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
            ("context_size", normalized_context_size),
            ("dp_size", dp_size),
            ("gpu_ids", normalized_gpu_ids),
            ("gpu_memory_utilization", gpu_memory_utilization),
            ("gpu_vendor", gpu_vendor),
            ("model_provenance", model_provenance),
            ("quantization", effective_quantization),
            ("served_model_name", container_model_path),
            ("tp_size", tp_size),
        ),
    )

    docker_command = ["run", "-d", *gpu_arguments, "--name", container_name]
    for label_name, label_value in sorted(spec.docker_labels.items()):
        docker_command.extend(["--label", f"{label_name}={label_value}"])
    docker_command.extend(spec.docker_isolation_arguments)
    docker_command.extend(
        [
            "-p",
            f"{bind_host}:{port}:{port}",
            "-v",
            model_mount.docker_volume_argument,
        ]
    )
    for variable_name, variable_value in environment:
        docker_command.extend(["-e", f"{variable_name}={variable_value}"])
    docker_command.extend(
        [
            image_reference,
            "python3",
            "-m",
            "sglang.launch_server",
            "--model-path",
            container_model_path,
            "--port",
            str(port),
            "--host",
            "0.0.0.0",
            "--mem-fraction-static",
            str(gpu_memory_utilization),
            "--tp",
            str(tp_size),
            "--dp",
            str(dp_size),
        ]
    )
    if effective_quantization:
        docker_command.extend(["--quantization", effective_quantization])
    if normalized_context_size:
        docker_command.extend(["--context-length", str(normalized_context_size)])

    return SglangContainerPlan(
        spec=spec,
        docker_command=tuple(docker_command),
        model_key=model_key,
        model_name=model_name,
        container_name=container_name,
        port=port,
        served_model_name=container_model_path,
        gpu_ids=normalized_gpu_ids,
    )


def _mapping_container_name(mapping: Any) -> Optional[str]:
    if isinstance(mapping, str):
        return mapping or None
    if isinstance(mapping, dict):
        for key in ("container_name", "container_id"):
            value = mapping.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _save_sglang_plan_mapping(
    plan: SglangContainerPlan,
    container_id: str,
) -> bool:
    mapping = {
        "container_name": plan.container_name,
        "container_id": container_id,
        "model_path": plan.spec.model_identity,
        "display_name": plan.model_name,
        "port": plan.port,
        "last_used": time.time(),
    }
    mapping.update(container_mapping_spec_fields(plan.spec))
    return save_container_for_model(
        plan.model_key,
        mapping,
        legacy_key=plan.model_name,
    )


def _remove_incompatible_sglang_container(container_name: str) -> bool:
    if remove_sglang_container(container_name) or not is_container_exists(
        container_name
    ):
        return True
    log.error(
        _LOG_PREFIX,
        f"Cannot remove incompatible container '{container_name}'",
    )
    return False


def _reuse_sglang_container(
    plan: SglangContainerPlan,
    wait_for_ready: bool,
) -> tuple[bool, Optional[str]]:
    mappings = load_sglang_model_containers()
    current_mapping = _mapping_container_name(mappings.get(plan.model_key))
    candidates = []
    for mapping_key in (plan.model_key, plan.model_name):
        candidate = _mapping_container_name(mappings.get(mapping_key))
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
                "Recreating SGLang container "
                f"({reuse_check.reason.replace('_', ' ')})...",
            )
            if not _remove_incompatible_sglang_container(candidate):
                return True, None
            continue

        global _last_sglang_container_name
        _last_sglang_container_name = candidate
        if is_container_running(candidate):
            log.msg(_LOG_PREFIX, f"Reusing exact container: {candidate}")
            if current_mapping != candidate or not mapping_matches_container_spec(
                mappings.get(plan.model_key), plan.spec
            ):
                _save_sglang_plan_mapping(plan, candidate)
            else:
                update_container_last_used(plan.model_key)
            if wait_for_ready and not wait_for_sglang_ready(
                f"http://localhost:{plan.port}/v1",
                timeout=30,
                container_name=candidate,
            ):
                return True, None
            return True, candidate

        for running in get_running_sglang_containers():
            running_name = running.get("name")
            if running_name and running_name != candidate:
                if not stop_sglang_container(running_name):
                    return True, None

        log.msg(_LOG_PREFIX, f"Restarting exact container: {candidate}")
        started, output = _run_docker_cmd(["start", candidate])
        if not started:
            log.warning(_LOG_PREFIX, f"Failed to restart container: {output}")
            if not _remove_incompatible_sglang_container(candidate):
                return True, None
            continue

        if wait_for_ready and not wait_for_sglang_ready(
            f"http://localhost:{plan.port}/v1",
            timeout=get_sglang_config().get("startup_timeout", 300),
            container_name=candidate,
        ):
            if not _remove_incompatible_sglang_container(candidate):
                return True, None
            continue

        if current_mapping != candidate or not mapping_matches_container_spec(
            mappings.get(plan.model_key), plan.spec
        ):
            _save_sglang_plan_mapping(plan, candidate)
        else:
            update_container_last_used(plan.model_key)
        return True, candidate

    return False, None


def start_sglang_container(
    model_path: str,
    quantization: Optional[str] = None,
    context_size: Optional[int] = None,
    gpu_ids: Optional[List[int]] = None,
    tp_size: Optional[int] = None,
    dp_size: Optional[int] = None,
    wait_for_ready: bool = True,
    model_provenance: str = "",
) -> Optional[str]:
    # Start or reuse the exact managed SGLang container for this request.
    if not ensure_docker_running():
        log.error(_LOG_PREFIX, "Docker is not running")
        return None

    try:
        sglang_config = get_sglang_config()
        docker_image = get_sglang_docker_image()
        port = get_sglang_port()
        tp = (
            tp_size
            if tp_size is not None
            else sglang_config.get("tp_size", 1)
        )
        dp = (
            dp_size
            if dp_size is not None
            else sglang_config.get("dp_size", 1)
        )

        from .backend_vllm_docker import get_global_docker_options

        gpu_util = get_global_docker_options().get(
            "gpu_memory_utilization",
            0.9,
        )
        plan = _build_sglang_container_plan(
            model_path=model_path,
            docker_image=docker_image,
            port=port,
            quantization=quantization,
            context_size=context_size,
            gpu_ids=gpu_ids,
            tp_size=tp,
            dp_size=dp,
            gpu_memory_utilization=gpu_util,
            container_name_prefix=sglang_config.get(
                "container_name_prefix",
                "sml_sglang",
            ),
            model_provenance=model_provenance,
        )

        reuse_handled, reused_container = _reuse_sglang_container(
            plan,
            wait_for_ready,
        )
        if reuse_handled:
            return reused_container

        for running in get_running_sglang_containers():
            running_name = running.get("name")
            if running_name and not stop_sglang_container(running_name):
                return None

        memory_gpu_id = plan.gpu_ids[0] if plan.gpu_ids else 0
        free_mem = get_free_gpu_memory_mb(memory_gpu_id)
        if free_mem < 4000:
            log.warning(
                _LOG_PREFIX,
                f"Low GPU memory: {free_mem}MB free. Model may not load.",
            )

        log.msg(_LOG_PREFIX, f"Starting SGLang container for: {plan.model_name}")
        log.debug(
            _LOG_PREFIX,
            f"Docker command: {' '.join(plan.docker_command)}",
        )
        success, output = _run_docker_cmd(
            list(plan.docker_command),
            timeout=60,
        )
        if not success:
            log.error(_LOG_PREFIX, f"Failed to start container: {output}")
            return None

        container_id = output.strip()
        global _last_sglang_container_name
        _last_sglang_container_name = plan.container_name
        log.msg(
            _LOG_PREFIX,
            f"Container started: {plan.container_name} ({container_id[:12]})",
        )

        creation_check = inspect_container_reuse(
            plan.container_name,
            plan.spec,
            _run_docker_cmd,
        )
        if not creation_check.reusable:
            log.error(
                _LOG_PREFIX,
                "New SGLang container identity could not be verified: "
                f"{creation_check.reason}",
            )
            _remove_incompatible_sglang_container(plan.container_name)
            return None

        if wait_for_ready and not wait_for_sglang_ready(
            f"http://localhost:{plan.port}/v1",
            timeout=sglang_config.get("startup_timeout", 300),
            container_name=plan.container_name,
        ):
            log.error(_LOG_PREFIX, "SGLang failed to start within timeout")
            stop_sglang_container(plan.container_name)
            return None

        _save_sglang_plan_mapping(plan, container_id)
        return plan.container_name

    except Exception as error:
        log.error(_LOG_PREFIX, f"Error starting container: {error}")
        return None


def auto_start_sglang_for_model(
    model_path: str,
    config: Optional[Dict[str, Any]] = None,
    quantization: Optional[str] = None,
    context_size: Optional[int] = None,
    model_provenance: str = "",
    tp_size: Optional[int] = None,
    dp_size: Optional[int] = None,
) -> bool:
    # Auto-start SGLang for a specific model.
    #
    # Args:
    #     model_path: Path to the model
    #     config: Optional config override
    #     quantization: Quantization method
    #     context_size: Context length
    #
    # Returns:
    #     True if container started successfully
    global _last_sglang_container_name
    container_name = start_sglang_container(
        model_path,
        quantization=quantization,
        context_size=context_size,
        model_provenance=model_provenance,
        tp_size=tp_size,
        dp_size=dp_size,
    )
    if container_name:
        _last_sglang_container_name = container_name

    return container_name is not None


# ==============================================================================
# SGLANG MODEL LOADING & GENERATION
# ==============================================================================


def is_sglang_available() -> bool:
    # Check if SGLang server is running and accessible.
    try:
        if not is_sglang_enabled():
            return False

        url = get_sglang_url()
        timeout = get_sglang_config().get("timeout", 5)

        import requests  # type: ignore

        response = requests.get(f"{url.removesuffix('/v1')}/health", timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


def is_sglang_serving_model(model_path: str) -> Optional[str]:
    # Check if SGLang server is serving the specified model.
    #
    # Args:
    #     model_path: Path to model folder or model name
    #
    # Returns:
    #     str: The model ID if found
    #     None: If server not running or model not found
    try:
        from openai import OpenAI  # type: ignore

        if not is_sglang_enabled():
            return None

        url = get_sglang_url()
        timeout = get_sglang_config().get("timeout", 5)
        request_timeout = get_sglang_request_timeout()

        # Quick health check
        import requests  # type: ignore

        response = requests.get(f"{url.removesuffix('/v1')}/health", timeout=timeout)
        if response.status_code != 200:
            return None

        # Check models
        client = OpenAI(base_url=url, api_key="not-needed", timeout=request_timeout)
        models = client.models.list()
        available_models = [m.id for m in models.data]

        # Extract model name from path
        model_name = Path(model_path).name

        # Try to find matching model
        for available in available_models:
            if model_name in available or available in model_name:
                return available

        return None

    except Exception as e:
        log.debug(_LOG_PREFIX, f"Error checking SGLang model: {e}")
        return None


def load_sglang(
    model_path: str,
    quantization: Optional[str] = None,
    context_size: Optional[int] = None,
    model_provenance: str = "",
    tp_size: Optional[int] = None,
    dp_size: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    # Load a model via SGLang Docker.
    #
    # This function is model-agnostic - works with any model supported by SGLang.
    #
    # Args:
    #     model_path: Full path to model folder
    #     quantization: Quantization method (fp8, awq, gptq, or None)
    #     context_size: Maximum context window size
    #
    # Returns:
    #     Dict with SGLang client info, or None if unavailable
    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        log.warning(_LOG_PREFIX, "Requires openai package: pip install openai")
        return None

    if not is_sglang_docker_available():
        log.warning(_LOG_PREFIX, "SGLang not available (Docker not found)")
        return None

    if not ensure_docker_running():
        log.warning(_LOG_PREFIX, "Docker is not running and could not be started")
        return None

    url = get_sglang_url()
    model_name = Path(model_path).name
    log.msg(_LOG_PREFIX, f"Validating container for {model_name}...")
    try:
        if not auto_start_sglang_for_model(
            model_path,
            quantization=quantization,
            context_size=context_size,
            model_provenance=model_provenance,
            tp_size=tp_size,
            dp_size=dp_size,
        ):
            log.warning(_LOG_PREFIX, "Failed to start or validate container")
            return None
    except Exception as e:
        log.warning(_LOG_PREFIX, f"Container start error: {e}")
        return None

    matched_model = is_sglang_serving_model(model_path)
    if not matched_model:
        log.warning(_LOG_PREFIX, "Container ready but model not detected")
        return None

    # Model found - create client
    request_timeout = get_sglang_request_timeout()
    client = OpenAI(base_url=url, api_key="not-needed", timeout=request_timeout)

    # Update last used timestamp
    model_identity = canonical_path_identity(model_path)
    update_container_last_used(stable_model_key("sglang", model_identity))

    log.debug(_LOG_PREFIX, "Using SGLang (Docker) backend")
    log.debug(_LOG_PREFIX, f"Model: {matched_model}")
    log.msg(_LOG_PREFIX, "✓ SGLang optimized inference enabled")

    return {"mode": "sglang", "client": client, "model_name": matched_model}


def generate_sglang(
    smart_lm_instance,
    prompt: str,
    image_paths: Optional[list] = None,
    max_tokens: int = 512,
    temperature: float = 0.7,
    top_p: float = 0.9,
    top_k: int = 50,
    seed: Optional[int] = None,
    llm_mode: Optional[str] = None,
    instruction_template: str = "",
    repetition_penalty: float = 1.0,
    vision_task: Optional[str] = None,
    use_few_shot: bool = True,
    **kwargs,
) -> str | tuple[str, str]:
    # Generate text using SGLang API (OpenAI-compatible).
    #
    # This function is model-agnostic - works with ANY model served by SGLang.
    #
    # Args:
    #     smart_lm_instance: The SmartLM instance with sglang_client
    #     prompt: Text prompt
    #     image_paths: Optional list of image paths for vision models
    #     max_tokens: Maximum tokens to generate
    #     temperature: Sampling temperature
    #     top_p: Nucleus sampling parameter
    #     top_k: Top-k sampling (passed to SGLang via extra_body)
    #     seed: Random seed for reproducibility
    #     llm_mode: LLM mode key for few-shot examples
    #     instruction_template: Custom instruction template
    #     repetition_penalty: Repetition penalty (passed to SGLang via extra_body)
    #
    # Returns:
    #     Generated text (or tuple (cleaned, raw) for LLM mode)
    log.debug(
        _LOG_PREFIX,
        f"generate_sglang: model={getattr(smart_lm_instance, 'sglang_model_name', 'unknown')}",
    )
    log.debug(_LOG_PREFIX, f"  prompt={prompt[:100] if prompt else 'None'}...")
    log.debug(_LOG_PREFIX, f"  image_paths={image_paths}")
    log.debug(_LOG_PREFIX, f"  llm_mode={llm_mode}")

    client = smart_lm_instance.sglang_client
    model_name = smart_lm_instance.sglang_model_name

    # Build messages
    messages = []

    if image_paths and len(image_paths) > 0:
        # Vision + text (multimodal)
        # Eclipse 3.5+ passes system + user separately via system_prompt kwarg.
        system_prompt = kwargs.get("system_prompt")
        user_message = ""

        if system_prompt is not None:
            user_message = (prompt or "").strip()
        elif "\n\n" in prompt:
            parts = prompt.split("\n\n", 1)
            system_prompt = parts[0].strip()
            if len(parts) > 1:
                remaining = parts[1].strip()
                if remaining.startswith("Additional context:"):
                    user_message = remaining.replace("Additional context:", "").strip()
                elif remaining:
                    user_message = remaining
        else:
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

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Inject text-only few-shot examples to guide output style (no prefixes, uncensored)
        if vision_task and use_few_shot:
            from .config_templates import get_vision_few_shot_messages

            few_shot = get_vision_few_shot_messages(vision_task)
            if few_shot:
                messages.extend(few_shot)

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
        # Simple text only
        messages.append({"role": "user", "content": prompt})

    # Call SGLang API
    try:
        gen_start = time.time()
        log.msg(_LOG_PREFIX, "Starting generation...")

        # SGLang accepts top_k / repetition_penalty / min_p via extra_body on its
        # OpenAI-compat endpoint (not standard OpenAI fields).
        extra_body: Dict[str, Any] = {}
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

        # Calculate tokens/sec
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

        if "is not a multimodal model" in error_msg or "image" in error_msg.lower():
            model_name_short = (
                Path(model_name).name if "/" in model_name else model_name
            )
            log.error(_LOG_PREFIX, f"Model '{model_name_short}' may not support vision")
            raise RuntimeError(
                f"Model '{model_name_short}' may not support image input.\n\n"
                "Try using a multimodal model or remove image input."
            ) from e

        log.error(_LOG_PREFIX, f"SGLang generation failed ({type(e).__name__})")
        log.debug(_LOG_PREFIX, f"SGLang generation error: {e}")
        if _last_sglang_container_name and not is_container_running(
            _last_sglang_container_name
        ):
            error = docker_error_handler.diagnose_sglang_error(
                _last_sglang_container_name, timeout_occurred=False
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
    "is_sglang_docker_available",
    "start_docker_daemon",
    "ensure_docker_running",
    # Configuration
    "load_docker_config",
    "save_docker_config",
    "get_sglang_config",
    "get_paths_config",
    "get_sglang_url",
    "get_sglang_docker_image",
    "get_sglang_port",
    "get_models_base_path",
    "set_models_base_path",
    # Container tracking
    "load_sglang_model_containers",
    "get_container_for_model",
    "save_container_for_model",
    "update_container_last_used",
    "cleanup_stale_containers",
    # Container management
    "is_sglang_container_running",
    "get_running_sglang_containers",
    "is_container_exists",
    "is_container_running",
    "start_existing_container",
    "stop_sglang_container",
    "remove_sglang_container",
    "start_sglang_container",
    "auto_start_sglang_for_model",
    # SGLang API
    "is_sglang_available",
    "is_sglang_serving_model",
    "wait_for_sglang_ready",
    "load_sglang",
    "generate_sglang",
]
