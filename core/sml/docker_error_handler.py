# Centralized Docker error handling for SML Docker backends.
#
# This module provides:
# - Container log retrieval and parsing
# - Error pattern detection with specific messages
# - Actionable suggestions for common errors
# - Unified error handling across all Docker backends (llama.cpp, vLLM, SGLang, Ollama)

import subprocess
import re
from typing import Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum
from .logger import log


_LOG_PREFIX = "Docker Error Handler"


# ==============================================================================
# ERROR TYPES
# ==============================================================================

class DockerErrorType(Enum):
    # Categories of Docker/model loading errors.
    UNKNOWN = "unknown"
    TIMEOUT = "timeout"
    OUT_OF_MEMORY = "out_of_memory"
    CUDA_ERROR = "cuda_error"
    MODEL_NOT_FOUND = "model_not_found"
    MODEL_CORRUPT = "model_corrupt"
    UNSUPPORTED_MODEL = "unsupported_model"
    CONTAINER_CRASH = "container_crash"
    NETWORK_ERROR = "network_error"
    PERMISSION_ERROR = "permission_error"
    DOCKER_ERROR = "docker_error"
    STILL_LOADING = "still_loading"


@dataclass
class DockerError:
    # Structured error information from Docker container.
    error_type: DockerErrorType
    message: str
    suggestion: str
    raw_log: str = ""
    is_recoverable: bool = False


# ==============================================================================
# ERROR PATTERNS
# ==============================================================================

# Patterns to detect specific errors from container logs
# Each pattern: (regex_pattern, error_type, message, suggestion, is_recoverable)
ERROR_PATTERNS: List[Tuple[str, DockerErrorType, str, str, bool]] = [
    # Out of Memory errors
    (
        r"(CUDA out of memory|OutOfMemoryError|OOM|torch\.cuda\.OutOfMemoryError|"
        r"RuntimeError: CUDA error: out of memory|"
        r"not enough memory|insufficient memory|"
        r"Failed to allocate|memory allocation failed)",
        DockerErrorType.OUT_OF_MEMORY,
        "GPU out of memory",
        "Try: 1) Use a smaller/quantized model, 2) Reduce context size, 3) Close other GPU applications, 4) Use CPU offloading",
        False
    ),
    # CUDA errors
    (
        r"(CUDA error|cudaError|cuda runtime error|"
        r"CUBLAS_STATUS|CUDNN_STATUS|"
        r"NCCL error|cuBLAS error)",
        DockerErrorType.CUDA_ERROR,
        "CUDA/GPU error",
        "Try: 1) Restart Docker, 2) Update GPU drivers, 3) Check GPU compatibility",
        False
    ),
    # Model not found
    (
        r"(model.*not found|FileNotFoundError|No such file|"
        r"Failed to load model|cannot find model|"
        r"model path.*does not exist)",
        DockerErrorType.MODEL_NOT_FOUND,
        "Model file not found",
        "Check: 1) Model path is correct, 2) Model is fully downloaded, 3) Volume mount is correct",
        False
    ),
    # Model corrupt/invalid
    (
        r"(Invalid model|corrupt|failed to read|"
        r"bad header|invalid header|"
        r"tensor.*error|weight.*error|"
        r"safetensors.*error)",
        DockerErrorType.MODEL_CORRUPT,
        "Model file appears corrupt or invalid",
        "Try: 1) Re-download the model, 2) Check file integrity, 3) Ensure download completed",
        False
    ),
    # Unsupported model architecture
    (
        r"(unsupported.*architecture|unknown.*model|"
        r"not supported|architecture.*not.*implemented|"
        r"unknown model type)",
        DockerErrorType.UNSUPPORTED_MODEL,
        "Model architecture not supported",
        "This model architecture may not be supported by this backend. Try a different backend or model.",
        False
    ),
    # Permission errors
    (
        r"(Permission denied|Access denied|"
        r"EACCES|cannot access)",
        DockerErrorType.PERMISSION_ERROR,
        "Permission denied",
        "Check: 1) File permissions, 2) Docker has access to the volume, 3) Run as administrator if needed",
        False
    ),
    # Network errors
    (
        r"(Connection refused|Network.*unreachable|"
        r"Failed to connect|Connection timed out|"
        r"DNS.*failed|name resolution)",
        DockerErrorType.NETWORK_ERROR,
        "Network connection error",
        "Check: 1) Internet connection, 2) Firewall settings, 3) Docker network configuration",
        True
    ),
    # Still loading indicators (not an error - just slow)
    (
        r"(Loading model|Initializing|"
        r"Loading weights|loading.*layers|"
        r"Building.*graph|Compiling|"
        r"preparing model|warming up|"
        r"progress|%.*complete)",
        DockerErrorType.STILL_LOADING,
        "Model is still loading",
        "The model is loading - this can take several minutes for large models. Consider increasing timeout.",
        True
    ),
    # Container crash/exit
    (
        r"(Segmentation fault|SIGSEGV|SIGKILL|"
        r"killed|terminated|aborted|"
        r"container.*exited|exit code [1-9])",
        DockerErrorType.CONTAINER_CRASH,
        "Container crashed unexpectedly",
        "The container crashed. Check: 1) Available memory (RAM + VRAM), 2) Model compatibility, 3) Docker logs for details",
        False
    ),
]


