# SmartLM Device - VRAM, GPU, and Memory Management
#
# Handles device detection and memory management:
# - VRAM/GPU detection and info
# - Memory cleanup functions
# - Device capability checks
# - llama-cpp-python availability
# - Temporary file cleanup
#
# Used by the SmartLM core loader and related modules (device/VRAM helpers).

import torch #type: ignore
import psutil
from pathlib import Path
from typing import Dict, Optional, Any

from .logger import log

_LOG_PREFIX = "Device"


# ============================================================================
# llama-cpp-python Availability Check
# ============================================================================

LLAMA_CPP_AVAILABLE = False


def _check_llama_cpp():
    # Check for llama-cpp-python availability.
    global LLAMA_CPP_AVAILABLE
    
    try:
        import llama_cpp #type: ignore
        LLAMA_CPP_AVAILABLE = True
        return True
    except ImportError:
        LLAMA_CPP_AVAILABLE = False
        return False


# Initialize on module load
_check_llama_cpp()


# ============================================================================
# Device Detection Functions
# ============================================================================

def get_gpu_info() -> Dict[str, Any]:
    # Get information about all available GPUs.
    #
    # Returns:
    #     Dict with:
    #         - gpu_count: Number of GPUs
    #         - gpus: List of dicts with 'index', 'name', 'vram_gb', 'free_gb' per GPU
    #         - total_vram_gb: Combined VRAM of all GPUs
    #         - min_vram_gb: Smallest GPU VRAM (bottleneck for tensor parallelism)
    result: Dict[str, Any] = {
        "gpu_count": 0,
        "gpus": [],
        "total_vram_gb": 0.0,
        "min_vram_gb": 0.0,
    }
    
    if not torch.cuda.is_available():
        return result
    
    try:
        gpu_count = torch.cuda.device_count()
        result["gpu_count"] = gpu_count
        
        min_vram = float('inf')
        total_vram = 0
        
        for i in range(gpu_count):
            props = torch.cuda.get_device_properties(i)
            vram_gb = props.total_memory / (1024**3)
            
            # Get free memory for this GPU
            try:
                free_bytes, _ = torch.cuda.mem_get_info(i)
                free_gb = free_bytes / (1024**3)
            except (AttributeError, RuntimeError):
                # Fallback for older PyTorch or if device not accessible
                free_gb = vram_gb * 0.9  # Estimate 90% free
            
            result["gpus"].append({
                "index": i,
                "name": props.name,
                "vram_gb": round(vram_gb, 2),
                "free_gb": round(free_gb, 2),
            })
            
            total_vram += vram_gb
            min_vram = min(min_vram, vram_gb)
        
        result["total_vram_gb"] = round(total_vram, 2)
        result["min_vram_gb"] = round(min_vram, 2) if min_vram != float('inf') else 0.0
        
    except Exception as e:
        log.debug(_LOG_PREFIX, f"GPU detection failed: {e}")
    
    return result


