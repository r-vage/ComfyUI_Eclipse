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

import json
import os
import subprocess
import time
import base64
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from .logger import log
from .device import get_docker_gpu_args, detect_gpu_vendor
from . import docker_error_handler

_LOG_PREFIX = "SGLang Docker"

_last_sglang_container_name = None  # Track container for error diagnosis


# ==============================================================================
# DOCKER DAEMON MANAGEMENT (centralized in docker_utils)
# ==============================================================================

from .docker_utils import (
    IS_WINDOWS,
    IS_LINUX,
    IS_MACOS,
    is_docker_installed,
    get_docker_version,
    get_cached_daemon_status,
    is_docker_daemon_running,
    start_docker_daemon,
    ensure_docker_running,
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
    "sglang": {
        "docker_image": "lmsysorg/sglang:latest",
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
                config["sglang"] = DEFAULT_SGLANG_CONFIG["sglang"].copy()
                save_docker_config(config)
            return config
        except Exception as e:
            log.warning(_LOG_PREFIX, f"Error loading docker_config.json: {e}")

    # Create default config
    save_docker_config(DEFAULT_SGLANG_CONFIG)
    return DEFAULT_SGLANG_CONFIG.copy()


def save_docker_config(config: Dict[str, Any]) -> bool:
    # Save Docker config to file.
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error saving docker_config.json: {e}")
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
    # Get SGLang Docker image name with automatic GPU vendor detection.
    #
    # Returns ROCm-optimized image (lmsysorg/sglang:v0.5.9-rocm720-mi30x) for AMD GPUs,
    # or the configured NVIDIA image (lmsysorg/sglang:latest) otherwise.
    from .device import detect_gpu_vendor, get_docker_image_for_vendor

    base_image = get_sglang_config().get("docker_image", "lmsysorg/sglang:latest")
    vendor = detect_gpu_vendor()

    if vendor == "amd":
        rocm_image = get_docker_image_for_vendor(base_image, vendor)
        if rocm_image != base_image:
            log.debug(_LOG_PREFIX, f"AMD GPU detected - using ROCm image: {rocm_image}")
        return rocm_image

    return base_image


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
    config = load_docker_config()
    if "paths" not in config:
        config["paths"] = {}
    config["paths"]["models_base"] = path
    return save_docker_config(config)


# ==============================================================================
# MODEL-CONTAINER TRACKING
# ==============================================================================


def get_container_for_model(model_name: str) -> Optional[Dict[str, Any]]:
    # Get container info for a model (for reuse).
    config = load_docker_config()
    containers = config.get("sglang_model_containers", {})
    return containers.get(model_name)


def save_container_for_model(model_name: str, container_info: Dict[str, Any]) -> bool:
    # Save container info for a model.
    config = load_docker_config()
    if "sglang_model_containers" not in config:
        config["sglang_model_containers"] = {}
    config["sglang_model_containers"][model_name] = container_info
    return save_docker_config(config)


def update_container_last_used(model_name: str) -> bool:
    # Update last_used timestamp for a container.
    config = load_docker_config()
    containers = config.get("sglang_model_containers", {})
    if model_name in containers:
        containers[model_name]["last_used"] = time.time()
        return save_docker_config(config)
    return False


def remove_container_for_model(model_name: str) -> bool:
    # Remove saved container entry for a model (e.g., container was deleted).
    config = load_docker_config()
    containers = config.get("sglang_model_containers", {})
    if model_name in containers:
        del containers[model_name]
        log.debug(_LOG_PREFIX, f"Removed container entry for model {model_name}")
        return save_docker_config(config)
    return True


def cleanup_stale_containers(max_age_hours: int = 24) -> int:
    # Remove container entries older than max_age_hours.
    config = load_docker_config()
    containers = config.get("sglang_model_containers", {})
    now = time.time()
    max_age_seconds = max_age_hours * 3600

    removed = 0
    for model_name in list(containers.keys()):
        last_used = containers[model_name].get("last_used", 0)
        if now - last_used > max_age_seconds:
            del containers[model_name]
            removed += 1

    if removed > 0:
        save_docker_config(config)
    return removed


# ==============================================================================
# CONTAINER MANAGEMENT
# ==============================================================================


def is_sglang_container_running() -> bool:
    # Check if any SGLang container is running.
    containers = get_running_sglang_containers()
    return len(containers) > 0


def get_running_sglang_containers() -> List[Dict[str, str]]:
    # Get list of running SGLang containers.
    if not DOCKER_AVAILABLE:
        return []

    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                "name=sml_sglang",
                "--format",
                "{{.ID}}\t{{.Names}}\t{{.Status}}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            containers = []
            for line in result.stdout.strip().split("\n"):
                parts = line.split("\t")
                if len(parts) >= 3:
                    containers.append(
                        {"id": parts[0], "name": parts[1], "status": parts[2]}
                    )
            return containers
    except Exception as e:
        log.debug(_LOG_PREFIX, f"Error getting containers: {e}")
    return []


def is_container_exists(container_name: str) -> bool:
    # Check if a container exists (running or stopped).
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name={container_name}",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return container_name in result.stdout
    except Exception:
        return False


def is_container_running(container_name: str) -> bool:
    # Check if a specific container is running.
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"name={container_name}",
                "--filter",
                "status=running",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return container_name in result.stdout
    except Exception:
        return False


def start_existing_container(container_name: str) -> bool:
    # Start an existing stopped container.
    try:
        log.msg(_LOG_PREFIX, f"Starting existing container: {container_name}")
        result = subprocess.run(
            ["docker", "start", container_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            log.msg(_LOG_PREFIX, f"Container {container_name} started")
            return True
        log.warning(_LOG_PREFIX, f"Failed to start container: {result.stderr}")
        return False
    except Exception as e:
        log.warning(_LOG_PREFIX, f"Error starting container: {e}")
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
            result = subprocess.run(
                ["docker", "stop", name], capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                log.msg(_LOG_PREFIX, f"Container {name} stopped")
            else:
                log.warning(_LOG_PREFIX, f"Failed to stop {name}: {result.stderr}")

        return True
    except Exception as e:
        log.warning(_LOG_PREFIX, f"Error stopping containers: {e}")
        return False


def remove_sglang_container(container_name: str) -> bool:
    # Remove an SGLang container.
    try:
        log.msg(_LOG_PREFIX, f"Removing container: {container_name}")
        result = subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception as e:
        log.warning(_LOG_PREFIX, f"Error removing container: {e}")
        return False


def wait_for_sglang_ready(
    url: str,
    timeout: int = 300,
    poll_interval: int = 5,
    container_name: Optional[str] = None,
) -> bool:
    # Wait for SGLang server to be ready.
    import requests  # type: ignore

    start_time = time.time()
    health_url = url.rstrip("/v1") + "/health"

    log.msg(_LOG_PREFIX, f"Waiting for SGLang to be ready (timeout: {timeout}s)...")

    while time.time() - start_time < timeout:
        # Check if container is still running before checking health
        if not is_sglang_container_running():
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


def start_sglang_container(
    model_path: str,
    quantization: Optional[str] = None,
    context_size: Optional[int] = None,
    gpu_ids: Optional[List[int]] = None,
    tp_size: Optional[int] = None,
    dp_size: Optional[int] = None,
) -> Optional[str]:
    # Start an SGLang container for a model.
    #
    # Args:
    #     model_path: Path to the model folder
    #     quantization: Quantization method (fp8, awq, gptq, or None)
    #     context_size: Maximum context length (max_model_len)
    #     gpu_ids: List of GPU indices to use
    #     tp_size: Tensor parallelism size
    #     dp_size: Data parallelism size
    #
    # Returns:
    #     Container name if successful, None otherwise
    if not ensure_docker_running():
        log.error(_LOG_PREFIX, "Docker is not running")
        return None

    sglang_config = get_sglang_config()
    model_name = Path(model_path).name

    # Generate container name
    safe_name = model_name.lower().replace(" ", "_").replace(".", "_")[:30]
    container_name = (
        f"{sglang_config.get('container_name_prefix', 'sml_sglang')}_{safe_name}"
    )
    port = get_sglang_port()

    # Check if we can reuse existing container (tracked in config)
    container_info = get_container_for_model(model_name)
    if container_info:
        existing_name = container_info.get("container_name")
        port = container_info.get("port", port)

        # Check if image was updated — if so, discard saved container
        from .docker_utils import is_container_image_stale

        sglang_image = get_sglang_docker_image()
        _image_stale = (
            existing_name
            and is_container_exists(existing_name)
            and is_container_image_stale(existing_name, sglang_image)
        )

        if _image_stale:
            log.msg(_LOG_PREFIX, "Removing stale container to use updated image...")
            remove_sglang_container(existing_name)
            remove_container_for_model(model_name)
        elif existing_name and is_container_running(existing_name):
            log.msg(_LOG_PREFIX, f"Reusing running container: {existing_name}")
            update_container_last_used(model_name)
            return existing_name
        elif existing_name and is_container_exists(existing_name):
            if start_existing_container(existing_name):
                # Wait for SGLang to be ready after restart
                url = f"http://localhost:{port}/v1"
                log.msg(_LOG_PREFIX, "Waiting for SGLang to be ready after restart...")
                if wait_for_sglang_ready(
                    url, timeout=120, container_name=existing_name
                ):
                    update_container_last_used(model_name)
                    return existing_name
                else:
                    log.warning(
                        _LOG_PREFIX,
                        "Container restarted but SGLang not ready, will recreate",
                    )
                    if not remove_sglang_container(existing_name):
                        log.warning(
                            _LOG_PREFIX,
                            f"Failed to remove container {existing_name}, retrying...",
                        )
                        time.sleep(2)
                        if not remove_sglang_container(
                            existing_name
                        ) and is_container_exists(existing_name):
                            log.error(
                                _LOG_PREFIX,
                                f"Cannot remove stale container '{existing_name}'.\n"
                                f"Please run manually: docker rm -f {existing_name}\n"
                                f"If that fails, try: docker system prune or restart Docker daemon",
                            )
                            return None

    # Check if container with same name exists but wasn't tracked (e.g., config was cleared)
    # This prevents "container name already in use" errors
    if is_container_exists(container_name):
        if is_container_running(container_name):
            log.msg(
                _LOG_PREFIX, f"✓ Reusing existing running container: {container_name}"
            )
            # Save to config for future tracking
            save_container_for_model(
                model_name,
                {
                    "container_name": container_name,
                    "port": port,
                    "last_used": time.time(),
                },
            )
            return container_name
        else:
            # Container exists but stopped - try to restart it
            log.msg(_LOG_PREFIX, f"Restarting existing container: {container_name}")
            # Stop any OTHER running SGLang containers first
            stop_sglang_container()

            if start_existing_container(container_name):
                url = f"http://localhost:{port}/v1"
                log.msg(_LOG_PREFIX, "Waiting for SGLang to be ready after restart...")
                if wait_for_sglang_ready(
                    url, timeout=120, container_name=container_name
                ):
                    # Save to config for future tracking
                    save_container_for_model(
                        model_name,
                        {
                            "container_name": container_name,
                            "port": port,
                            "last_used": time.time(),
                        },
                    )
                    return container_name
                else:
                    log.warning(
                        _LOG_PREFIX,
                        "Container restarted but SGLang not ready, will recreate",
                    )

            # Failed to restart, remove and recreate
            log.warning(_LOG_PREFIX, "Failed to restart container, will recreate")
            if not remove_sglang_container(container_name):
                log.warning(
                    _LOG_PREFIX,
                    f"Failed to remove container {container_name}, retrying...",
                )
                time.sleep(2)
                if not remove_sglang_container(container_name) and is_container_exists(
                    container_name
                ):
                    log.error(
                        _LOG_PREFIX,
                        f"Cannot remove stale container '{container_name}'.\n"
                        f"Please run manually: docker rm -f {container_name}\n"
                        f"If that fails, try: docker system prune or restart Docker daemon",
                    )
                    return None

    # Stop any existing SGLang containers (single model at a time for VRAM)
    stop_sglang_container()

    # Check GPU memory
    free_mem = get_free_gpu_memory_mb(0)
    if free_mem < 4000:  # Require at least 4GB free
        log.warning(
            _LOG_PREFIX, f"Low GPU memory: {free_mem}MB free. Model may not load."
        )

    # Build docker run command
    docker_image = get_sglang_docker_image()
    port = get_sglang_port()

    # Validate image string + resolve bind host (defense-in-depth before subprocess)
    from .docker_utils import validate_docker_image, get_docker_bind_host

    docker_image = validate_docker_image(docker_image)
    bind_host = get_docker_bind_host()

    # gpu_memory_utilization is a global setting
    from .backend_vllm_docker import get_global_docker_options

    global_opts = get_global_docker_options()
    gpu_util = global_opts.get("gpu_memory_utilization", 0.9)

    # Determine model path for container
    models_base = get_models_base_path()
    from .docker_utils import make_docker_volume

    if models_base and model_path.startswith(models_base):
        # Model is within configured base path
        relative_path = os.path.relpath(model_path, models_base)
        container_model_path = f"/models/{relative_path}"
        volume_mount = make_docker_volume(models_base, "/models")
    else:
        # Mount model directory directly
        model_dir = os.path.dirname(model_path)
        container_model_path = f"/models/{model_name}"
        volume_mount = make_docker_volume(model_dir, "/models")

    # Build command
    tp = tp_size or sglang_config.get("tp_size", 1)
    dp = dp_size or sglang_config.get("dp_size", 1)

    cmd = [
        "docker",
        "run",
        "-d",  # Detached mode
        *get_docker_gpu_args(),  # GPU flags: NVIDIA "--gpus all" or AMD "/dev/kfd, /dev/dri"
        "--name",
        container_name,
        "-p",
        f"{bind_host}:{port}:{port}",
        "-v",
        volume_mount,
        "--shm-size",
        "16g",
        "--ipc",
        "host",
        docker_image,
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
        str(gpu_util),
        "--tp",
        str(tp),
        "--dp",
        str(dp),
    ]

    # Add quantization if specified
    if quantization:
        quant_lower = quantization.lower()
        if quant_lower == "fp8":
            cmd.extend(["--quantization", "fp8"])
        elif quant_lower == "awq":
            cmd.extend(["--quantization", "awq"])
        elif quant_lower == "gptq":
            cmd.extend(["--quantization", "gptq"])

    # Add context size if specified
    if context_size:
        cmd.extend(["--context-length", str(context_size)])

    # Log and execute
    log.msg(_LOG_PREFIX, f"Starting SGLang container for: {model_name}")
    log.debug(_LOG_PREFIX, f"Docker command: {' '.join(cmd)}")

    # Check if image exists, pull if needed
    try:
        check_image = subprocess.run(
            ["docker", "images", "-q", docker_image],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if not check_image.stdout.strip():
            log.msg(
                _LOG_PREFIX,
                f"Pulling SGLang image: {docker_image} (this may take 5-10 minutes)...",
            )
            log.msg(
                _LOG_PREFIX,
                "  Watch Docker Desktop or run 'docker pull lmsysorg/sglang:latest' for progress",
            )
            # Don't capture output so user sees docker pull progress in terminal
            # Use Popen for non-blocking with timeout
            pull_process = subprocess.Popen(
                ["docker", "pull", docker_image],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            # Stream output line by line
            if pull_process.stdout:
                last_layer = ""
                while True:
                    line = pull_process.stdout.readline()
                    if not line and pull_process.poll() is not None:
                        break
                    if line:
                        line = line.strip()
                        # Show layer progress updates
                        if (
                            "Pulling" in line
                            or "Download" in line
                            or "Pull complete" in line
                        ):
                            if line != last_layer:
                                log.msg(_LOG_PREFIX, f"  {line[:80]}")
                                last_layer = line

            pull_process.wait(timeout=1800)
            if pull_process.returncode != 0:
                log.error(
                    _LOG_PREFIX,
                    f"Failed to pull image (exit code {pull_process.returncode})",
                )
                return None
            log.msg(_LOG_PREFIX, "✓ Image pulled successfully")
    except subprocess.TimeoutExpired:
        log.error(_LOG_PREFIX, "Image pull timed out (30 min limit)")
        return None
    except Exception as e:
        log.warning(_LOG_PREFIX, f"Could not check/pull image: {e}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,  # Container creation should be fast after image is pulled
        )

        if result.returncode != 0:
            log.error(_LOG_PREFIX, f"Failed to start container: {result.stderr}")
            return None

        container_id = result.stdout.strip()[:12]
        log.msg(_LOG_PREFIX, f"Container started: {container_name} ({container_id})")

        # Wait for SGLang to be ready
        url = f"http://localhost:{port}/v1"
        if not wait_for_sglang_ready(url, timeout=300, container_name=container_name):
            log.error(_LOG_PREFIX, "SGLang failed to start within timeout")
            stop_sglang_container(container_name)
            return None

        # Save container info for reuse
        save_container_for_model(
            model_name,
            {
                "container_name": container_name,
                "container_id": container_id,
                "model_path": model_path,
                "port": port,
                "last_used": time.time(),
                "quantization": quantization,
            },
        )

        return container_name

    except subprocess.TimeoutExpired:
        log.error(_LOG_PREFIX, "Docker command timed out")
        return None
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error starting container: {e}")
        return None


def auto_start_sglang_for_model(
    model_path: str,
    config: Optional[Dict[str, Any]] = None,
    quantization: Optional[str] = None,
    context_size: Optional[int] = None,
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

        response = requests.get(f"{url.rstrip('/v1')}/health", timeout=timeout)
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

        response = requests.get(f"{url.rstrip('/v1')}/health", timeout=timeout)
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

    sglang_config = get_sglang_config()
    url = get_sglang_url()

    # Check if SGLang is serving the correct model
    matched_model = is_sglang_serving_model(model_path)
    model_name = Path(model_path).name

    if not matched_model:
        # Always start container when model not found
        log.msg(_LOG_PREFIX, f"Starting SGLang container for {model_name}...")
        try:
            if auto_start_sglang_for_model(
                model_path,
                sglang_config,
                quantization=quantization,
                context_size=context_size,
            ):
                matched_model = is_sglang_serving_model(model_path)
                if matched_model:
                    log.msg(_LOG_PREFIX, "✓ Container started successfully!")
                else:
                    log.warning(_LOG_PREFIX, "Container started but model not detected")
                    return None
            else:
                log.warning(_LOG_PREFIX, "Failed to start SGLang container")
                return None
        except Exception as e:
            log.warning(_LOG_PREFIX, f"Container start error: {e}")
            return None

    # Model found - create client
    request_timeout = get_sglang_request_timeout()
    client = OpenAI(base_url=url, api_key="not-needed", timeout=request_timeout)

    # Update last used timestamp
    update_container_last_used(model_name)

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

        # Strip thinking tags from "Thinker" models (e.g., Qwen3-VL-Thinking, DeepSeek-R1)
        from .common import strip_thinking_tags, strip_llm_prefixes

        cleaned_result, raw_result = strip_thinking_tags(result)
        cleaned_result = strip_llm_prefixes(cleaned_result)

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

        log.error(_LOG_PREFIX, f"SGLang generation error: {e}")
        if _last_sglang_container_name:
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