# ==============================================================================
# CORE FUNCTIONS
# ==============================================================================

def get_container_logs(container_name_or_id: str, tail: int = 100, timeout: int = 10) -> Optional[str]:
    # Get logs from a Docker container.
    #
    # Args:
    #     container_name_or_id: Container name or ID
    #     tail: Number of lines to retrieve from end
    #     timeout: Command timeout in seconds
    #
    # Returns:
    #     Log content as string, or None if failed
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", str(tail), container_name_or_id],
            capture_output=True,
            timeout=timeout,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        # Docker logs go to stderr for real-time output
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        log.warning(_LOG_PREFIX, f"Timeout getting logs for {container_name_or_id}")
        return None
    except Exception as e:
        log.debug(_LOG_PREFIX, f"Failed to get container logs: {e}")
        return None


def is_container_running(container_name_or_id: str, timeout: int = 5) -> bool:
    # Check if a container is currently running.
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_name_or_id],
            capture_output=True,
            timeout=timeout,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        return result.stdout.strip().lower() == "true"
    except Exception:
        return False


def get_container_exit_code(container_name_or_id: str, timeout: int = 5) -> Optional[int]:
    # Get the exit code of a stopped container.
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.ExitCode}}", container_name_or_id],
            capture_output=True,
            timeout=timeout,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        return int(result.stdout.strip())
    except Exception:
        return None


def analyze_error(logs: str, container_running: bool = True, exit_code: Optional[int] = None) -> DockerError:
    # Analyze container logs to determine the error type and provide suggestions.
    #
    # Args:
    #     logs: Container log content
    #     container_running: Whether container is still running
    #     exit_code: Container exit code if stopped
    #
    # Returns:
    #     DockerError with analysis results
    if not logs:
        if not container_running:
            return DockerError(
                error_type=DockerErrorType.CONTAINER_CRASH,
                message="Container stopped with no logs available",
                suggestion="Container may have crashed immediately. Check Docker Desktop or 'docker logs' for details.",
                is_recoverable=False
            )
        return DockerError(
            error_type=DockerErrorType.UNKNOWN,
            message="No logs available",
            suggestion="Unable to determine error - check Docker Desktop for container status",
            is_recoverable=False
        )
    
    logs_lower = logs.lower()
    
    # Check each error pattern
    for pattern, error_type, message, suggestion, is_recoverable in ERROR_PATTERNS:
        if re.search(pattern, logs, re.IGNORECASE):
            # Special handling for "still loading" - it's not really an error
            if error_type == DockerErrorType.STILL_LOADING and container_running:
                return DockerError(
                    error_type=error_type,
                    message=message,
                    suggestion=suggestion,
                    raw_log=_extract_relevant_log(logs, pattern),
                    is_recoverable=True
                )
            elif error_type != DockerErrorType.STILL_LOADING:
                return DockerError(
                    error_type=error_type,
                    message=message,
                    suggestion=suggestion,
                    raw_log=_extract_relevant_log(logs, pattern),
                    is_recoverable=is_recoverable
                )
    
    # Container stopped but no recognized error
    if not container_running:
        exit_msg = f" (exit code: {exit_code})" if exit_code is not None else ""
        return DockerError(
            error_type=DockerErrorType.CONTAINER_CRASH,
            message=f"Container stopped unexpectedly{exit_msg}",
            suggestion="Check the full container logs with 'docker logs <container>' for details",
            raw_log=logs[-500:] if len(logs) > 500 else logs,  # Last 500 chars
            is_recoverable=False
        )
    
    # Unknown error while running
    return DockerError(
        error_type=DockerErrorType.UNKNOWN,
        message="Unknown error or timeout",
        suggestion="Check container logs for more details",
        raw_log=logs[-500:] if len(logs) > 500 else logs,
        is_recoverable=False
    )