def estimate_model_size_gb(model_path: str) -> float:
    # Estimate model size in GB from weight files.
    #
    # Handles models that may have multiple formats (Mistral-native consolidated.safetensors
    # AND HuggingFace sharded model-*.safetensors) - avoids double-counting.
    #
    # Priority:
    # 1. consolidated.safetensors (Mistral-native) - if exists, use this
    # 2. model.safetensors or model-*.safetensors (HuggingFace) - sharded or single
    # 3. *.bin files (older PyTorch format)
    # 4. *.gguf files (quantized)
    #
    # Args:
    #     model_path: Path to model folder or file
    #
    # Returns:
    #     Estimated model size in GB
    total_size_gb = 0
    try:
        model_folder = Path(model_path)
        
        # Handle single file (e.g., GGUF)
        if model_folder.is_file():
            return round(model_folder.stat().st_size / (1024**3), 2)
        
        if not model_folder.exists():
            return 0
        
        # Check for Mistral-native format first (consolidated.safetensors)
        consolidated = model_folder / "consolidated.safetensors"
        if consolidated.exists():
            # Use consolidated.safetensors as the model size
            # This is the Mistral-native format that vLLM will actually load
            total_size_gb = consolidated.stat().st_size / (1024**3)
            log.debug(_LOG_PREFIX, f"Using consolidated.safetensors size: {total_size_gb:.2f}GB")
            return round(total_size_gb, 2)
        
        # Check for HuggingFace format (model.safetensors or sharded model-*.safetensors)
        hf_single = model_folder / "model.safetensors"
        hf_sharded = list(model_folder.glob("model-*.safetensors"))
        
        if hf_single.exists():
            total_size_gb = hf_single.stat().st_size / (1024**3)
        elif hf_sharded:
            for f in hf_sharded:
                total_size_gb += f.stat().st_size / (1024**3)
        else:
            # Fallback: sum all safetensors (older or non-standard naming)
            for f in model_folder.glob("*.safetensors"):
                total_size_gb += f.stat().st_size / (1024**3)
        
        # Also check for .bin files if no safetensors found
        if total_size_gb == 0:
            for f in model_folder.glob("*.bin"):
                # Skip optimizer/training files
                if "optimizer" in f.name.lower() or "training" in f.name.lower():
                    continue
                total_size_gb += f.stat().st_size / (1024**3)
        
        # Also check for GGUF files
        if total_size_gb == 0:
            for f in model_folder.glob("*.gguf"):
                total_size_gb += f.stat().st_size / (1024**3)
                
    except Exception as e:
        log.debug(_LOG_PREFIX, f"Could not estimate model size: {e}")
    
    return round(total_size_gb, 2)


def check_model_fits(
    model_path: str,
    gpu_memory_utilization: float = 0.9,
    tensor_parallel_size: int = 1,
) -> Dict[str, Any]:
    # Check if a model will fit in available VRAM.
    #
    # Args:
    #     model_path: Path to model folder or file
    #     gpu_memory_utilization: Fraction of VRAM to use (0.0-1.0)
    #     tensor_parallel_size: Number of GPUs to use for tensor parallelism
    #
    # Returns:
    #     Dict with:
    #         - fits: bool - Whether model will fit
    #         - model_size_gb: Estimated model size
    #         - estimated_required_gb: Model + overhead
    #         - available_vram_gb: Usable VRAM with utilization setting
    #         - gpu_info: Full GPU info dict
    #         - suggested_tensor_parallel: Recommended tensor parallel size
    #         - message: Human-readable status
    gpu_info = get_gpu_info()
    model_size_gb = estimate_model_size_gb(model_path)
    
    # Model weights + ~30% for KV cache/activations overhead
    overhead_multiplier = 1.3
    estimated_required_gb = model_size_gb * overhead_multiplier
    
    result = {
        "fits": False,
        "model_size_gb": model_size_gb,
        "estimated_required_gb": round(estimated_required_gb, 2),
        "available_vram_gb": 0,
        "gpu_info": gpu_info,
        "suggested_tensor_parallel": 1,
        "message": "",
    }
    
    if gpu_info["gpu_count"] == 0:
        result["message"] = "No GPUs detected"
        return result
    
    # Calculate available VRAM based on tensor parallelism
    if tensor_parallel_size <= 1:
        # Single GPU: use first GPU's VRAM
        available_vram = gpu_info["gpus"][0]["vram_gb"] * gpu_memory_utilization
    else:
        # Multi-GPU: limited by smallest GPU (tensor parallelism splits model)
        # With tensor parallelism, model is split across GPUs
        tp_size = min(tensor_parallel_size, gpu_info["gpu_count"])
        available_vram = gpu_info["min_vram_gb"] * gpu_memory_utilization * tp_size
    
    result["available_vram_gb"] = round(available_vram, 2)
    
    # Check if model fits
    if estimated_required_gb <= available_vram:
        result["fits"] = True
        result["message"] = f"Model ({model_size_gb:.1f}GB) fits in available VRAM ({available_vram:.1f}GB)"
    else:
        # Model doesn't fit with current settings - calculate what would work
        result["fits"] = False
        
        # Check if tensor parallelism could help
        if gpu_info["gpu_count"] > 1:
            for tp in range(2, gpu_info["gpu_count"] + 1):
                potential_vram = gpu_info["min_vram_gb"] * gpu_memory_utilization * tp
                if estimated_required_gb <= potential_vram:
                    result["suggested_tensor_parallel"] = tp
                    result["message"] = (
                        f"Model ({model_size_gb:.1f}GB, needs ~{estimated_required_gb:.1f}GB) "
                        f"exceeds single GPU VRAM ({available_vram:.1f}GB). "
                        f"Consider using tensor_parallel_size={tp} with {gpu_info['gpu_count']} GPUs."
                    )
                    break
            else:
                # Even with all GPUs, model won't fit
                max_vram = gpu_info["min_vram_gb"] * gpu_memory_utilization * gpu_info["gpu_count"]
                result["message"] = (
                    f"Model ({model_size_gb:.1f}GB, needs ~{estimated_required_gb:.1f}GB) "
                    f"exceeds total available VRAM ({max_vram:.1f}GB across {gpu_info['gpu_count']} GPUs). "
                    f"Consider using a quantized (GGUF) version or a smaller model."
                )
        else:
            # Single GPU and model doesn't fit
            result["message"] = (
                f"Model ({model_size_gb:.1f}GB, needs ~{estimated_required_gb:.1f}GB) "
                f"exceeds available VRAM ({available_vram:.1f}GB on single GPU). "
                f"Consider using a quantized (GGUF) version or a smaller model."
            )
    
    return result


