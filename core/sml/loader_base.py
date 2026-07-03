# SmartLM Base - Model Loading Functions
#
# Contains:
# - Method-first support matrix
# - Model auto-discovery
# - Backend routing (load_model_with_backend)
# - Dynamic model filtering
#
# Uses shared modules for common functionality:
# - model_types.py: Enums and type detection
# - config_templates.py: Template loading/saving
# - device.py: VRAM/GPU management
# - model_files.py: File operations, downloads (ensure_model_path, ensure_mmproj_path)
import re
import torch #type: ignore
from pathlib import Path
from typing import Any, Optional

# ============================================================================
# Import from shared modular files
# ============================================================================

# Types and enums
from .model_types import (
    ModelType, ModelFamily, LoadingMethod,
    METHOD_SUPPORT_V2,
    get_model_family_from_name,
    is_mistral3_vision_model,
)

# Template functions
from .config_templates import (
    get_llm_models_path,
    TemplateContext,
)

# Centralized logger
from .logger import log


_LOG_PREFIX = "Base"


# ============================================================================
# Backend Wrapper Classes (used by load_model_with_backend)
# ============================================================================

class VLLMWrapper:
    # Wrapper for vLLM Docker backend instances.
    def __init__(self, vllm_info):
        self.is_vllm = True
        self.is_gguf = False
        self.is_quantized = True
        self.vllm_client = vllm_info.get("client")
        self.vllm_model_name = vllm_info.get("model_name")


class VLLMNativeWrapper:
    # Wrapper for vLLM Native backend instances.
    def __init__(self, vllm_model, model_name):
        self.is_vllm = True
        self.is_vllm_native = True
        self.is_gguf = False
        self.is_quantized = False
        self.vllm_model = vllm_model
        self.vllm_model_name = model_name


class SGLangWrapper:
    # Wrapper for SGLang Docker backend instances.
    def __init__(self, sglang_info):
        self.is_sglang = True
        self.is_vllm = False
        self.is_gguf = False
        self.is_quantized = True
        self.sglang_client = sglang_info.get("client")
        self.sglang_model_name = sglang_info.get("model_name")


class OllamaWrapper:
    # Wrapper for Ollama Docker backend instances.
    def __init__(self, ollama_info, context_size=8192):
        self.is_vllm = False
        self.is_ollama = True
        self.is_gguf = ollama_info.get("is_gguf", False)
        self.is_quantized = True
        self.ollama_client = ollama_info.get("client")
        self.ollama_model_name = ollama_info.get("model_name")
        self.ollama_base_url = ollama_info.get("base_url")
        self.context_size = context_size


class LlamaCppWrapper:
    # Wrapper for llama.cpp Docker backend instances.
    def __init__(self, llamacpp_info):
        self.is_vllm = False
        self.is_llamacpp_docker = True
        self.is_gguf = True
        self.is_quantized = True
        self.llamacpp_client = llamacpp_info.get("client")
        self.llamacpp_model_name = llamacpp_info.get("model_name")
        self.llamacpp_base_url = llamacpp_info.get("base_url")


# ============================================================================
# Family → ModelType mapping for backend routing
# ============================================================================

_FAMILY_TO_MODEL_TYPE = {
    ModelFamily.MISTRAL: ModelType.MISTRAL3,
    ModelFamily.QWEN: ModelType.QWENVL,
    ModelFamily.LLM_TEXT: ModelType.LLM,
    ModelFamily.LLAVA: ModelType.LLAVA,
    ModelFamily.FLORENCE: ModelType.FLORENCE2,
    ModelFamily.VLM: ModelType.LLAVA,
}


# Transformers version compatibility helpers
from .vlm_loader import dtype_kwarg


# ============================================================================
# Quantization Resolution Helpers
# ============================================================================

def _resolve_vllm_quantization(
    model_path: str,
    quantization: str,
    ctx,
    *,
    is_mistral3_vision: bool = False,
    backend_label: str = "vLLM",
) -> Optional[str]:
    # Resolve quantization setting for vLLM/SGLang backends.
    #
    # Handles: auto-select, pre-quantized detection, Mistral3 vision guard,
    # 4bit/8bit → bitsandbytes mapping, fp16/bf16/none → None.
    #
    # Args:
    #     model_path: Path to model directory
    #     quantization: Raw quantization value from kwargs
    #     ctx: TemplateContext (for template quantization update)
    #     is_mistral3_vision: If True, block BitsAndBytes (unsupported)
    #     backend_label: Label for log messages ("vLLM", "vLLM Native", etc.)
    #
    # Returns:
    #     Resolved quantization string for the backend, or None
    is_prequantized, prequant_type = detect_prequantized_model(Path(model_path))
    if is_prequantized:
        log.debug(_LOG_PREFIX, f"Model is pre-quantized ({prequant_type}), skipping additional quantization")

    if quantization == "auto":
        if is_prequantized:
            log.debug(_LOG_PREFIX, f"Auto mode: model already {prequant_type} quantized, using native dtype")
            return None
        if is_mistral3_vision:
            log.debug(_LOG_PREFIX, f"Auto mode: Mistral3/Pixtral vision model - BitsAndBytes not supported, using native dtype")
            return None
        model_size_gb = calculate_model_size(Path(model_path))
        auto_quant = auto_select_quantization(
            model_name=model_path.split("/")[-1] if "/" in model_path else model_path,
            estimated_size_gb=model_size_gb,
        )
        if auto_quant in ("4bit", "8bit"):
            log.debug(_LOG_PREFIX, f"Auto mode: using bitsandbytes 4-bit for {backend_label} (auto selected {auto_quant})")
            return "bitsandbytes"
        log.debug(_LOG_PREFIX, f"Auto mode: sufficient VRAM, no quantization needed")
        return None
    elif quantization in ("4bit", "8bit"):
        if is_mistral3_vision:
            log.warning(_LOG_PREFIX, "Mistral3/Pixtral vision models don't support BitsAndBytes in vLLM - using native dtype")
            return None
        if quantization == "8bit":
            log.warning(_LOG_PREFIX, f"{backend_label} bitsandbytes only supports 4-bit. Falling back to 4-bit.")
        else:
            log.debug(_LOG_PREFIX, f"Using bitsandbytes 4-bit quantization for {backend_label}")
        return "bitsandbytes"
    elif quantization in ("fp16", "bf16", "fp32", "none"):
        return None
    return quantization