def _extract_relevant_log(logs: str, pattern: str, context_lines: int = 3) -> str:
    # Extract the relevant portion of logs around the error pattern.
    lines = logs.split('\n')
    for i, line in enumerate(lines):
        if re.search(pattern, line, re.IGNORECASE):
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            return '\n'.join(lines[start:end])
    return logs[-300:] if len(logs) > 300 else logs


def diagnose_container(container_name_or_id: str, timeout_occurred: bool = False) -> DockerError:
    # Full diagnosis of a container's state and any errors.
    #
    # Args:
    #     container_name_or_id: Container to diagnose
    #     timeout_occurred: Whether we're diagnosing after a timeout
    #
    # Returns:
    #     DockerError with full diagnosis
    # Check if container is running
    running = is_container_running(container_name_or_id)
    exit_code = None if running else get_container_exit_code(container_name_or_id)
    
    # Get logs
    logs = get_container_logs(container_name_or_id, tail=200)
    
    # Analyze
    error = analyze_error(logs or "", running, exit_code)
    
    # If timeout occurred and model is still loading, provide better message
    if timeout_occurred and error.error_type == DockerErrorType.STILL_LOADING:
        error = DockerError(
            error_type=DockerErrorType.TIMEOUT,
            message="Startup timeout - model is still loading",
            suggestion="The model is loading but taking longer than expected. Try: 1) Increase startup_timeout in docker_config.json, 2) Wait and retry, 3) Use a smaller model",
            raw_log=error.raw_log,
            is_recoverable=True
        )
    elif timeout_occurred and error.error_type == DockerErrorType.UNKNOWN and running:
        # Container is running but we timed out and no clear error
        error = DockerError(
            error_type=DockerErrorType.TIMEOUT,
            message="Startup timeout - server not responding",
            suggestion="Server didn't respond in time. Try: 1) Increase startup_timeout, 2) Check if model is very large, 3) Check GPU memory availability",
            raw_log=error.raw_log,
            is_recoverable=True
        )
    
    return error


def format_error_message(error: DockerError, include_suggestion: bool = True, include_log: bool = False) -> str:
    # Format a DockerError into a human-readable message.
    #
    # Args:
    #     error: The DockerError to format
    #     include_suggestion: Whether to include the suggestion
    #     include_log: Whether to include raw log excerpt
    #
    # Returns:
    #     Formatted error message string
    parts = [error.message]
    
    if include_suggestion and error.suggestion:
        parts.append(f"\n  → {error.suggestion}")
    
    if include_log and error.raw_log:
        # Truncate log if too long
        log_excerpt = error.raw_log[:200] + "..." if len(error.raw_log) > 200 else error.raw_log
        parts.append(f"\n  Log: {log_excerpt}")
    
    return ''.join(parts)


# ==============================================================================
# BACKEND-SPECIFIC HELPERS
# ==============================================================================