def get_device_info() -> Dict[str, Any]:
    # Get comprehensive device and memory information.
    #
    # Returns:
    #     Dict with gpu, system_memory, device_type, recommended_device, device_name
    gpu_info = {"available": False, "total_memory": 0, "free_memory": 0}
    device_type = "cpu"
    recommended_device = "cpu"
    
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        total = props.total_memory / (1024**3)
        
        # Get actual free memory (not just PyTorch allocations)
        # memory_reserved = CUDA memory reserved by PyTorch (includes cached/unused)
        # memory_allocated = actively used by tensors
        # For true free: use torch.cuda.mem_get_info() which reports actual GPU free memory
        try:
            free_bytes, total_bytes = torch.cuda.mem_get_info(0)
            free_memory = free_bytes / (1024**3)
        except AttributeError:
            # Fallback for older PyTorch versions
            reserved = torch.cuda.memory_reserved(0) / (1024**3)
            free_memory = total - reserved
        
        gpu_info = {
            "available": True,
            "total_memory": total,
            "free_memory": free_memory,
        }
        device_type = "cuda"
        recommended_device = "cuda"
    elif hasattr(torch.backends, 'mps') and hasattr(torch.backends.mps, 'is_available') and torch.backends.mps.is_available():
        device_type = "mps"
        recommended_device = "mps"
        gpu_info = {"available": True, "total_memory": 0, "free_memory": 0}
    
    sys_mem = psutil.virtual_memory()
    return {
        "gpu": gpu_info,
        "system_memory": {
            "total": sys_mem.total / (1024**3),
            "available": sys_mem.available / (1024**3),
        },
        "device_type": device_type,
        "recommended_device": recommended_device,
        "device": recommended_device,
        "device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU",
    }


