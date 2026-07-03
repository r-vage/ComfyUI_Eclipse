# llama.cpp/Docker integration for SML.
#
# llama.cpp server is the reference GGUF inference engine:
# - Native GGUF support (designed for it)
# - Fast inference with optimized kernels
# - Flexible GPU layer offloading
# - OpenAI-compatible API
#
# Docker image: ghcr.io/ggerganov/llama.cpp:server-cuda

import json
import subprocess
import time
import requests  # type: ignore
import base64
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from .logger import log
from .device import get_gpu_info, estimate_model_size_gb, get_docker_gpu_args, detect_gpu_vendor
from .config_templates import get_llm_models_absolute_path
from . import docker_error_handler


_LOG_PREFIX = "llama.cpp Docker"


# ==============================================================================
# DOCKER DAEMON MANAGEMENT (centralized in docker_utils)
# ==============================================================================

from .docker_utils import (
    is_docker_installed, get_docker_version, get_cached_daemon_status,
    is_docker_daemon_running, start_docker_daemon,
    ensure_docker_running as _ensure_docker_running,
)

# Module-level availability flags (used throughout this file)
# Uses cached values from docker_utils — no extra subprocess calls at import time
DOCKER_AVAILABLE = is_docker_installed()
DOCKER_VERSION = get_docker_version()


# ==============================================================================
# CONSTANTS
# ==============================================================================

# Docker images for llama.cpp (official ggml-org repo)
# NVIDIA: CUDA-enabled server image
# AMD/ROCm: CPU fallback (no official ROCm image available)
_LLAMACPP_IMAGE_NVIDIA = "ghcr.io/ggml-org/llama.cpp:server-cuda"
_LLAMACPP_IMAGE_CPU = "ghcr.io/ggml-org/llama.cpp:server"
LLAMACPP_DEFAULT_PORT = 8080
LLAMACPP_CONTAINER_PREFIX = "sml-llamacpp"


def get_llamacpp_docker_image() -> str:
    # Get llama.cpp Docker image from config with automatic GPU vendor detection.
    #
    # Reads docker_image from docker_config.json llamacpp section,
    # then returns CPU image for AMD (no ROCm image available).
    base_image = _get_llamacpp_config().get("docker_image", _LLAMACPP_IMAGE_NVIDIA)
    vendor = detect_gpu_vendor()
    
    if vendor == "amd":
        log.debug(_LOG_PREFIX, "AMD GPU detected - using CPU image (no ROCm image available for llama.cpp)")
        return _LLAMACPP_IMAGE_CPU
    
    return base_image

# mmproj patterns for auto-detection (vision support)
MMPROJ_PATTERNS = [
    "mmproj*.gguf",      # Official naming: mmproj-F16.gguf, mmproj-Q8_0.gguf
    "*-mmproj.gguf",     # e.g., model-mmproj.gguf
    "*_mmproj.gguf",
    "*mmproj*.gguf",
    "*projector*.gguf",
    "*-clip-*.gguf",     # Some models use clip naming
]


# ==============================================================================
# CONFIGURATION
# ==============================================================================

_CONFIG_PATH = Path(__file__).parent.parent.parent / "docker_config.json"


def _get_llamacpp_config() -> Dict[str, Any]:
    # Get llama.cpp-specific configuration from docker_config.json.
    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                return config.get("llamacpp", {})
    except Exception as e:
        log.debug(_LOG_PREFIX, f"Could not load llamacpp config: {e}")
    
    return {
        "docker_image": _LLAMACPP_IMAGE_NVIDIA,
        "port": LLAMACPP_DEFAULT_PORT,
        "n_gpu_layers": -1,  # -1 = all layers on GPU
    }


def get_llamacpp_startup_timeout() -> int:
    # Get llama.cpp startup timeout from config (default 120s / 2 min).
    config = _get_llamacpp_config()
    return config.get("startup_timeout", 120)


def get_llamacpp_request_timeout() -> int:
    # Get llama.cpp request timeout from config (default 180s / 3 min).
    config = _get_llamacpp_config()
    return config.get("request_timeout", 180)


def _save_llamacpp_config(llamacpp_config: Dict[str, Any]):
    # Save llama.cpp configuration to docker_config.json.
    try:
        config = {}
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
        
        config["llamacpp"] = llamacpp_config
        
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        log.error(_LOG_PREFIX, f"Could not save llamacpp config: {e}")


def load_llamacpp_model_containers() -> Dict[str, Dict]:
    # Load model-to-container mappings for llama.cpp.
    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                return config.get("llamacpp_containers", {})
    except Exception:
        pass
    return {}


