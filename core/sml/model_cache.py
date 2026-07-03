# SmartLM Model Cache
#
# Centralized caching for loaded models across all backends.
# Prevents reloading the same model on each queue execution.
#
# Cache types:
# - Transformers cache: HuggingFace Transformers models (VLM, LLM, Florence)
# - GGUF cache: Native llama-cpp-python models (NOT Docker backends)
#
# Also provides unified cache clearing for multi-node workflows
# and Docker container lifecycle management.

import gc
import sys
import torch  # type: ignore
from typing import Dict, Any, Optional

from .logger import log

_LOG_PREFIX = "Cache"


# ============================================================================
# Transformers Model Cache
# ============================================================================
# Cache for loaded Transformers models to avoid reloading on each queue
# Key: "{model_path}:{quantization}:{attention}"
# Value: (model, processor, model_type)

_transformers_model_cache: Dict[str, tuple] = {}


def get_transformers_cache_key(model_path: str, quantization: str, attention: str) -> str:
    # Build cache key for Transformers models.
    return f"{model_path}:{quantization or 'none'}:{attention or 'auto'}"


def get_cached_transformers_model(cache_key: str) -> Optional[tuple]:
    # Get cached Transformers model if available.
    #
    # Returns:
    #     Tuple of (model, processor, model_type) or None if not cached
    if cache_key in _transformers_model_cache:
        log.debug(_LOG_PREFIX, f"Using cached Transformers model: {cache_key.split(':')[0].split('/')[-1]}")
        return _transformers_model_cache[cache_key]
    return None


def set_cached_transformers_model(cache_key: str, model: Any, processor: Any, model_type):
    # Store Transformers model in cache.
    #
    # Also clears any other cached models to avoid VRAM accumulation.
    global _transformers_model_cache

    # Clear existing cache if loading a different model
    if _transformers_model_cache and cache_key not in _transformers_model_cache:
        log.debug(_LOG_PREFIX, "Clearing previous Transformers model from cache (different model requested)")
        clear_transformers_cache()

    _transformers_model_cache[cache_key] = (model, processor, model_type)
    log.msg(_LOG_PREFIX, f"Cached Transformers model for reuse")


def clear_transformers_cache():
    # Clear all cached Transformers models and free VRAM.
    global _transformers_model_cache

    if not _transformers_model_cache:
        return

    log.debug(_LOG_PREFIX, "Clearing Transformers model cache...")

    for key, (model, processor, _) in list(_transformers_model_cache.items()):
        try:
            # Clear any cached states/gradients to help free memory
            if hasattr(model, 'eval'):
                model.eval()
            if hasattr(model, 'zero_grad'):
                try:
                    model.zero_grad(set_to_none=True)
                except Exception:
                    pass
            # NOTE: Don't call model.to('cpu') - it's very slow for large models
            # (can take 10-30+ seconds for 7B+ models) and requires that much free RAM.
            # Instead, just delete references and let CUDA free memory via empty_cache().
            del model
            del processor
        except Exception as e:
            log.debug(_LOG_PREFIX, f"  Error clearing model {key}: {e}")

    _transformers_model_cache.clear()

    # Force garbage collection multiple passes and VRAM cleanup
    for _ in range(3):
        gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()

    log.debug(_LOG_PREFIX, "Transformers cache cleared")


def get_cached_model_key() -> Optional[str]:
    # Get the key of the currently cached Transformers model, if any.
    #
    # Used to check if a different model needs to evict the current one.
    if _transformers_model_cache:
        return next(iter(_transformers_model_cache.keys()))
    return None


def is_transformers_cache_empty() -> bool:
    # Check if the Transformers model cache is empty.
    return not bool(_transformers_model_cache)


# ============================================================================
# GGUF Model Cache (Native llama-cpp-python ONLY)
# ============================================================================
# Cache for loaded GGUF models using native llama-cpp-python to avoid reloading.
# This cache is NOT used by Docker backends (llama.cpp Docker, Ollama Docker).
# Docker backends manage their own model lifecycle via container lifecycle.
#
# Key: "{model_path}:{context_size}"
# Value: (model, chat_handler, model_type)
#
# IMPORTANT: With proper KV cache clearing between calls, GGUF models
# can now be safely reused. The chat_handler's mtmd_ctx is lazily
# initialized once and reusable across calls.
# ============================================================================

