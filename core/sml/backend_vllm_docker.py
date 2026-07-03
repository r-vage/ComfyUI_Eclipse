# vLLM/Docker integration for SML.
#
# This module handles ALL vLLM functionality:
# - Docker configuration loading/saving (docker_config.json in repo root)
# - Container lifecycle management (start, stop, reuse)
# - Model-container tracking for efficient reuse
# - vLLM model loading and generation (works with ANY model: Mistral, Qwen, Llama, etc.)
#
# The vLLM API is model-agnostic - same code works for all models served by vLLM.

import json
import subprocess
import time
import base64
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from .logger import log
from .config_templates import get_llm_models_absolute_path
from .device import get_docker_gpu_args, detect_gpu_vendor
from . import docker_error_handler


_LOG_PREFIX = "vLLM Docker"

# Docker-based vLLM works on all platforms: Windows, Linux, macOS
# For native Linux vLLM (faster, no Docker overhead), use backend_vllm_native.py instead

# ==============================================================================
# DOCKER DAEMON MANAGEMENT (centralized in docker_utils)
# ==============================================================================

from .docker_utils import (
    IS_WINDOWS, IS_LINUX, IS_MACOS,
    is_docker_installed, get_docker_version, get_cached_daemon_status,
    is_docker_daemon_running, start_docker_daemon, ensure_docker_running,
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
_cached_config: Optional[Dict] = None


def _get_default_config() -> Dict:
    # Return default configuration if file doesn't exist
    return {
        "_comment": "Docker Configuration for SML - Supports vLLM, SGLang, Ollama, and llama.cpp backends",
        "backend": "vllm",
        "gpu_memory_utilization": 0.6,
        "dtype": "auto",
        "trust_remote_code": False,
        "docker_bind_host": "127.0.0.1",
        "vllm": {
            "docker_image": "vllm/vllm-openai:latest",
            "url": "http://localhost:8000/v1",
            "port": 8000,
            "timeout": 2,
            "startup_timeout": 600,
            "request_timeout": 300,
            "tensor_parallel_size": 1,
        },
        "ollama": {
            "docker_image": "ollama/ollama",
            "port": 11434,
            "url": "http://localhost:11434/v1",
            "auto_pull": True,
        },
        "llamacpp": {
            "docker_image": "ghcr.io/ggml-org/llama.cpp:server-cuda",
            "port": 8080,
            "url": "http://localhost:8080/v1",
            "n_gpu_layers": -1,
        },
        "paths": {
            "models_base": "",
            "docker_mount": "/models"
        },
        "active_model": {
            "name": "",
            "container_id": "",
            "last_started": ""
        },
        "model_containers": {}
    }


def load_docker_config(force_reload: bool = False) -> Dict:
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
        # Try to copy from .example first (preserves full config with comments)
        _example_path = _CONFIG_PATH.with_suffix('.json.example')
        if _example_path.exists():
            import shutil
            shutil.copy2(_example_path, _CONFIG_PATH)
            log.msg(_LOG_PREFIX, "Created docker_config.json from .example template")
        else:
            log.debug(_LOG_PREFIX, f"Config file not found, creating defaults: {_CONFIG_PATH}")
            _cached_config = _get_default_config()
            save_docker_config(_cached_config)
            return _cached_config
    
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            _cached_config = json.load(f)
        log.debug(_LOG_PREFIX, f"Loaded config from {_CONFIG_PATH}")
        return _cached_config
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error loading config: {e}")
        _cached_config = _get_default_config()
        return _cached_config


def save_docker_config(config: Dict) -> bool:
    # Save configuration to JSON file.
    #
    # Args:
    #     config: Configuration dictionary to save
    #
    # Returns:
    #     bool: True if successful
    global _cached_config
    
    try:
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
        
        _cached_config = config
        return True
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error saving config: {e}")
        return False


# ------------------------------------------------------------------------------
# Config Section Getters
# ------------------------------------------------------------------------------

def get_vllm_config() -> Dict:
    # Get vLLM configuration section
    config = load_docker_config()
    return config.get("vllm", {})


def get_global_docker_options() -> Dict:
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


def get_paths_config() -> Dict:
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

def get_container_for_model(model_name: str) -> Optional[str]:
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


def save_container_for_model(model_name: str, container_id: str) -> bool:
    # Save container ID for a specific model.
    #
    # Args:
    #     model_name: Name of the model
    #     container_id: Docker container ID
    #
    # Returns:
    #     bool: True if successful
    config = load_docker_config()
    
    if "model_containers" not in config:
        config["model_containers"] = {}
    
    now = datetime.now().isoformat()
    config["model_containers"][model_name] = {
        "container_id": container_id,
        "created": now,
        "last_used": now
    }
    
    log.debug(_LOG_PREFIX, f"Saved container {container_id[:12]} for model {model_name}")
    return save_docker_config(config)


def update_container_last_used(model_name: str) -> bool:
    # Update last_used timestamp for a model's container.
    #
    # Args:
    #     model_name: Name of the model
    #
    # Returns:
    #     bool: True if successful
    config = load_docker_config()
    containers = config.get("model_containers", {})
    
    if model_name in containers:
        if isinstance(containers[model_name], dict):
            containers[model_name]["last_used"] = datetime.now().isoformat()
        return save_docker_config(config)
    
    return False


def remove_container_for_model(model_name: str) -> bool:
    # Remove saved container ID for a model (e.g., container was deleted).
    #
    # Args:
    #     model_name: Name of the model
    #
    # Returns:
    #     bool: True if successful
    config = load_docker_config()
    containers = config.get("model_containers", {})
    
    if model_name in containers:
        del containers[model_name]
        log.debug(_LOG_PREFIX, f"Removed container entry for model {model_name}")
        return save_docker_config(config)
    
    return True


def cleanup_stale_containers(max_age_hours: int = 24) -> int:
    # Remove container entries older than max_age_hours.
    config = load_docker_config()
    containers = config.get("model_containers", {})
    now = datetime.now()
    max_age_seconds = max_age_hours * 3600
    
    removed = 0
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
            
    if removed > 0:
        save_docker_config(config)
        log.debug(_LOG_PREFIX, f"Cleaned up {removed} stale container entries")
    return removed


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
    # Get Docker image name with automatic GPU vendor detection.
    #
    # Returns ROCm-optimized image (rocm/vllm:latest) for AMD GPUs,
    # or the configured NVIDIA image (vllm/vllm-openai:latest) otherwise.
    from .device import detect_gpu_vendor, get_docker_image_for_vendor
    
    base_image = get_vllm_config().get("docker_image", "vllm/vllm-openai:latest")
    vendor = detect_gpu_vendor()
    
    if vendor == "amd":
        rocm_image = get_docker_image_for_vendor(base_image, vendor)
        if rocm_image != base_image:
            log.debug(_LOG_PREFIX, f"AMD GPU detected - using ROCm image: {rocm_image}")
        return rocm_image
    
    return base_image


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
    config = load_docker_config()
    if "paths" not in config:
        config["paths"] = {}
    config["paths"]["models_base"] = path
    return save_docker_config(config)


# ==============================================================================
# DOCKER CONTAINER MANAGEMENT
# ==============================================================================

def is_docker_available() -> bool:
    # Check if Docker is available.
    # Uses cached startup check for fast fail.
    return DOCKER_AVAILABLE


def is_vllm_container_running() -> bool:
    # Check if any vLLM container is currently running
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "ancestor=vllm/vllm-openai", "--format", "{{.ID}}"],
            capture_output=True,
            timeout=5,
            text=True,
            encoding='utf-8',
            errors='replace',  # Handle non-UTF8 bytes gracefully on Windows
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def get_running_vllm_containers() -> List[str]:
    # Get list of running vLLM container IDs
    if not DOCKER_AVAILABLE:
        return []
    
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "ancestor=vllm/vllm-openai", "--format", "{{.ID}}"],
            capture_output=True,
            timeout=5,
            text=True,
            encoding='utf-8',
            errors='replace',  # Handle non-UTF8 bytes gracefully on Windows
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        return [cid.strip() for cid in result.stdout.strip().split('\n') if cid.strip()]
    except Exception:
        return []


