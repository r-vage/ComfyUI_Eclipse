# Ollama/Docker integration for SML.
#
# Ollama is a lightweight LLM server that excels at:
# - GGUF model support (native)
# - Easy model management (ollama pull/run)
# - Mistral3 support (as of late 2024)
# - Low memory overhead
#
# API: OpenAI-compatible at /v1/chat/completions (with OLLAMA_ORIGINS=*)
# Docker image: ollama/ollama

import hashlib
import json
import re
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests  # type: ignore

from . import docker_error_handler
from .config_templates import (
    TemplateContext,
    get_llm_models_absolute_path,
    get_llm_models_path,
    infer_model_family_from_name,
    infer_model_type_from_name,
)
from .container_spec import (
    DEFAULT_CONTAINER_SECURITY_OPTIONS,
    qualified_container_hardening,
    ContainerMount,
    ContainerSpec,
    inspect_container_reuse,
)
from .device import get_docker_gpu_args
from .docker_image_policy import resolve_managed_docker_image
from .json_store import update_json_object, write_json_object
from .logger import log

_LOG_PREFIX = "Ollama Docker"


# ==============================================================================
# DOCKER DAEMON MANAGEMENT (centralized in docker_utils)
# ==============================================================================

from .docker_utils import (
    ensure_docker_running as _ensure_docker_running,
)
from .docker_utils import (
    get_cached_daemon_status,
    get_docker_version,
    is_docker_daemon_running,
    is_docker_installed,
    start_docker_daemon,
)

# Module-level availability flags (used throughout this file)
# Uses cached values from docker_utils — no extra subprocess calls at import time
DOCKER_AVAILABLE = is_docker_installed()
DOCKER_VERSION = get_docker_version()


# ==============================================================================
# CONSTANTS
# ==============================================================================

OLLAMA_DEFAULT_PORT = 11434
OLLAMA_CONTAINER_NAME = "sml-ollama"

# Default images per GPU vendor
_OLLAMA_IMAGE_NVIDIA = "ollama/ollama"

_OLLAMA_MODEL_COMPONENT = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_OLLAMA_MODEL_NAME_PATTERN = re.compile(
    rf"^{_OLLAMA_MODEL_COMPONENT}(?:/{_OLLAMA_MODEL_COMPONENT})*"
    rf"(?::{_OLLAMA_MODEL_COMPONENT})?$"
)
_OLLAMA_MODEL_NAME_MAX_LENGTH = 255
_OLLAMA_QUANTIZATIONS = {
    "q4_k_s": "q4_K_S",
    "q4_k_m": "q4_K_M",
    "q8_0": "q8_0",
}


def get_ollama_docker_image() -> str:
    # Resolve the configured image through the shared release-pin policy.
    from .device import detect_gpu_vendor

    full_config = _load_full_config()
    ollama_config = full_config.get("ollama", {})
    if not isinstance(ollama_config, dict):
        ollama_config = {}
    vendor = detect_gpu_vendor()
    image_key = "docker_image_rocm" if vendor == "amd" else "docker_image"
    configured_image = ollama_config.get(
        image_key,
        ollama_config.get("docker_image", _OLLAMA_IMAGE_NVIDIA),
    )
    return resolve_managed_docker_image(
        "ollama",
        configured_image,
        vendor,
        allow_unpinned=full_config.get("allow_unpinned_docker_images", False),
    )


# Ollama model name mappings for common models
# Maps local GGUF filename patterns to Ollama model names
# NOTE: Ministral 3 requires Ollama 0.13.1+
OLLAMA_MODEL_MAPPINGS = {
    "ministral-3": "ministral-3",  # Ministral 3 family (3b, 8b, 14b) - requires Ollama 0.13.1+
    "ministral": "ministral-3",  # Alias for Ministral 3
    "mistral-7b": "mistral",
    "mistral-8x7b": "mixtral",
    "llama-3": "llama3",
    "llama-2": "llama2",
    "qwen2": "qwen2",
    "phi-3": "phi3",
    "gemma": "gemma",
}


# ==============================================================================
# CONFIGURATION
# ==============================================================================

_CONFIG_PATH = Path(__file__).parent.parent.parent / "docker_config.json"


def _get_ollama_config() -> Dict[str, Any]:
    # Get Ollama-specific configuration from docker_config.json.
    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
                return config.get("ollama", {})
    except Exception as e:
        log.debug(_LOG_PREFIX, f"Could not load ollama config: {e}")

    return {
        "docker_image": _OLLAMA_IMAGE_NVIDIA,
        "port": OLLAMA_DEFAULT_PORT,
        "gpu_layers": -1,  # -1 = all layers on GPU
    }


def get_ollama_startup_timeout() -> int:
    # Get Ollama startup timeout from config (default 120s / 2 min).
    config = _get_ollama_config()
    return config.get("startup_timeout", 120)


def get_ollama_request_timeout() -> int:
    # Get Ollama request timeout from config (default 300s / 5 min).
    config = _get_ollama_config()
    return config.get("request_timeout", 300)


def get_ollama_pull_timeout() -> int:
    # Get Ollama model pull timeout from config (default 1800s / 30 min).
    config = _get_ollama_config()
    return config.get("pull_timeout", 1800)


def _save_ollama_config(ollama_config: Dict[str, Any]):
    # Save Ollama configuration to docker_config.json.
    try:
        update_json_object(
            _CONFIG_PATH,
            lambda config: config.update({"ollama": ollama_config}),
        )
    except Exception as e:
        log.error(_LOG_PREFIX, f"Could not save ollama config: {e}")


def _load_full_config() -> Dict[str, Any]:
    # Load full docker_config.json.
    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log.debug(_LOG_PREFIX, f"Could not load config: {e}")
    return {}


def _save_full_config(config: Dict[str, Any]):
    # Save full docker_config.json.
    try:
        write_json_object(_CONFIG_PATH, config)
    except Exception as e:
        log.error(_LOG_PREFIX, f"Could not save config: {e}")


# ==============================================================================
# DOCKER HELPERS
# ==============================================================================