_gguf_model_cache: Dict[str, tuple] = {}


def get_gguf_cache_key(model_path: str, context_size: int) -> str:
    # Build cache key for GGUF models.
    return f"{model_path}:{context_size}"


def get_cached_gguf_model(cache_key: str) -> Optional[tuple]:
    # Get cached GGUF model if available.
    #
    # Returns:
    #     Tuple of (model, chat_handler, model_type) or None if not cached
    if cache_key in _gguf_model_cache:
        log.debug(_LOG_PREFIX, f"Using cached GGUF model: {cache_key.split(':')[0].split('/')[-1]}")
        return _gguf_model_cache[cache_key]
    return None


def set_cached_gguf_model(cache_key: str, model: Any, chat_handler: Any, model_type):
    # Store GGUF model in cache.
    #
    # Also clears any other cached models to avoid VRAM accumulation.
    global _gguf_model_cache

    # Clear existing cache if loading a different model
    if _gguf_model_cache and cache_key not in _gguf_model_cache:
        log.debug(_LOG_PREFIX, "Clearing previous GGUF model from cache (different model requested)")
        clear_gguf_cache()

    _gguf_model_cache[cache_key] = (model, chat_handler, model_type)
    log.msg(_LOG_PREFIX, f"Cached GGUF model for reuse")


def clear_gguf_cache():
    # Clear all cached GGUF models and free VRAM.
    global _gguf_model_cache

    if not _gguf_model_cache:
        return

    log.debug(_LOG_PREFIX, "Clearing GGUF model cache...")

    # Import the proper cleanup function that handles vision handlers
    from .backend_gguf import cleanup_chat_handler_vision

    for key, (model, chat_handler, _) in list(_gguf_model_cache.items()):
        try:
            # Cleanup chat_handler FIRST (holds CLIP/mtmd vision model - 1-2GB VRAM)
            # Must use proper cleanup that calls clip_free/mtmd_free
            if chat_handler is not None:
                log.debug(_LOG_PREFIX, f"  Cleaning up chat_handler for {key}")
                cleanup_chat_handler_vision(chat_handler)

            # Then close the model (calls llama_free in C)
            if model is not None:
                # Reset KV cache first (may not be available in all versions)
                try:
                    if hasattr(model, 'reset'):
                        model.reset()
                except Exception:
                    pass
                # Close the model - this is the safe way to free resources
                if hasattr(model, 'close') and callable(model.close):
                    model.close()

            del model
            del chat_handler
        except Exception as e:
            log.debug(_LOG_PREFIX, f"  Error clearing GGUF model {key}: {e}")

    _gguf_model_cache.clear()

    # Force garbage collection and VRAM cleanup
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()

    log.debug(_LOG_PREFIX, "GGUF cache cleared")


def get_cached_gguf_model_key() -> Optional[str]:
    # Get the key of the currently cached GGUF model, if any.
    if _gguf_model_cache:
        return next(iter(_gguf_model_cache.keys()))
    return None


def is_gguf_cache_empty() -> bool:
    # Check if the GGUF model cache is empty.
    return not bool(_gguf_model_cache)


# ============================================================================
# Unified Cache Management
# ============================================================================