def stop_vllm_container(container_id: Optional[str] = None) -> bool:
    # Stop vLLM Docker container.
    #
    # Args:
    #     container_id: Specific container ID to stop, or None to stop all vLLM containers
    #
    # Returns:
    #     bool: True if successful
    try:
        if container_id:
            containers = [container_id]
        else:
            containers = get_running_vllm_containers()
        
        if not containers:
            return True
        
        for cid in containers:
            log.debug(_LOG_PREFIX, f"Stopping container {cid[:12]}...")
            subprocess.run(
                ["docker", "stop", cid],
                capture_output=True,
                timeout=30,
                text=True,
                encoding='utf-8',
                errors='replace',  # Handle non-UTF8 bytes gracefully on Windows
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
        
        return True
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error stopping container: {e}")
        return False


def is_container_running(container_id: str) -> bool:
    # Check if a specific container is running.
    #
    # Args:
    #     container_id: Docker container ID
    #
    # Returns:
    #     bool: True if container is running
    try:
        result = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"id={container_id}"],
            capture_output=True,
            timeout=5,
            text=True,
            encoding='utf-8',
            errors='replace',  # Handle non-UTF8 bytes gracefully on Windows
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def is_container_exists(container_id: str) -> bool:
    # Check if a container exists (running or stopped).
    #
    # Args:
    #     container_id: Docker container ID
    #
    # Returns:
    #     bool: True if container exists
    try:
        result = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"id={container_id}"],
            capture_output=True,
            timeout=5,
            text=True,
            encoding='utf-8',
            errors='replace',  # Handle non-UTF8 bytes gracefully on Windows
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


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
            encoding='utf-8',
            errors='replace',  # Handle non-UTF8 bytes gracefully on Windows
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        return result.returncode == 0
    except Exception as e:
        log.error(_LOG_PREFIX, f"Failed to start container: {e}")
        return False