def diagnose_llamacpp_error(container_name: str, timeout_occurred: bool = False) -> DockerError:
    # Diagnose llama.cpp container errors with backend-specific patterns.
    error = diagnose_container(container_name, timeout_occurred)
    
    # Add llama.cpp specific pattern checks
    logs = get_container_logs(container_name, tail=200)
    if logs:
        # llama.cpp specific: check for model loading progress
        if "llama_model_load" in logs.lower() or "llama_load_model" in logs.lower():
            if "error" not in logs.lower() and "fail" not in logs.lower():
                if timeout_occurred:
                    return DockerError(
                        error_type=DockerErrorType.TIMEOUT,
                        message="Model loading timed out (model file is being read)",
                        suggestion="The GGUF model is being loaded. Large models (>8GB) can take 2-5 minutes. Increase startup_timeout in docker_config.json.",
                        raw_log=error.raw_log,
                        is_recoverable=True
                    )
        
        # llama.cpp specific: GGUF errors
        if re.search(r"(gguf.*error|invalid gguf|bad gguf)", logs, re.IGNORECASE):
            return DockerError(
                error_type=DockerErrorType.MODEL_CORRUPT,
                message="Invalid or corrupt GGUF file",
                suggestion="The GGUF file may be corrupt or incompatible. Re-download the model or try a different quantization.",
                raw_log=error.raw_log,
                is_recoverable=False
            )
    
    return error


def diagnose_vllm_error(container_id: str, timeout_occurred: bool = False) -> DockerError:
    # Diagnose vLLM container errors with backend-specific patterns.
    error = diagnose_container(container_id, timeout_occurred)
    
    logs = get_container_logs(container_id, tail=200)
    if logs:
        # vLLM specific: weight loading
        if "loading weights" in logs.lower() or "loading model" in logs.lower():
            if timeout_occurred and is_container_running(container_id):
                return DockerError(
                    error_type=DockerErrorType.TIMEOUT,
                    message="Model loading timed out (weights are being loaded)",
                    suggestion="vLLM is loading model weights. Large models can take 5-10 minutes. Increase startup_timeout in docker_config.json.",
                    raw_log=error.raw_log,
                    is_recoverable=True
                )
        
        # vLLM specific: tensor parallel errors
        if re.search(r"(tensor.*parallel.*error|tp.*mismatch|world_size)", logs, re.IGNORECASE):
            return DockerError(
                error_type=DockerErrorType.CUDA_ERROR,
                message="Tensor parallelism configuration error",
                suggestion="Check tensor_parallel_size matches available GPUs. Single GPU should use tp=1.",
                raw_log=error.raw_log,
                is_recoverable=False
            )
    
    return error


def diagnose_sglang_error(container_name: str, timeout_occurred: bool = False) -> DockerError:
    # Diagnose SGLang container errors with backend-specific patterns.
    error = diagnose_container(container_name, timeout_occurred)
    
    logs = get_container_logs(container_name, tail=200)
    if logs:
        # SGLang specific patterns
        if "cuda" in logs.lower() and "initialized" in logs.lower():
            if timeout_occurred and is_container_running(container_name):
                return DockerError(
                    error_type=DockerErrorType.TIMEOUT,
                    message="Model loading timed out (CUDA initializing)",
                    suggestion="SGLang is initializing. This can take several minutes for large models. Increase timeout or use a smaller model.",
                    raw_log=error.raw_log,
                    is_recoverable=True
                )
    
    return error


def diagnose_ollama_error(container_name: str, timeout_occurred: bool = False) -> DockerError:
    # Diagnose Ollama container errors with backend-specific patterns.
    error = diagnose_container(container_name, timeout_occurred)
    
    logs = get_container_logs(container_name, tail=200)
    if logs:
        # Ollama specific: pulling model
        if "pulling" in logs.lower() or "downloading" in logs.lower():
            if timeout_occurred:
                return DockerError(
                    error_type=DockerErrorType.TIMEOUT,
                    message="Model download timed out",
                    suggestion="Ollama is downloading the model. This can take a while depending on model size and internet speed.",
                    raw_log=error.raw_log,
                    is_recoverable=True
                )
    
    return error