def _run_docker_cmd(args: List[str], timeout: int = 30) -> tuple[bool, str]:
    # Run a docker command and return (success, output).
    if not DOCKER_AVAILABLE:
        return False, "Docker not available"

    try:
        result = subprocess.run(
            ["docker"] + args,
            capture_output=True,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",  # Handle non-UTF8 bytes gracefully on Windows
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
        return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def _validate_ollama_model_name(model_name: str) -> str:
    # Validate local Ollama create names before passing them to the CLI.
    if not model_name or len(model_name) > _OLLAMA_MODEL_NAME_MAX_LENGTH:
        raise ValueError(
            f"Ollama model name must contain 1-{_OLLAMA_MODEL_NAME_MAX_LENGTH} characters"
        )
    if not _OLLAMA_MODEL_NAME_PATTERN.fullmatch(model_name):
        raise ValueError(
            "Invalid Ollama model name. Use ASCII letters, numbers, '.', '_', or "
            "'-', with optional namespace segments and one ':tag'."
        )
    return model_name


def _validate_ollama_quantization(quantize: Optional[str]) -> Optional[str]:
    # Restrict create-time quantization to levels documented by Ollama.
    if quantize is None:
        return None
    normalized = _OLLAMA_QUANTIZATIONS.get(quantize.casefold())
    if normalized is None:
        supported = ", ".join(_OLLAMA_QUANTIZATIONS.values())
        raise ValueError(
            f"Unsupported Ollama quantization '{quantize}'. Supported: {supported}"
        )
    return normalized


def _default_ollama_model_name(raw_name: str, prefix: str = "") -> str:
    # Produce a stable valid name for local files, including Unicode-only names.
    ascii_name = (
        unicodedata.normalize("NFKD", raw_name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    cleaned = re.sub(r"[^a-z0-9._-]+", "_", ascii_name.replace("-", "_"))
    cleaned = cleaned.strip("._-")
    if not cleaned:
        digest = hashlib.sha256(raw_name.encode("utf-8")).hexdigest()[:12]
        cleaned = f"model_{digest}"
    return _validate_ollama_model_name(f"{prefix}{cleaned}")


def _validate_ollama_source_path(source_path: str) -> str:
    # Modelfile FROM receives a container path, never a host path or instruction.
    if not source_path.startswith("/models/"):
        raise ValueError("Ollama import source must be inside the /models mount")
    if len(source_path) > 4096:
        raise ValueError("Ollama import source path is too long")
    if any(ord(char) < 32 or ord(char) == 127 for char in source_path):
        raise ValueError("Ollama import source path contains control characters")
    if ".." in Path(source_path).parts:
        raise ValueError("Ollama import source path cannot contain parent traversal")
    return source_path


def _create_ollama_model_from_path(
    source_path: str,
    model_name: str,
    quantize: Optional[str] = None,
    timeout: int = 300,
) -> tuple[bool, str]:
    # Create a local Ollama model without passing model-controlled text to a shell.
    validated_source = _validate_ollama_source_path(source_path)
    validated_name = _validate_ollama_model_name(model_name)
    validated_quantize = _validate_ollama_quantization(quantize)
    host_modelfile: Optional[Path] = None
    container_modelfile: Optional[str] = None
    container_copy_attempted = False
    copied_to_container = False

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="sml-ollama-",
            suffix=".Modelfile",
            delete=False,
        ) as temp_file:
            temp_file.write(f"FROM {validated_source}\n")
            host_modelfile = Path(temp_file.name)

        container_modelfile = f"/tmp/{host_modelfile.name}"
        container_copy_attempted = True
        copy_success, copy_output = _run_docker_cmd(
            [
                "cp",
                str(host_modelfile),
                f"{OLLAMA_CONTAINER_NAME}:{container_modelfile}",
            ],
            timeout=30,
        )
        if not copy_success:
            return False, f"Failed to copy temporary Modelfile: {copy_output}"
        copied_to_container = True

        create_args = ["exec", OLLAMA_CONTAINER_NAME, "ollama", "create"]
        if validated_quantize:
            create_args.extend(["--quantize", validated_quantize])
        create_args.extend([validated_name, "-f", container_modelfile])
        return _run_docker_cmd(create_args, timeout=timeout)
    finally:
        if container_copy_attempted and container_modelfile:
            try:
                cleanup_success, cleanup_output = _run_docker_cmd(
                    [
                        "exec",
                        OLLAMA_CONTAINER_NAME,
                        "rm",
                        "-f",
                        container_modelfile,
                    ],
                    timeout=10,
                )
                if not cleanup_success and copied_to_container:
                    log.warning(
                        _LOG_PREFIX,
                        f"Could not remove temporary container Modelfile: {cleanup_output}",
                    )
            except Exception as cleanup_error:
                if copied_to_container:
                    log.warning(
                        _LOG_PREFIX,
                        f"Could not remove temporary container Modelfile: {cleanup_error}",
                    )
        if host_modelfile:
            try:
                host_modelfile.unlink(missing_ok=True)
            except OSError as cleanup_error:
                log.warning(
                    _LOG_PREFIX,
                    f"Could not remove temporary host Modelfile: {cleanup_error}",
                )


def is_ollama_container_running() -> bool:
    # Check if Ollama container is running.
    success, output = _run_docker_cmd(
        ["ps", "-q", "-f", f"name={OLLAMA_CONTAINER_NAME}"]
    )
    return success and bool(output.strip())


def is_ollama_container_exists() -> bool:
    # Check if Ollama container exists (running or stopped).
    success, output = _run_docker_cmd(
        ["ps", "-aq", "-f", f"name={OLLAMA_CONTAINER_NAME}"]
    )
    return success and bool(output.strip())


# ==============================================================================
# DOCKER IMAGE MANAGEMENT
# ==============================================================================


def is_image_available(image_name: str) -> bool:
    # Check if a Docker image is available locally.
    # `docker images -q` does not resolve a tag-plus-digest reference even
    # after that exact image has been pulled. Inspect resolves tags, digests,
    # and tag-plus-digest release pins consistently.
    success, output = _run_docker_cmd(
        ["image", "inspect", image_name, "--format", "{{.Id}}"],
        timeout=10,
    )
    return success and bool(output.strip())


def pull_docker_image(image_name: str, timeout: int = 300) -> bool:
    # Pull a Docker image from registry.
    #
    # Args:
    #     image_name: Image to pull (e.g., "ollama/ollama")
    #     timeout: Maximum seconds to wait for pull (default 5 minutes)
    #
    # Returns:
    #     bool: True if image was pulled successfully
    log.msg(
        _LOG_PREFIX,
        f"Pulling Docker image: {image_name} (this may take a few minutes)...",
    )

    try:
        # Use longer timeout for image pull
        result = subprocess.run(
            ["docker", "pull", image_name],
            capture_output=True,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",  # Handle non-UTF8 bytes gracefully on Windows
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )

        if result.returncode == 0:
            log.msg(_LOG_PREFIX, f"✓ Image {image_name} pulled successfully")
            return True
        else:
            log.error(_LOG_PREFIX, f"Failed to pull image: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        log.error(
            _LOG_PREFIX,
            f"Image pull timed out after {timeout}s - check your internet connection",
        )
        return False
    except Exception as e:
        log.error(_LOG_PREFIX, f"Failed to pull image: {e}")
        return False


def ensure_ollama_image() -> bool:
    # Ensure the Ollama Docker image is available locally.
    # Pulls it if not present.
    #
    # Returns:
    #     bool: True if image is available
    ollama_image = get_ollama_docker_image()
    if is_image_available(ollama_image):
        log.debug(_LOG_PREFIX, f"Image {ollama_image} is available locally")
        return True

    log.msg(_LOG_PREFIX, f"Image {ollama_image} not found locally, downloading...")
    return pull_docker_image(ollama_image)


# ==============================================================================
# CONTAINER LIFECYCLE
# ==============================================================================


def _get_local_ollama_image_id(image_reference: str) -> str:
    # Resolve the immutable local image ID used by the requested image reference.
    success, output = _run_docker_cmd(
        ["image", "inspect", image_reference, "--format", "{{.ID}}"],
        timeout=10,
    )
    image_id = output.strip() if success else ""
    if not image_id:
        raise RuntimeError(
            f"Could not inspect local Docker image ID for {image_reference}: {output}"
        )
    return image_id


def _get_gpu_vendor_from_arguments(gpu_arguments: tuple[str, ...]) -> str:
    # Record the effective GPU mode represented by the actual Docker arguments.
    if "--gpus" in gpu_arguments:
        return "nvidia"
    if any("/dev/kfd" in argument for argument in gpu_arguments):
        return "amd"
    return "none"


def _build_ollama_container_plan(port: int) -> tuple[ContainerSpec, List[str]]:
    # Build the fingerprint and Docker argv from the same normalized values.
    from .docker_utils import (
        get_docker_bind_host,
        host_path_for_docker,
        validate_docker_image,
    )

    if isinstance(port, bool) or not isinstance(port, int):
        raise TypeError("Ollama Docker port must be an integer")
    if not 1 <= port <= 65535:
        raise ValueError("Ollama Docker port must be between 1 and 65535")

    image_reference = validate_docker_image(get_ollama_docker_image())
    image_id = _get_local_ollama_image_id(image_reference)
    bind_host = get_docker_bind_host(_load_full_config())
    gpu_arguments = tuple(get_docker_gpu_args())
    gpu_vendor = _get_gpu_vendor_from_arguments(gpu_arguments)
    hardening = qualified_container_hardening("ollama", gpu_vendor)
    environment = (
        ("OLLAMA_HOST", "0.0.0.0"),
        ("OLLAMA_ORIGINS", "*"),
    )

    models_base = Path(get_llm_models_absolute_path()).resolve()
    mounts = []
    volume_arguments = []
    if models_base.exists():
        ollama_models_dir = models_base / "ollama"
        ollama_models_dir.mkdir(parents=True, exist_ok=True)

        storage_source = host_path_for_docker(ollama_models_dir)
        models_source = host_path_for_docker(models_base)
        mounts.extend(
            [
                ContainerMount(
                    source=storage_source,
                    target="/root/.ollama",
                    read_only=False,
                ),
                ContainerMount(
                    source=models_source,
                    target="/models",
                    read_only=True,
                ),
            ]
        )
        volume_arguments.extend(
            [
                "-v",
                f"{storage_source}:/root/.ollama",
                "-v",
                f"{models_source}:/models:ro",
            ]
        )

    spec = ContainerSpec(
        backend="ollama",
        image_reference=image_reference,
        image_id=image_id,
        bind_host=bind_host,
        host_port=port,
        container_port=OLLAMA_DEFAULT_PORT,
        mounts=tuple(mounts),
        gpu_arguments=gpu_arguments,
        environment=environment,
        security_options=DEFAULT_CONTAINER_SECURITY_OPTIONS,
        capability_drops=hardening.capability_drops,
        read_only_rootfs=hardening.read_only_rootfs,
        tmpfs_mounts=hardening.tmpfs_mounts,
        model_identity="ollama-server",
        settings=(
            ("container_name", OLLAMA_CONTAINER_NAME),
            ("gpu_vendor", gpu_vendor),
        ),
    )

    docker_cmd = [
        "run",
        "-d",
        "--name",
        OLLAMA_CONTAINER_NAME,
    ]
    for label_name, label_value in sorted(spec.docker_labels.items()):
        docker_cmd.extend(["--label", f"{label_name}={label_value}"])
    docker_cmd.extend(spec.docker_isolation_arguments)
    docker_cmd.extend(gpu_arguments)
    docker_cmd.extend(
        [
            "-p",
            f"{bind_host}:{port}:{OLLAMA_DEFAULT_PORT}",
        ]
    )
    for name, value in environment:
        docker_cmd.extend(["-e", f"{name}={value}"])
    docker_cmd.extend(volume_arguments)
    docker_cmd.append(image_reference)
    return spec, docker_cmd


def _remove_ollama_container_for_recreation(reason: str) -> bool:
    # Never reuse a container whose creation identity cannot be proven.
    log.msg(
        _LOG_PREFIX,
        f"Recreating Ollama container ({reason.replace('_', ' ')})...",
    )
    success, output = _run_docker_cmd(
        ["rm", "-f", OLLAMA_CONTAINER_NAME],
        timeout=30,
    )
    if success or not is_ollama_container_exists():
        return True
    log.error(
        _LOG_PREFIX,
        f"Could not remove incompatible container '{OLLAMA_CONTAINER_NAME}': {output}",
    )
    return False


def start_ollama_container(
    port: Optional[int] = None,
    gpu_layers: int = -1,
) -> bool:
    # Start Ollama Docker container.
    #
    # Ollama runs as a persistent server - models are loaded/unloaded via API.
    # Unlike vLLM, we don't need to specify the model at container start.
    #
    # Args:
    #     port: Port to expose (default: 11434)
    #     gpu_layers: Number of layers to offload to GPU (-1 = all)
    #
    # Returns:
    #     bool: True if container started successfully
    # Ensure Docker daemon is running (auto-start on Windows)
    if not ensure_docker_running():
        log.error(_LOG_PREFIX, "Docker is not available or could not be started")
        return False

    # Ensure Docker image is available (auto-pull if needed)
    if not ensure_ollama_image():
        log.error(
            _LOG_PREFIX,
            "Failed to get Ollama Docker image - check your internet connection",
        )
        return False

    config = _get_ollama_config()
    requested_port = port if port is not None else config.get(
        "port", OLLAMA_DEFAULT_PORT
    )
    try:
        expected_spec, docker_cmd = _build_ollama_container_plan(requested_port)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        log.error(_LOG_PREFIX, f"Could not build Ollama container plan: {error}")
        return False

    container_running = is_ollama_container_running()
    container_exists = container_running or is_ollama_container_exists()
    if container_exists:
        reuse_check = inspect_container_reuse(
            OLLAMA_CONTAINER_NAME,
            expected_spec,
            _run_docker_cmd,
        )
        if reuse_check.reusable:
            if container_running:
                log.msg(_LOG_PREFIX, "✓ Ollama container already running")
                return True
            log.msg(_LOG_PREFIX, "Restarting existing Ollama container...")
            success, output = _run_docker_cmd(["start", OLLAMA_CONTAINER_NAME])
            if success:
                log.msg(_LOG_PREFIX, "✓ Ollama container restarted")
                startup_timeout = get_ollama_startup_timeout()
                return wait_for_ollama_ready(timeout=startup_timeout)
            log.warning(_LOG_PREFIX, f"Failed to restart container: {output}")
            if not _remove_ollama_container_for_recreation("restart_failed"):
                return False
        elif not _remove_ollama_container_for_recreation(reuse_check.reason):
            return False

    log.msg(_LOG_PREFIX, "Starting new Ollama container...")

    success, output = _run_docker_cmd(docker_cmd, timeout=60)

    if success:
        creation_check = inspect_container_reuse(
            OLLAMA_CONTAINER_NAME,
            expected_spec,
            _run_docker_cmd,
        )
        if not creation_check.reusable:
            log.error(
                _LOG_PREFIX,
                "New Ollama container failed identity verification "
                f"({creation_check.reason.replace('_', ' ')})",
            )
            _remove_ollama_container_for_recreation(
                f"new_container_{creation_check.reason}"
            )
            return False
        log.msg(_LOG_PREFIX, f"✓ Ollama container started on port {requested_port}")
        startup_timeout = get_ollama_startup_timeout()
        return wait_for_ollama_ready(timeout=startup_timeout)
    else:
        log.error(_LOG_PREFIX, f"Failed to start Ollama container: {output}")
        return False


def stop_ollama_container() -> bool:
    # Stop the Ollama container.
    if not is_ollama_container_exists():
        return True

    success, output = _run_docker_cmd(["stop", OLLAMA_CONTAINER_NAME], timeout=30)
    if success:
        log.msg(_LOG_PREFIX, "✓ Ollama container stopped")
    return success


def wait_for_ollama_ready(timeout: int = 60) -> bool:
    # Wait for Ollama API to be ready.
    config = _get_ollama_config()
    port = config.get("port", OLLAMA_DEFAULT_PORT)
    url = f"http://localhost:{port}/api/tags"

    log.msg(_LOG_PREFIX, f"Waiting for Ollama to be ready (timeout: {timeout}s)...")

    start_time = time.time()
    poll_interval = 2

    while time.time() - start_time < timeout:
        # Check if container is still running
        if not is_ollama_container_running():
            log.warning(_LOG_PREFIX, "Ollama container stopped unexpectedly")
            # Use centralized error handler to diagnose
            error = docker_error_handler.diagnose_ollama_error(
                OLLAMA_CONTAINER_NAME, timeout_occurred=False
            )
            log.error(_LOG_PREFIX, docker_error_handler.format_error_message(error))
            return False

        try:
            response = requests.get(url, timeout=2)
            if response.status_code == 200:
                elapsed = time.time() - start_time
                log.msg(_LOG_PREFIX, f"✓ Ollama ready in {elapsed:.1f}s")
                return True
        except requests.exceptions.RequestException:
            pass

        elapsed = int(time.time() - start_time)
        if elapsed % 15 == 0 and elapsed > 0:
            log.msg(_LOG_PREFIX, f"Still waiting for Ollama... ({elapsed}s)")

        time.sleep(poll_interval)

    # Timeout occurred - use centralized error handler to diagnose
    log.warning(_LOG_PREFIX, f"Ollama did not become ready within {timeout}s")
    error = docker_error_handler.diagnose_ollama_error(
        OLLAMA_CONTAINER_NAME, timeout_occurred=True
    )
    log.error(_LOG_PREFIX, docker_error_handler.format_error_message(error))
    return False


# ==============================================================================
# OLLAMA STORAGE ANALYSIS
# ==============================================================================


def get_ollama_storage_path() -> Optional[Path]:
    # Get the path to Ollama's model storage directory.
    try:
        models_base = get_llm_models_absolute_path()
        ollama_path = Path(models_base) / "ollama" / "models"
        if ollama_path.exists():
            return ollama_path
    except Exception as e:
        log.debug(_LOG_PREFIX, f"Could not get Ollama storage path: {e}")
    return None


def _resolve_ollama_manifest_path(
    ollama_storage: Path, model_name: str, tag: Optional[str] = None
) -> Optional[Path]:
    # Resolve an Ollama model name to its manifest file path.
    #
    # Handles both library models (e.g., "gemma3:4b") and namespaced models
    # (e.g., "huihui_ai/gemma3-abliterated:4b").
    #
    # Args:
    #     ollama_storage: Path to ollama/models/ directory
    #     model_name: Ollama model name, may include ":tag" suffix
    #     tag: Explicit tag override (if None, parsed from model_name or defaults to "latest")
    #
    # Returns:
    #     Path to manifest file, or None if not found
    # Split name:tag if tag not explicitly provided
    if tag is None:
        if ":" in model_name:
            model_part, tag = model_name.rsplit(":", 1)
        else:
            model_part, tag = model_name, "latest"
    else:
        model_part = model_name.split(":")[0] if ":" in model_name else model_name

    # Determine namespace: library/ for bare names, owner/ for namespaced
    if "/" in model_part:
        namespace = model_part
    else:
        namespace = f"library/{model_part}"

    manifest_path = (
        ollama_storage / "manifests" / "registry.ollama.ai" / namespace / tag
    )
    return manifest_path if manifest_path.exists() else None


def get_ollama_model_vision_from_storage(model_name: str) -> Optional[bool]:
    # Detect vision capability of an Ollama model from local storage.
    #
    # Checks two reliable signals from the Ollama manifest/config blobs:
    # 1. Presence of a projector layer (application/vnd.ollama.image.projector)
    # 2. model_families containing "clip" (older VLMs like LLaVA)
    # 3. model_family name indicating a VLM (e.g., "gemma3", "qwen25vl", "mistral3")
    #
    # Args:
    #     model_name: Ollama model name (e.g., "gemma3:4b", "huihui_ai/gemma3-abliterated:4b")
    #
    # Returns:
    #     True if vision, False if text-only, None if can't determine
    ollama_path = get_ollama_storage_path()
    if not ollama_path:
        return None

    manifest_path = _resolve_ollama_manifest_path(ollama_path, model_name)
    if not manifest_path:
        return None

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        # Signal 1: projector layer in manifest (definitive for older VLMs)
        layers = manifest.get("layers", [])
        has_projector = any("projector" in l.get("mediaType", "") for l in layers)
        if has_projector:
            log.debug(
                _LOG_PREFIX, f"  '{model_name}' has projector layer → vision=True"
            )
            return True

        # Read config blob for family-based detection
        config_digest = manifest.get("config", {}).get("digest", "")
        if config_digest:
            blob_path = ollama_path / "blobs" / config_digest.replace(":", "-")
            if blob_path.exists():
                config = json.loads(blob_path.read_text(encoding="utf-8"))
                families = config.get("model_families", [])
                family = config.get("model_family", "")

                # Signal 2: "clip" in families (LLaVA-style VLMs)
                if "clip" in families:
                    log.debug(
                        _LOG_PREFIX,
                        f"  '{model_name}' has 'clip' in families → vision=True",
                    )
                    return True

                # Signal 3: model_family indicates a known VLM architecture
                # These families are inherently VLMs (they always have vision)
                _VLM_FAMILIES = {
                    "gemma3",
                    "qwen25vl",
                    "qwen3vl",
                    "mistral3",
                    "llava",
                    "mllama",
                    "pixtral",
                }
                if family.lower() in _VLM_FAMILIES:
                    log.debug(
                        _LOG_PREFIX,
                        f"  '{model_name}' family='{family}' is known VLM → vision=True",
                    )
                    return True

                # If we have a config but none of the VLM signals matched, it's text-only
                log.debug(
                    _LOG_PREFIX,
                    f"  '{model_name}' family='{family}' families={families} → vision=False",
                )
                return False

    except Exception as e:
        log.debug(_LOG_PREFIX, f"Error checking vision for '{model_name}': {e}")

    return None


def parse_ollama_manifest(
    model_name: str, tag: str = "latest"
) -> Optional[Dict[str, Any]]:
    # Parse an Ollama manifest file to get human-readable model information.
    #
    # Args:
    #     model_name: Model name (e.g., "local_mistral_7b_instruct_v0.3", "gemma3:4b")
    #     tag: Model tag (e.g., "latest", "8b"). Ignored if model_name contains ":tag".
    #
    # Returns:
    #     Dict with parsed manifest info including layer details, or None on error
    ollama_path = get_ollama_storage_path()
    if not ollama_path:
        return None

    # Use centralized path resolution
    manifest_path = _resolve_ollama_manifest_path(ollama_path, model_name, tag)

    if not manifest_path:
        log.debug(_LOG_PREFIX, f"Manifest not found for '{model_name}' tag='{tag}'")
        return None

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        result = {
            "model_name": model_name,
            "tag": tag,
            "manifest_path": str(manifest_path),
            "schema_version": manifest.get("schemaVersion"),
            "layers": [],
            "config": None,
        }

        # Parse config blob
        config_digest = manifest.get("config", {}).get("digest", "")
        if config_digest:
            config_blob_path = ollama_path / "blobs" / config_digest.replace(":", "-")
            if config_blob_path.exists():
                try:
                    with open(config_blob_path, "r", encoding="utf-8") as f:
                        result["config"] = json.load(f)
                except Exception:
                    pass

        # Parse layers
        for layer in manifest.get("layers", []):
            media_type = layer.get("mediaType", "")
            digest = layer.get("digest", "")
            size = layer.get("size", 0)

            # Determine layer type from media type
            layer_type = "unknown"
            if "model" in media_type:
                layer_type = "model_weights"
            elif "projector" in media_type:
                layer_type = "vision_projector"
            elif "template" in media_type:
                layer_type = "chat_template"
            elif "params" in media_type:
                layer_type = "parameters"
            elif "license" in media_type:
                layer_type = "license"

            layer_info = {
                "type": layer_type,
                "media_type": media_type,
                "digest": digest,
                "size_bytes": size,
                "size_human": (
                    f"{size / (1024*1024*1024):.2f} GB"
                    if size > 1024 * 1024 * 1024
                    else (
                        f"{size / (1024*1024):.2f} MB"
                        if size > 1024 * 1024
                        else f"{size / 1024:.2f} KB" if size > 1024 else f"{size} B"
                    )
                ),
                "blob_path": str(ollama_path / "blobs" / digest.replace(":", "-")),
            }

            # For small blobs (templates, params), read content
            if size < 10000 and layer_type in ["chat_template", "parameters"]:
                blob_path = ollama_path / "blobs" / digest.replace(":", "-")
                if blob_path.exists():
                    try:
                        with open(blob_path, "r", encoding="utf-8") as f:
                            layer_info["content"] = f.read()
                    except Exception:
                        pass

            # Check if this is a GGUF from local import (has "from" field)
            if "from" in layer:
                layer_info["original_path"] = layer["from"]

            result["layers"].append(layer_info)

        return result

    except Exception as e:
        log.debug(_LOG_PREFIX, f"Error parsing manifest for {model_name}: {e}")
        return None


def _collect_all_ollama_digests(
    ollama_storage: Path, exclude_manifest: Optional[Path] = None
) -> set:
    # Scan all Ollama manifests and collect every referenced blob digest.
    # Optionally exclude one manifest (the one being deleted) from the scan.
    #
    # Returns:
    #     Set of digest strings (e.g. "sha256:abc123...") referenced by other manifests.
    digests = set()
    manifests_root = ollama_storage / "manifests"
    if not manifests_root.exists():
        return digests

    for manifest_file in manifests_root.rglob("*"):
        if not manifest_file.is_file():
            continue
        if exclude_manifest and manifest_file.resolve() == exclude_manifest.resolve():
            continue
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            # Config digest
            config_digest = data.get("config", {}).get("digest", "")
            if config_digest:
                digests.add(config_digest)
            # Layer digests
            for layer in data.get("layers", []):
                d = layer.get("digest", "")
                if d:
                    digests.add(d)
        except Exception:
            continue

    return digests


def delete_ollama_model_local(model_name: str) -> Dict[str, Any]:
    # Delete an Ollama model by removing its manifest and exclusive blobs from local storage.
    #
    # Safely handles shared blobs: only deletes blobs not referenced by any other manifest.
    # Cleans up empty directories in the manifests tree after deletion.
    #
    # Args:
    #     model_name: Ollama model name (e.g. "gemma3:4b", "huihui_ai/gemma3-abliterated:4b")
    #
    # Returns:
    #     {"success": bool, "deleted": str, "details": {...}} or {"success": False, "error": str}
    ollama_path = get_ollama_storage_path()
    if not ollama_path:
        return {
            "success": False,
            "error": "Ollama storage path not found (no ollama/models/ directory)",
        }

    # Resolve manifest
    manifest_path = _resolve_ollama_manifest_path(ollama_path, model_name)
    if not manifest_path:
        return {
            "success": False,
            "error": f"Manifest not found for '{model_name}' in local storage",
        }

    # Parse manifest to collect digests for this model
    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"success": False, "error": f"Failed to read manifest: {e}"}

    target_digests = set()
    config_digest = manifest_data.get("config", {}).get("digest", "")
    if config_digest:
        target_digests.add(config_digest)
    for layer in manifest_data.get("layers", []):
        d = layer.get("digest", "")
        if d:
            target_digests.add(d)

    # Collect digests used by ALL OTHER manifests (exclude ours)
    shared_digests = _collect_all_ollama_digests(
        ollama_path, exclude_manifest=manifest_path
    )

    # Determine which blobs are exclusive to this model
    exclusive_digests = target_digests - shared_digests
    shared_count = len(target_digests) - len(exclusive_digests)

    blobs_dir = ollama_path / "blobs"
    deleted_blobs = []
    failed_blobs = []

    # Delete exclusive blobs
    for digest in exclusive_digests:
        blob_path = blobs_dir / digest.replace(":", "-")
        if blob_path.exists():
            try:
                blob_path.unlink()
                deleted_blobs.append(str(blob_path))
            except OSError as e:
                failed_blobs.append(f"{blob_path}: {e}")

    # Delete the manifest file
    try:
        manifest_path.unlink()
    except OSError as e:
        return {
            "success": False,
            "error": f"Failed to delete manifest: {e}",
            "details": {"deleted_blobs": deleted_blobs, "failed_blobs": failed_blobs},
        }

    # Clean up empty parent directories up to manifests/registry.ollama.ai/
    registry_root = ollama_path / "manifests" / "registry.ollama.ai"
    parent = manifest_path.parent
    while parent != registry_root and parent.is_relative_to(registry_root):
        try:
            if not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
            else:
                break
        except OSError:
            break

    details: Dict[str, Any] = {
        "blobs_deleted": len(deleted_blobs),
        "blobs_shared_kept": shared_count,
        "blobs_failed": len(failed_blobs),
    }
    if failed_blobs:
        details["failed_details"] = failed_blobs

    log.msg(
        _LOG_PREFIX,
        f"Deleted Ollama model '{model_name}' from local storage "
        f"({len(deleted_blobs)} blobs removed, {shared_count} shared blobs kept)",
    )

    return {"success": True, "deleted": model_name, "details": details}


# ==============================================================================
# MODEL MANAGEMENT
# ==============================================================================


def list_ollama_models() -> List[str]:
    # List models available in Ollama.
    config = _get_ollama_config()
    port = config.get("port", OLLAMA_DEFAULT_PORT)
    url = f"http://localhost:{port}/api/tags"

    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return [m["name"] for m in data.get("models", [])]
    except Exception as e:
        log.debug(_LOG_PREFIX, f"Could not list models: {e}")

    return []


def get_ollama_model_info(model_name: str) -> Optional[Dict[str, Any]]:
    # Get detailed information about an Ollama model.
    #
    # Args:
    #     model_name: Model name (e.g., "ministral-3:8b", "llava")
    #
    # Returns:
    #     Dict with model info including 'capabilities' list, or None on error
    config = _get_ollama_config()
    port = config.get("port", OLLAMA_DEFAULT_PORT)
    url = f"http://localhost:{port}/api/show"

    try:
        response = requests.post(url, json={"model": model_name}, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data
    except Exception as e:
        log.debug(_LOG_PREFIX, f"Could not get model info for {model_name}: {e}")

    return None


def check_model_has_vision(model_name: str) -> bool:
    # Check if a model has vision capability.
    #
    # Args:
    #     model_name: Ollama model name
    #
    # Returns:
    #     bool: True if model supports vision
    info = get_ollama_model_info(model_name)
    if info:
        capabilities = info.get("capabilities", [])
        has_vision = "vision" in capabilities
        log.debug(
            _LOG_PREFIX,
            f"Model {model_name} capabilities: {capabilities}, has_vision: {has_vision}",
        )
        return has_vision
    return False


# Track last pull error for smarter error messages
_last_pull_error: str = ""


def get_last_pull_error() -> str:
    # Get the last pull error message (for detailed error reporting).
    return _last_pull_error


def pull_ollama_model(model_name: str) -> bool:
    # Pull a model from Ollama registry.
    #
    # Args:
    #     model_name: Model name (e.g., "mistral", "llama3:8b", "mistral:7b-instruct-q4_K_M")
    #
    # Returns:
    #     bool: True if model pulled successfully
    global _last_pull_error
    _last_pull_error = ""  # Clear previous error

    config = _get_ollama_config()
    port = config.get("port", OLLAMA_DEFAULT_PORT)
    url = f"http://localhost:{port}/api/pull"

    pull_timeout = get_ollama_pull_timeout()
    log.msg(
        _LOG_PREFIX,
        f"Pulling model: {model_name} (this may take a while, timeout: {pull_timeout}s)...",
    )

    try:
        # Ollama pull is streaming, we need to handle it properly
        response = requests.post(
            url,
            json={"name": model_name},
            stream=True,
            timeout=pull_timeout,
        )

        if response.status_code == 200:
            # Track progress per digest (file) to avoid spam
            current_digest = None
            last_pct = -1
            pull_success = False
            last_status = ""
            last_error = ""  # Track error messages from Ollama

            # Stream the response to show progress
            for line in response.iter_lines():
                if line:
                    try:
                        data = json.loads(line)
                        status = data.get("status", "")
                        last_status = status
                        digest = data.get("digest", "")

                        # Check for error field in response (Ollama sends errors this way)
                        if "error" in data:
                            last_error = data["error"]
                            _last_pull_error = (
                                last_error  # Store for detailed error reporting
                            )
                            # Finish any in-progress line
                            if current_digest is not None:
                                print()
                                current_digest = None
                            log.error(
                                _LOG_PREFIX, f"Pull error from Ollama: {last_error}"
                            )
                            return False

                        if "pulling" in status.lower():
                            # Progress update for a specific file/layer
                            completed = data.get("completed", 0)
                            total = data.get("total", 0)

                            if total > 0:
                                pct = int((completed / total) * 100)

                                # New digest = new file, print header
                                if digest != current_digest:
                                    if current_digest is not None:
                                        # Finish previous line
                                        print()
                                    current_digest = digest
                                    last_pct = -1
                                    # Extract short digest for display
                                    short_digest = (
                                        digest.split(":")[-1][:12]
                                        if ":" in digest
                                        else digest[:12]
                                    )
                                    total_mb = total / (1024 * 1024)
                                    log.msg(
                                        _LOG_PREFIX,
                                        f"  Pulling layer {short_digest} ({total_mb:.1f} MB)",
                                    )

                                # Only update every 5% to reduce output
                                if pct >= last_pct + 5 or pct == 100:
                                    last_pct = pct
                                    completed_mb = completed / (1024 * 1024)
                                    total_mb = total / (1024 * 1024)
                                    # Print progress on same line using carriage return
                                    print(
                                        f"\r    Progress: {pct:3d}% ({completed_mb:.1f}/{total_mb:.1f} MB)",
                                        end="",
                                        flush=True,
                                    )

                        elif status == "success":
                            # Finish any in-progress line
                            print()
                            log.msg(
                                _LOG_PREFIX, f"✓ Model {model_name} pulled successfully"
                            )
                            pull_success = True
                            return True

                        elif (
                            "verifying" in status.lower() or "writing" in status.lower()
                        ):
                            # Finish progress line if needed
                            if current_digest is not None:
                                print()
                                current_digest = None
                            log.msg(_LOG_PREFIX, f"  {status}...")

                        elif "error" in status.lower():
                            # Error in status string itself
                            _last_pull_error = (
                                status  # Store for detailed error reporting
                            )
                            if current_digest is not None:
                                print()
                                current_digest = None
                            log.error(_LOG_PREFIX, f"Pull error: {status}")
                            return False

                    except json.JSONDecodeError:
                        pass

            # Ensure we end with a newline
            if current_digest is not None:
                print()

            # If we completed without error but didn't see explicit "success",
            # check if we actually got any meaningful status updates
            if not pull_success:
                if not last_status:
                    # Empty response stream - model likely doesn't exist
                    _last_pull_error = f"model '{model_name}' not found in Ollama registry (empty response)"
                    log.error(_LOG_PREFIX, f"Pull failed: {_last_pull_error}")
                    return False
                # We got some status but no success - might be incomplete or verification failed
                # This can happen if verifying digest fails but Ollama doesn't return explicit error
                if "verifying" in last_status.lower():
                    _last_pull_error = (
                        f"verification incomplete - possible digest mismatch"
                    )
                    log.error(
                        _LOG_PREFIX,
                        f"Pull failed: verification incomplete for '{model_name}' (last status: {last_status})",
                    )
                    log.error(
                        _LOG_PREFIX,
                        f"  This may indicate a digest mismatch - try: ollama rm {model_name} && ollama pull {model_name}",
                    )
                    return False
                log.msg(
                    _LOG_PREFIX,
                    f"✓ Model {model_name} pull completed (last status: {last_status})",
                )
            return True
        else:
            # Check for specific error messages
            error_text = response.text
            if (
                "not found" in error_text.lower()
                or "does not exist" in error_text.lower()
            ):
                _last_pull_error = f"model '{model_name}' not found in Ollama registry"
                log.error(
                    _LOG_PREFIX, f"Model '{model_name}' not found in Ollama registry"
                )
            else:
                _last_pull_error = error_text
                log.error(_LOG_PREFIX, f"Failed to pull model: {error_text}")
            return False
    except requests.exceptions.Timeout:
        _last_pull_error = f"timeout pulling model (model may be very large)"
        log.error(
            _LOG_PREFIX, f"Timeout pulling model {model_name} - model may be very large"
        )
        return False
    except Exception as e:
        _last_pull_error = str(e)
        log.error(_LOG_PREFIX, f"Error pulling model: {e}")
        return False


def infer_ollama_model_name(local_model_path: str) -> Optional[str]:
    # Infer Ollama model name from local model path/filename.
    #
    # Args:
    #     local_model_path: Path to local GGUF file or model name
    #
    # Returns:
    #     Ollama model name or None if can't infer
    if not local_model_path:
        return None

    # If the input contains ":" (Ollama tag separator), it's already
    # a registry name (e.g., "huihui_ai/qwen3-vl:8b") — return as-is.
    # Skip Windows drive letters (single char before colon like "C:\...")
    stripped = local_model_path.strip()
    colon_idx = stripped.find(":")
    if colon_idx > 1:
        return stripped

    filename = Path(local_model_path).stem.lower()

    # Check for specific model patterns
    # Ministral-3-8B-Instruct-2512-Q4_K_M -> ministral-3:8b
    # NOTE: Ollama's ministral-3 uses simple tags like :3b, :8b, :14b

    # Extract quantization suffix if present
    quant = None
    for q in ["q4_k_m", "q5_k_m", "q8_0", "q4_0", "q5_0", "q6_k", "q4_k_s", "q5_k_s"]:
        if q in filename:
            quant = q.upper()
            break

    # Try to match known model families
    if "ministral" in filename or "mistral-3" in filename:
        # Ministral 3 series - uses "ministral-3" in Ollama (requires 0.13.1+)
        # Available tags: :3b, :8b, :14b (with vision support)
        if "14b" in filename:
            return "ministral-3:14b"
        elif "8b" in filename:
            return "ministral-3:8b"
        elif "3b" in filename:
            return "ministral-3:3b"
        else:
            return "ministral-3:8b"  # Default to 8b

    elif "mistral" in filename:
        if "7b" in filename:
            base = "mistral:7b"
        elif "8x7b" in filename or "mixtral" in filename:
            base = "mixtral"
        else:
            base = "mistral"

        if "instruct" in filename:
            base += "-instruct"
        if quant:
            base += f"-{quant.lower()}"

        return base

    elif "llama" in filename:
        if "3" in filename:
            base = "llama3"
        elif "2" in filename:
            base = "llama2"
        else:
            base = "llama3"

        # Size
        for size in ["70b", "13b", "8b", "7b"]:
            if size in filename:
                base += f":{size}"
                break

        if quant:
            base += f"-{quant.lower()}"

        return base

    elif "qwen" in filename:
        base = "qwen2"
        for size in ["72b", "7b", "1.5b", "0.5b"]:
            if size in filename:
                base += f":{size}"
                break
        return base

    elif "phi" in filename:
        return "phi3"

    elif "gemma" in filename:
        base = "gemma"
        for size in ["7b", "2b"]:
            if size in filename:
                base += f":{size}"
                break
        return base

    # Couldn't infer
    return None


def import_gguf_to_ollama(
    gguf_path: str,
    model_name: Optional[str] = None,
    ctx: Optional[TemplateContext] = None,
) -> Optional[str]:
    # Import a local GGUF file into Ollama.
    #
    # This creates a Modelfile and uses `ollama create` to import the GGUF model.
    #
    # NOTE: Vision/mmproj files are detected but CANNOT be used with Ollama.
    # Ollama's Modelfile format doesn't support PROJECTOR directive.
    # For local GGUF files with vision capability, use llama.cpp Docker backend instead.
    #
    # Args:
    #     gguf_path: Full path to the GGUF file
    #     model_name: Name to give the model in Ollama (auto-generated if None)
    #     ctx: TemplateContext with widget values (model_family, model_type, etc.)
    #
    # Returns:
    #     str: Ollama model name if successful, None otherwise
    if not ensure_ollama_running():
        log.error(_LOG_PREFIX, "Ollama container not running")
        return None

    gguf_file = Path(gguf_path)
    if not gguf_file.exists():
        log.error(_LOG_PREFIX, f"GGUF file does not exist: {gguf_path}")
        return None

    if not gguf_file.suffix.lower() == ".gguf":
        log.error(_LOG_PREFIX, f"File is not a GGUF file: {gguf_path}")
        return None

    # Check for mmproj file (vision support)
    # NOTE: Ollama doesn't support adding mmproj to GGUF via Modelfile
    # Vision requires either: 1) Safetensors source, or 2) Ollama registry model
    mmproj_file = None
    parent_dir = gguf_file.parent
    mmproj_patterns = [
        "*-mmproj.gguf",
        "*_mmproj.gguf",
        "*mmproj*.gguf",
        "*projector*.gguf",
        "*-clip-*.gguf",
    ]

    for pattern in mmproj_patterns:
        matches = list(parent_dir.glob(pattern))
        matches = [m for m in matches if m != gguf_file]
        if matches:
            mmproj_file = matches[0]
            break

    if mmproj_file:
        log.warning(_LOG_PREFIX, f"Found mmproj file: {mmproj_file.name}")
        log.warning(
            _LOG_PREFIX,
            "NOTE: Ollama cannot use mmproj with GGUF files (Modelfile limitation)",
        )
        log.warning(
            _LOG_PREFIX,
            "For vision with local GGUF+mmproj, use 'llama.cpp (Docker)' backend instead",
        )
        log.warning(
            _LOG_PREFIX,
            "Or use an Ollama registry model like 'ministral-3:8b' for vision",
        )

    try:
        # Generate model name if not provided
        if not model_name:
            # Use filename without extension, cleaned up
            generated_name = gguf_file.stem.lower()
            # Remove quantization suffix for cleaner name
            for q in [
                "q4_k_m",
                "q4_k_s",
                "q5_k_m",
                "q5_k_s",
                "q8_0",
                "q6_k",
                "q3_k",
                "q2_k",
                "fp16",
                "f16",
            ]:
                generated_name = generated_name.replace(f"-{q}", "").replace(
                    f"_{q}", ""
                )
            # Add a prefix to distinguish from registry models
            model_name = _default_ollama_model_name(
                generated_name, prefix="local_"
            )
        model_name = _validate_ollama_model_name(model_name)
    except ValueError as e:
        log.error(_LOG_PREFIX, str(e))
        return None

    # Check if model already exists in Ollama
    existing_models = list_ollama_models()
    if model_name in existing_models:
        log.msg(_LOG_PREFIX, f"✓ Model {model_name} already exists in Ollama")
        return model_name

    # Also check for variations (with :latest tag)
    if f"{model_name}:latest" in existing_models:
        log.msg(_LOG_PREFIX, f"✓ Model {model_name}:latest already exists in Ollama")
        return f"{model_name}:latest"

    # Get the docker-internal path
    # The models directory is mounted at /models in the container
    try:
        models_base = get_llm_models_absolute_path()
    except ValueError as e:
        log.error(_LOG_PREFIX, str(e))
        log.error(
            _LOG_PREFIX,
            "Cannot import GGUF file into Ollama without configured models path",
        )
        return None

    models_base_path = Path(models_base).resolve()
    gguf_file_resolved = gguf_file.resolve()

    log.debug(_LOG_PREFIX, f"Models base path: {models_base_path}")
    log.debug(_LOG_PREFIX, f"GGUF file path: {gguf_file_resolved}")

    # Check if GGUF file is under the models base directory
    try:
        rel_path = gguf_file_resolved.relative_to(models_base_path)
        docker_gguf_path = f"/models/{rel_path.as_posix()}"
    except ValueError:
        # Try to find common LLM subfolder pattern
        # E.g., if models_base is "D:/AI/ComfyUI/models" and file is in "models/LLM/..."
        gguf_str = gguf_file_resolved.as_posix()
        models_str = models_base_path.as_posix()

        if models_str in gguf_str:
            # Extract path relative to models_base
            idx = gguf_str.find(models_str) + len(models_str)
            rel_part = gguf_str[idx:].lstrip("/")
            docker_gguf_path = f"/models/{rel_part}"
            log.debug(_LOG_PREFIX, f"Using extracted relative path: {docker_gguf_path}")
        else:
            log.error(
                _LOG_PREFIX,
                f"GGUF file must be in models directory: {models_base_path}",
            )
            log.error(_LOG_PREFIX, f"Current file location: {gguf_file_resolved}")
            log.error(_LOG_PREFIX, "Move the GGUF file to your models/LLM directory")
            return None

    log.msg(_LOG_PREFIX, f"Importing GGUF file into Ollama: {gguf_file.name}")
    log.msg(_LOG_PREFIX, f"  → Creating model: {model_name}")
    log.msg(_LOG_PREFIX, f"  → Docker path: {docker_gguf_path}")

    # First, check if the file exists inside the container
    check_cmd = ["exec", OLLAMA_CONTAINER_NAME, "ls", "-la", docker_gguf_path]
    success, output = _run_docker_cmd(check_cmd, timeout=10)
    if not success:
        log.error(
            _LOG_PREFIX,
            f"GGUF file not accessible inside container at: {docker_gguf_path}",
        )
        log.error(_LOG_PREFIX, f"Container output: {output}")
        log.error(_LOG_PREFIX, "Check that the models directory is correctly mounted")
        return None
    else:
        log.debug(_LOG_PREFIX, f"GGUF file found in container: {output.strip()}")

    # Note: mmproj file is detected but cannot be used
    # Ollama doesn't support adding mmproj to GGUF via Modelfile

    # Create the model using a unique temporary Modelfile.
    # Note: Ollama's FROM directive for GGUF requires the full path
    # NOTE: Ollama does NOT support PROJECTOR directive - vision models must be:
    #   1) Built from Safetensors (with vision components), or
    #   2) Pulled from Ollama registry (pre-built with vision)
    # For local GGUF+mmproj vision, users should use llama.cpp Docker backend

    log.msg(_LOG_PREFIX, "Creating Ollama model from GGUF (this may take a moment)...")

    try:
        success, output = _create_ollama_model_from_path(
            docker_gguf_path,
            model_name,
            timeout=300,
        )
    except ValueError as e:
        log.error(_LOG_PREFIX, str(e))
        return None
    log.debug(_LOG_PREFIX, f"Ollama create output: {output}")

    if success:
        log.msg(_LOG_PREFIX, f"✓ Model {model_name} created successfully")
        # Verify model exists
        time.sleep(1)  # Brief pause for Ollama to register the model
        existing_models = list_ollama_models()
        log.debug(_LOG_PREFIX, f"Available models after import: {existing_models}")

        if model_name in existing_models or f"{model_name}:latest" in existing_models:
            actual_name = (
                model_name if model_name in existing_models else f"{model_name}:latest"
            )
            log.msg(_LOG_PREFIX, f"  → Save a template from the UI to use this model")
            return actual_name
        else:
            log.warning(
                _LOG_PREFIX,
                f"Model created but not found in list. Models available: {existing_models}",
            )
            return model_name  # Try using the name anyway
    else:
        log.error(_LOG_PREFIX, f"Failed to create Ollama model: {output}")
        return None


def import_hf_model_to_ollama(
    local_model_path: str,
    model_name: Optional[str] = None,
    quantize: Optional[str] = None,
) -> Optional[str]:
    # Import a local HuggingFace Safetensors model into Ollama.
    #
    # This uses `ollama create` to convert and import the model.
    # Supports: Llama, Mistral, Gemma, Phi3 architectures.
    #
    # Args:
    #     local_model_path: Path to directory containing safetensors files
    #     model_name: Name to give the model in Ollama (auto-generated if None)
    #     quantize: Quantization level (q4_K_S, q4_K_M, q8_0, or None for FP16)
    #
    # Returns:
    #     str: Ollama model name if successful, None otherwise
    if not ensure_ollama_running():
        log.error(_LOG_PREFIX, "Ollama container not running")
        return None

    model_path = Path(local_model_path)
    if not model_path.exists():
        log.error(_LOG_PREFIX, f"Model path does not exist: {local_model_path}")
        return None

    # Check for safetensors files
    safetensors_files = list(model_path.glob("*.safetensors"))
    if not safetensors_files:
        log.error(_LOG_PREFIX, f"No safetensors files found in {local_model_path}")
        return None

    try:
        # Generate model name if not provided
        if not model_name:
            generated_name = model_path.name.lower()
            # Remove version suffixes for cleaner names
            generated_name = (
                generated_name.split("_v")[0]
                if "_v" in generated_name
                else generated_name
            )
            model_name = _default_ollama_model_name(generated_name)
        model_name = _validate_ollama_model_name(model_name)
        quantize = _validate_ollama_quantization(quantize)
    except ValueError as e:
        log.error(_LOG_PREFIX, str(e))
        return None

    # Check if model already exists in Ollama
    existing_models = list_ollama_models()
    if model_name in existing_models or any(
        m.startswith(model_name) for m in existing_models
    ):
        log.msg(_LOG_PREFIX, f"✓ Model {model_name} already exists in Ollama")
        return model_name

    log.msg(
        _LOG_PREFIX,
        f"Importing HuggingFace model to Ollama: {model_path.name} -> {model_name}",
    )

    # Get the docker-internal path (models are mounted at /models).
    try:
        models_base = Path(get_llm_models_absolute_path()).resolve()
    except ValueError as e:
        log.error(_LOG_PREFIX, str(e))
        log.error(_LOG_PREFIX, "Models base path not configured")
        return None

    # Calculate relative path from models_base
    try:
        rel_path = model_path.resolve().relative_to(models_base)
        docker_model_path = f"/models/{rel_path.as_posix()}"
    except ValueError:
        log.error(
            _LOG_PREFIX,
            f"Model path {local_model_path} is not under models_base {models_base}",
        )
        return None

    # Create the model using a unique temporary Modelfile.
    log.msg(
        _LOG_PREFIX,
        "Creating Ollama model (this may take several minutes for large models)...",
    )

    # This can take a long time for large models
    pull_timeout = get_ollama_pull_timeout()
    try:
        success, output = _create_ollama_model_from_path(
            docker_model_path,
            model_name,
            quantize=quantize,
            timeout=pull_timeout,
        )
    except ValueError as e:
        log.error(_LOG_PREFIX, str(e))
        return None

    if success:
        log.msg(_LOG_PREFIX, f"✓ Model {model_name} imported successfully")
        return model_name
    else:
        log.error(_LOG_PREFIX, f"Failed to import model: {output}")
        return None


def is_hf_model_directory(path: str) -> bool:
    # Check if a path is a HuggingFace model directory (contains safetensors).
    model_path = Path(path)
    if not model_path.is_dir():
        return False

    # Check for safetensors files
    safetensors_files = list(model_path.glob("*.safetensors"))
    if safetensors_files:
        return True

    # Check for config.json (HF model marker)
    if (model_path / "config.json").exists():
        return True

    return False


# ==============================================================================
# GENERATION API
# ==============================================================================


def generate_with_ollama(
    model_name: str,
    messages: List[Dict[str, Any]],
    max_tokens: int = 1024,
    temperature: float = 0.7,
    top_p: float = 0.9,
    stream: bool = False,
    context_size: Optional[int] = None,
    top_k: int = 0,
    seed: int = -1,
    repeat_penalty: float = 1.0,
    min_p: float = 0.0,
    mirostat: int = 0,
    mirostat_eta: float = 0.1,
    mirostat_tau: float = 5.0,
    repeat_last_n: int = 64,
    stop_sequences: Optional[List[str]] = None,
) -> Optional[str]:
    # Generate text using Ollama API.
    #
    # Uses native /api/chat endpoint to support num_ctx (context window).
    #
    # Args:
    #     model_name: Ollama model name (e.g., "mistral", "llama3:8b")
    #     messages: List of message dicts with 'role' and 'content'
    #     max_tokens: Maximum tokens to generate
    #     temperature: Sampling temperature
    #     top_p: Top-p sampling
    #     stream: Whether to stream response (not implemented yet)
    #     context_size: Context window size (num_ctx)
    #     top_k: Top-k sampling (0 = omit, use model default)
    #     seed: Random seed (-1 = omit, use random)
    #     repeat_penalty: Repetition penalty (1.0 = omit, use model default)
    #
    # Returns:
    #     Generated text or None on error
    config = _get_ollama_config()
    port = config.get("port", OLLAMA_DEFAULT_PORT)

    # Use native /api/chat endpoint (supports num_ctx via options)
    url = f"http://localhost:{port}/api/chat"

    options: Dict[str, Any] = {
        "num_predict": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
    }
    if context_size and context_size > 0:
        options["num_ctx"] = context_size
    if top_k and top_k > 0:
        options["top_k"] = top_k
    if seed is not None and seed >= 0:
        options["seed"] = seed
    if repeat_penalty and repeat_penalty != 1.0:
        options["repeat_penalty"] = repeat_penalty
    if min_p and min_p > 0.0:
        options["min_p"] = min_p
    if mirostat and mirostat > 0:
        options["mirostat"] = mirostat
        options["mirostat_eta"] = mirostat_eta
        options["mirostat_tau"] = mirostat_tau
    if repeat_last_n != 64:
        options["repeat_last_n"] = repeat_last_n
    if stop_sequences:
        options["stop"] = stop_sequences

    payload = {
        "model": model_name,
        "messages": messages,
        "options": options,
        "stream": False,
        "think": False,  # Disable thinking mode (qwen3.5 etc.)
    }

    request_timeout = get_ollama_request_timeout()

    try:
        response = requests.post(url, json=payload, timeout=request_timeout)

        if response.status_code == 200:
            data = response.json()
            message = data.get("message", {})
            return message.get("content", "")
        else:
            log.error(
                _LOG_PREFIX,
                f"Ollama API error: HTTP {response.status_code}",
            )
            log.debug(_LOG_PREFIX, f"Ollama API error body: {response.text[:2000]}")
            raise RuntimeError(
                f"Ollama generation request failed with HTTP {response.status_code}"
            )

    except requests.exceptions.Timeout as e:
        log.error(_LOG_PREFIX, "Ollama request timed out")
        error = docker_error_handler.diagnose_ollama_error(
            OLLAMA_CONTAINER_NAME, timeout_occurred=True
        )
        log.error(_LOG_PREFIX, docker_error_handler.format_error_message(error))
        raise TimeoutError("Ollama generation request timed out") from e
    except Exception as e:
        log.error(_LOG_PREFIX, f"Ollama request failed ({type(e).__name__})")
        log.debug(_LOG_PREFIX, f"Ollama request error: {e}")
        error = docker_error_handler.diagnose_ollama_error(
            OLLAMA_CONTAINER_NAME, timeout_occurred=False
        )
        log.error(_LOG_PREFIX, docker_error_handler.format_error_message(error))
        raise RuntimeError("Ollama generation request failed") from e


def get_ollama_version() -> Optional[str]:
    # Get the Ollama server version.
    config = _get_ollama_config()
    port = config.get("port", OLLAMA_DEFAULT_PORT)

    try:
        response = requests.get(f"http://localhost:{port}/api/version", timeout=5)
        if response.status_code == 200:
            data = response.json()
            return data.get("version", "unknown")
    except Exception as e:
        log.debug(_LOG_PREFIX, f"Could not get Ollama version: {e}")
    return None


def generate_with_ollama_vision(
    model_name: str,
    messages: List[Dict[str, Any]],
    images: Optional[List[str]] = None,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    context_size: Optional[int] = None,
    top_p: float = 0.9,
    top_k: int = 0,
    seed: int = -1,
    repeat_penalty: float = 1.0,
    min_p: float = 0.0,
    mirostat: int = 0,
    mirostat_eta: float = 0.1,
    mirostat_tau: float = 5.0,
    repeat_last_n: int = 64,
    stop_sequences: Optional[List[str]] = None,
) -> Optional[str]:
    # Generate text with vision using Ollama API.
    #
    # Args:
    #     model_name: Vision-capable model (e.g., "llava", "bakllava", "ministral-3")
    #     messages: List of message dicts
    #     images: List of base64-encoded images
    #     max_tokens: Maximum tokens to generate
    #     temperature: Sampling temperature
    #
    # Returns:
    #     Generated text or None on error
    config = _get_ollama_config()
    port = config.get("port", OLLAMA_DEFAULT_PORT)

    # Log Ollama version for debugging (ministral-3 requires 0.13.1+)
    ollama_version = get_ollama_version()
    log.debug(_LOG_PREFIX, f"Ollama version: {ollama_version}")

    # Extract prompt from messages (used for fallback /api/generate)
    prompt = ""
    system_prompt = ""
    for msg in messages:
        if msg["role"] == "user":
            content = msg.get("content")
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif isinstance(item, str):
                        text_parts.append(item)
                prompt = "\n".join(text_parts)
            else:
                prompt = str(content or "")
        elif msg["role"] == "system":
            system_prompt = str(msg.get("content") or "")

    # Try /api/chat first (more reliable for newer models like ministral-3)
    # The /api/chat endpoint supports images in the messages array
    chat_url = f"http://localhost:{port}/api/chat"

    # Build messages with images for chat API (preserving few-shot history)
    chat_messages = []
    for msg in messages:
        msg_copy = dict(msg)
        content = msg_copy.get("content")
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    text_parts.append(item)
            msg_copy["content"] = "\n".join(text_parts)
        chat_messages.append(msg_copy)

    # Find the last user message to attach the images to
    last_user_idx = -1
    for i in range(len(chat_messages) - 1, -1, -1):
        if chat_messages[i]["role"] == "user":
            last_user_idx = i
            break

    if last_user_idx != -1:
        # Ensure user content is not empty (required by some VLMs)
        content = chat_messages[last_user_idx].get("content")
        is_empty = False
        if not content:
            is_empty = True
        elif isinstance(content, str) and not content.strip():
            is_empty = True

        if is_empty:
            chat_messages[last_user_idx][
                "content"
            ] = "Please follow the instructions above for this image."

        if images:
            chat_messages[last_user_idx]["images"] = images
    else:
        # Fallback if no user message exists in the list (should not happen)
        user_content = "Please follow the instructions above for this image."
        user_message: Dict[str, Any] = {"role": "user", "content": user_content}
        if images:
            user_message["images"] = images
        chat_messages.append(user_message)

    log.debug(
        _LOG_PREFIX,
        f"  Chat messages: count={len(chat_messages)}, images={len(images) if images else 0}",
    )

    vision_options: Dict[str, Any] = {
        "num_predict": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
    }
    if context_size and context_size > 0:
        vision_options["num_ctx"] = context_size
    if top_k and top_k > 0:
        vision_options["top_k"] = top_k
    if seed is not None and seed >= 0:
        vision_options["seed"] = seed
    if repeat_penalty and repeat_penalty != 1.0:
        vision_options["repeat_penalty"] = repeat_penalty
    if min_p and min_p > 0.0:
        vision_options["min_p"] = min_p
    if mirostat and mirostat > 0:
        vision_options["mirostat"] = mirostat
        vision_options["mirostat_eta"] = mirostat_eta
        vision_options["mirostat_tau"] = mirostat_tau
    if repeat_last_n != 64:
        vision_options["repeat_last_n"] = repeat_last_n
    if stop_sequences:
        vision_options["stop"] = stop_sequences

    chat_payload = {
        "model": model_name,
        "messages": chat_messages,
        "options": vision_options,
        "stream": False,
        "think": False,  # Disable thinking mode (qwen3.5 etc.) — uses all tokens on reasoning, leaves content empty
    }

    request_timeout = get_ollama_request_timeout()

    # Retry mechanism for empty responses (Qwen3-VL sometimes returns empty on first try)
    max_retries = 2

    for attempt in range(max_retries):
        try:
            log.debug(
                _LOG_PREFIX,
                f"Trying /api/chat for vision with model: {model_name}"
                + (f" (attempt {attempt + 1}/{max_retries})" if attempt > 0 else ""),
            )
            response = requests.post(
                chat_url, json=chat_payload, timeout=request_timeout
            )

            if response.status_code == 200:
                data = response.json()
                log.debug(_LOG_PREFIX, f"  Full response data: {str(data)[:500]}")
                message = data.get("message", {})
                content = message.get("content", "")

                # Check if response indicates the model is still "thinking" (Qwen3-VL thinking mode)
                if not content and data.get("done") == False:
                    log.debug(_LOG_PREFIX, f"  Model not done yet, may need streaming")

                # If we got content, return it
                if content:
                    return content

                # Empty response - retry with slightly different temperature
                if attempt < max_retries - 1:
                    log.warning(
                        _LOG_PREFIX,
                        f"Empty response from Ollama (attempt {attempt + 1}/{max_retries}), retrying...",
                    )
                    # Slightly adjust temperature for next attempt
                    chat_payload["options"]["temperature"] = temperature + (
                        0.1 * (attempt + 1)
                    )
                    import time

                    time.sleep(0.5)  # Brief pause before retry
                    continue
                else:
                    log.debug(_LOG_PREFIX, f"All {max_retries} attempts returned empty")
                    return ""
            else:
                # Log detailed error for debugging
                error_detail = ""
                try:
                    error_detail = response.text[:500]
                except Exception:
                    pass
                log.debug(
                    _LOG_PREFIX,
                    f"/api/chat failed: {response.status_code} - {error_detail}",
                )
                break  # Don't retry on HTTP errors

        except Exception as e:
            log.debug(_LOG_PREFIX, f"/api/chat request failed: {e}")
            break  # Don't retry on exceptions

    # Fallback to /api/generate (older method)
    log.debug(_LOG_PREFIX, f"Falling back to /api/generate for vision")
    generate_url = f"http://localhost:{port}/api/generate"

    fallback_options: Dict[str, Any] = {
        "num_predict": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
    }
    if context_size and context_size > 0:
        fallback_options["num_ctx"] = context_size
    if top_k and top_k > 0:
        fallback_options["top_k"] = top_k
    if seed is not None and seed >= 0:
        fallback_options["seed"] = seed
    if repeat_penalty and repeat_penalty != 1.0:
        fallback_options["repeat_penalty"] = repeat_penalty
    if min_p and min_p > 0.0:
        fallback_options["min_p"] = min_p
    if mirostat and mirostat > 0:
        fallback_options["mirostat"] = mirostat
        fallback_options["mirostat_eta"] = mirostat_eta
        fallback_options["mirostat_tau"] = mirostat_tau
    if repeat_last_n != 64:
        fallback_options["repeat_last_n"] = repeat_last_n
    if stop_sequences:
        fallback_options["stop"] = stop_sequences

    generate_payload = {
        "model": model_name,
        "prompt": prompt,
        "images": images or [],
        "options": fallback_options,
        "stream": False,
        "think": False,  # Disable thinking mode
    }

    if system_prompt:
        generate_payload["system"] = system_prompt

    try:
        response = requests.post(
            generate_url, json=generate_payload, timeout=request_timeout
        )

        if response.status_code == 200:
            data = response.json()
            return data.get("response", "")
        else:
            # Log detailed error
            error_detail = ""
            try:
                error_detail = response.text[:500]
            except Exception:
                pass
            log.error(
                _LOG_PREFIX,
                f"Ollama vision API error: HTTP {response.status_code}",
            )
            log.debug(_LOG_PREFIX, f"Ollama vision API error body: {error_detail}")

            # Check for version-related issues
            if "ministral" in model_name.lower():
                log.error(
                    _LOG_PREFIX,
                    f"Note: ministral-3 requires Ollama 0.13.1+ (current: {ollama_version})",
                )

            raise RuntimeError(
                f"Ollama vision request failed with HTTP {response.status_code}"
            )

    except Exception as e:
        log.error(
            _LOG_PREFIX,
            f"Ollama vision request failed ({type(e).__name__})",
        )
        log.debug(_LOG_PREFIX, f"Ollama vision request error: {e}")
        raise RuntimeError("Ollama vision generation request failed") from e


def generate_ollama(
    smart_lm_instance,
    prompt: str,
    image_paths: Optional[List[str]] = None,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    top_p: float = 0.9,
    top_k: int = 40,
    seed: int = -1,
    llm_mode: Optional[str] = None,
    instruction_template: str = "",
    repetition_penalty: float = 1.0,
    vision_task: Optional[str] = None,
    use_few_shot: bool = True,
    min_p: float = 0.0,
    mirostat: int = 0,
    mirostat_eta: float = 0.1,
    mirostat_tau: float = 5.0,
    repeat_last_n: int = 64,
    stop_sequences: Optional[List[str]] = None,
    **kwargs,
):
    # High-level generation function for SmartLoader v2 integration.
    #
    # This is the main entry point for Ollama generation, matching the pattern
    # of generate_vllm() and generate_transformers().
    #
    # Args:
    #     smart_lm_instance: OllamaWrapper instance with ollama_model_name and ollama_base_url
    #     prompt: The prompt text
    #     image_paths: Optional list of image paths for vision models
    #     max_tokens: Maximum tokens to generate
    #     temperature: Sampling temperature
    #     top_p: Top-p sampling
    #     top_k: Top-k sampling (0 or negative = use Ollama default)
    #     seed: Random seed (-1 for random / model default)
    #     llm_mode: LLM mode for text-only generation
    #     instruction_template: Custom instruction template for LLM mode
    #     repetition_penalty: Repetition penalty (1.0 = use Ollama default; mapped to `repeat_penalty`)
    #
    # Returns:
    #     tuple: (result_text, raw_output) for compatibility with other generators
    # Get model info from wrapper
    model_name = smart_lm_instance.ollama_model_name
    base_url = smart_lm_instance.ollama_base_url
    context_size = getattr(smart_lm_instance, "context_size", None)

    log.debug(
        _LOG_PREFIX,
        f"Generating with Ollama: model={model_name}, prompt_len={len(prompt)}",
    )

    # Build messages in chat format
    messages = []

    # Handle LLM mode (text-only with system instructions and few-shot examples)
    if llm_mode and llm_mode != "raw":
        # Get few-shot config for examples and instruction_template
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
    elif image_paths and len(image_paths) > 0:
        # Vision mode — system + user are passed separately by Eclipse 3.5+.
        # Legacy callers may still send a combined "system\n\nuser" string.
        system_prompt = kwargs.get("system_prompt")
        user_message = ""

        if system_prompt is not None:
            # Explicit split — use as-is
            user_message = (prompt or "").strip()
            log.debug(
                _LOG_PREFIX,
                f"  Split: System: {(system_prompt or 'None')[:50]}..., User: {(user_message or 'empty')[:50]}...",
            )
        elif "\n\nAdditional context:" in prompt:
            # Legacy explicit marker — split there regardless of other \n\n in the system text
            head, tail = prompt.split("\n\nAdditional context:", 1)
            system_prompt = head.strip()
            user_message = tail.strip()
            log.debug(
                _LOG_PREFIX,
                f"  Parsed - System: {system_prompt[:50] if system_prompt else 'None'}..., User: {user_message[:50] if user_message else 'empty'}...",
            )
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

        # Add system message if we have one
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Inject text-only few-shot examples to guide output style (no prefixes, uncensored)
        if vision_task and use_few_shot:
            from .config_templates import get_vision_few_shot_messages

            few_shot = get_vision_few_shot_messages(vision_task)
            if few_shot:
                messages.extend(few_shot)

        # Add user message (can be empty - system prompt already has instruction)
        messages.append({"role": "user", "content": user_message})
    else:
        # Simple text-only (no llm_mode, no images) - use prompt as-is
        messages.append({"role": "user", "content": prompt})

    # Check if this is a vision request and if model supports it
    use_vision = False
    if image_paths and len(image_paths) > 0:
        # Check if model has vision capability
        has_vision = check_model_has_vision(model_name)
        if not has_vision:
            # Check if this is a local GGUF model (they typically don't have vision)
            is_local_model = model_name.startswith("local_")

            if is_local_model:
                # Local GGUF models don't have vision - fall back to text-only
                log.warning(
                    _LOG_PREFIX,
                    f"Model {model_name} does not support vision (local GGUF files are text-only)",
                )
                log.warning(
                    _LOG_PREFIX,
                    "Falling back to text-only generation (image will be ignored)",
                )
                log.warning(
                    _LOG_PREFIX,
                    "TIP: For vision support, use 'llama.cpp (Docker)' backend or Ollama registry models",
                )
                use_vision = False
            else:
                # Non-local model might have vision, try anyway
                log.warning(
                    _LOG_PREFIX,
                    f"Model {model_name} may not support vision. Trying anyway...",
                )
                use_vision = True
        else:
            use_vision = True

    if use_vision and image_paths:
        # Use vision API
        import base64

        # Convert images to base64
        images_b64 = []
        for img_path in image_paths:
            try:
                with open(img_path, "rb") as f:
                    img_data = f.read()
                    images_b64.append(base64.b64encode(img_data).decode("utf-8"))
                    log.debug(
                        _LOG_PREFIX, f"Loaded image: {img_path} ({len(img_data)} bytes)"
                    )
            except Exception as e:
                log.error(_LOG_PREFIX, f"Failed to read image {img_path}: {e}")

        if not images_b64:
            log.error(_LOG_PREFIX, "No images could be loaded for vision request")
            return "", ""

        log.debug(_LOG_PREFIX, f"Sending {len(images_b64)} images to Ollama vision API")

        result = generate_with_ollama_vision(
            model_name=model_name,
            messages=messages,
            images=images_b64,
            max_tokens=max_tokens,
            temperature=temperature,
            context_size=context_size,
            top_p=top_p,
            top_k=top_k,
            seed=seed,
            repeat_penalty=repetition_penalty,
            min_p=min_p,
            mirostat=mirostat,
            mirostat_eta=mirostat_eta,
            mirostat_tau=mirostat_tau,
            repeat_last_n=repeat_last_n,
            stop_sequences=stop_sequences,
        )
    else:
        # Text-only generation
        result = generate_with_ollama(
            model_name=model_name,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            context_size=context_size,
            top_k=top_k,
            seed=seed,
            repeat_penalty=repetition_penalty,
            min_p=min_p,
            mirostat=mirostat,
            mirostat_eta=mirostat_eta,
            mirostat_tau=mirostat_tau,
            repeat_last_n=repeat_last_n,
            stop_sequences=stop_sequences,
        )

    if result is None:
        log.error(_LOG_PREFIX, "Ollama generation returned None")
        raise RuntimeError("Ollama generation returned no response")
    else:
        # Strip leading/trailing whitespace from output
        result = result.strip()

        # Log raw result before any processing for debugging
        log.debug(
            _LOG_PREFIX,
            f"Ollama raw response (before processing): {result[:500] if result else 'empty'}...",
        )

        from .common import clean_model_output

        result, raw_output = clean_model_output(result)

        # If result is empty after stripping but we had content, the model only output thinking
        if not result and raw_output:
            log.warning(
                _LOG_PREFIX,
                "Model output only contained thinking tags with no actual answer - using raw output",
            )
            result = raw_output

    log.debug(_LOG_PREFIX, f"Ollama result: {result[:100] if result else 'empty'}...")

    return result, raw_output  # Return (cleaned, raw) for compatibility


# ==============================================================================
# HIGH-LEVEL API
# ==============================================================================


def ensure_ollama_running() -> bool:
    # Validate creation identity even when the fixed-name container is running.
    return start_ollama_container()


def load_model_in_ollama(model_name: str, auto_pull: bool = True) -> tuple:
    # Ensure a model is loaded in Ollama.
    #
    # Args:
    #     model_name: Ollama model name (e.g., "ministral-3:8b")
    #     auto_pull: If True, pull model if not available
    #
    # Returns:
    #     tuple: (success: bool, actual_model_name: str)
    #            The actual_model_name is the exact name available in Ollama
    if not ensure_ollama_running():
        return False, model_name

    # Check if model is available
    available_models = list_ollama_models()
    log.debug(_LOG_PREFIX, f"Available models in Ollama: {available_models}")

    # Parse model name to check if a specific tag was requested
    model_base = model_name.split(":")[0]
    has_specific_tag = ":" in model_name
    requested_tag = model_name.split(":")[1] if has_specific_tag else None

    # First, check for exact match
    if model_name in available_models:
        log.msg(_LOG_PREFIX, f"✓ Model {model_name} ready (exact match)")
        return True, model_name

    # If no specific tag, check for :latest tag
    if not has_specific_tag:
        latest_name = f"{model_base}:latest"
        if latest_name in available_models:
            log.msg(_LOG_PREFIX, f"✓ Model {latest_name} ready (using :latest)")
            return True, latest_name

    # Only use variant fallback if NO specific tag was requested
    # If user asked for ministral-3:3b, don't give them ministral-3:8b!
    if not has_specific_tag:
        matching_models = [m for m in available_models if m.startswith(model_base)]
        if matching_models:
            # Use the first matching model
            actual_name = matching_models[0]
            log.msg(
                _LOG_PREFIX, f"✓ Model {actual_name} ready (variant of {model_base})"
            )
            return True, actual_name

    # Model not found - try to pull it
    if auto_pull:
        log.msg(_LOG_PREFIX, f"Model {model_name} not found, pulling...")
        if pull_ollama_model(model_name):
            # After pulling, check what name it actually has
            # Add a small delay to allow Ollama to update its model list
            time.sleep(2)

            available_models = list_ollama_models()
            log.debug(_LOG_PREFIX, f"Available models after pull: {available_models}")
            log.debug(
                _LOG_PREFIX,
                f"Looking for model_name={model_name}, model_base={model_base}",
            )

            # Check for exact match first - this should be the normal case after pull
            if model_name in available_models:
                log.msg(_LOG_PREFIX, f"✓ Model {model_name} ready")
                return True, model_name

            # Only use variant fallback if NO specific tag was requested
            # If user asked for ministral-3:3b, don't give them ministral-3:8b!
            if not has_specific_tag:
                # Check for any new model with same base
                matching_models = [
                    m for m in available_models if m.startswith(model_base)
                ]
                if matching_models:
                    actual_name = matching_models[0]
                    log.msg(_LOG_PREFIX, f"✓ Model pulled as {actual_name}")
                    return True, actual_name

                # More flexible matching: check if model_base is contained anywhere
                # This handles cases like "qwen2.5-vl:7b" being stored as "qwen2.5-vl:7b-q4_0"
                flexible_matches = [m for m in available_models if model_base in m]
                if flexible_matches:
                    actual_name = flexible_matches[0]
                    log.msg(
                        _LOG_PREFIX, f"✓ Model pulled as {actual_name} (flexible match)"
                    )
                    return True, actual_name

            # Last resort: try to use the model directly (Ollama might have it under requested name)
            # Sometimes the API list is stale but the model is actually available
            log.msg(
                _LOG_PREFIX,
                f"Model not in list but pull succeeded - trying to use {model_name} directly",
            )
            return True, model_name
        else:
            return False, model_name
    else:
        log.error(_LOG_PREFIX, f"Model {model_name} not available")
        return False, model_name


# ==============================================================================
# UNIFIED LOAD API (for SmartLoader v2)
# ==============================================================================


def load_ollama(
    model_path: str,
    model_type: str = "llm",
    use_gguf: bool = False,
    ctx: Optional[TemplateContext] = None,
    **kwargs,
) -> Dict[str, Any]:
    # Load a model via Ollama Docker for SmartLoader v2 integration.
    #
    # Args:
    #     model_path: GGUF file path (if use_gguf=True) or Ollama model name (e.g. "mistral:7b")
    #     model_type: Type of model ("llm", "vlm")
    #     use_gguf: If True, model_path is a GGUF file; if False, it's an Ollama model name
    #     ctx: TemplateContext with widget values (for template creation)
    #     **kwargs: Additional configuration options
    #
    # Returns:
    #     Dict with client info: {"client": None, "model_name": str, "base_url": str, "backend": str}
    config = _get_ollama_config()
    port = config.get("port", OLLAMA_DEFAULT_PORT)
    base_url = f"http://localhost:{port}"

    # Ensure container is running first
    if not ensure_ollama_running():
        raise RuntimeError("Failed to start Ollama Docker container")

    if use_gguf:
        # For GGUF files, import them directly into Ollama
        gguf_path = Path(model_path)

        if not gguf_path.exists():
            raise RuntimeError(f"GGUF file not found: {model_path}")

        log.msg(_LOG_PREFIX, f"Loading local GGUF file: {gguf_path.name}")

        # Import the GGUF file into Ollama (pass context for template creation)
        actual_model_name = import_gguf_to_ollama(model_path, ctx=ctx)

        if not actual_model_name:
            raise RuntimeError(
                f"Failed to import GGUF file '{gguf_path.name}' into Ollama.\n\n"
                f"Possible causes:\n"
                f"  1. The GGUF file is not in your models directory\n"
                f"  2. The file format is not supported\n"
                f"  3. Docker cannot access the file\n\n"
                f"Recommendations:\n"
                f"  - Ensure the GGUF is in your ComfyUI models/LLM folder\n"
                f"  - Try 'llama.cpp (Docker)' backend for more direct GGUF support"
            )

        is_gguf = True
    else:
        # model_path is already an Ollama model name (e.g., "ministral-3:3b")
        model_name = model_path
        is_gguf = False

        log.msg(_LOG_PREFIX, f"Loading Ollama model: {model_name}")

        # Pull/load the model from registry - get the actual model name
        success, actual_model_name = load_model_in_ollama(model_name, auto_pull=True)
        if not success:
            # Get the specific error for smarter error message
            pull_error = get_last_pull_error()

            # Build error message based on the specific failure type
            if pull_error and "digest mismatch" in pull_error.lower():
                raise RuntimeError(
                    f"Digest mismatch for model '{model_name}' - the model file was corrupted during download.\n\n"
                    f"Error: {pull_error}\n\n"
                    f"Ollama has removed the corrupted file. Simply re-queue the workflow to download again."
                )
            elif pull_error and (
                "not found" in pull_error.lower()
                or "empty response" in pull_error.lower()
            ):
                raise RuntimeError(
                    f"Model '{model_name}' not found in Ollama registry.\n\n"
                    f"Verify the model name at: https://ollama.com/library\n"
                    f"Common model names: mistral, llama3, ministral-3:3b, ministral-3:8b"
                )
            elif pull_error and "timeout" in pull_error.lower():
                raise RuntimeError(
                    f"Timeout downloading model '{model_name}'.\n\n"
                    f"The model may be very large. Try:\n"
                    f"  - Increase pull_timeout in docker_config.json\n"
                    f"  - Pull manually: docker exec sml-ollama ollama pull {model_name}"
                )
            else:
                # Generic error with specific message if available
                error_detail = f"\n\nError: {pull_error}" if pull_error else ""
                raise RuntimeError(
                    f"Failed to load model '{model_name}' in Ollama.{error_detail}\n\n"
                    f"Possible causes:\n"
                    f"  1. Network error during download\n"
                    f"  2. Model digest mismatch (corrupted download)\n"
                    f"  3. Insufficient disk space in Docker\n"
                    f"  4. Model name '{model_name}' may not exist in Ollama registry\n\n"
                    f"Recommendations:\n"
                    f"  - Check the console log above for specific error messages\n"
                    f"  - Try manually: docker exec sml-ollama ollama pull {model_name}\n"
                    f"  - If digest mismatch: docker exec sml-ollama ollama rm {model_name}\n"
                    f"  - Verify model exists at: https://ollama.com/library"
                )

        # Log if using different model name than requested
        if actual_model_name != model_name:
            log.debug(
                _LOG_PREFIX,
                f"Using actual model name: {actual_model_name} (requested: {model_name})",
            )

    log.msg(_LOG_PREFIX, f"✓ Ollama Docker ready: {actual_model_name} @ {base_url}")

    return {
        "client": None,  # Ollama uses HTTP API, no client object
        "model_name": actual_model_name,  # Use the actual available model name
        "base_url": base_url,
        "backend": "ollama_docker",
        "model_type": model_type,
        "is_gguf": is_gguf,
    }


# ==============================================================================
# AVAILABILITY CHECK & DOCKER DAEMON MANAGEMENT
# ==============================================================================

OLLAMA_DOCKER_AVAILABLE = DOCKER_AVAILABLE


def ensure_docker_running() -> bool:
    # Ensure Docker is running for Ollama. Start daemon if needed.
    if not OLLAMA_DOCKER_AVAILABLE:
        return False
    return _ensure_docker_running()


# Check on module load (uses cached daemon status — no extra subprocess call)
if OLLAMA_DOCKER_AVAILABLE:
    if get_cached_daemon_status():
        log.debug(_LOG_PREFIX, "Docker available for Ollama (daemon running)")
    else:
        log.debug(
            _LOG_PREFIX, "Docker available for Ollama (will auto-start when needed)"
        )