def start_vllm_container(
    model_path: str,
    models_base_path: Optional[str] = None,
    docker_image: Optional[str] = None,
    port: Optional[int] = None,
    max_model_len: Optional[int] = None,
    wait_for_ready: bool = True,
    quantization: Optional[str] = None,
    gpu_memory_utilization: Optional[float] = None,
    trust_remote_code: Optional[bool] = None,
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
        docker_image = docker_cfg.get("docker_image", "vllm/vllm-openai:latest")
    if port is None:
        port = docker_cfg.get("port", 8000)
    if max_model_len is None:
        max_model_len = 8192  # Default, normally overridden by context_size from template
    
    try:
        model_name = Path(model_path).name
        model_dir = Path(model_path)
        
        # Check if we have a saved container ID for this model
        saved_container_id = get_container_for_model(model_name)
        
        if saved_container_id:
            log.msg(_LOG_PREFIX, f"Found saved container for {model_name}: {saved_container_id[:12]}")
            
            # Verify model files still exist before reusing container
            # For Mistral3 models, the container was created with consolidated.safetensors
            # If that file was deleted (e.g., bad conversion cleaned up), we can't reuse the container
            model_files_valid = True
            has_consolidated = (model_dir / "consolidated.safetensors").exists()
            
            # Check if this was a Mistral3 model that needs consolidated.safetensors
            # Use shared detection function from model_types
            try:
                from .model_types import is_mistral3_vision_model
                is_mistral3 = is_mistral3_vision_model(str(model_dir))
            except ImportError:
                # Fallback if import fails
                is_mistral3 = "ministral" in model_name.lower() or "pixtral" in model_name.lower()
            
            # If Mistral3 model but no consolidated.safetensors, container config is invalid
            if is_mistral3 and not has_consolidated:
                log.warning(_LOG_PREFIX, "⚠ Mistral3 model missing consolidated.safetensors - cannot reuse container")
                log.warning(_LOG_PREFIX, "  Will attempt auto-conversion when creating new container...")
                model_files_valid = False
                # Remove the invalid container mapping
                remove_container_for_model(model_name)
            
            # Check if container exists AND model files are valid
            if model_files_valid and is_container_exists(saved_container_id):
                # Track container for error diagnosis
                _set_last_vllm_container(saved_container_id)
                
                # Check if image was updated — if so, discard saved container
                from .docker_utils import is_container_image_stale
                vllm_image = docker_image if docker_image else docker_cfg.get("docker_image", "vllm/vllm-openai:latest")
                if is_container_image_stale(saved_container_id, vllm_image):
                    log.msg(_LOG_PREFIX, "Removing stale container to use updated image...")
                    stop_vllm_container()
                    remove_container_for_model(model_name)
                elif is_container_running(saved_container_id):
                    log.msg(_LOG_PREFIX, "✓ Container already running")
                    update_container_last_used(model_name)
                    if wait_for_ready:
                        return wait_for_vllm_ready(timeout=30, container_id=saved_container_id)
                    return True
                else:
                    # Container exists but stopped - restart it
                    log.msg(_LOG_PREFIX, "Container exists but stopped, restarting...")
                    if start_existing_container(saved_container_id):
                        log.msg(_LOG_PREFIX, "✓ Container restarted successfully")
                        update_container_last_used(model_name)
                        if wait_for_ready:
                            startup_timeout = get_vllm_startup_timeout()
                            return wait_for_vllm_ready(timeout=startup_timeout, container_id=saved_container_id)
                        return True
                    else:
                        log.warning(_LOG_PREFIX, "⚠ Failed to restart container, creating new one...")
            elif model_files_valid:
                log.warning(_LOG_PREFIX, "⚠ Saved container no longer exists, creating new one...")
        
        # Stop any existing vLLM containers first
        if is_vllm_container_running():
            log.warning(_LOG_PREFIX, "Stopping other vLLM containers...")
            stop_vllm_container()
            time.sleep(2)
        
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
        tensor_parallel_size = docker_cfg.get("tensor_parallel_size", 1)
        
        # ==============================================================================
        # GPU VRAM CHECK - Detect if model will fit before attempting to load
        # ==============================================================================
        fit_check = check_model_fits(model_path, gpu_memory_utilization, tensor_parallel_size)
        gpu_info = fit_check["gpu_info"]
        
        if gpu_info["gpu_count"] > 0:
            # Log GPU info
            for gpu in gpu_info["gpus"]:
                log.debug(_LOG_PREFIX, f"GPU {gpu['index']}: {gpu['name']} ({gpu['vram_gb']:.1f}GB)")
            
            if fit_check["model_size_gb"] > 0:
                log.msg(_LOG_PREFIX, f"Model size: ~{fit_check['model_size_gb']:.1f}GB (needs ~{fit_check['estimated_required_gb']:.1f}GB with overhead)")
            
            if not fit_check["fits"]:
                # Model doesn't fit with current settings
                log.warning(_LOG_PREFIX, f"⚠ {fit_check['message']}")
                
                # Check if we can use tensor parallelism to make it fit
                if fit_check["suggested_tensor_parallel"] > 1 and gpu_info["gpu_count"] >= fit_check["suggested_tensor_parallel"]:
                    tensor_parallel_size = fit_check["suggested_tensor_parallel"]
                    log.msg(_LOG_PREFIX, f"Auto-enabling tensor parallelism: using {tensor_parallel_size} GPUs")
                    # Re-check with new tensor parallel size
                    fit_check = check_model_fits(model_path, gpu_memory_utilization, tensor_parallel_size)
                
                # If still doesn't fit, we should warn but still try (user might have other memory free)
                if not fit_check["fits"]:
                    log.error(_LOG_PREFIX, f"⚠ Model may not fit in VRAM!")
                    log.error(_LOG_PREFIX, f"  Model needs ~{fit_check['estimated_required_gb']:.1f}GB, available: {fit_check['available_vram_gb']:.1f}GB")
                    log.error(_LOG_PREFIX, f"  Consider using a GGUF quantized version or smaller model")
                    # Don't return False - let vLLM try and fail with a clearer error
            else:
                log.debug(_LOG_PREFIX, f"✓ {fit_check['message']}")
        
        # Get quantization setting - parameter overrides (no config fallback, comes from template)
        if quantization is None:
            quantization = None  # No config fallback — quantization comes from template/node
        
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
        is_gguf_model = model_name.lower().endswith('.gguf')

        # Validate image string + resolve bind host (defense-in-depth before passing to subprocess)
        from .docker_utils import validate_docker_image, get_docker_bind_host
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
            base_name_parts = gguf_filename.replace('.gguf', '').split('-')
            # Remove common quantization suffixes (Q4_K_M, Q5_K_S, IQ4_XS, BF16, etc.)
            while base_name_parts and base_name_parts[-1].startswith(('Q', 'F', 'IQ', 'BF')):
                base_name_parts.pop()
            if base_name_parts:
                base_name = '-'.join(base_name_parts)
                # Common HuggingFace repo patterns
                if 'ministral' in base_name.lower() or 'mistral' in base_name.lower():
                    tokenizer_hint = f"mistralai/{base_name}"
                elif 'qwen' in base_name.lower():
                    tokenizer_hint = f"Qwen/{base_name}"
                elif 'llama' in base_name.lower():
                    tokenizer_hint = f"meta-llama/{base_name}"
                elif 'phi' in base_name.lower():
                    tokenizer_hint = f"microsoft/{base_name}"
                elif 'gemma' in base_name.lower():
                    tokenizer_hint = f"google/{base_name}"
            
            docker_cmd = [
                "docker", "run",
                *get_docker_gpu_args(),  # GPU flags: NVIDIA "--gpus all" or AMD "/dev/kfd, /dev/dri"
                "-v", f"{gguf_parent_posix}:/models",
                "-p", f"{bind_host}:{port}:8000",
                "--ipc=host",
                "-d",  # Detached mode
                docker_image,
                "--model", docker_model_path,
                "--dtype", dtype,
                "--max-model-len", str(max_model_len),
                "--gpu-memory-utilization", str(gpu_memory_utilization),
            ]
            
            # Add tensor parallelism for multi-GPU
            if tensor_parallel_size > 1:
                docker_cmd.extend(["--tensor-parallel-size", str(tensor_parallel_size)])
                log.msg(_LOG_PREFIX, f"  Using tensor parallelism: {tensor_parallel_size} GPUs")
            
            # Add tokenizer from HuggingFace (vLLM will download it)
            if tokenizer_hint:
                docker_cmd.extend(["--tokenizer", tokenizer_hint])
                log.msg(_LOG_PREFIX, f"  Tokenizer: {tokenizer_hint} (will download from HuggingFace)")
            else:
                log.warning(_LOG_PREFIX, "  ⚠ Could not infer tokenizer - vLLM will convert from GGUF metadata (slow)")
        else:
            # Standard model folder
            from .docker_utils import host_path_for_docker
            docker_cmd = [
                "docker", "run",
                *get_docker_gpu_args(),  # GPU flags: NVIDIA "--gpus all" or AMD "/dev/kfd, /dev/dri"
                "-v", f"{host_path_for_docker(models_base)}:/models",
                "-p", f"{bind_host}:{port}:8000",
                "--ipc=host",
                "-d",  # Detached mode
                docker_image,
                "--model", f"/models/{model_name}",
                "--dtype", dtype,
                "--max-model-len", str(max_model_len),
                "--gpu-memory-utilization", str(gpu_memory_utilization),
            ]
            
            # Add tensor parallelism for multi-GPU
            if tensor_parallel_size > 1:
                docker_cmd.extend(["--tensor-parallel-size", str(tensor_parallel_size)])
                log.msg(_LOG_PREFIX, f"  Using tensor parallelism: {tensor_parallel_size} GPUs")
        
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
            has_consolidated = (model_dir / "consolidated.safetensors").exists()
            has_hf_format = (
                (model_dir / "model.safetensors").exists() or
                any(model_dir.glob("model-*.safetensors"))  # Sharded HF format
            )
            
            # Use shared detection function from model_types
            try:
                from .model_types import is_mistral3_vision_model
                is_mistral3_model = is_mistral3_vision_model(str(model_dir))
            except ImportError:
                # Fallback if import fails
                model_name_lower = model_name.lower()
                is_mistral3_model = "ministral" in model_name_lower or "pixtral" in model_name_lower
        
        # Only use mistral load format if we have consolidated.safetensors (Mistral-native format)
        # HuggingFace format Mistral3 models need conversion - vLLM can't load them directly
        # (vLLM expects Mistral-native weight keys like 'layers.*' not HF keys like 'language_model.model.*')
        if is_mistral3_model:
            if has_consolidated:
                log.msg(_LOG_PREFIX, "  Detected Mistral3/Pixtral with Mistral-native format - using mistral load format")
                docker_cmd.extend(["--load-format", "mistral"])
                # Enforce eager mode to avoid CUDA graph issues with Pixtral
                docker_cmd.append("--enforce-eager")
            elif has_hf_format and not has_consolidated:
                # HuggingFace format Mistral3 - need to convert to Mistral-native format
                # vLLM's default loader can't handle HF-format Mistral3 models (weight key mismatch)
                log.msg(_LOG_PREFIX, "  Detected Mistral3/Pixtral with HuggingFace format - attempting auto-conversion...")
                try:
                    from .mistral_weight_converter import convert_weights_to_mistral
                    
                    success, message = convert_weights_to_mistral(model_path)
                    if success:
                        log.msg(_LOG_PREFIX, f"  ✓ {message}")
                        log.msg(_LOG_PREFIX, "  Using mistral load format with converted weights")
                        docker_cmd.extend(["--load-format", "mistral"])
                        docker_cmd.append("--enforce-eager")
                    else:
                        log.error(_LOG_PREFIX, f"  Auto-conversion failed: {message}")
                        log.error(_LOG_PREFIX, "  This HuggingFace-format Mistral3/Pixtral model cannot be used with vLLM Docker.")
                        log.error(_LOG_PREFIX, "  Solutions:")
                        log.error(_LOG_PREFIX, "    1. Use 'Transformers' backend instead (supports HF format directly)")
                        log.error(_LOG_PREFIX, "    2. Download the original Mistral-native model (with consolidated.safetensors)")
                        log.error(_LOG_PREFIX, "    3. For FP8 models: use 'vLLM' (local) or 'Transformers' backend")
                        return False
                except ImportError as e:
                    log.error(_LOG_PREFIX, f"⚠️  Weight converter not available: {e}")
                    log.error(_LOG_PREFIX, "    This Mistral3/Pixtral model uses HuggingFace weight format.")
                    log.error(_LOG_PREFIX, "    Solutions:")
                    log.error(_LOG_PREFIX, "      1. Use 'Transformers' backend instead (supports HF format)")
                    log.error(_LOG_PREFIX, "      2. Download the original Mistral-native model (with consolidated.safetensors)")
                    return False
        
        # Add quantization if specified (not for GGUF - they're already quantized)
        # Valid options: awq, gptq, squeezellm, bitsandbytes, fp8
        # NOTE: Mistral3/Pixtral vision models do NOT support BitsAndBytes quantization in vLLM
        if not is_gguf_model and quantization and quantization.lower() not in ["none", "auto", "bf16", "fp16"]:
            if is_mistral3_model and quantization.lower() == "bitsandbytes":
                log.warning(_LOG_PREFIX, "⚠ Mistral3/Pixtral models don't support BitsAndBytes quantization in vLLM")
                log.warning(_LOG_PREFIX, "  Running without quantization (requires more VRAM)")
            else:
                docker_cmd.extend(["--quantization", quantization.lower()])
        
        # Start container
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            timeout=30,
            text=True,
            encoding='utf-8',
            errors='replace',  # Handle non-UTF8 bytes gracefully on Windows
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        
        if result.returncode != 0:
            log.error(_LOG_PREFIX, f"Failed to start container: {result.stderr}")
            return False
        
        container_id = result.stdout.strip()
        log.msg(_LOG_PREFIX, f"✓ Container created: {container_id[:12]}")
        
        # Track container for error diagnosis
        _set_last_vllm_container(container_id)
        
        # Save container ID for future reuse
        model_name = Path(model_path).name
        if save_container_for_model(model_name, container_id):
            log.msg(_LOG_PREFIX, f"✓ Saved container ID for {model_name}")
        
        if wait_for_ready:
            startup_timeout = get_vllm_startup_timeout()
            return wait_for_vllm_ready(timeout=startup_timeout, container_id=container_id)
        
        return True
        
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error starting container: {e}")
        return False


# Module-level variable to track last container ID for error diagnosis
_last_vllm_container_id = None


def _set_last_vllm_container(container_id: str):
    global _last_vllm_container_id
    _last_vllm_container_id = container_id


def wait_for_vllm_ready(timeout: int = 600, container_id: Optional[str] = None) -> bool:
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
    
    log.msg(_LOG_PREFIX, f"Waiting for vLLM to be ready (timeout: {timeout}s)...")
    
    start_time = time.time()
    poll_interval = 5
    
    while time.time() - start_time < timeout:
        # Check if container is still running
        if not is_vllm_container_running():
            log.warning(_LOG_PREFIX, "vLLM container stopped unexpectedly")
            # Use centralized error handler to diagnose
            if diag_container:
                error = docker_error_handler.diagnose_vllm_error(diag_container, timeout_occurred=False)
                log.error(_LOG_PREFIX, docker_error_handler.format_error_message(error))
            return False
        
        try:
            response = requests.get("http://localhost:8000/health", timeout=2)
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
        error = docker_error_handler.diagnose_vllm_error(diag_container, timeout_occurred=True)
        log.error(_LOG_PREFIX, docker_error_handler.format_error_message(error))
        if error.raw_log:
            log.debug(_LOG_PREFIX, f"Container log excerpt: {error.raw_log[:300]}")
    return False


def auto_start_vllm_for_model(
    model_path: str,
    quantization: Optional[str] = None,
    context_size: Optional[int] = None,
    trust_remote_code: bool = False,
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


def is_vllm_serving_model(model_path: str) -> Optional[str]:
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
        from openai import OpenAI #type: ignore
        
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


def load_vllm(model_path: str, quantization: Optional[str] = None, context_size: Optional[int] = None, trust_remote_code: bool = False) -> Optional[Dict[str, Any]]:
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
        from openai import OpenAI #type: ignore
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
    
    # Check if vLLM is serving the correct model
    matched_model = is_vllm_serving_model(model_path)
    model_name = Path(model_path).name
    
    if not matched_model:
        # Model not found — start container with the requested model
        log.msg(_LOG_PREFIX, f"Starting container for {model_name}...")
        try:
            if auto_start_vllm_for_model(model_path, quantization=quantization, context_size=context_size, trust_remote_code=trust_remote_code):
                matched_model = is_vllm_serving_model(model_path)
                if matched_model:
                    log.debug(_LOG_PREFIX, "Container started successfully!")
                else:
                    log.warning(_LOG_PREFIX, "⚠ Container started but model not detected")
                    return None
            else:
                log.warning(_LOG_PREFIX, "⚠ Failed to start container")
                return None
        except Exception as e:
            log.warning(_LOG_PREFIX, f"⚠ Container start error: {e}")
            return None
    
    # Model found! Use vLLM
    request_timeout = get_vllm_request_timeout()
    client = OpenAI(base_url=url, api_key="not-needed", timeout=request_timeout)
    
    # Update last used timestamp
    update_container_last_used(model_name)
    
    # Store vLLM client info
    # Check if this is a GGUF model
    is_gguf_model = model_name.lower().endswith('.gguf')
    
    log.debug(_LOG_PREFIX, "Using vLLM (Docker) backend")
    log.debug(_LOG_PREFIX, f"Model: {matched_model}")
    if is_gguf_model:
        log.debug(_LOG_PREFIX, "GGUF format (experimental vLLM support)")
    log.debug(_LOG_PREFIX, "Optimized inference enabled")
    
    return {"mode": "vllm", "client": client, "model_name": matched_model}


def generate_vllm(
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
    **kwargs
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
    log.debug(_LOG_PREFIX, f"generate_vllm: model={getattr(smart_lm_instance, 'vllm_model_name', 'unknown')}")
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
            log.debug(_LOG_PREFIX, f"  Parsed - System: {system_prompt[:50] if system_prompt else 'None'}..., User: {user_message[:50] if user_message else 'empty'}...")
        else:
            # No separator - use entire prompt as user message (Custom task)
            user_message = prompt
        
        # Build image data
        image_data = []
        for img_path in image_paths:
            with open(img_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode('utf-8')
                image_data.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img_b64}"
                    }
                })
        
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
        user_content = user_message if user_message else "Please follow the instructions above for this image."
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
            config = {"display_name": display_name, "instruction_template": "", "examples": []}
            log.debug(_LOG_PREFIX, f"No few-shot config for '{llm_mode}', using task system prompt for '{display_name}'")
        
        # Get system_prompt from prompt_defaults (authoritative source)
        system_prompt = get_system_prompt(display_name)
        if not system_prompt:
            system_prompt = "You are a helpful assistant."
        
        examples_val = config.get("examples", []) if use_few_shot else []
        examples = examples_val if isinstance(examples_val, list) else []
        template_val = instruction_template if instruction_template else config.get("instruction_template", "")
        if isinstance(template_val, list):
            template = "\n".join(str(item) for item in template_val)
        else:
            template = str(template_val or "")
            
        if isinstance(prompt, list):
            prompt = "\n".join(str(item) for item in prompt)
        elif not isinstance(prompt, str):
            prompt = str(prompt or "")
        
        log.debug(_LOG_PREFIX, f"  LLM mode: display_name={display_name}, {len(examples)} examples (use_few_shot={use_few_shot})")
        
        # Build messages: system + (optional examples) + user request
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add few-shot examples only if available for this task
        if examples:
            messages.extend(examples)
        
        # Build user request
        if llm_mode != "direct_chat" and template:
            req = template.replace("{prompt}", prompt) if "{prompt}" in template else f"{template} {prompt}"
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
        
        # Calculate tokens/sec if we have usage info
        usage_info = ""
        if hasattr(response, 'usage') and response.usage:
            tokens = response.usage.completion_tokens
            if tokens and gen_elapsed > 0:
                tok_per_sec = tokens / gen_elapsed
                usage_info = f" ({tokens} tokens, {tok_per_sec:.1f} tok/s)"
        
        log.msg(_LOG_PREFIX, f"✓ Generation completed in {gen_elapsed:.1f}s{usage_info}")
        
        # Fix common UTF-8 encoding artifacts (mojibake)
        # These occur when UTF-8 smart quotes are decoded as Latin-1/Windows-1252
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
        cleaned_result, raw_result = strip_thinking_tags(result)
        cleaned_result = strip_llm_prefixes(cleaned_result)
        
        # For LLM mode, return tuple (cleaned, raw) for compatibility
        if llm_mode:
            return cleaned_result, raw_result
        
        return cleaned_result
        
    except Exception as e:
        error_msg = str(e)
        
        # Provide helpful error messages for common issues
        if "is not a multimodal model" in error_msg:
            model_name_short = Path(model_name).name if "/" in model_name else model_name
            log.error(_LOG_PREFIX, f"Model '{model_name_short}' is text-only, not a vision model")
            log.error(_LOG_PREFIX, "Solutions:")
            log.error(_LOG_PREFIX, "  1. Use 'LLM (Text-Only)' as model_family (no image input)")
            log.error(_LOG_PREFIX, "  2. Or use a multimodal model like Ministral-3B-Instruct or Mistral-Small-3.1")
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
        
        log.error(_LOG_PREFIX, f"Generation error: {e}")
        if _last_vllm_container_id:
            error = docker_error_handler.diagnose_vllm_error(_last_vllm_container_id, timeout_occurred=False)
            log.error(_LOG_PREFIX, docker_error_handler.format_error_message(error))
        raise


# ==============================================================================
# MODULE EXPORTS
# ==============================================================================

__all__ = [
    # Docker availability (re-exported from docker_utils)
    'IS_WINDOWS',
    'IS_LINUX',
    'IS_MACOS',
    'DOCKER_AVAILABLE',
    'DOCKER_VERSION',
    'is_docker_available',
    'is_docker_daemon_running',
    'is_vllm_docker_available',
    'start_docker_daemon',
    'ensure_docker_running',
    
    # Configuration
    'load_docker_config',
    'save_docker_config',
    'get_vllm_config',
    'get_paths_config',
    'get_vllm_url',
    'get_docker_image',
    'get_models_base_path',
    'set_models_base_path',
    'get_global_docker_options',
    
    # Container tracking
    'get_container_for_model',
    'save_container_for_model',
    'update_container_last_used',
    'cleanup_stale_containers',
    
    # Container management
    'is_vllm_container_running',
    'get_running_vllm_containers',
    'is_container_exists',
    'is_container_running',
    'start_existing_container',
    'stop_vllm_container',
    'start_vllm_container',
    'auto_start_vllm_for_model',
    
    # vLLM API
    'is_vllm_serving_model',
    'wait_for_vllm_ready',
    'load_vllm',
    'generate_vllm',
]