def _resolve_sglang_quantization(
    model_path: str,
    quantization: str,
    ctx,
) -> Optional[str]:
    # Resolve quantization setting for SGLang backend.
    #
    # SGLang supports fp8/awq/gptq natively but NOT bitsandbytes.
    is_prequantized, prequant_type = detect_prequantized_model(Path(model_path))
    if is_prequantized:
        log.debug(_LOG_PREFIX, f"Model is pre-quantized ({prequant_type}), using native format")

    if is_prequantized:
        return prequant_type.lower() if prequant_type in ("FP8", "AWQ", "GPTQ") else None
    if quantization == "auto":
        model_size_gb = calculate_model_size(Path(model_path))
        auto_quant = auto_select_quantization(
            model_name=model_path.split("/")[-1] if "/" in model_path else model_path,
            estimated_size_gb=model_size_gb,
        )
        if auto_quant in ("4bit", "8bit"):
            log.debug(_LOG_PREFIX, "Auto mode: SGLang doesn't support bitsandbytes, using native dtype")
        return None
    elif quantization in ("4bit", "8bit"):
        log.warning(_LOG_PREFIX, "SGLang doesn't support bitsandbytes quantization - using native dtype")
        return None
    elif quantization in ("fp16", "bf16", "fp32", "none"):
        return None
    return quantization


# GGUF file selection priority
_GGUF_PRIORITY = ['Q4_K_M', 'Q5_K_M', 'Q8_0', 'Q6_K', 'Q4_K_S', 'BF16', 'F16']


def _select_best_gguf(folder: Path, *, filter_mmproj: bool = True) -> Path:
    # Select the best GGUF model file from a directory.
    #
    # Args:
    #     folder: Directory containing GGUF files
    #     filter_mmproj: If True, exclude mmproj files from selection
    #
    # Returns:
    #     Path to the best GGUF file
    #
    # Raises:
    #     FileNotFoundError: If no GGUF model files found
    gguf_files = list(folder.glob("*.gguf"))
    if filter_mmproj:
        gguf_files = [f for f in gguf_files if 'mmproj' not in f.name.lower()]
    if not gguf_files:
        raise FileNotFoundError(f"No GGUF model files found in: {folder}")

    log.debug(_LOG_PREFIX, f"_select_best_gguf: {len(gguf_files)} candidate(s) in {folder.name}, priority order: {_GGUF_PRIORITY}")

    for priority in _GGUF_PRIORITY:
        for f in gguf_files:
            if priority in f.name:
                log.msg(_LOG_PREFIX, f"Selected GGUF: {f.name} (from {len(gguf_files)} available)")
                return f

    log.msg(_LOG_PREFIX, f"Selected GGUF: {gguf_files[0].name} (from {len(gguf_files)} available)")
    return gguf_files[0]


# Device and memory functions
from .common import cleanup_memory_before_load
from .device import (
    auto_select_attention, auto_select_quantization,
    LLAMA_CPP_AVAILABLE,
)

# Model caches (moved to model_cache.py)
from .model_cache import (
    get_transformers_cache_key, get_cached_transformers_model,
    set_cached_transformers_model, clear_transformers_cache,
    get_cached_model_key, is_transformers_cache_empty,
    get_gguf_cache_key, get_cached_gguf_model,
    set_cached_gguf_model, clear_gguf_cache,
    get_cached_gguf_model_key, is_gguf_cache_empty,
    clear_all_model_caches,
    stop_all_docker_containers, stop_other_docker_containers,
)


# File operations
from .model_files import (
    calculate_model_size,
    ensure_mmproj_path,
    discover_models_in_folder,
    detect_prequantized_model,
)


# ============================================================================
# Unified VLM Loader (moved to vlm_loader.py)
# ============================================================================
from .vlm_loader import load_vlm_transformers as _load_vlm_transformers


# ============================================================================
# Model Loading
# ============================================================================