def get_available_devices() -> list:
    # Get list of available compute devices for dropdown.
    # Returns device names that can be used in model loading.
    #
    # Returns:
    #     List of device strings, e.g. ["cuda", "cpu"] or ["mps", "cpu"]
    devices = []
    
    # Check for CUDA (NVIDIA) - also covers AMD ROCm which uses torch.cuda API
    if torch.cuda.is_available():
        devices.append("cuda")
    
    # Check for MPS (Apple Silicon)
    if hasattr(torch.backends, 'mps') and hasattr(torch.backends.mps, 'is_available'):
        if torch.backends.mps.is_available():
            devices.append("mps")
    
    # CPU is always available as fallback
    devices.append("cpu")
    
    return devices


# ============================================================================
# GPU Vendor Detection (NVIDIA vs AMD/ROCm)
# ============================================================================

# Cache for GPU vendor detection (expensive to check repeatedly)
_gpu_vendor_cache: Optional[str] = None


def detect_gpu_vendor() -> str:
    # Detect GPU vendor: "nvidia", "amd", or "none".
    #
    # Detection methods (in order):
    # 1. Check torch.version.hip (AMD ROCm PyTorch build)
    # 2. Check torch.version.cuda (NVIDIA PyTorch build)
    # 3. Check GPU device name for vendor strings
    # 4. Check for /dev/kfd (AMD ROCm kernel device)
    # 5. Check nvidia-smi availability
    #
    # Returns:
    #     "nvidia", "amd", or "none"
    global _gpu_vendor_cache
    
    if _gpu_vendor_cache is not None:
        return _gpu_vendor_cache
    
    import subprocess
    import platform
    
    # Method 1: Check PyTorch build type
    # ROCm PyTorch has torch.version.hip set
    if hasattr(torch.version, 'hip') and torch.version.hip is not None:
        _gpu_vendor_cache = "amd"
        log.debug(_LOG_PREFIX, f"GPU vendor detected via torch.version.hip: AMD ROCm ({torch.version.hip})")
        return "amd"
    
    # Method 2: Check if CUDA is available (could be NVIDIA or ROCm HIP)
    if not torch.cuda.is_available():
        _gpu_vendor_cache = "none"
        log.debug(_LOG_PREFIX, "No GPU detected (torch.cuda not available)")
        return "none"
    
    # Method 3: Check GPU name for vendor strings
    try:
        gpu_name = torch.cuda.get_device_name(0).lower()
        if "nvidia" in gpu_name or "geforce" in gpu_name or "rtx" in gpu_name or "gtx" in gpu_name or "quadro" in gpu_name or "tesla" in gpu_name:
            _gpu_vendor_cache = "nvidia"
            log.debug(_LOG_PREFIX, f"GPU vendor detected via device name: NVIDIA ({torch.cuda.get_device_name(0)})")
            return "nvidia"
        if "amd" in gpu_name or "radeon" in gpu_name or "rx " in gpu_name or "vega" in gpu_name or "navi" in gpu_name:
            _gpu_vendor_cache = "amd"
            log.debug(_LOG_PREFIX, f"GPU vendor detected via device name: AMD ({torch.cuda.get_device_name(0)})")
            return "amd"
    except Exception:
        pass
    
    # Method 4: Linux-specific device file checks
    if platform.system() == "Linux":
        # Check for AMD ROCm device
        try:
            from pathlib import Path
            if Path("/dev/kfd").exists():
                _gpu_vendor_cache = "amd"
                log.debug(_LOG_PREFIX, "GPU vendor detected via /dev/kfd: AMD ROCm")
                return "amd"
        except Exception:
            pass
    
    # Method 5: Check for nvidia-smi (NVIDIA driver tool)
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            timeout=3,
            text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            _gpu_vendor_cache = "nvidia"
            log.debug(_LOG_PREFIX, f"GPU vendor detected via nvidia-smi: NVIDIA ({result.stdout.strip()})")
            return "nvidia"
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass
    
    # Method 6: Check for rocm-smi (AMD ROCm tool)
    try:
        result = subprocess.run(
            ["rocm-smi", "--showproductname"],
            capture_output=True,
            timeout=3,
            text=True
        )
        if result.returncode == 0:
            _gpu_vendor_cache = "amd"
            log.debug(_LOG_PREFIX, "GPU vendor detected via rocm-smi: AMD ROCm")
            return "amd"
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass
    
    # Default: assume NVIDIA if CUDA is available but vendor unclear
    # This is the safest assumption for Docker --gpus flag
    if torch.cuda.is_available():
        _gpu_vendor_cache = "nvidia"
        log.debug(_LOG_PREFIX, "GPU vendor unknown but CUDA available, assuming NVIDIA")
        return "nvidia"
    
    _gpu_vendor_cache = "none"
    return "none"