def save_llamacpp_model_container(model_name: str, container_id: str):
    # Save model-container mapping for llama.cpp.
    try:
        config = {}
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
        
        if "llamacpp_containers" not in config:
            config["llamacpp_containers"] = {}
        
        config["llamacpp_containers"][model_name] = {
            "container_id": container_id,
            "created": datetime.now().isoformat(),
            "last_used": datetime.now().isoformat(),
        }
        
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
            
        log.debug(_LOG_PREFIX, f"Saved container {container_id[:12]} for {model_name}")
    except Exception as e:
        log.error(_LOG_PREFIX, f"Could not save container mapping: {e}")


def _load_full_config() -> Dict[str, Any]:
    # Load full docker_config.json.
    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        log.debug(_LOG_PREFIX, f"Could not load config: {e}")
    return {}


def _save_full_config(config: Dict[str, Any]):
    # Save full docker_config.json.
    try:
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
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
            encoding='utf-8',
            errors='replace',  # Handle non-UTF8 bytes gracefully on Windows
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def _get_container_name(model_name: str) -> str:
    # Generate container name from model name.
    # Sanitize model name for container naming
    safe_name = model_name.replace("/", "-").replace(":", "-").replace(".", "-")
    return f"{LLAMACPP_CONTAINER_PREFIX}-{safe_name}"


def is_container_running(container_id_or_name: str) -> bool:
    # Check if a container is running.
    success, output = _run_docker_cmd(["ps", "-q", "-f", f"id={container_id_or_name}"])
    if success and output:
        return True
    success, output = _run_docker_cmd(["ps", "-q", "-f", f"name={container_id_or_name}"])
    return success and bool(output.strip())


def is_container_exists(container_id_or_name: str) -> bool:
    # Check if a container exists (running or stopped).
    success, output = _run_docker_cmd(["ps", "-aq", "-f", f"id={container_id_or_name}"])
    if success and output:
        return True
    success, output = _run_docker_cmd(["ps", "-aq", "-f", f"name={container_id_or_name}"])
    return success and bool(output.strip())


def get_running_llamacpp_containers() -> List[str]:
    # Get list of running llama.cpp containers.
    success, output = _run_docker_cmd(["ps", "-q", "-f", f"name={LLAMACPP_CONTAINER_PREFIX}"])
    if success and output:
        return output.strip().split('\n')
    return []


# ==============================================================================
# DOCKER IMAGE MANAGEMENT
# ==============================================================================

def is_image_available(image_name: str) -> bool:
    # Check if a Docker image is available locally.
    success, output = _run_docker_cmd(["images", "-q", image_name])
    return success and bool(output.strip())