def load_model_with_backend(
    loading_method: str,
    model_family: str,
    model_path: str,
    ctx: TemplateContext,
    **kwargs
) -> Any:
    # Load model using specified backend (method-first workflow).
    #
    # Routes to appropriate loader based on method + family combination:
    # - Transformers: smartlm_mistral.py, smartlm_qwenvl.py, smartlm_florence2.py, smartlm_llm.py
    # - GGUF: gguf_wrapper.py (universal)
    # - vLLM (Docker): backend_vllm_docker.py (Windows, Docker-based)
    # - vLLM (Native): backend_vllm_native.py (Linux, native pip install)
    #
    # Args:
    #     loading_method: "Transformers", "GGUF (llama-cpp-python)", "vLLM (Docker)", or "vLLM (Native)"
    #     model_family: "Mistral", "Qwen", "Florence", or "LLM (Text-Only)"
    #     model_path: Path to model folder or .gguf file
    #     ctx: TemplateContext with configuration values
    #     **kwargs: Additional loading parameters (quantization, device, etc.)
    #
    # Returns:
    #     Loaded model, processor/tokenizer tuple, and detected ModelType
    from . import backend_vllm_docker
    from . import florence2_wrapper
    
    # Only import native vLLM when needed (it prints warnings on Windows)
    backend_vllm_native = None
    
    log.debug(_LOG_PREFIX, f"load_model_with_backend: method={loading_method}, family={model_family}")
    log.debug(_LOG_PREFIX, f"  model_path={model_path}")
    log.debug(_LOG_PREFIX, f"  kwargs={kwargs}")
    
    # Extract cache-relevant kwargs
    quantization = kwargs.get('quantization', 'auto')
    attention_mode = kwargs.get('attention_mode', 'auto')
    memory_cleanup = kwargs.get('memory_cleanup', True)
    keep_model_loaded = kwargs.get('keep_model_loaded', False)
    
    # Resolve 'auto' values BEFORE cache check so keys are consistent
    # This ensures cache lookup uses the same resolved values as cache store
    if loading_method == "Transformers":
        # Resolve attention mode
        if attention_mode == "auto":
            resolved_attention = auto_select_attention()
        else:
            resolved_attention = attention_mode
        
        # Resolve quantization (for cache key, actual logic is below)
        if quantization == "auto":
            model_size_gb = calculate_model_size(Path(model_path))
            resolved_quantization = auto_select_quantization(
                model_name=Path(model_path).name,
                estimated_size_gb=model_size_gb
            )
        else:
            resolved_quantization = quantization
    else:
        resolved_attention = attention_mode
        resolved_quantization = quantization
    
    # Clear Transformers cache if memory_cleanup requested AND NOT keeping model loaded
    # The user explicitly wants to keep the model, so don't clear it even if memory_cleanup is True
    if memory_cleanup and not keep_model_loaded:
        log.debug(_LOG_PREFIX, f"Cache: memory_cleanup=True, keep_model_loaded=False → clearing cache")
        clear_transformers_cache()
        cleanup_memory_before_load(aggressive=False)
    elif memory_cleanup and keep_model_loaded:
        # Only cleanup non-model memory (don't clear the Transformers cache)
        log.debug(_LOG_PREFIX, f"Cache: memory_cleanup=True, keep_model_loaded=True → preserving model cache")
        cleanup_memory_before_load(aggressive=False)
    else:
        log.debug(_LOG_PREFIX, f"Cache: memory_cleanup=False → skipping cleanup")
    
    # ============================================================================
    # CROSS-BACKEND CACHE INVALIDATION
    # ============================================================================
    # When switching between backends (e.g., GGUF → Transformers or vice versa),
    # we MUST clear the other backend's cache to free VRAM before loading.
    # This is critical even with keep_model_loaded=True - you can only keep ONE
    # model loaded at a time, not models from different backends.
    # We also stop Docker containers since they hold models in GPU memory.
    # ============================================================================
    
    # Track current backend for smart Docker container management
    # Get the currently loaded backend from cache status
    current_backend = None
    if get_cached_model_key():
        current_backend = "Transformers"
    elif get_cached_gguf_model_key():
        current_backend = "GGUF (llama-cpp-python)"
    # Note: Docker backends don't use local cache, so we check containers below
    
    # Docker backends list for easy checking
    docker_backends = ("vLLM (Docker)", "Ollama (Docker)", "llama.cpp (Docker)", "SGLang (Docker)")
    
    # Check if any Docker containers are running (indicates Docker backend currently active)
    docker_containers_running = False
    try:
        from . import backend_vllm_docker, backend_sglang_docker, backend_ollama_docker, backend_llamacpp_docker
        docker_containers_running = (
            backend_vllm_docker.get_running_vllm_containers() or
            backend_sglang_docker.get_running_sglang_containers() or
            backend_ollama_docker.is_ollama_container_running() or
            backend_llamacpp_docker.get_running_llamacpp_containers()
        )
        if docker_containers_running:
            current_backend = "Docker"  # Generic Docker backend
    except ImportError:
        pass
    except Exception:
        pass  # Failed to check containers, assume none running
    
    # Determine if we actually need to stop Docker containers
    # Only stop when switching FROM or TO Docker backends
    should_stop_docker = (
        loading_method in docker_backends or  # Switching TO Docker (stop others)
        current_backend == "Docker" or        # Switching FROM Docker (stop current)
        (current_backend and current_backend != loading_method)  # Backend switch involving potential cleanup
    )
    
    # Check which backend we're loading and clear OTHER backend caches + stop Docker containers if needed
    if loading_method == "Transformers":
        # Loading Transformers - clear GGUF cache and conditionally stop Docker containers
        if get_cached_gguf_model_key():
            log.msg(_LOG_PREFIX, f"Backend switch: clearing GGUF cache before loading Transformers model")
            clear_gguf_cache()
        # Stop Docker containers only if switching from Docker or if any are running
        if should_stop_docker:
            stop_all_docker_containers()
    elif loading_method == "GGUF (llama-cpp-python)":
        # Loading GGUF - clear Transformers cache and conditionally stop Docker containers
        if get_cached_model_key():
            log.msg(_LOG_PREFIX, f"Backend switch: clearing Transformers cache before loading GGUF model")
            clear_transformers_cache()
        # Stop Docker containers only if switching from Docker or if any are running
        if should_stop_docker:
            stop_all_docker_containers()
    elif loading_method == "vLLM (Native)":
        # Loading vLLM Native - clear both GGUF and Transformers caches and conditionally stop Docker containers
        if get_cached_model_key() or get_cached_gguf_model_key():
            log.msg(_LOG_PREFIX, f"Backend switch: clearing model caches before loading vLLM Native model")
            clear_transformers_cache()
            clear_gguf_cache()
        # Stop Docker containers only if switching from Docker or if any are running
        if should_stop_docker:
            stop_all_docker_containers()
    # Docker backends (vLLM Docker, Ollama Docker, llama.cpp Docker, SGLang Docker)
    # Clear local caches and stop OTHER Docker containers when switching
    elif loading_method in docker_backends:
        if get_cached_model_key() or get_cached_gguf_model_key():
            log.msg(_LOG_PREFIX, f"Backend switch: clearing local model caches before loading Docker model")
            clear_transformers_cache()
            clear_gguf_cache()
        # Stop OTHER Docker containers (not the one we're about to use)
        # This ensures we free VRAM from other backends while keeping the current one running
        stop_other_docker_containers(exclude_backend=loading_method)
    
    # ============================================================================
    # SAME-BACKEND CACHE CHECK (keep_model_loaded optimization)
    # ============================================================================
    # When keep_model_loaded=True, check if the SAME or DIFFERENT model is cached
    # within the same backend. Reuse if same, evict if different.
    # ============================================================================
    if loading_method == "Transformers":
        cache_key = get_transformers_cache_key(model_path, resolved_quantization, resolved_attention)
        current_cached_key = get_cached_model_key()
        
        log.debug(_LOG_PREFIX, f"Cache check: key={cache_key.split('/')[-1] if '/' in cache_key else cache_key}")
        log.debug(_LOG_PREFIX, f"  current_cached_key={current_cached_key.split('/')[-1] if current_cached_key and '/' in current_cached_key else current_cached_key}")
        log.debug(_LOG_PREFIX, f"  keep_model_loaded={keep_model_loaded}")
        if current_cached_key and current_cached_key != cache_key:
            # Different model is cached - need to evict it first
            log.msg(_LOG_PREFIX, f"Multi-node workflow: evicting cached model to load different model")
            clear_all_model_caches()
        elif keep_model_loaded:
            # Same model - check cache for reuse
            cached = get_cached_transformers_model(cache_key)
            if cached:
                log.msg(_LOG_PREFIX, f"Using cached model (skipping load)")
                return cached  # Returns (model, processor, model_type)
            else:
                log.debug(_LOG_PREFIX, f"Cache miss: model not in cache despite keep_model_loaded=True")
        else:
            log.debug(_LOG_PREFIX, f"Cache: keep_model_loaded=False → model will be loaded fresh")
    elif loading_method == "vLLM (Native)":
        # vLLM native also needs cache check - handled internally by _clear_vllm_cache_if_different
        pass
    
    # Parse enums
    try:
        method = LoadingMethod(loading_method)
        family = ModelFamily(model_family)
    except ValueError as e:
        raise ValueError(f"Invalid loading method or family: {e}")
    
    # Resolve Auto Detect if it wasn't resolved upstream (safety fallback)
    was_auto_detect = family == ModelFamily.AUTO_DETECT
    if was_auto_detect:
        from .model_types import get_model_family_from_name
        vision_flag = kwargs.get("has_vision", False)
        family = get_model_family_from_name(model_path, has_vision=vision_flag)
        model_family = family.value
        log.msg(_LOG_PREFIX, f"Auto Detect resolved in backend: {model_family}")
    
    # Check if combination is supported (METHOD_SUPPORT_V2 maps method -> list of families)
    supported_families = METHOD_SUPPORT_V2.get(method, [])
    if family not in supported_families:
        # Provide more specific error messages for known incompatibilities
        from .model_types import MISTRAL3_TRANSFORMERS_COMPATIBLE
        if family == ModelFamily.MISTRAL and method == LoadingMethod.TRANSFORMERS and not MISTRAL3_TRANSFORMERS_COMPATIBLE:
            raise ValueError(f"Mistral3 requires Transformers v5+ for Transformers backend. Use vLLM (Docker) or upgrade Transformers.")
        elif was_auto_detect:
            # Auto Detect resolved to a family unsupported by the chosen method.
            # Fall back to Transformers which supports all families (vision included).
            # This preserves vision capabilities for detected VLMs (Qwen VL, LLaVA, etc.)
            # instead of forcing LLM_TEXT which would lose vision support.
            log.warning(_LOG_PREFIX, f"Auto Detect: {model_family} not supported with {loading_method}, "
                        f"falling back to Transformers")
            method = LoadingMethod.TRANSFORMERS
            loading_method = method.value
            # Keep detected family - only override if also unsupported by Transformers
            transformers_families = METHOD_SUPPORT_V2.get(LoadingMethod.TRANSFORMERS, [])
            if family not in transformers_families:
                log.warning(_LOG_PREFIX, f"  {model_family} also unsupported by Transformers, using LLM (Text-Only)")
                family = ModelFamily.LLM_TEXT
                model_family = family.value
        else:
            raise ValueError(f"{model_family} is not supported with {loading_method}")
    
    log.debug(_LOG_PREFIX, f"  Routing to backend: {method.value} + {family.value}")
    
    # Route to correct backend
    if method == LoadingMethod.VLLM_DOCKER:
        # vLLM Docker backend - returns dict with client info, not model/processor
        if family not in _FAMILY_TO_MODEL_TYPE:
            raise ValueError(f"vLLM does not support {model_family}")
        
        quantization = kwargs.get("quantization", "auto")
        context_size = kwargs.get("context_size", None)
        
        is_m3v = family == ModelFamily.MISTRAL and is_mistral3_vision_model(model_path)
        quantization = _resolve_vllm_quantization(
            model_path, quantization, ctx, is_mistral3_vision=is_m3v,
        )
        
        vllm_info = backend_vllm_docker.load_vllm(
            model_path,
            quantization=quantization,
            context_size=context_size,
            trust_remote_code=bool(kwargs.get("trust_remote_code", False)),
        )
        
        if vllm_info is None:
            raise RuntimeError(
                "vLLM server not available or not serving the requested model.\n\n"
                "Solutions:\n"
                "  1. Ensure Docker is installed and running\n"
                "  2. Check docker_config.json for correct settings\n"
                "  3. Switch to Transformers or SGLang loading method"
            )
        
        wrapper = VLLMWrapper(vllm_info)
        return wrapper, None, _FAMILY_TO_MODEL_TYPE[family]
    
    elif method == LoadingMethod.SGLANG_DOCKER:
        # SGLang Docker backend - alternative to vLLM with RadixAttention
        from . import backend_sglang_docker
        
        quantization = kwargs.get("quantization", "auto")
        context_size = kwargs.get("context_size", None)
        
        quantization = _resolve_sglang_quantization(model_path, quantization, ctx)
        
        if family in (ModelFamily.MISTRAL, ModelFamily.QWEN, ModelFamily.LLM_TEXT):
            sglang_info = backend_sglang_docker.load_sglang(
                model_path,
                quantization=quantization,
                context_size=context_size
            )
            
            if sglang_info is None:
                raise RuntimeError(
                    "SGLang server not available or not serving the requested model.\n\n"
                    "Solutions:\n"
                    "  1. Ensure Docker is installed and running\n"
                    "  2. Check docker_config.json for correct settings\n"
                    "  3. Switch to vLLM (Docker) or Transformers loading method"
                )
            
            wrapper = SGLangWrapper(sglang_info)
            model_type = ModelType.MISTRAL3 if family == ModelFamily.MISTRAL else (
                ModelType.QWENVL if family == ModelFamily.QWEN else ModelType.LLM
            )
            return wrapper, None, model_type
        else:
            raise ValueError(f"SGLang does not support {model_family}")
    
    elif method == LoadingMethod.VLLM_NATIVE:
        # Native vLLM backend (Linux only, direct pip install)
        from . import backend_vllm_native
        if not backend_vllm_native.VLLM_AVAILABLE:
            raise ImportError(
                "vLLM is required for native vLLM loading but was not found.\n\n"
                "Install with: pip install vllm\n"
                "Note: vLLM native is only available on Linux with NVIDIA GPUs."
            )
        
        if family not in _FAMILY_TO_MODEL_TYPE:
            raise ValueError(f"vLLM (Native) does not support {model_family}")
        
        quantization = kwargs.get("quantization", "auto")
        context_size = kwargs.get("context_size", None)
        
        quantization = _resolve_vllm_quantization(
            model_path, quantization, ctx, backend_label="vLLM Native",
        )
        
        vllm_info = backend_vllm_native.load_vllm(
            model_path,
            quantization=quantization,
            context_size=context_size,
            trust_remote_code=bool(kwargs.get("trust_remote_code", False)),
        )
        if vllm_info is None:
            raise RuntimeError(
                "Failed to load model with native vLLM.\n\n"
                "Solutions:\n"
                "  1. Ensure vLLM is installed: pip install vllm\n"
                "  2. Verify you're running on Linux with NVIDIA GPU\n"
                "  3. Check that model path is valid"
            )
        
        wrapper = VLLMNativeWrapper(vllm_info.get("model"), model_path)
        return wrapper, None, _FAMILY_TO_MODEL_TYPE[family]
    
    elif method == LoadingMethod.GGUF:
        # GGUF backend (llama-cpp-python)
        if family == ModelFamily.FLORENCE:
            raise ValueError("Florence-2 is not available in GGUF format")
        
        if not LLAMA_CPP_AVAILABLE:
            raise ImportError(
                "llama-cpp-python is required for GGUF models but was not found. "
                "Install with: pip install llama-cpp-python"
            )
        
        # Get model file path - handle both files and folders
        model_file = Path(model_path)
        
        if model_file.is_dir():
            model_file = _select_best_gguf(model_file)
        
        if not model_file.exists():
            raise FileNotFoundError(f"GGUF model file not found: {model_file}")
        
        context_size = kwargs.get('context_size', 32768)
        device = kwargs.get('device', 'cuda')
        keep_model_loaded = kwargs.get('keep_model_loaded', False)
        n_batch = kwargs.get('n_batch', 512)
        
        # Determine GPU layers (-1 = full offload for CUDA)
        n_gpu_layers = -1 if device == "cuda" else 0
        
        # ============================================================================
        # GGUF Model Cache Check (Native llama-cpp-python ONLY)
        # ============================================================================
        # This caching ONLY applies to native GGUF loading (llama-cpp-python).
        # Docker backends (llama.cpp Docker, Ollama Docker) have separate loading
        # methods and manage model lifecycle via container lifecycle instead.
        # With proper KV cache clearing between calls, GGUF models can be safely reused.
        # ============================================================================
        gguf_cache_key = get_gguf_cache_key(str(model_file), context_size)
        current_gguf_cached_key = get_cached_gguf_model_key()
        
        if current_gguf_cached_key and current_gguf_cached_key != gguf_cache_key:
            # Different GGUF model is cached - need to evict it first
            log.msg(_LOG_PREFIX, f"GGUF cache: evicting cached model to load different model")
            clear_gguf_cache()
        elif current_gguf_cached_key:
            # Same model is cached - reuse it (regardless of keep_model_loaded)
            # If keep_model_loaded=False, the caller will unload AFTER generation
            # This avoids wasteful unload→load→generate→unload cycle
            cached = get_cached_gguf_model(gguf_cache_key)
            if cached:
                model, chat_handler, model_type = cached
                # Clear KV cache before reuse to ensure clean state
                if model is not None:
                    if hasattr(model, 'reset'):
                        model.reset()
                    if hasattr(model, '_ctx') and hasattr(model._ctx, 'kv_cache_clear'):
                        model._ctx.kv_cache_clear()
                if keep_model_loaded:
                    log.msg(_LOG_PREFIX, f"Using cached GGUF model (KV cache cleared)")
                else:
                    log.msg(_LOG_PREFIX, f"Using cached GGUF model for final run (will unload after)")
                return model, None, model_type
        
        # GGUF models use llama-cpp-python
        if family == ModelFamily.QWEN:
            # Qwen VL with vision support
            from llama_cpp import Llama #type: ignore
            
            # Get mmproj file for vision support - always search and update template
            mmproj_file = ensure_mmproj_path(
                ctx.to_dict(), 
                str(model_file.parent),
            )
            
            if mmproj_file:
                # Vision model with mmproj
                log.msg(_LOG_PREFIX, "Loading Qwen VL GGUF (vision)")
                
                # Detect Qwen version for appropriate chat handler
                model_name_lower = model_file.name.lower()
                is_qwen25 = "qwen2.5" in model_name_lower or "qwen_2_5" in model_name_lower
                
                try:
                    if is_qwen25:
                        from llama_cpp.llama_chat_format import Qwen25VLChatHandler #type: ignore
                        chat_handler = Qwen25VLChatHandler(clip_model_path=mmproj_file)
                    else:
                        from llama_cpp.llama_chat_format import Qwen2VLChatHandler #type: ignore
                        chat_handler = Qwen2VLChatHandler(clip_model_path=mmproj_file)
                except ImportError as e:
                    log.warning(_LOG_PREFIX, "Qwen chat handler not available, falling back to Llava")
                    from llama_cpp.llama_chat_format import Llava16ChatHandler #type: ignore
                    chat_handler = Llava16ChatHandler(clip_model_path=mmproj_file)
                
                model = Llama(
                    model_path=str(model_file),
                    chat_handler=chat_handler,
                    n_ctx=context_size,
                    n_gpu_layers=n_gpu_layers,
                    n_batch=n_batch,
                    verbose=False,
                )
                # Store chat_handler reference for proper VRAM cleanup later
                # The chat_handler holds the CLIP model which uses significant VRAM
                setattr(model, '_sml_chat_handler', chat_handler)
                # Cache model if keep_model_loaded is enabled
                if keep_model_loaded:
                    set_cached_gguf_model(gguf_cache_key, model, chat_handler, ModelType.QWENVL)
                return model, None, ModelType.QWENVL
            else:
                # Text-only Qwen (no mmproj)
                log.msg(_LOG_PREFIX, "Loading Qwen GGUF (text-only)")
                model = Llama(
                    model_path=str(model_file),
                    n_ctx=context_size,
                    n_gpu_layers=n_gpu_layers,
                    n_batch=n_batch,
                    verbose=False,
                )
                # Cache model if keep_model_loaded is enabled
                if keep_model_loaded:
                    set_cached_gguf_model(gguf_cache_key, model, None, ModelType.LLM)
                return model, None, ModelType.LLM
        
        elif family == ModelFamily.MISTRAL or family == ModelFamily.LLM_TEXT:
            from llama_cpp import Llama #type: ignore
            
            # Check for mmproj file - always search and update template
            mmproj_file = ensure_mmproj_path(
                ctx.to_dict(), 
                str(model_file.parent),
            )
            
            if mmproj_file:
                # Vision model with mmproj (e.g., Ministral with Pixtral vision)
                log.msg(_LOG_PREFIX, f"Loading Mistral VL GGUF (vision): {model_file.name}")
                log.msg(_LOG_PREFIX, f"  mmproj: {Path(mmproj_file).name}")
                
                try:
                    from llama_cpp.llama_chat_format import Llava16ChatHandler #type: ignore
                    chat_handler = Llava16ChatHandler(clip_model_path=mmproj_file)
                    
                    model = Llama(
                        model_path=str(model_file),
                        chat_handler=chat_handler,
                        n_ctx=context_size,
                        n_gpu_layers=n_gpu_layers,
                        n_batch=n_batch,
                        verbose=False,
                    )
                    # Store chat_handler reference for proper VRAM cleanup later
                    setattr(model, '_sml_chat_handler', chat_handler)
                    # Cache model if keep_model_loaded is enabled
                    if keep_model_loaded:
                        set_cached_gguf_model(gguf_cache_key, model, chat_handler, ModelType.MISTRAL3)
                    return model, None, ModelType.MISTRAL3  # Vision-capable Mistral
                except ValueError as e:
                    error_msg = str(e)
                    if "unknown model architecture" in error_msg.lower():
                        # Extract architecture name from error
                        arch_name = error_msg.split(":")[-1].strip().strip("'")
                        raise ValueError(
                            f"Model architecture '{arch_name}' is not yet supported by llama-cpp-python. "
                            f"This is a new architecture that requires a newer version of llama.cpp. "
                            f"Options: 1) Wait for llama-cpp-python update, 2) Use 'Transformers' loading method instead, "
                            f"3) Build llama-cpp-python from source with latest llama.cpp"
                        )
                    log.warning(_LOG_PREFIX, f"Failed to load with vision support: {e}")
                    log.warning(_LOG_PREFIX, "Falling back to text-only mode")
                except Exception as e:
                    log.warning(_LOG_PREFIX, f"Failed to load with vision support: {e}")
                    log.warning(_LOG_PREFIX, "Falling back to text-only mode")
            
            # Text-only LLM
            log.msg(_LOG_PREFIX, f"Loading LLM GGUF: {model_file.name}")
            try:
                model = Llama(
                    model_path=str(model_file),
                    n_ctx=context_size,
                    n_gpu_layers=n_gpu_layers,
                    n_batch=n_batch,
                    verbose=False,
                )
                # Cache model if keep_model_loaded is enabled
                if keep_model_loaded:
                    set_cached_gguf_model(gguf_cache_key, model, None, ModelType.LLM)
                return model, None, ModelType.LLM
            except ValueError as e:
                error_msg = str(e)
                if "unknown model architecture" in error_msg.lower():
                    arch_name = error_msg.split(":")[-1].strip().strip("'")
                    raise ValueError(
                        f"Model architecture '{arch_name}' is not yet supported by llama-cpp-python. "
                        f"This is a new architecture that requires a newer version of llama.cpp. "
                        f"Options: 1) Wait for llama-cpp-python update, 2) Use 'Transformers' loading method instead, "
                        f"3) Build llama-cpp-python from source with latest llama.cpp"
                    )
                raise
        
        elif family == ModelFamily.LLAVA:
            # LLaVA vision models with mmproj
            from llama_cpp import Llama #type: ignore
            
            # Get mmproj file for vision support - always search and update template
            mmproj_file = ensure_mmproj_path(
                ctx.to_dict(), 
                str(model_file.parent),
            )
            
            if not mmproj_file:
                raise ValueError(
                    f"LLaVA requires an mmproj file for vision support. "
                    f"Please provide mmproj_url in the template or place the mmproj file in the model folder."
                )
            
            log.msg(_LOG_PREFIX, f"Loading LLaVA GGUF (vision): {model_file.name}")
            log.msg(_LOG_PREFIX, f"  mmproj: {Path(mmproj_file).name}")
            
            try:
                from llama_cpp.llama_chat_format import Llava16ChatHandler #type: ignore
                chat_handler = Llava16ChatHandler(clip_model_path=mmproj_file)
                
                model = Llama(
                    model_path=str(model_file),
                    chat_handler=chat_handler,
                    n_ctx=context_size,
                    n_gpu_layers=n_gpu_layers,
                    n_batch=n_batch,
                    verbose=False,
                )
                # Store chat_handler reference for proper VRAM cleanup later
                setattr(model, '_sml_chat_handler', chat_handler)
                # Cache model if keep_model_loaded is enabled
                if keep_model_loaded:
                    set_cached_gguf_model(gguf_cache_key, model, chat_handler, ModelType.LLAVA)
                return model, None, ModelType.LLAVA
            except ValueError as e:
                error_msg = str(e)
                if "unknown model architecture" in error_msg.lower():
                    arch_name = error_msg.split(":")[-1].strip().strip("'")
                    raise ValueError(
                        f"Model architecture '{arch_name}' is not yet supported by llama-cpp-python. "
                        f"This is a new architecture that requires a newer version of llama.cpp. "
                        f"Options: 1) Wait for llama-cpp-python update, 2) Use 'Ollama (Docker)' loading method instead, "
                        f"3) Build llama-cpp-python from source with latest llama.cpp"
                    )
                raise
        
        else:
            raise ValueError(f"GGUF not supported for {model_family}")
    
    elif method == LoadingMethod.OLLAMA_DOCKER:
        # Ollama Docker backend - supports GGUF files OR Ollama registry models
        from . import backend_ollama_docker
        
        context_size = kwargs.get("context_size", 8192)

        # Check if this is an Ollama registry model template
        model_source = ctx.model_source
        ollama_model_from_template = ctx.ollama_model
        
        if model_source and model_source.lower() == "ollama" and ollama_model_from_template:
            # Template specifies an Ollama registry model directly
            log.debug(_LOG_PREFIX, f"Using Ollama registry model: {ollama_model_from_template}")
            ollama_model_name = ollama_model_from_template
            use_gguf = False
            
            ollama_info = backend_ollama_docker.load_ollama(
                model_path=ollama_model_name,
                model_type="llm" if family == ModelFamily.LLM_TEXT else "vlm",
                use_gguf=False,
            )
            
            if ollama_info is None:
                raise RuntimeError(
                    f"Ollama Docker failed to load registry model: {ollama_model_name}\n\n"
                    f"Solutions:\n"
                    f"  1. Ensure Docker is installed and running\n"
                    f"  2. The model will be auto-pulled from Ollama registry on first use\n"
                    f"  3. Check your internet connection"
                )
            
            wrapper = OllamaWrapper(ollama_info, context_size=context_size)
            
            if family == ModelFamily.MISTRAL:
                return wrapper, None, ModelType.MISTRAL3
            elif family == ModelFamily.QWEN:
                return wrapper, None, ModelType.QWENVL
            elif family == ModelFamily.LLAVA:
                # LLAVA family includes both LLaVA and Mllama - Ollama handles detection internally
                return wrapper, None, ModelType.LLAVA
            elif family == ModelFamily.VLM or ctx.has_vision:
                # VLM family or unknown family with vision — use generic LLAVA execution type
                return wrapper, None, ModelType.LLAVA
            else:
                return wrapper, None, ModelType.LLM
        
        # Try to find GGUF file OR infer Ollama registry model
        model_file = Path(model_path)
        use_gguf = False
        ollama_model_name = None
        
        if model_file.is_dir():
            # Check for GGUF files in directory
            gguf_files = list(model_file.glob("*.gguf"))
            if gguf_files:
                # Select best GGUF file
                try:
                    model_file = _select_best_gguf(model_file)
                except FileNotFoundError:
                    model_file = gguf_files[0]
                use_gguf = True
            elif backend_ollama_docker.is_hf_model_directory(model_path):
                # HuggingFace Safetensors model - try to import into Ollama
                log.debug(_LOG_PREFIX, f"HuggingFace model detected, attempting to import into Ollama...")
                imported_name = backend_ollama_docker.import_hf_model_to_ollama(
                    model_path,
                    quantize="q4_K_M",  # Default quantization for efficiency
                )
                if imported_name:
                    ollama_model_name = imported_name
                    use_gguf = False
                else:
                    # Import failed, try to infer Ollama registry model
                    ollama_model_name = backend_ollama_docker.infer_ollama_model_name(model_path)
                    if not ollama_model_name:
                        raise ValueError(
                            f"Failed to import HuggingFace model into Ollama.\n\n"
                            f"The model at {model_path} could not be converted.\n"
                            f"Supported architectures: Llama, Mistral, Gemma, Phi3\n\n"
                            f"Alternatives:\n"
                            f"  1. Use 'Transformers' backend for native HF model loading\n"
                            f"  2. Use 'vLLM (Docker)' backend for HF models\n"
                            f"  3. Download a GGUF version and use 'llama.cpp (Docker)'"
                        )
            else:
                # No GGUF files, not a HF model - try to infer Ollama registry model
                ollama_model_name = backend_ollama_docker.infer_ollama_model_name(model_path)
                if not ollama_model_name:
                    raise ValueError(
                        f"No GGUF files in {model_path} and could not infer Ollama model name.\n\n"
                        f"For HuggingFace models without GGUF, Ollama (Docker) needs a matching\n"
                        f"model in the Ollama registry. Try:\n"
                        f"  1. Download a GGUF version of this model\n"
                        f"  2. Use 'vLLM (Docker)' or 'Transformers' backend for HF models\n"
                        f"  3. Check if an equivalent model exists in Ollama registry"
                    )
        elif str(model_file).lower().endswith('.gguf'):
            use_gguf = True
        else:
            # Single file that's not GGUF - try to infer Ollama model name
            ollama_model_name = backend_ollama_docker.infer_ollama_model_name(model_path)
            if not ollama_model_name:
                raise ValueError(
                    f"Ollama (Docker) requires a GGUF file or matching Ollama registry model.\n"
                    f"Got: {model_file}\n\n"
                    f"For HuggingFace safetensor models, use 'Transformers' or 'vLLM (Docker)' backend."
                )
        
        # Ensure model_family from widget is set on context
        ctx.model_family = model_family
        
        ollama_info = backend_ollama_docker.load_ollama(
            model_path=str(model_file) if use_gguf else (ollama_model_name or ""),
            model_type="llm" if family == ModelFamily.LLM_TEXT else "vlm",
            use_gguf=use_gguf,
            ctx=ctx,
        )
        
        if ollama_info is None:
            raise RuntimeError(
                "Ollama Docker not available or failed to load model.\n\n"
                "Solutions:\n"
                "  1. Ensure Docker is installed and running\n"
                "  2. Check that the GGUF file exists\n"
                "  3. Try 'llama.cpp (Docker)' backend as alternative"
            )
        
        wrapper = OllamaWrapper(ollama_info, context_size=context_size)
        
        if family == ModelFamily.MISTRAL:
            return wrapper, None, ModelType.MISTRAL3
        elif family == ModelFamily.QWEN:
            return wrapper, None, ModelType.QWENVL
        else:
            return wrapper, None, ModelType.LLM
    
    elif method == LoadingMethod.LLAMACPP_DOCKER:
        # llama.cpp Docker backend - supports Mistral3 GGUF models
        from . import backend_llamacpp_docker
        
        context_size = kwargs.get("context_size", 8192)

        # Find GGUF file in model path
        model_file = Path(model_path)
        if model_file.is_dir():
            model_file = _select_best_gguf(model_file)
        
        if not str(model_file).lower().endswith('.gguf'):
            raise ValueError(f"llama.cpp (Docker) requires a GGUF model file, got: {model_file}")
        
        # Get mmproj path from ctx if available
        mmproj_file = ctx.mmproj_path if ctx.mmproj_path else None
        if not mmproj_file:
            # Try to get mmproj_url and download it
            mmproj_file = ensure_mmproj_path(
                ctx.to_dict(), 
                str(model_file.parent),
            )
        
        # Get models base path for correct Docker mount
        models_base = str(get_llm_models_path())
        
        llamacpp_info = backend_llamacpp_docker.load_llamacpp(
            model_path=str(model_file),
            model_type="llm" if family == ModelFamily.LLM_TEXT else "vlm",
            ctx_size=context_size,
            models_base_path=models_base,
            mmproj_path=mmproj_file,
        )
        
        if llamacpp_info is None:
            raise RuntimeError(
                "llama.cpp Docker not available or failed to load model.\n\n"
                "Solutions:\n"
                "  1. Ensure Docker is installed and running\n"
                "  2. Check that the GGUF file exists\n"
                "  3. Try 'Ollama (Docker)' backend as alternative"
            )
        
        wrapper = LlamaCppWrapper(llamacpp_info)
        
        if family == ModelFamily.MISTRAL:
            return wrapper, None, ModelType.MISTRAL3
        elif family == ModelFamily.QWEN:
            return wrapper, None, ModelType.QWENVL
        else:
            return wrapper, None, ModelType.LLM
    
    else:  # LoadingMethod.TRANSFORMERS
        # Transformers backend - load models directly
        quantization = kwargs.get('quantization', 'auto')
        attention_mode = kwargs.get('attention_mode', 'auto')
        device = kwargs.get('device', 'cuda')
        
        # Auto-select attention mode if 'auto'
        if attention_mode == "auto":
            attn_impl = auto_select_attention()
        else:
            attn_impl = attention_mode
        
        # Check if model is pre-quantized using multiple methods:
        # 1. Inspect config.json for quantization_config (most reliable)
        # 2. Check template's quantized flag
        # 3. Check filename markers (fallback)
        model_name = Path(model_path).name
        
        # Primary: Check actual model files (config.json, params.json)
        is_prequantized, quant_type = detect_prequantized_model(Path(model_path))
        
        # Secondary: Template flag (may be user-set, less reliable alone)
        template_is_quantized = ctx.quantized
        
        # Combine: Trust file inspection, but warn if template disagrees
        if is_prequantized and not template_is_quantized:
            log.debug(_LOG_PREFIX, f"Model detected as pre-quantized ({quant_type}) but template says quantized=false")
        elif not is_prequantized and template_is_quantized:
            # Trust template if file inspection didn't find quantization
            # (some formats may not have detectable markers)
            is_prequantized = True
            quant_type = "unknown"
            log.debug(_LOG_PREFIX, f"Template says quantized=true but no quantization config detected in files")
        
        # Handle quantization selection
        if is_prequantized:
            # Pre-quantized model - don't apply additional quantization (would break/corrupt)
            if quantization in ["4bit", "8bit"]:
                log.warning(_LOG_PREFIX, f"Model is pre-quantized ({quant_type}), ignoring {quantization} request")
            # For FP8: we'll use FineGrainedFP8Config(dequantize=True) to convert to BF16
            # For others: load as-is with native dtype handling
            if quant_type == "fp8":
                quantization = "fp8"  # Actual FP8 loading handled via is_fp8_model branch
                log.msg(_LOG_PREFIX, f"Pre-quantized model ({quant_type}), will dequantize to BF16")
            else:
                quantization = "fp16"  # Non-FP8 pre-quantized: load with native dtype
                log.msg(_LOG_PREFIX, f"Pre-quantized model ({quant_type}), loading with native dtype")
        elif quantization == "auto":
            # Auto-select based on model size vs available VRAM
            model_size_gb = calculate_model_size(Path(model_path))
            quantization = auto_select_quantization(
                model_name=model_name,
                estimated_size_gb=model_size_gb,
            )
        
        if family in (ModelFamily.QWEN, ModelFamily.MISTRAL, ModelFamily.LLAVA, ModelFamily.VLM):
            # Unified VLM loader for Qwen VL, Mistral VL, LLaVA, Mllama, and generic VLM.
            # _load_vlm_transformers auto-detects the actual arch from config.json, so a
            # generic ModelFamily.VLM tag (used by user_models.json entries that don't
            # know the exact family) routes through the same path.
            # Filter out kwargs already passed as explicit arguments to avoid duplicates
            vlm_kwargs = {k: v for k, v in kwargs.items() if k not in (
                'quantization', 'attention_mode', 'keep_model_loaded', 'device', 'memory_cleanup'
            )}
            return _load_vlm_transformers(
                model_path=model_path,
                quantization=quantization,
                attn_impl=attn_impl,
                is_prequantized=is_prequantized,
                quant_type=quant_type,
                keep_model_loaded=keep_model_loaded,
                resolved_quantization=resolved_quantization,
                resolved_attention=resolved_attention,
                **vlm_kwargs
            )
        
        elif family == ModelFamily.FLORENCE:
            # Load Florence-2 with transformers
            # Build load kwargs for florence2_wrapper
            florence_kwargs: dict[str, Any] = {"low_cpu_mem_usage": True}
            if attn_impl:
                florence_kwargs["attn_implementation"] = attn_impl
            
            # Florence-2 supports BitsAndBytes quantization for lower VRAM usage
            if quantization == "4bit":
                from transformers import BitsAndBytesConfig #type: ignore
                florence_kwargs["quantization_config"] = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype=torch.float16,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True,
                )
                florence_kwargs["device_map"] = {"": 0}  # All to GPU 0 for BitsAndBytes
            elif quantization == "8bit":
                from transformers import BitsAndBytesConfig #type: ignore
                florence_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
                florence_kwargs["device_map"] = {"": 0}  # All to GPU 0 for BitsAndBytes
            else:
                # Non-quantized: let ComfyUI handle offload
                dtype_map = {
                    "fp16": torch.float16,
                    "bf16": torch.bfloat16,
                    "fp32": torch.float32,
                    "auto": "auto",
                }
                florence_kwargs[dtype_kwarg()] = dtype_map.get(quantization, "auto")
                florence_kwargs["device_map"] = None  # ComfyUI handles memory
            
            model = florence2_wrapper.load_florence2_model(model_path, trust_remote_code=bool(kwargs.get("trust_remote_code", False)), **florence_kwargs)
            processor = florence2_wrapper.load_florence2_processor(model_path, trust_remote_code=bool(kwargs.get("trust_remote_code", False)))
            
            # Apply torch.compile if requested (non-quantized only)
            use_torch_compile = kwargs.get('use_torch_compile', False)
            is_quantized = quantization in ["4bit", "8bit"]
            if use_torch_compile and not is_quantized and torch.cuda.is_available():
                try:
                    model = torch.compile(model, mode="reduce-overhead")
                    log.msg(_LOG_PREFIX, "✓ Applied torch.compile optimization")
                except Exception as e:
                    log.warning(_LOG_PREFIX, f"torch.compile failed: {e}")
            elif use_torch_compile and is_quantized:
                log.debug(_LOG_PREFIX, "  torch.compile skipped (not compatible with quantization)")
            
            # Cache model if keep_model_loaded is enabled
            if keep_model_loaded:
                cache_key = get_transformers_cache_key(model_path, resolved_quantization, resolved_attention)
                set_cached_transformers_model(cache_key, model, processor, ModelType.FLORENCE2)
            
            return model, processor, ModelType.FLORENCE2
        
        elif family == ModelFamily.LLM_TEXT:
            # Load text-only LLM with transformers
            from transformers import AutoTokenizer, AutoModelForCausalLM #type: ignore
            
            log.msg(_LOG_PREFIX, f"Loading LLM ({quantization}, {attn_impl})")
            
            # Build common kwargs
            load_kwargs: dict[str, Any] = {"device_map": "auto"}
            if attn_impl:
                load_kwargs["attn_implementation"] = attn_impl
            
            if quantization == "4bit":
                from transformers import BitsAndBytesConfig #type: ignore
                load_kwargs["quantization_config"] = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype=torch.float16,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True,
                )
                model = AutoModelForCausalLM.from_pretrained(model_path, **load_kwargs)
            elif quantization == "8bit":
                from transformers import BitsAndBytesConfig #type: ignore
                load_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
                model = AutoModelForCausalLM.from_pretrained(model_path, **load_kwargs)
            else:
                dtype_map = {
                    "fp16": torch.float16,
                    "bf16": torch.bfloat16,
                    "fp32": torch.float32,
                    "auto": "auto",
                }
                load_kwargs[dtype_kwarg()] = dtype_map.get(quantization, "auto")
                model = AutoModelForCausalLM.from_pretrained(model_path, **load_kwargs)
            
            tokenizer = AutoTokenizer.from_pretrained(model_path)
            
            # Apply torch.compile if requested (non-quantized only)
            use_torch_compile = kwargs.get('use_torch_compile', False)
            is_quantized = quantization in ["4bit", "8bit"]
            if use_torch_compile and not is_quantized and torch.cuda.is_available():
                try:
                    model = torch.compile(model, mode="reduce-overhead")
                    log.msg(_LOG_PREFIX, "✓ Applied torch.compile optimization")
                except Exception as e:
                    log.warning(_LOG_PREFIX, f"torch.compile failed: {e}")
            elif use_torch_compile and is_quantized:
                log.debug(_LOG_PREFIX, "  torch.compile skipped (not compatible with quantization)")
            
            # Cache model if keep_model_loaded is enabled
            if keep_model_loaded:
                cache_key = get_transformers_cache_key(model_path, resolved_quantization, resolved_attention)
                set_cached_transformers_model(cache_key, model, tokenizer, ModelType.LLM)
            
            return model, tokenizer, ModelType.LLM
        
        else:
            raise ValueError(f"Unknown model family: {model_family}")