def get_docker_gpu_args() -> list:
    # Get Docker command-line arguments for GPU access.
    #
    # Returns appropriate flags based on GPU vendor:
    # - NVIDIA: ["--gpus", "all"]
    # - AMD/ROCm: ["--device=/dev/kfd", "--device=/dev/dri", "--group-add", "video"]
    # - None: [] (no GPU flags)
    #
    # Returns:
    #     List of Docker command-line arguments for GPU access
    vendor = detect_gpu_vendor()
    
    if vendor == "nvidia":
        return ["--gpus", "all"]
    elif vendor == "amd":
        # ROCm Docker GPU access
        # /dev/kfd - Kernel Fusion Driver (compute)
        # /dev/dri - Direct Rendering Infrastructure (needed for GPU access)
        # video group - required for GPU access on most distros
        return ["--device=/dev/kfd", "--device=/dev/dri", "--group-add", "video"]
    else:
        return []


def get_docker_image_for_vendor(base_image: str, vendor: Optional[str] = None) -> str:
    # Get appropriate Docker image based on GPU vendor.
    #
    # Returns ROCm-optimized images for AMD GPUs when available.
    #
    # Image mappings (NVIDIA → AMD/ROCm):
    # - vllm/vllm-openai:latest → rocm/vllm:latest (official ROCm repository)
    # - lmsysorg/sglang:latest → lmsysorg/sglang:v0.5.9-rocm720-mi30x (versioned, arch-specific)
    # - ollama/ollama → ollama/ollama:rocm (official Ollama ROCm image)
    # - ghcr.io/ggml-org/llama.cpp:server-cuda → ghcr.io/ggml-org/llama.cpp:server (CPU, no ROCm image)
    #
    # Note: SGLang ROCm images are architecture-specific (mi30x=MI300X, mi35x=MI350X)
    # and version-pinned. No :latest tag exists. Update version when new releases appear.
    #
    # Args:
    #     base_image: Base Docker image name (NVIDIA/default version)
    #     vendor: GPU vendor ("nvidia", "amd") or None to auto-detect
    #
    # Returns:
    #     Appropriate Docker image for the GPU vendor
    if vendor is None:
        vendor = detect_gpu_vendor()
    
    if vendor != "amd":
        return base_image
    
    # ROCm image mappings (NVIDIA image → AMD/ROCm image)
    rocm_images = {
        # Ollama - official ROCm support
        "ollama/ollama": "ollama/ollama:rocm",
        "ollama/ollama:latest": "ollama/ollama:rocm",
        # vLLM - official ROCm repository image
        "vllm/vllm-openai": "rocm/vllm:latest",
        "vllm/vllm-openai:latest": "rocm/vllm:latest",
        # SGLang - official ROCm image (versioned, arch-specific, no :latest)
        "lmsysorg/sglang:latest": "lmsysorg/sglang:v0.5.9-rocm720-mi30x",
        "lmsysorg/sglang": "lmsysorg/sglang:v0.5.9-rocm720-mi30x",
        # llama.cpp - no official ROCm image, fall back to CPU
        "ghcr.io/ggml-org/llama.cpp:server-cuda": "ghcr.io/ggml-org/llama.cpp:server",
    }
    
    return rocm_images.get(base_image, base_image)


# ============================================================================
# Attention Mode Auto-Selection
# ============================================================================