def pull_docker_image(image_name: str, timeout: int = 300) -> bool:
    # Pull a Docker image from registry.
    #
    # Args:
    #     image_name: Image to pull (e.g., "ghcr.io/ggerganov/llama.cpp:server-cuda")
    #     timeout: Maximum seconds to wait for pull (default 5 minutes)
    #
    # Returns:
    #     bool: True if image was pulled successfully
    log.msg(_LOG_PREFIX, f"Pulling Docker image: {image_name} (this may take a few minutes)...")
    
    try:
        # Use longer timeout for image pull
        result = subprocess.run(
            ["docker", "pull", image_name],
            capture_output=True,
            timeout=timeout,
            text=True,
            encoding='utf-8',
            errors='replace',  # Handle non-UTF8 bytes gracefully on Windows
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        
        if result.returncode == 0:
            log.msg(_LOG_PREFIX, f"✓ Image {image_name} pulled successfully")
            return True
        else:
            log.error(_LOG_PREFIX, f"Failed to pull image: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        log.error(_LOG_PREFIX, f"Image pull timed out after {timeout}s - check your internet connection")
        return False
    except Exception as e:
        log.error(_LOG_PREFIX, f"Failed to pull image: {e}")
        return False


def ensure_llamacpp_image() -> bool:
    # Ensure the llama.cpp Docker image is available locally.
    # Pulls it if not present. Uses GPU vendor detection to select
    # CUDA image (NVIDIA) or CPU image (AMD/other).
    #
    # Returns:
    #     bool: True if image is available
    docker_image = get_llamacpp_docker_image()
    
    if is_image_available(docker_image):
        log.debug(_LOG_PREFIX, f"Image {docker_image} is available locally")
        return True
    
    log.msg(_LOG_PREFIX, f"Image {docker_image} not found locally, downloading...")
    return pull_docker_image(docker_image)


# ==============================================================================
# CONTAINER LIFECYCLE
# ==============================================================================

def start_llamacpp_container(
    model_path: str,
    models_base_path: Optional[str] = None,
    mmproj_path: Optional[str] = None,
    port: Optional[int] = None,
    n_gpu_layers: int = -1,
    ctx_size: int = 8192,
    wait_for_ready: bool = True,
) -> bool:
    # Start llama.cpp Docker container with specified GGUF model.
    #
    # Args:
    #     model_path: Full path to GGUF model file
    #     models_base_path: Base directory to mount (parent of model)
    #     mmproj_path: Optional path to mmproj file for vision support (auto-detected if None)
    #     port: Port to expose (default: 8080)
    #     n_gpu_layers: Number of layers to offload to GPU (-1 = all)
    #     ctx_size: Context size
    #     wait_for_ready: Wait for server to be ready
    #
    # Returns:
    #     bool: True if container started successfully
    # Ensure Docker daemon is running (auto-start on Windows)
    if not ensure_docker_running():
        log.error(_LOG_PREFIX, "Docker is not available or could not be started")
        return False
    
    # Ensure Docker image is available (auto-pull if needed)
    if not ensure_llamacpp_image():
        log.error(_LOG_PREFIX, "Failed to get llama.cpp Docker image - check your internet connection")
        return False
    
    config = _get_llamacpp_config()
    port = port or config.get("port", LLAMACPP_DEFAULT_PORT)
    n_gpu_layers = n_gpu_layers if n_gpu_layers != -1 else config.get("n_gpu_layers", -1)
    # ctx_size comes from template/node parameter, no config fallback
    
    model_path_path = Path(model_path)
    model_name = model_path_path.name
    
    # Auto-detect mmproj file for vision support
    detected_mmproj = None
    if mmproj_path:
        detected_mmproj = Path(mmproj_path)
        if not detected_mmproj.exists():
            log.warning(_LOG_PREFIX, f"Specified mmproj file not found: {mmproj_path}")
            detected_mmproj = None
    else:
        # Auto-detect mmproj in the same directory as the model
        parent_dir = model_path_path.parent
        for pattern in MMPROJ_PATTERNS:
            matches = list(parent_dir.glob(pattern))
            # Exclude the main model file
            matches = [m for m in matches if m != model_path_path]
            if matches:
                detected_mmproj = matches[0]
                log.msg(_LOG_PREFIX, f"Auto-detected mmproj: {detected_mmproj.name}")
                break
    
    # Check if we have a saved container for this model
    saved_containers = load_llamacpp_model_containers()
    container_name = _get_container_name(model_name)
    _set_last_container(container_name)  # Track for error diagnosis
    
    if model_name in saved_containers:
        saved_id = saved_containers[model_name]["container_id"]
        if is_container_exists(saved_id):
            # Check if image was updated
            from .docker_utils import is_container_image_stale
            llamacpp_image = get_llamacpp_docker_image()
            if is_container_image_stale(saved_id, llamacpp_image):
                log.msg(_LOG_PREFIX, "Removing stale container to use updated image...")
                _run_docker_cmd(["rm", "-f", saved_id])
            elif is_container_running(saved_id):
                log.msg(_LOG_PREFIX, f"✓ Container already running for {model_name}")
                return True
            else:
                # Restart existing container
                log.msg(_LOG_PREFIX, f"Restarting container for {model_name}...")
                success, _ = _run_docker_cmd(["start", saved_id])
                if success:
                    log.msg(_LOG_PREFIX, "✓ Container restarted")
                    if wait_for_ready:
                        startup_timeout = get_llamacpp_startup_timeout()
                        return wait_for_llamacpp_ready(port, timeout=startup_timeout, container_name=container_name)
                    return True
    
    # Check if container with same name already exists (even if not tracked in config)
    if is_container_exists(container_name):
        # Check if image was updated
        from .docker_utils import is_container_image_stale
        llamacpp_image = get_llamacpp_docker_image()
        if is_container_image_stale(container_name, llamacpp_image):
            log.msg(_LOG_PREFIX, "Removing stale container to use updated image...")
            _run_docker_cmd(["rm", "-f", container_name])
        elif is_container_running(container_name):
            log.msg(_LOG_PREFIX, f"✓ Reusing existing running container: {container_name}")
            # Save to config for future tracking
            success, container_id = _run_docker_cmd(["ps", "-q", "-f", f"name={container_name}"])
            if success and container_id:
                save_llamacpp_model_container(model_name, container_id.strip())
            return True
        else:
            # Container exists but stopped - restart it
            log.msg(_LOG_PREFIX, f"Restarting existing container: {container_name}")
            # Stop any OTHER running llama.cpp containers first
            for cid in get_running_llamacpp_containers():
                log.warning(_LOG_PREFIX, f"Stopping other container {cid[:12]}...")
                _run_docker_cmd(["stop", cid])
            
            success, _ = _run_docker_cmd(["start", container_name])
            if success:
                log.msg(_LOG_PREFIX, "✓ Container restarted")
                # Save to config for future tracking
                success, container_id = _run_docker_cmd(["ps", "-q", "-f", f"name={container_name}"])
                if success and container_id:
                    save_llamacpp_model_container(model_name, container_id.strip())
                if wait_for_ready:
                    startup_timeout = get_llamacpp_startup_timeout()
                    return wait_for_llamacpp_ready(port, timeout=startup_timeout, container_name=container_name)
                return True
            else:
                # Failed to restart, remove and recreate
                log.warning(_LOG_PREFIX, f"Failed to restart container, will recreate")
                rm_success, rm_output = _run_docker_cmd(["rm", "-f", container_name])
                if not rm_success:
                    log.warning(_LOG_PREFIX, f"Failed to remove container: {rm_output}")
                    import time
                    time.sleep(2)
                    rm_success, rm_output = _run_docker_cmd(["rm", "-f", container_name])
                    if not rm_success and is_container_exists(container_name):
                        log.error(_LOG_PREFIX,
                            f"Cannot remove stale container '{container_name}'.\n"
                            f"Please run manually: docker rm -f {container_name}\n"
                            f"If that fails, try: docker system prune or restart Docker daemon")
                        return False
    
    # Stop any existing llama.cpp containers (we only run one at a time)
    for container_id in get_running_llamacpp_containers():
        log.warning(_LOG_PREFIX, f"Stopping existing container {container_id[:12]}...")
        _run_docker_cmd(["stop", container_id])
    
    log.msg(_LOG_PREFIX, f"Starting new llama.cpp container for: {model_name}")
    
    # Determine mount path - use llm_models_absolute_path from config
    if models_base_path:
        mount_path = Path(models_base_path)
    else:
        try:
            mount_path = Path(get_llm_models_absolute_path())
            log.debug(_LOG_PREFIX, f"Using llm_models_absolute_path: {mount_path}")
        except ValueError:
            # Fallback to model parent dir if not configured
            mount_path = model_path_path.parent
            log.warning(_LOG_PREFIX, f"llm_models_absolute_path not configured, using model parent: {mount_path}")
    
    # Calculate relative model path inside container
    if models_base_path:
        rel_path = model_path_path.relative_to(models_base_path)
        docker_model_path = f"/models/{rel_path.as_posix()}"
    else:
        docker_model_path = f"/models/{model_name}"
    
    # Calculate mmproj path inside container (if available)
    docker_mmproj_path = None
    if detected_mmproj:
        if models_base_path:
            try:
                mmproj_rel = detected_mmproj.relative_to(models_base_path)
                docker_mmproj_path = f"/models/{mmproj_rel.as_posix()}"
            except ValueError:
                # mmproj not under models_base, use filename only
                docker_mmproj_path = f"/models/{detected_mmproj.name}"
        else:
            docker_mmproj_path = f"/models/{detected_mmproj.name}"
    
    # Convert to Docker-compatible path format
    from .docker_utils import host_path_for_docker
    mount_posix = host_path_for_docker(mount_path)
    
    # Log GPU info
    gpu_info = get_gpu_info()
    if gpu_info["gpu_count"] > 0:
        for gpu in gpu_info["gpus"]:
            log.debug(_LOG_PREFIX, f"GPU {gpu['index']}: {gpu['name']} ({gpu['vram_gb']}GB)")
    
    # Log model size
    model_size = estimate_model_size_gb(str(model_path_path))
    if model_size > 0:
        log.msg(_LOG_PREFIX, f"Model size: ~{model_size}GB")
    
    # Log vision support
    if docker_mmproj_path and detected_mmproj:
        log.msg(_LOG_PREFIX, f"  → Vision support: ENABLED (mmproj: {detected_mmproj.name})")
    else:
        log.msg(_LOG_PREFIX, f"  → Vision support: disabled (no mmproj file found)")
    
    # Build docker command
    docker_image = get_llamacpp_docker_image()
    # Validate image string + resolve bind host (defense-in-depth before subprocess)
    from .docker_utils import validate_docker_image, get_docker_bind_host
    docker_image = validate_docker_image(docker_image)
    bind_host = get_docker_bind_host()
    docker_cmd = [
        "run",
        "-d",  # Detached
        "--name", container_name,
        *get_docker_gpu_args(),  # GPU flags: NVIDIA "--gpus all" or AMD "/dev/kfd, /dev/dri"
        "-v", f"{mount_posix}:/models",
        "-p", f"{bind_host}:{port}:8080",
        docker_image,
        "-m", docker_model_path,
        "--host", "0.0.0.0",
        "--port", "8080",
        "-c", str(ctx_size),
        "-ngl", str(n_gpu_layers),
    ]
    
    # Add mmproj for vision support
    if docker_mmproj_path:
        docker_cmd.extend(["--mmproj", docker_mmproj_path])
    
    log.debug(_LOG_PREFIX, f"Docker command: docker {' '.join(docker_cmd)}")
    
    # Track container name for error diagnosis
    _set_last_container(container_name)
    
    success, output = _run_docker_cmd(docker_cmd, timeout=60)
    
    if success:
        container_id = output.strip()
        log.msg(_LOG_PREFIX, f"✓ Container created: {container_id[:12]}")
        
        # Save container mapping
        save_llamacpp_model_container(model_name, container_id)
        
        if wait_for_ready:
            startup_timeout = get_llamacpp_startup_timeout()
            return wait_for_llamacpp_ready(port, timeout=startup_timeout, container_name=container_name)
        return True
    else:
        log.error(_LOG_PREFIX, f"Failed to start container: {output}")
        _set_failure_reason(f"Docker container creation failed: {output}")
        return False


def stop_llamacpp_container(model_name: Optional[str] = None) -> bool:
    # Stop llama.cpp container.
    #
    # Args:
    #     model_name: Specific model container to stop, or None for all
    #
    # Returns:
    #     bool: True if stopped successfully
    if model_name:
        containers = load_llamacpp_model_containers()
        if model_name in containers:
            container_id = containers[model_name]["container_id"]
            success, _ = _run_docker_cmd(["stop", container_id])
            return success
        return True
    else:
        # Stop all llama.cpp containers
        for container_id in get_running_llamacpp_containers():
            _run_docker_cmd(["stop", container_id])
        return True


# Module-level variable to track last failure reason
_last_failure_reason = None
_last_container_name = None  # Track container for error diagnosis


def get_last_failure_reason() -> Optional[str]:
    # Get the last failure reason for better error messages.
    global _last_failure_reason
    return _last_failure_reason


def _set_failure_reason(reason: str):
    # Set the last failure reason.
    global _last_failure_reason
    _last_failure_reason = reason


def _set_last_container(container_name: str):
    # Track the last container name for error diagnosis.
    global _last_container_name
    _last_container_name = container_name


def wait_for_llamacpp_ready(port: int = LLAMACPP_DEFAULT_PORT, timeout: int = 120, container_name: Optional[str] = None) -> bool:
    # Wait for llama.cpp server to be ready.
    global _last_failure_reason, _last_container_name
    url = f"http://localhost:{port}/health"
    
    # Use provided container name or the last tracked one
    diag_container = container_name or _last_container_name
    
    log.msg(_LOG_PREFIX, f"Waiting for llama.cpp to be ready (timeout: {timeout}s)...")
    
    start_time = time.time()
    poll_interval = 3
    
    while time.time() - start_time < timeout:
        # Check if container is still running
        running_containers = get_running_llamacpp_containers()
        if not running_containers:
            log.warning(_LOG_PREFIX, "llama.cpp container stopped unexpectedly")
            # Use centralized error handler to diagnose
            if diag_container:
                error = docker_error_handler.diagnose_llamacpp_error(diag_container, timeout_occurred=False)
                _set_failure_reason(docker_error_handler.format_error_message(error))
            else:
                _set_failure_reason("Container stopped unexpectedly - check docker logs for errors")
            return False
        
        try:
            response = requests.get(url, timeout=2)
            if response.status_code == 200:
                elapsed = time.time() - start_time
                log.msg(_LOG_PREFIX, f"✓ llama.cpp ready in {elapsed:.1f}s")
                _last_failure_reason = None  # Clear on success
                return True
        except requests.exceptions.RequestException:
            pass
        
        elapsed = int(time.time() - start_time)
        if elapsed % 15 == 0 and elapsed > 0:
            log.msg(_LOG_PREFIX, f"Still waiting for llama.cpp... ({elapsed}s)")
        
        time.sleep(poll_interval)
    
    # Timeout occurred - use centralized error handler to diagnose
    log.warning(_LOG_PREFIX, f"llama.cpp did not become ready within {timeout}s")
    if diag_container:
        error = docker_error_handler.diagnose_llamacpp_error(diag_container, timeout_occurred=True)
        _set_failure_reason(docker_error_handler.format_error_message(error))
        # Log more details for debugging
        if error.raw_log:
            log.debug(_LOG_PREFIX, f"Container log excerpt: {error.raw_log[:300]}")
    else:
        _set_failure_reason(f"Server startup timeout ({timeout}s) - model may be too large or GPU memory insufficient")
    return False


# ==============================================================================
# GENERATION API
# ==============================================================================

def generate_llamacpp(
    smart_lm_instance,
    prompt: str,
    image_paths: Optional[List[str]] = None,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    top_p: float = 0.9,
    top_k: int = 50,
    seed: int = -1,
    repetition_penalty: float = 1.0,
    llm_mode: Optional[str] = None,
    vision_task: Optional[str] = None,
    use_few_shot: bool = True,
    **kwargs,
) -> tuple:
    # Generate text using llama.cpp Docker server.
    #
    # Compatible interface with generate_ollama for SmartLoader v2.
    #
    # Args:
    #     smart_lm_instance: LlamaCppWrapper instance
    #     prompt: Text prompt
    #     image_paths: Optional list of image file paths for vision
    #     max_tokens: Maximum tokens to generate
    #     temperature: Sampling temperature
    #     top_p: Top-p sampling
    #     top_k: Top-k sampling (passed to llama.cpp server)
    #     seed: Random seed (-1 for random)
    #     repetition_penalty: Repetition penalty
    #     llm_mode: LLM mode for text-only generation (for API compatibility)
    #
    # Returns:
    #     tuple: (result_text, data_dict)
    
    config = _get_llamacpp_config()
    port = config.get("port", LLAMACPP_DEFAULT_PORT)
    
    # Build messages in OpenAI format
    content = []
    
    # Parse prompt to extract system instruction and user message for few-shot injection.
    # Eclipse 3.5+ passes system + user separately via system_prompt kwarg; legacy callers
    # may still send a combined "system\n\nuser" string.
    system_prompt = kwargs.get("system_prompt")
    user_message = prompt

    if image_paths and system_prompt is not None:
        user_message = (prompt or "").strip()
    elif image_paths and "\n\n" in prompt:
        parts = prompt.split("\n\n", 1)
        system_prompt = parts[0].strip()
        if len(parts) > 1:
            remaining = parts[1].strip()
            if remaining.startswith("Additional context:"):
                user_message = remaining.replace("Additional context:", "").strip()
            elif remaining:
                user_message = remaining
            else:
                user_message = ""
        else:
            user_message = ""
    
    # Add images if provided (vision support)
    if image_paths:
        for img_path in image_paths:
            try:
                with open(img_path, 'rb') as f:
                    img_data = base64.b64encode(f.read()).decode('utf-8')
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_data}"}
                })
            except Exception as e:
                log.warning(_LOG_PREFIX, f"Failed to load image {img_path}: {e}")
    
    # Build messages with proper structure for few-shot support
    messages = []
    
    # Add system message if extracted
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    
    # Inject text-only few-shot examples to guide output style (no prefixes, uncensored)
    if vision_task and image_paths and use_few_shot:
        from .config_templates import get_vision_few_shot_messages
        few_shot = get_vision_few_shot_messages(vision_task)
        if few_shot:
            messages.extend(few_shot)
    
    # Add user message with images
    if image_paths:
        user_content = user_message if user_message else "Please follow the instructions above for this image."
        content.append({"type": "text", "text": user_content})
        messages.append({"role": "user", "content": content})
    else:
        # Text-only mode
        if llm_mode and llm_mode != "raw":
            # Honor system prompt + LLM few-shot training (parity with Ollama / vLLM / SGLang).
            from .config_templates import get_llm_few_shot_examples
            from .tasks import get_system_prompt

            LLM_FEW_SHOT_EXAMPLES = get_llm_few_shot_examples()
            config = LLM_FEW_SHOT_EXAMPLES.get(llm_mode, {})
            display_name = config.get("display_name") or llm_mode.replace("_", " ").title()

            sys_prompt = get_system_prompt(display_name)
            if not sys_prompt:
                sys_prompt = "You are a helpful assistant."

            instruction_template = kwargs.get("instruction_template", "")
            template_val = instruction_template if instruction_template else config.get("instruction_template", "")
            if isinstance(template_val, list):
                template = "\n".join(str(item) for item in template_val)
            else:
                template = str(template_val or "")
                
            examples_val = config.get("examples", []) if use_few_shot else []
            examples = examples_val if isinstance(examples_val, list) else []

            if isinstance(prompt, list):
                prompt = "\n".join(str(item) for item in prompt)
            elif not isinstance(prompt, str):
                prompt = str(prompt or "")

            # Reset messages — build LLM-style chat from scratch
            messages = [{"role": "system", "content": sys_prompt}]
            if examples:
                messages.extend(examples)

            if llm_mode != "direct_chat" and template:
                req = template.replace("{prompt}", prompt) if "{prompt}" in template else f"{template} {prompt}"
                messages.append({"role": "user", "content": req})
            else:
                messages.append({"role": "user", "content": prompt})

            log.debug(_LOG_PREFIX, f"  LLM mode '{llm_mode}': system + {len(examples)} few-shot + user")
        else:
            # Legacy raw-prompt path
            content.append({"type": "text", "text": prompt})
            messages.append({"role": "user", "content": content})
    
    # llama.cpp server supports OpenAI-compatible endpoint
    url = f"http://localhost:{port}/v1/chat/completions"
    
    payload = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "stream": False,
    }
    
    if top_k and top_k > 0:
        payload["top_k"] = top_k
    
    if repetition_penalty != 1.0:
        payload["repeat_penalty"] = repetition_penalty
    
    if seed >= 0:
        payload["seed"] = seed
    
    min_p = kwargs.get("min_p", 0.0)
    if min_p and min_p > 0.0:
        payload["min_p"] = min_p
    mirostat = kwargs.get("mirostat", 0)
    if mirostat and mirostat > 0:
        payload["mirostat"] = mirostat
        payload["mirostat_eta"] = kwargs.get("mirostat_eta", 0.1)
        payload["mirostat_tau"] = kwargs.get("mirostat_tau", 5.0)
    repeat_last_n = kwargs.get("repeat_last_n", 64)
    if repeat_last_n != 64:
        payload["repeat_last_n"] = repeat_last_n
    stop_sequences = kwargs.get("stop_sequences")
    if stop_sequences:
        payload["stop"] = stop_sequences
    
    request_timeout = get_llamacpp_request_timeout()
    
    try:
        log.debug(_LOG_PREFIX, f"Sending request to llama.cpp: {url}")
        response = requests.post(url, json=payload, timeout=request_timeout)
        
        if response.status_code == 200:
            data = response.json()
            result = data["choices"][0]["message"]["content"]
            # Clean up whitespace
            result = result.strip()
            
            # Fix common UTF-8 encoding artifacts (mojibake)
            encoding_fixes = {
                'âĢĻ': "'",   # Right single quotation mark (U+2019)
                'âĢľ': '"',   # Left double quotation mark (U+201C)
                'âĢĿ': '"',   # Right double quotation mark (U+201D)
                'âĢĺ': "'",   # Left single quotation mark (U+2018)
                'âĢ"': '—',   # Em dash (U+2014)
                'âĢ"': '–',   # En dash (U+2013)
                'âĢ¦': '…',   # Horizontal ellipsis (U+2026)
            }
            for wrong, correct in encoding_fixes.items():
                result = result.replace(wrong, correct)
            
            # Strip thinking tags from "Thinker" models (e.g., Qwen3-VL-Thinking, DeepSeek-R1)
            from .common import strip_thinking_tags, strip_llm_prefixes
            result, _ = strip_thinking_tags(result)
            result = strip_llm_prefixes(result)
            
            log.msg(_LOG_PREFIX, f"✓ Generated {len(result)} chars")
            return result, {"usage": data.get("usage", {})}
        else:
            error_text = response.text
            log.error(_LOG_PREFIX, f"llama.cpp API error: {response.status_code} - {error_text}")
            
            # Check for context overflow error and provide helpful message
            if response.status_code == 400 and "exceed_context_size" in error_text:
                import json as json_module
                try:
                    error_data = json_module.loads(error_text)
                    error_info = error_data.get("error", {})
                    n_prompt = error_info.get("n_prompt_tokens", 0)
                    n_ctx = error_info.get("n_ctx", 0)
                    excess = n_prompt - n_ctx if n_prompt and n_ctx else 0
                    
                    raise RuntimeError(
                        f"Context overflow: {n_prompt:,} tokens > {n_ctx:,} max context\n\n"
                        f"Each image uses ~2,000-3,000 tokens. You have {excess:,} tokens over the limit.\n\n"
                        f"Solutions:\n"
                        f"  • Reduce number of images/frames (try {max(1, (n_prompt - n_ctx) // 2500)} fewer)\n"
                        f"  • Increase context_size widget (if your GPU has enough VRAM)\n"
                        f"  • Use a shorter prompt"
                    )
                except json_module.JSONDecodeError:
                    pass
            
            return f"Error: llama.cpp returned {response.status_code}", {}
            
    except requests.exceptions.Timeout:
        log.error(_LOG_PREFIX, f"llama.cpp request timed out ({request_timeout}s)")
        if _last_container_name:
            error = docker_error_handler.diagnose_llamacpp_error(_last_container_name, timeout_occurred=True)
            log.error(_LOG_PREFIX, docker_error_handler.format_error_message(error))
        return "Error: Request timed out", {}
    except Exception as e:
        log.error(_LOG_PREFIX, f"llama.cpp request failed: {e}")
        if _last_container_name:
            error = docker_error_handler.diagnose_llamacpp_error(_last_container_name, timeout_occurred=False)
            log.error(_LOG_PREFIX, docker_error_handler.format_error_message(error))
        return f"Error: {str(e)}", {}