def clear_all_model_caches():
    # Clear ALL model caches across all backends to free VRAM.
    #
    # This is called when loading a different model to ensure VRAM is freed
    # BEFORE the new model is loaded, preventing OOM in multi-node workflows.
    #
    # Clears:
    # - Transformers cache (_transformers_model_cache)
    # - GGUF cache (_gguf_model_cache)
    # - vLLM Native cache (if available)
    log.debug(_LOG_PREFIX, "Clearing all model caches for multi-node workflow...")

    # Clear Transformers cache
    clear_transformers_cache()

    # Clear GGUF cache
    clear_gguf_cache()

    # Clear vLLM Native cache only if module was already imported
    # (avoid importing it — module-level vLLM check produces noisy warnings)
    _pkg = __name__.rsplit('.', 1)[0]
    _vllm_mod = sys.modules.get(f"{_pkg}.backend_vllm_native")
    if _vllm_mod and getattr(_vllm_mod, '_vllm_model_cache', None):
        _vllm_mod.unload_vllm()
        log.debug(_LOG_PREFIX, "  Cleared vLLM Native cache")

    # Clear WD14 ONNX session if loaded
    try:
        from .backend_wd14 import unload_wd14_model, is_wd14_cached
        if is_wd14_cached():
            unload_wd14_model()
            log.debug(_LOG_PREFIX, "  Cleared WD14 cache")
    except ImportError:
        pass  # WD14 module not available

    # Force final cleanup
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()

    log.debug(_LOG_PREFIX, "All model caches cleared")


def stop_all_docker_containers():
    # Stop all running Docker containers for LLM backends.
    #
    # This is called when switching between backends to free GPU VRAM.
    # Each Docker container holds its model in GPU memory, so we need to
    # stop them when switching to a different backend.
    #
    # Stops:
    # - vLLM Docker containers
    # - SGLang Docker containers
    # - Ollama Docker container
    # - llama.cpp Docker containers
    stop_other_docker_containers(exclude_backend=None)


def stop_other_docker_containers(exclude_backend: Optional[str] = None):
    # Stop Docker containers for LLM backends, optionally excluding one.
    #
    # This is called when switching between backends to free GPU VRAM.
    # Each Docker container holds its model in GPU memory, so we need to
    # stop them when switching to a different backend.
    #
    # Args:
    #     exclude_backend: Backend to NOT stop (e.g., "Ollama (Docker)").
    #                      If None, stops all containers.
    #
    # Stops (unless excluded):
    # - vLLM Docker containers
    # - SGLang Docker containers
    # - Ollama Docker container
    # - llama.cpp Docker containers
    log.debug(_LOG_PREFIX, f"Stopping Docker containers (exclude={exclude_backend})...")

    # Stop vLLM Docker containers
    if exclude_backend != "vLLM (Docker)":
        try:
            from . import backend_vllm_docker
            if backend_vllm_docker.get_running_vllm_containers():
                log.msg(_LOG_PREFIX, "Stopping vLLM Docker container(s)...")
                backend_vllm_docker.stop_vllm_container()
        except ImportError:
            pass
        except Exception as e:
            log.debug(_LOG_PREFIX, f"  Error stopping vLLM containers: {e}")

    # Stop SGLang Docker containers
    if exclude_backend != "SGLang (Docker)":
        try:
            from . import backend_sglang_docker
            if backend_sglang_docker.get_running_sglang_containers():
                log.msg(_LOG_PREFIX, "Stopping SGLang Docker container(s)...")
                backend_sglang_docker.stop_sglang_container()
        except ImportError:
            pass
        except Exception as e:
            log.debug(_LOG_PREFIX, f"  Error stopping SGLang containers: {e}")

    # Stop Ollama Docker container
    if exclude_backend != "Ollama (Docker)":
        try:
            from . import backend_ollama_docker
            if backend_ollama_docker.is_ollama_container_running():
                log.msg(_LOG_PREFIX, "Stopping Ollama Docker container...")
                backend_ollama_docker.stop_ollama_container()
        except ImportError:
            pass
        except Exception as e:
            log.debug(_LOG_PREFIX, f"  Error stopping Ollama container: {e}")

    # Stop llama.cpp Docker containers
    if exclude_backend != "llama.cpp (Docker)":
        try:
            from . import backend_llamacpp_docker
            if backend_llamacpp_docker.get_running_llamacpp_containers():
                log.msg(_LOG_PREFIX, "Stopping llama.cpp Docker container(s)...")
                backend_llamacpp_docker.stop_llamacpp_container()
        except ImportError:
            pass
        except Exception as e:
            log.debug(_LOG_PREFIX, f"  Error stopping llama.cpp containers: {e}")

    log.debug(_LOG_PREFIX, "Docker containers stopped")