def auto_select_attention() -> str:
    # Auto-select attention implementation based on availability.
    #
    # Priority:
    # 1. flash_attention_2 (fastest, requires flash-attn package)
    # 2. sdpa (PyTorch's scaled dot product attention)
    # 3. eager (fallback)
    #
    # Returns:
    #     Attention mode: "flash_attention_2", "sdpa", or "eager"
    # Try flash_attention_2 first
    try:
        import flash_attn #type: ignore
        return "flash_attention_2"
    except ImportError:
        pass
    
    # Check for SDPA support (PyTorch 2.0+)
    if hasattr(torch.nn.functional, 'scaled_dot_product_attention'):
        return "sdpa"
    
    # Fallback to eager
    return "eager"


# ============================================================================
# Quantization Auto-Selection
# ============================================================================

def auto_select_quantization(
    model_name: str,
    estimated_size_gb: float = 0.0,
    device_info: Optional[Dict[str, Any]] = None
) -> str:
    # Auto-select quantization based on available memory.
    #
    # Calculates required VRAM from file size with overhead factors for:
    # - KV cache, activations, and buffers
    # - BitsAndBytes keeps embeddings/layernorms in fp32 (~20% extra)
    #
    # Args:
    #     model_name: Model name (for logging only)
    #     estimated_size_gb: Model size in GB from disk
    #     device_info: Device info dict (from get_device_info())
    #
    # Returns:
    #     Quantization mode: "fp16", "8bit", or "4bit"
    if device_info is None:
        device_info = get_device_info()
    
    # Get available memory
    if device_info["recommended_device"] in {"cpu", "mps"}:
        available = device_info["system_memory"]["available"]
    else:
        available = device_info["gpu"]["free_memory"]
    
    # If size unknown, default to fp16 (caller should warn)
    if estimated_size_gb <= 0:
        return "fp16"
    
    # Calculate requirements with overhead for KV cache, activations, etc.
    # File size (bf16/fp16) ≈ model weights size
    # BitsAndBytes quantization keeps embeddings and layernorms in fp32 (~20% extra)
    # 8-bit (bitsandbytes) ≈ 50% of fp16 weight size + fp32 layers + buffers
    # 4-bit (bitsandbytes) ≈ 25% of fp16 weight size + fp32 layers + buffers
    # Use conservative estimates to avoid OOM errors
    needed_fp16 = estimated_size_gb * 1.3  # 30% overhead for activations
    needed_8bit = estimated_size_gb * 0.85  # ~50% quantized + 20% fp32 layers + 15% buffers
    needed_4bit = estimated_size_gb * 0.55  # ~25% quantized + 20% fp32 layers + 10% buffers
    
    # Choose quantization based on available memory with safety margin.
    # The margin must cover KV cache, attention activations, and image processing
    # (for VLMs). These scale with model size — larger models have more layers/heads
    # and need proportionally larger KV caches.
    # BnB int8 CUDA kernels segfault (instead of raising OOM) when VRAM runs out,
    # so we must be conservative.
    safety_margin = max(2.5, estimated_size_gb * 0.25)
    effective_available = available - safety_margin
    
    if needed_fp16 <= effective_available:
        selected = "fp16"
    elif needed_8bit <= effective_available:
        selected = "8bit"
    elif needed_4bit <= effective_available:
        selected = "4bit"
    else:
        selected = "4bit"
        log.warning(_LOG_PREFIX, f"Low memory ({available:.1f} GB). Using 4-bit.")
    
    # Log the auto-selection decision
    log.msg(_LOG_PREFIX, f"Auto quantization: model={estimated_size_gb:.1f}GB, free={available:.1f}GB, headroom={safety_margin:.1f}GB, effective={effective_available:.1f}GB (need: fp16={needed_fp16:.1f}, 8bit={needed_8bit:.1f}, 4bit={needed_4bit:.1f}) → {selected}")
    
    return selected