# ==============================================================================
# HIGH-LEVEL API
# ==============================================================================

def load_gguf_model(
    model_path: str,
    models_base_path: Optional[str] = None,
    mmproj_path: Optional[str] = None,
    n_gpu_layers: int = -1,
    ctx_size: int = 8192,
) -> bool:
    # Load a GGUF model using llama.cpp Docker.
    #
    # Args:
    #     model_path: Path to GGUF file
    #     models_base_path: Base models directory
    #     mmproj_path: Path to mmproj file for vision (auto-detected if None)
    #     n_gpu_layers: GPU layers (-1 = all)
    #     ctx_size: Context size
    #
    # Returns:
    #     bool: True if model loaded successfully
    return start_llamacpp_container(
        model_path=model_path,
        models_base_path=models_base_path,
        mmproj_path=mmproj_path,
        n_gpu_layers=n_gpu_layers,
        ctx_size=ctx_size,
        wait_for_ready=True,
    )


# ==============================================================================
# UNIFIED LOAD API (for SmartLoader v2)
# ==============================================================================

def load_llamacpp(
    model_path: str,
    model_type: str = "llm",
    n_gpu_layers: int = -1,
    ctx_size: int = 8192,
    models_base_path: Optional[str] = None,
    mmproj_path: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    # Load a GGUF model via llama.cpp Docker for SmartLoader v2 integration.
    #
    # Args:
    #     model_path: Path to GGUF file
    #     model_type: Type of model ("llm", "vlm")
    #     n_gpu_layers: GPU layers (-1 = all)
    #     ctx_size: Context size
    #     models_base_path: Base models directory
    #     mmproj_path: Path to mmproj file for vision (auto-detected if None)
    #     **kwargs: Additional configuration options
    #
    # Returns:
    #     Dict with client info: {"client": None, "model_name": str, "base_url": str, "backend": str}
    config = _get_llamacpp_config()
    port = config.get("port", LLAMACPP_DEFAULT_PORT)
    base_url = f"http://localhost:{port}"
    
    # Extract model name from path
    model_name = Path(model_path).stem if model_path else "unknown"
    
    # Load the model (starts container if needed)
    if not load_gguf_model(
        model_path=model_path,
        models_base_path=models_base_path,
        mmproj_path=mmproj_path,
        n_gpu_layers=n_gpu_layers,
        ctx_size=ctx_size,
    ):
        # Get specific failure reason if available
        failure_reason = get_last_failure_reason()
        if failure_reason:
            raise RuntimeError(f"Failed to load GGUF model {model_path} in llama.cpp Docker: {failure_reason}")
        else:
            raise RuntimeError(f"Failed to load GGUF model {model_path} in llama.cpp Docker")
    
    log.msg(_LOG_PREFIX, f"✓ llama.cpp Docker ready: {model_name} @ {base_url}")
    
    return {
        "client": None,  # llama.cpp uses HTTP API, no client object
        "model_name": model_name,
        "base_url": base_url,
        "backend": "llamacpp_docker",
        "model_type": model_type,
    }


# ==============================================================================
# AVAILABILITY CHECK & DOCKER DAEMON MANAGEMENT
# ==============================================================================

LLAMACPP_DOCKER_AVAILABLE = DOCKER_AVAILABLE


def ensure_docker_running() -> bool:
    # Ensure Docker is running for llama.cpp. Start daemon if needed.
    if not LLAMACPP_DOCKER_AVAILABLE:
        return False
    return _ensure_docker_running()


# Check on module load (uses cached daemon status — no extra subprocess call)
if LLAMACPP_DOCKER_AVAILABLE:
    if get_cached_daemon_status():
        log.debug(_LOG_PREFIX, "Docker available for llama.cpp (daemon running)")
    else:
        log.debug(_LOG_PREFIX, "Docker available for llama.cpp (will auto-start when needed)")
