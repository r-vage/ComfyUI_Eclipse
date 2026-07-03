# pyright: reportOptionalMemberAccess=false
# Nunchaku Model Wrapper for Smart Loader Plus
#
# This module provides detection and loading support for Nunchaku quantized models.
# Nunchaku models are quantized FLUX and Qwen models (INT4/FP4/FP8) that require special loading.
#
# Key Features:
# - Automatic detection via filename patterns and metadata inspection
# - Graceful fallback when 'nunchaku' pip package is not installed
# - Hybrid detection: filename patterns + metadata checks
# - Compatible with ComfyUI ModelPatcher interface
# - LoRA support for both Flux and Qwen models

import os
import json
import re
import copy
from pathlib import Path
from collections import defaultdict
from typing import Optional, Any, TYPE_CHECKING, Dict, List, Tuple, Union, Callable
import torch #type: ignore
from torch import nn #type: ignore

from .logger import log


_LOG_PREFIX = "Nunchaku"


log.debug(_LOG_PREFIX, "Module loading started...")

# Import Nunchaku - graceful fallback if pip package not available
NUNCHAKU_AVAILABLE = False
NunchakuFluxTransformer2dModel: Optional[Any] = None
NunchakuQwenImageTransformer2DModel: Optional[Any] = None
apply_cache_on_transformer: Optional[Any] = None
ComfyFluxWrapper: Optional[Any] = None
QwenConfig: Optional[Any] = None
QwenModelBase: Optional[Any] = None
NunchakuModelPatcher: Optional[Any] = None
ZImageModelPatcher: Optional[Any] = None
ZImageConfig: Optional[Any] = None
patch_zimage_model: Optional[Any] = None

try:
    # Core nunchaku pip package imports (compiled CUDA kernels)
    from nunchaku import NunchakuFluxTransformer2dModel as _NunchakuFluxTransformer2dModel #type: ignore
    from nunchaku.caching.diffusers_adapters.flux import apply_cache_on_transformer as _apply_cache_on_transformer #type: ignore

    NunchakuFluxTransformer2dModel = _NunchakuFluxTransformer2dModel
    apply_cache_on_transformer = _apply_cache_on_transformer

    log.msg(_LOG_PREFIX, "✓ Nunchaku base imports successful")

    # Qwen model from pip package
    try:
        from nunchaku.models.qwenimage import NunchakuQwenImageTransformer2DModel as _NunchakuQwenImageTransformer2DModel #type: ignore
        NunchakuQwenImageTransformer2DModel = _NunchakuQwenImageTransformer2DModel
        log.debug(_LOG_PREFIX, "Qwen model import successful")
    except ImportError as e:
        log.debug(_LOG_PREFIX, f"Qwen model not available: {e}")
        NunchakuQwenImageTransformer2DModel = None

    # ComfyUI glue code from vendored extern package (no external custom node dependency)
    try:
        from ..extern.nunchaku.wrappers.flux import ComfyFluxWrapper as _ComfyFluxWrapper
        from ..extern.nunchaku.model_configs.qwenimage import NunchakuQwenImage as _QwenConfig
        from ..extern.nunchaku.model_base.qwenimage import NunchakuQwenImage as _QwenModelBase
        from ..extern.nunchaku.model_patcher.common import NunchakuModelPatcher as _NunchakuModelPatcher
        from ..extern.nunchaku.model_patcher.zimage import ZImageModelPatcher as _ZImageModelPatcher
        from ..extern.nunchaku.model_configs.zimage import NunchakuZImage as _ZImageConfig
        from ..extern.nunchaku.models.zimage import patch_model as _patch_zimage_model

        ComfyFluxWrapper = _ComfyFluxWrapper
        QwenConfig = _QwenConfig
        QwenModelBase = _QwenModelBase
        NunchakuModelPatcher = _NunchakuModelPatcher
        ZImageModelPatcher = _ZImageModelPatcher
        ZImageConfig = _ZImageConfig
        patch_zimage_model = _patch_zimage_model

        log.msg(_LOG_PREFIX, "✓ All Nunchaku classes imported successfully")
    except Exception as e:
        log.error(_LOG_PREFIX, f"Could not import Nunchaku glue classes: {type(e).__name__}: {e}")
        ComfyFluxWrapper = None
        QwenConfig = None
        QwenModelBase = None
        NunchakuModelPatcher = None
        ZImageModelPatcher = None
        ZImageConfig = None
        patch_zimage_model = None

    NUNCHAKU_AVAILABLE = True
except ImportError as e:
    log.warning(_LOG_PREFIX, f"Nunchaku package not available: {e}")

# ComfyUI imports
from typing import cast
Flux: Any = cast(Any, None)
FluxSchnell: Any = cast(Any, None)
comfy: Any = cast(Any, None)
try:
    import comfy.model_patcher #type: ignore
    import comfy.model_management #type: ignore
    import comfy.utils #type: ignore
    import comfy.model_detection #type: ignore
    from comfy.supported_models import Flux as _Flux, FluxSchnell as _FluxSchnell #type: ignore
    import comfy #type: ignore
    Flux = _Flux
    FluxSchnell = _FluxSchnell
except ImportError:
    pass


def is_nunchaku_model_by_name(filename: str) -> bool:
    # Detect Nunchaku models by filename patterns.
    #
    # Nunchaku quantized models typically follow naming conventions:
    # - svdq-fp4_* (SVDQuant FP4 quantization)
    # - svdq-int4_* (SVDQuant INT4 quantization)
    # - nunchaku-* (Generic Nunchaku prefix)
    # - *-quant-* (Generic quantization marker)
    #
    # Parameters
    # ----------
    # filename : str
    #     The model filename to check
    #
    # Returns
    # -------
    # bool
    #     True if filename matches Nunchaku patterns
    #
    # Examples
    # --------
    # >>> is_nunchaku_model_by_name("svdq-fp4_r32-flux-dev.safetensors")
    # True
    # >>> is_nunchaku_model_by_name("flux1-dev.safetensors")
    # False
    filename_lower = filename.lower()
    
    nunchaku_patterns = [
        'svdq-fp4',      # SVDQuant FP4
        'svdq-int4',     # SVDQuant INT4
        'nunchaku-',     # Nunchaku prefix
        'svdquant-',     # SVDQuant generic
        '-quant-',       # Generic quantization marker
        'z-image',       # ZImage models
        'zimage',        # ZImage models (alternative spelling)
    ]
    
    return any(pattern in filename_lower for pattern in nunchaku_patterns)


def is_nunchaku_model_by_metadata(model_path: str) -> bool:
    # Check if model is Nunchaku format by inspecting safetensors metadata.
    #
    # This reads only the metadata header, not the full weights, making it fast.
    # Nunchaku models store special metadata like 'comfy_config', 'quantization', etc.
    #
    # Parameters
    # ----------
    # model_path : str
    #     Full path to the model file
    #
    # Returns
    # -------
    # bool
    #     True if metadata indicates Nunchaku format
    if not os.path.exists(model_path):
        return False
    
    try:
        # Use safetensors to read only metadata (fast, no weight loading)
        import safetensors.torch #type: ignore
        
        with safetensors.safe_open(model_path, framework="pt") as f:
            metadata = f.metadata()
            
            if metadata is None:
                return False
            
            # Nunchaku models have these metadata keys
            nunchaku_metadata_keys = [
                'comfy_config',      # ComfyUI configuration embedded by Nunchaku
                'quantization',      # Quantization parameters
                'nunchaku_version',  # Version marker
                'svdquant',          # SVDQuant marker
            ]
            
            # Check if any Nunchaku-specific key exists
            for key in nunchaku_metadata_keys:
                if key in metadata:
                    return True
            
            # Also check metadata values for Nunchaku indicators
            metadata_str = str(metadata).lower()
            if 'nunchaku' in metadata_str or 'svdquant' in metadata_str:
                return True
    
    except Exception:
        # If metadata reading fails, assume not Nunchaku
        pass
    
    return False


def _patch_zimage_state_dict(state_dict: dict) -> dict:
    # Patch ZImage state dict keys from diffusers style to ComfyUI style.
    #
    # Converts keys to match ComfyUI's expected format for NextDiT/Lumina2 models.
    # Based on ComfyUI-nunchaku's implementation.
    #
    # Parameters
    # ----------
    # state_dict : dict
    #     State dict with diffusers-style keys
    #
    # Returns
    # -------
    # dict
    #     Patched state dict with ComfyUI-style keys
    patched_state_dict = {}
    quant_sub_keys = ["wscales", "wcscales", "wtscale", "smooth_factor_orig", "smooth_factor", "proj_down", "proj_up"]
    
    for key, value in state_dict.items():
        # Attention QKV conversion
        if "attention.to_qkv" in key:
            patched_state_dict[key.replace("to_qkv", "qkv")] = value
        elif "attention.to_q" in key:
            q_weight = state_dict[key]
            k_weight = state_dict[key.replace("to_q", "to_k")]
            v_weight = state_dict[key.replace("to_q", "to_v")]
            patched_state_dict[key.replace("to_q", "qkv")] = torch.cat([q_weight, k_weight, v_weight], dim=0)
        elif "attention.to_k" in key or "attention.to_v" in key:
            continue
        elif "attention.to_out" in key:
            patched_state_dict[key.replace("to_out.0", "out")] = value
        
        # Feed forward - quantized
        elif "feed_forward.net.0.proj.qweight" in key:
            patched_state_dict[key.replace("net.0.proj", "w13")] = value
            for subkey in quant_sub_keys:
                quant_param_key = key.replace("qweight", subkey)
                if quant_param_key in state_dict:
                    patched_state_dict[quant_param_key.replace("net.0.proj", "w13")] = state_dict[quant_param_key]
        elif any("feed_forward.net.0.proj." + subkey in key for subkey in quant_sub_keys):
            continue
        elif "feed_forward.net.2.qweight" in key:
            patched_state_dict[key.replace("net.2", "w2")] = value
            for subkey in quant_sub_keys:
                quant_param_key = key.replace("qweight", subkey)
                if quant_param_key in state_dict:
                    patched_state_dict[quant_param_key.replace("net.2", "w2")] = state_dict[quant_param_key]
        elif any("feed_forward.net.2." + subkey in key for subkey in quant_sub_keys):
            continue
        
        # Feed forward - non-quantized
        elif "feed_forward.net.0.proj.weight" in key:
            w3, w1 = torch.chunk(value, chunks=2, dim=0)
            w2 = state_dict[key.replace("0.proj", "2")]
            patched_state_dict[key.replace("net.0.proj", "w1")] = w1
            patched_state_dict[key.replace("net.0.proj", "w2")] = w2
            patched_state_dict[key.replace("net.0.proj", "w3")] = w3
        elif "feed_forward.net.2.weight" in key:
            continue
        
        # Norms and others
        elif "attention.norm_q" in key:
            patched_state_dict[key.replace("norm_q", "q_norm")] = value
        elif "attention.norm_k" in key:
            patched_state_dict[key.replace("norm_k", "k_norm")] = value
        elif "all_final_layer.2-1" in key:
            patched_state_dict[key.replace("all_final_layer.2-1", "final_layer")] = value
        elif "all_x_embedder.2-1" in key:
            patched_state_dict[key.replace("all_x_embedder.2-1", "x_embedder")] = value
        else:
            patched_state_dict[key] = value
    
    return patched_state_dict


def detect_nunchaku_model(model_path: str, model_name: str) -> bool:
    # Hybrid detection: Combine filename patterns and metadata checks.
    #
    # Uses a two-stage approach:
    # 1. Fast filename pattern matching (no I/O)
    # 2. Metadata inspection if filename check fails (minimal I/O)
    #
    # Parameters
    # ----------
    # model_path : str
    #     Full path to the model file
    # model_name : str
    #     Model filename (for pattern matching)
    #
    # Returns
    # -------
    # bool
    #     True if model is detected as Nunchaku format
    # Stage 1: Fast filename check
    if is_nunchaku_model_by_name(model_name):
        return True
    
    # Stage 2: Metadata check (if file exists and is accessible)
    if os.path.exists(model_path) and os.path.isfile(model_path):
        return is_nunchaku_model_by_metadata(model_path)
    
    return False


def detect_nunchaku_model_type(model_path: str, model_name: str) -> str:
    # Auto-detect Nunchaku model type (flux, qwen, or zimage).
    #
    # Uses filename patterns and metadata to determine the model architecture.
    #
    # Parameters
    # ----------
    # model_path : str
    #     Full path to the model file
    # model_name : str
    #     Model filename
    #
    # Returns
    # -------
    # str
    #     Model type: "flux", "qwen", or "zimage"
    
    filename_lower = model_name.lower()
    
    # Check for ZImage in filename
    if 'z-image' in filename_lower or 'zimage' in filename_lower:
        return "zimage"
    
    # Check for Qwen in filename
    if 'qwen' in filename_lower:
        return "qwen"
    
    # Check metadata if file exists
    if os.path.exists(model_path):
        try:
            import safetensors #type: ignore
            with safetensors.safe_open(model_path, framework="pt") as f:
                metadata = f.metadata()
                if metadata:
                    # Check for model_config which indicates architecture
                    comfy_config_str = metadata.get("comfy_config")
                    if comfy_config_str:
                        comfy_config = json.loads(comfy_config_str)
                        model_config = comfy_config.get("model_config", {})
                        
                        # Check for architecture indicators
                        if "image_model" in model_config:
                            image_model = model_config.get("image_model")
                            if image_model == "lumina2":
                                return "zimage"
                            elif image_model == "qwen_image":
                                return "qwen"
                        
                        # Check for z_image_modulation (ZImage specific)
                        if model_config.get("z_image_modulation"):
                            return "zimage"
        except Exception:
            pass
    
    # Default to flux if no other indicators found
    return "flux"


def load_nunchaku_model(
    model_path: str,
    device: Optional[torch.device] = None,
    dtype: Optional[torch.dtype] = None,
    cpu_offload: bool = False,
    cache_threshold: float = 0.0,
    attention: str = "flash-attention2",
    data_type: str = "bfloat16",
    i2f_mode: str = "enabled",
    num_blocks_on_gpu: int = 1,
    use_pin_memory: bool = False,
    model_type: str = "flux"
) -> object:
    # Load a Nunchaku quantized model and wrap it for ComfyUI compatibility.
    #
    # Supports both Flux and Qwen-Image Nunchaku quantized models.
    #
    # Parameters
    # ----------
    # model_path : str
    #     Full path to the Nunchaku model file
    # device : torch.device, optional
    #     Device to load model on (default: auto-detect CUDA)
    # dtype : torch.dtype, optional
    #     Data type for model (default: based on data_type parameter)
    # cpu_offload : bool, default=False
    #     Enable CPU offload for low VRAM (<14GB)
    # cache_threshold : float, default=0.0
    #     First-block caching threshold (0=disabled, 0.12=typical)
    #     Higher values = faster but lower quality
    # attention : str, default="flash-attention2"
    #     Attention implementation: "flash-attention2" or "nunchaku-fp16"
    # data_type : str, default="bfloat16"
    #     Model data type: "bfloat16" (30/40-series) or "float16" (20-series GPUs)
    # i2f_mode : str, default="enabled"
    #     GEMM implementation for 20-series GPUs: "enabled" or "always"
    #     (ignored on other GPU architectures, Flux models only)
    # num_blocks_on_gpu : int, default=1
    #     Number of transformer blocks to keep on GPU when CPU offload is enabled
    #     (Qwen models only)
    # use_pin_memory : bool, default=False
    #     Use pinned memory for faster CPU-GPU transfer when CPU offload is enabled
    #     (Qwen models only)
    # model_type : str, default="flux"
    #     Model architecture type: "flux" for Flux models or "qwen" for Qwen-Image models
    #
    # Returns
    # -------
    # ModelPatcher
    #     ComfyUI ModelPatcher object (NunchakuModelPatcher for Qwen, standard for Flux)
    #
    # Raises
    # ------
    # RuntimeError
    #     If Nunchaku is not available or loading fails
    # ValueError
    #     If model file not found or invalid
    #
    # Examples
    # --------
    # >>> # Load Flux model
    # >>> model, name = load_nunchaku_model(
    # ...     "models/svdq-fp4_r32-flux.safetensors",
    # ...     cpu_offload=True,
    # ...     cache_threshold=0.12,
    # ...     model_type="flux"
    # ... )
    # >>>
    # >>> # Load Qwen model
    # >>> model, name = load_nunchaku_model(
    # ...     "models/qwen-image-quant.safetensors",
    # ...     cpu_offload=True,
    # ...     model_type="qwen"
    # ... )
    if not NUNCHAKU_AVAILABLE:
        raise RuntimeError(
            "Nunchaku support is not available.\n\n"
            "To load Nunchaku quantized models, install the 'nunchaku' pip package:\n"
            "  pip install nunchaku\n\n"
            "Then restart ComfyUI.\n"
            "Alternatively, use a standard (non-quantized) model."
        )
    
    # Validate model type and check required components
    if model_type not in ["flux", "qwen", "zimage"]:
        raise ValueError(f"Invalid model_type '{model_type}'. Must be 'flux', 'qwen', or 'zimage'")
    
    if model_type == "flux" and ComfyFluxWrapper is None:
        raise RuntimeError(
            "Nunchaku ComfyFluxWrapper not found.\n"
            "Please ensure the 'nunchaku' pip package is installed."
        )
    
    if model_type == "qwen" and (QwenConfig is None or QwenModelBase is None or NunchakuModelPatcher is None):
        raise RuntimeError(
            "Nunchaku Qwen support not found.\n"
            "Please ensure ComfyUI-nunchaku is properly installed with Qwen model support."
        )
    
    if model_type == "zimage" and (ZImageModelPatcher is None or ZImageConfig is None or patch_zimage_model is None):
        raise RuntimeError(
            "Nunchaku ZImage support not found.\n"
            "Please ensure ComfyUI-nunchaku v1.1.0+ is installed with ZImage support."
        )
    
    if not os.path.exists(model_path):
        raise ValueError(f"Model file not found: {model_path}")
    
    # Auto-detect device if not provided
    if device is None:
        if comfy is not None:
            device = comfy.model_management.get_torch_device()
        if device is None:
            device = torch.device("cuda")
    
    # Auto-detect dtype if not provided, otherwise use data_type parameter
    if dtype is None:
        dtype = torch.float16 if data_type == "float16" else torch.bfloat16
    
    # Auto-enable CPU offload for low VRAM GPUs
    if cpu_offload == "auto":
        if torch.cuda.is_available():
            try:
                gpu_memory_gb = torch.cuda.get_device_properties(device).total_memory / (1024**3)
                cpu_offload = (gpu_memory_gb < 14)
            except Exception:
                # Device properties unavailable (e.g. non-CUDA device passed)
                log.warning(_LOG_PREFIX, "Could not query GPU memory for auto-offload, defaulting to enabled")
                cpu_offload = True
        else:
            # Non-CUDA backend (Nunchaku requires CUDA but handle gracefully)
            log.warning(_LOG_PREFIX, "CUDA not available — Nunchaku requires an NVIDIA GPU. Enabling CPU offload as fallback.")
            cpu_offload = True
    
    model_label = "Flux" if model_type == "flux" else ("ZImage" if model_type == "zimage" else "Qwen-Image")
    log.msg(f"Nunchaku {model_label}", f"Loading quantized model: {os.path.basename(model_path)}")
    log.msg(f"Nunchaku {model_label}", f"  Device: {device}")
    log.msg(f"Nunchaku {model_label}", f"  CPU Offload: {cpu_offload}")
    
    if model_type == "flux":
        log.msg(f"Nunchaku {model_label}", f"  Data Type: {data_type} ({dtype})")
        log.msg(f"Nunchaku {model_label}", f"  Cache Threshold: {cache_threshold}")
        log.msg(f"Nunchaku {model_label}", f"  Attention: {attention}")
        log.msg(f"Nunchaku {model_label}", f"  I2F Mode: {i2f_mode}")
    elif model_type == "qwen":
        log.msg(f"Nunchaku {model_label}", f"  Num Blocks on GPU: {num_blocks_on_gpu}")
        log.msg(f"Nunchaku {model_label}", f"  Use Pin Memory: {use_pin_memory}")
    elif model_type == "zimage":
        log.msg(f"Nunchaku {model_label}", f"  Data Type: {data_type} ({dtype})")
    
    # ============================================================
    # Load model based on type
    # ============================================================
    
    if model_type == "qwen":
        # Qwen-Image models use state dict loading (no wrapper exists yet)
        # This is a simplified version that uses ComfyUI-nunchaku's model config
        import safetensors.torch #type: ignore
        import safetensors #type: ignore
        
        # Load state dict and metadata
        sd = safetensors.torch.load_file(model_path)
        
        # Read metadata from safetensors
        with safetensors.safe_open(model_path, framework="pt") as f:
            metadata = f.metadata() if f.metadata() else {}
        
        # Import utility functions from nunchaku package directly
        try:
            from nunchaku.utils import check_hardware_compatibility, get_precision_from_quantization_config #type: ignore
        except ImportError as e:
            raise ImportError(
                f"Failed to import nunchaku utility functions: {e}\n\n"
                "Make sure nunchaku package is properly installed:\n"
                "  pip install nunchaku\n"
            )
        
        # Parse quantization config from metadata
        quantization_config = json.loads(metadata.get("quantization_config", "{}"))
        precision = get_precision_from_quantization_config(quantization_config)
        rank = quantization_config.get("rank", 32)
        
        # Check hardware compatibility
        check_hardware_compatibility(quantization_config, device)
        
        # Prepare state dict (handle checkpoint format)
        diffusion_model_prefix = comfy.model_detection.unet_prefix_from_state_dict(sd)
        temp_sd = comfy.utils.state_dict_prefix_replace(sd, {diffusion_model_prefix: ""}, filter_keys=True)
        if len(temp_sd) > 0:
            sd = temp_sd
        
        # Calculate parameters and dtype
        parameters = comfy.utils.calculate_parameters(sd)
        weight_dtype = comfy.utils.weight_dtype(sd)
        
        # Type guard for QwenConfig
        if QwenConfig is None:
            raise ImportError("QwenConfig not available from ComfyUI-nunchaku")
        
        # Create model config (using globally imported QwenConfig)
        model_config = QwenConfig({
            "image_model": "qwen_image",
            "scale_shift": 0,
            "rank": rank,
            "precision": precision
        })
        model_config.optimizations["fp8"] = False
        
        # Determine dtype
        unet_weight_dtype = list(model_config.supported_inference_dtypes)
        if getattr(model_config, "scaled_fp8", None) is not None:
            weight_dtype = None
        
        if dtype is None:
            unet_dtype = comfy.model_management.unet_dtype(
                model_params=parameters,
                supported_dtypes=unet_weight_dtype,
                weight_dtype=weight_dtype
            )
        else:
            unet_dtype = dtype
        
        manual_cast_dtype = comfy.model_management.unet_manual_cast(
            unet_dtype, device, model_config.supported_inference_dtypes
        )
        model_config.set_inference_dtype(unet_dtype, manual_cast_dtype)
        
        # Create and load base model
        model = model_config.get_model(sd, "")
        offload_device = comfy.model_management.unet_offload_device()
        model = model.to(offload_device)
        model.load_model_weights(sd, "")
        
        # Get the Qwen transformer
        qwen_transformer = model.diffusion_model
        
        # Extract comfy_config from metadata if available
        comfy_config_str = metadata.get("comfy_config", None)
        if comfy_config_str:
            comfy_config = json.loads(comfy_config_str)
        else:
            # Fallback: use basic config
            comfy_config = {"model_config": {}}
        
        # Wrap transformer in ComfyQwenImageWrapper for LoRA support
        wrapper = ComfyQwenImageWrapper(
            qwen_transformer,
            comfy_config.get("model_config", {}),
            customized_forward=None,
            forward_kwargs={},
            cpu_offload_setting="auto" if cpu_offload else "disable",
            vram_margin_gb=4.0
        )
        
        # Set CPU offload if enabled
        if cpu_offload and hasattr(qwen_transformer, 'set_offload'):
            qwen_transformer.set_offload(
                True, 
                num_blocks_on_gpu=num_blocks_on_gpu,
                use_pin_memory=use_pin_memory
            )
        
        # Replace diffusion_model with wrapper
        model.diffusion_model = wrapper
        
        # Type guard for NunchakuModelPatcher
        if NunchakuModelPatcher is None:
            raise ImportError("NunchakuModelPatcher not available from ComfyUI-nunchaku")
        
        # Create model patcher (using globally imported NunchakuModelPatcher)
        model_patcher = NunchakuModelPatcher(
            model,
            load_device=device,
            offload_device=offload_device
        )
        
        log.msg(f"Nunchaku {model_label}", f"✓ Model loaded successfully: {os.path.basename(model_path)}")
        log.msg(f"Nunchaku {model_label}", "✓ Qwen LoRA support enabled via ComfyQwenImageWrapper")
        
        return model_patcher
    
    elif model_type == "zimage":
        # ZImage models (NextDiT/Lumina2 architecture with SVDQ quantization)
        import safetensors.torch #type: ignore
        from nunchaku.utils import get_precision_from_quantization_config #type: ignore
        from nunchaku.models.transformers.utils import convert_fp16, patch_scale_key #type: ignore
        from nunchaku.utils import is_turing #type: ignore
        
        # Load state dict and metadata
        sd, metadata = comfy.utils.load_torch_file(model_path, return_metadata=True)
        
        # Extract quantization config from metadata
        quantization_config = json.loads(metadata.get("quantization_config", "{}"))
        precision = get_precision_from_quantization_config(quantization_config)
        rank = quantization_config.get("rank", 32)
        skip_refiners = quantization_config.get("skip_refiners", False)
        
        # Check for diffusion model prefix
        diffusion_model_prefix = comfy.model_detection.unet_prefix_from_state_dict(sd)
        temp_sd = comfy.utils.state_dict_prefix_replace(sd, {diffusion_model_prefix: ""}, filter_keys=True)
        if len(temp_sd) > 0:
            sd = temp_sd
        
        offload_device = comfy.model_management.unet_offload_device()
        
        # Determine dtype based on GPU generation
        if not is_turing():
            unet_dtype = torch.bfloat16
            manual_cast_dtype = None
            torch_dtype = torch.bfloat16
        else:
            unet_dtype = torch.bfloat16
            manual_cast_dtype = torch.float16
            torch_dtype = torch.float16
        
        log.debug(_LOG_PREFIX, f"unet_dtype: {unet_dtype}, manual_cast_dtype: {manual_cast_dtype}, svdq_dtype: {torch_dtype}")
        
        # Type guard for ZImageConfig
        if ZImageConfig is None:
            raise ImportError("ZImageConfig not available from ComfyUI-nunchaku")
        
        # Create model config
        model_config = ZImageConfig(rank=rank, precision=precision, skip_refiners=skip_refiners)
        model_config.set_inference_dtype(unet_dtype, manual_cast_dtype)
        
        # Patch state dict keys (diffusers → ComfyUI style)
        patched_sd = _patch_zimage_state_dict(sd)
        
        # Get model and patch with SVDQ quantized layers
        model = model_config.get_model(patched_sd, "", torch_dtype=torch_dtype)
        
        # Apply scale key patching and FP16 conversion if needed
        patch_scale_key(model.diffusion_model, patched_sd)
        if torch_dtype == torch.float16:
            convert_fp16(model.diffusion_model, patched_sd)
        
        # Load model weights
        model.load_model_weights(patched_sd, "")
        
        # Type guard for ZImageModelPatcher
        if ZImageModelPatcher is None:
            raise ImportError("ZImageModelPatcher not available from ComfyUI-nunchaku")
        
        # Create ZImage model patcher (supports LoRA for SVDQ quantized layers)
        model_patcher = ZImageModelPatcher(model, load_device=device, offload_device=offload_device)
        
        log.msg(f"Nunchaku {model_label}", f"✓ Model loaded successfully: {os.path.basename(model_path)}")
        log.msg(f"Nunchaku {model_label}", "✓ LoRA support enabled for SVDQ quantized layers")
        
        return model_patcher
    
    elif model_type == "flux":
        # Type guards for Flux components
        if ComfyFluxWrapper is None:
            raise ImportError(
                "ComfyFluxWrapper not available.\n"
                "Make sure ComfyUI-nunchaku extension is properly installed.\n"
                "The wrappers/flux.py file should exist in ComfyUI-nunchaku."
            )
        
        if NunchakuFluxTransformer2dModel is None:
            raise ImportError("NunchakuFluxTransformer2dModel not available")
        
        # Load quantized Flux transformer
        transformer, metadata = NunchakuFluxTransformer2dModel.from_pretrained(  # type: ignore
            model_path,
            offload=cpu_offload,
            device=device,
            torch_dtype=dtype,
            return_metadata=True
        )
    
        # Apply caching if enabled
        if cache_threshold > 0:
            if apply_cache_on_transformer is None:
                raise ImportError("apply_cache_on_transformer not available")
            transformer = apply_cache_on_transformer(  # type: ignore
                transformer=transformer,
                residual_diff_threshold=cache_threshold
            )
        
        # Set attention implementation
        if attention == "nunchaku-fp16":
            transformer.set_attention_impl("nunchaku-fp16")
        else:
            transformer.set_attention_impl("flashattn2")
        
        # Extract ComfyUI config from metadata
        if metadata is None:
            raise ValueError("Model metadata not found - this may not be a valid Nunchaku model")
        
        comfy_config_str = metadata.get("comfy_config", None)
        if comfy_config_str is None:
            raise ValueError(
                "Model is missing 'comfy_config' metadata.\n"
                "This may be an older Nunchaku model or corrupted file."
            )
        
        comfy_config = json.loads(comfy_config_str)
        
        # Determine model class (Flux or FluxSchnell)
        model_class_name = comfy_config.get("model_class", "Flux")
        if model_class_name == "FluxSchnell":
            model_class = FluxSchnell
        else:
            model_class = Flux
        
        # Ensure disable_unet_model_creation is set (we provide our own diffusion_model via wrapper)
        model_config_dict = comfy_config["model_config"]
        if "disable_unet_model_creation" not in model_config_dict:
            model_config_dict["disable_unet_model_creation"] = True
        
        # Create ComfyUI model configuration
        model_config = model_class(model_config_dict)
        model_config.set_inference_dtype(dtype, None)
        model_config.custom_operations = None
        
        # Create ComfyUI model structure (empty - diffusion_model provided by wrapper)
        from comfy import model_base as _comfy_model_base #type: ignore
        _had_default = hasattr(_comfy_model_base.BaseModel, 'diffusion_model')
        if not _had_default:
            _comfy_model_base.BaseModel.diffusion_model = None
        _orig_archive = comfy.model_management.archive_model_dtypes
        comfy.model_management.archive_model_dtypes = lambda *a, **kw: None
        try:
            model = model_config.get_model({})
        finally:
            comfy.model_management.archive_model_dtypes = _orig_archive
            if not _had_default:
                try:
                    del _comfy_model_base.BaseModel.diffusion_model
                except AttributeError:
                    pass
        
        # Wrap transformer in ComfyUI-compatible wrapper
        wrapper = ComfyFluxWrapper(
            transformer,
            config=comfy_config["model_config"],
            ctx_for_copy={
                "comfy_config": comfy_config,
                "model_config": model_config,
                "device": device,
                "device_id": device.index if hasattr(device, 'index') else 0,
            },
        )
        
        model.diffusion_model = wrapper
        
        # Create ModelPatcher for ComfyUI integration
        device_id = device.index if hasattr(device, 'index') else 0
        model_patcher = comfy.model_patcher.ModelPatcher(model, device, device_id)
        
        log.msg(f"Nunchaku {model_label}", f"✓ Model loaded successfully: {os.path.basename(model_path)}")
        
        return model_patcher


def get_nunchaku_info() -> dict:
    # Get information about Nunchaku support availability.
    #
    # Returns
    # -------
    # dict
    #     Dictionary with keys:
    #     - 'available': bool - Whether Nunchaku is available
    #     - 'version': str | None - Nunchaku version if available
    #     - 'wrapper_available': bool - Whether ComfyFluxWrapper is available
    info: dict[str, bool | str | None] = {
        'available': NUNCHAKU_AVAILABLE,
        'version': None,
        'wrapper_available': ComfyFluxWrapper is not None
    }
    
    if NUNCHAKU_AVAILABLE:
        try:
            import nunchaku #type: ignore
            # Try to get version from __version__ attribute
            version = getattr(nunchaku, '__version__', None)  # type: str | None
            
            # If __version__ not available, try package metadata
            if not version:
                try:
                    from importlib.metadata import version as get_version
                    version = get_version('nunchaku')
                except Exception:
                    pass
            
            info['version'] = version
        except Exception:
            pass
    
    return info


# ============================================================================
# Qwen LoRA Support - Composition Engine
# ============================================================================

# Centralized key mapping for Qwen LoRA layers
# This regex-based approach efficiently maps LoRA keys to model layer names
_QWEN_KEY_MAPPING = [
    # Fused QKV (Double Block)
    (re.compile(r"^(transformer_blocks)[._](\d+)[._]attn[._]to[._]qkv$"), r"\1.\2.attn.to_qkv", "qkv", None),
    # Decomposed QKV (Double Block)
    (re.compile(r"^(transformer_blocks)[._](\d+)[._]attn[._]to[._](q|k|v)$"), r"\1.\2.attn.to_qkv", "qkv",
     lambda m: m.group(3).upper()),
    (re.compile(r"^(transformer_blocks)[._](\d+)[._]attn[._](q|k|v)[._]proj$"), r"\1.\2.attn.to_qkv", "qkv",
     lambda m: m.group(3).upper()),
    # Fused Add_QKV (Double Block)
    (re.compile(r"^(transformer_blocks)[._](\d+)[._]attn[._]add[._]qkv[._]proj$"), r"\1.\2.attn.add_qkv_proj",
     "add_qkv", None),
    # Decomposed Add_QKV (Double Block)
    (re.compile(r"^(transformer_blocks)[._](\d+)[._]attn[._]add[._](q|k|v)[._]proj$"), r"\1.\2.attn.add_qkv_proj",
     "add_qkv", lambda m: m.group(3).upper()),
    # Fused QKV (Single Block)
    (re.compile(r"^(single_transformer_blocks)[._](\d+)[._]attn[._]to[._]qkv$"), r"\1.\2.attn.to_qkv", "qkv", None),
    # Decomposed QKV (Single Block)
    (re.compile(r"^(single_transformer_blocks)[._](\d+)[._]attn[._]to[._](q|k|v)$"), r"\1.\2.attn.to_qkv", "qkv",
     lambda m: m.group(3).upper()),
    # Output Projections
    (re.compile(r"^(transformer_blocks)[._](\d+)[._]out[._]proj[._]context$"), r"\1.\2.attn.to_add_out", "regular", None),
    (re.compile(r"^(transformer_blocks)[._](\d+)[._]attn[._]to[._]add[._]out$"), r"\1.\2.attn.to_add_out", "regular", None),
    (re.compile(r"^(transformer_blocks)[._](\d+)[._]out[._]proj$"), r"\1.\2.attn.to_out.0", "regular", None),
    (re.compile(r"^(transformer_blocks)[._](\d+)[._]attn[._]to[._]out$"), r"\1.\2.attn.to_out.0", "regular", None),
    (re.compile(r"^(transformer_blocks)[._](\d+)[._]attn[._]to[._]out[._]0$"), r"\1.\2.attn.to_out.0", "regular", None),
    (re.compile(r"^(single_transformer_blocks)[._](\d+)[._]attn[._]to[._]out$"), r"\1.\2.attn.to_out", "regular", None),
    # Feed-Forward / MLP Layers (Standard)
    (re.compile(r"^(transformer_blocks)[._](\d+)[._]ff[._]net[._]0(?:[._]proj)?$"), r"\1.\2.mlp_fc1", "regular", None),
    (re.compile(r"^(transformer_blocks)[._](\d+)[._]ff[._]net[._]2$"), r"\1.\2.mlp_fc2", "regular", None),
    (re.compile(r"^(transformer_blocks)[._](\d+)[._]ff_context[._]net[._]0(?:[._]proj)?$"),
     r"\1.\2.mlp_context_fc1", "regular", None),
    (re.compile(r"^(transformer_blocks)[._](\d+)[._]ff_context[._]net[._]2$"), r"\1.\2.mlp_context_fc2", "regular", None),
    # Feed-Forward / MLP Layers (img/txt)
    (re.compile(r"^(transformer_blocks)[._](\d+)[._](img_mlp)[._](net)[._](0)[._](proj)$"), r"\1.\2.\3.\4.\5.\6",
     "regular", None),
    (re.compile(r"^(transformer_blocks)[._](\d+)[._](img_mlp)[._](net)[._](0)$"), r"\1.\2.\3.\4.\5", "regular", None),
    (re.compile(r"^(transformer_blocks)[._](\d+)[._](img_mlp)[._](net)[._](2)$"), r"\1.\2.\3.\4.\5", "regular", None),
    (re.compile(r"^(transformer_blocks)[._](\d+)[._](txt_mlp)[._](net)[._](0)[._](proj)$"), r"\1.\2.\3.\4.\5.\6",
     "regular", None),
    (re.compile(r"^(transformer_blocks)[._](\d+)[._](txt_mlp)[._](net)[._](0)$"), r"\1.\2.\3.\4.\5", "regular", None),
    (re.compile(r"^(transformer_blocks)[._](\d+)[._](txt_mlp)[._](net)[._](2)$"), r"\1.\2.\3.\4.\5", "regular", None),
    # Mod Layers (img/txt)
    (re.compile(r"^(transformer_blocks)[._](\d+)[._](img_mod)[._](1)$"), r"\1.\2.\3.\4", "regular", None),
    (re.compile(r"^(transformer_blocks)[._](\d+)[._](txt_mod)[._](1)$"), r"\1.\2.\3.\4", "regular", None),
    # Single Block Projections
    (re.compile(r"^(single_transformer_blocks)[._](\d+)[._]proj[._]out$"), r"\1.\2.proj_out", "single_proj_out", None),
    (re.compile(r"^(single_transformer_blocks)[._](\d+)[._]proj[._]mlp$"), r"\1.\2.mlp_fc1", "regular", None),
    # Normalization Layers
    (re.compile(r"^(single_transformer_blocks)[._](\d+)[._]norm[._]linear$"), r"\1.\2.norm.linear", "regular", None),
    (re.compile(r"^(transformer_blocks)[._](\d+)[._]norm1[._]linear$"), r"\1.\2.norm1.linear", "regular", None),
    (re.compile(r"^(transformer_blocks)[._](\d+)[._]norm1_context[._]linear$"), r"\1.\2.norm1_context.linear",
     "regular", None),
    # Top-level diffusion_model modules
    (re.compile(r"^(img_in)$"), r"\1", "regular", None),
    (re.compile(r"^(txt_in)$"), r"\1", "regular", None),
    (re.compile(r"^(proj_out)$"), r"\1", "regular", None),
    (re.compile(r"^(norm_out)[._](linear)$"), r"\1.\2", "regular", None),
    (re.compile(r"^(time_text_embed)[._](timestep_embedder)[._](linear_1)$"), r"\1.\2.\3", "regular", None),
    (re.compile(r"^(time_text_embed)[._](timestep_embedder)[._](linear_2)$"), r"\1.\2.\3", "regular", None),
]

_RE_LORA_SUFFIX = re.compile(r"\.(?P<tag>lora(?:[._](?:A|B|down|up)))(?:\.[^.]+)*\.weight$")
_RE_ALPHA_SUFFIX = re.compile(r"\.(?:alpha|lora_alpha)(?:\.[^.]+)*$")


def _rename_layer_underscore_layer_name(old_name: str) -> str:
    # Convert underscore patterns to dot notation for Qwen layer names.
    rules = [
        (r'_(\d+)_attn_to_out_(\d+)', r'.\1.attn.to_out.\2'),
        (r'_(\d+)_img_mlp_net_(\d+)_proj', r'.\1.img_mlp.net.\2.proj'),
        (r'_(\d+)_txt_mlp_net_(\d+)_proj', r'.\1.txt_mlp.net.\2.proj'),
        (r'_(\d+)_img_mlp_net_(\d+)', r'.\1.img_mlp.net.\2'),
        (r'_(\d+)_txt_mlp_net_(\d+)', r'.\1.txt_mlp.net.\2'),
        (r'_(\d+)_img_mod_(\d+)', r'.\1.img_mod.\2'),
        (r'_(\d+)_txt_mod_(\d+)', r'.\1.txt_mod.\2'),
        (r'_(\d+)_attn_', r'.\1.attn.'),
    ]
    
    new_name = old_name
    for pattern, replacement in rules:
        new_name = re.sub(pattern, replacement, new_name)
    
    return new_name


def _classify_and_map_qwen_key(key: str) -> Optional[Tuple[str, str, Optional[str], str]]:
    # Classify and map a Qwen LoRA key using regex patterns.
    k = key
    if k.startswith("transformer."):
        k = k[len("transformer."):]
    if k.startswith("diffusion_model."):
        k = k[len("diffusion_model."):]
    if k.startswith("lora_unet_"):
        k = k[len("lora_unet_"):]
        k = _rename_layer_underscore_layer_name(k)
    
    base = None
    ab = None
    
    m = _RE_LORA_SUFFIX.search(k)
    if m:
        tag = m.group("tag")
        base = k[: m.start()]
        if "lora_A" in tag or tag.endswith(".A") or "down" in tag:
            ab = "A"
        elif "lora_B" in tag or tag.endswith(".B") or "up" in tag:
            ab = "B"
    else:
        m = _RE_ALPHA_SUFFIX.search(k)
        if m:
            ab = "alpha"
            base = k[: m.start()]
    
    if base is None or ab is None:
        return None
    
    for pattern, template, group, comp_fn in _QWEN_KEY_MAPPING:
        match = pattern.match(base)
        if match:
            final_key = match.expand(template)
            component = comp_fn(match) if comp_fn else None
            return group, final_key, component, ab
    
    return None


def _is_indexable_module(m):
    # Check if a module is list-like.
    return isinstance(m, (nn.ModuleList, nn.Sequential, list, tuple))


def _get_module_by_name(model: nn.Module, name: str) -> Optional[nn.Module]:
    # Traverse a path like 'a.b.3.c' to find and return a module.
    if not name:
        return model
    module = model
    for part in name.split("."):
        if not part:
            continue
        
        if hasattr(module, part):
            module = getattr(module, part)
        elif part.isdigit() and _is_indexable_module(module):
            try:
                module = module[int(part)]  # type: ignore
            except (IndexError, TypeError):
                log.warning("Qwen LoRA", f"Failed to index module {name} with part {part}")
                return None
        else:
            return None
    return module


def _resolve_module_name(model: nn.Module, name: str) -> Tuple[str, Optional[nn.Module]]:
    # Resolve a name string path to a module with fallback paths.
    m = _get_module_by_name(model, name)
    if m is not None:
        return name, m
    
    if name.endswith(".attn.to_out.0"):
        alt = name[:-2]
        m = _get_module_by_name(model, alt)
        if m is not None:
            return alt, m
    elif name.endswith(".attn.to_out"):
        alt = name + ".0"
        m = _get_module_by_name(model, alt)
        if m is not None:
            return alt, m
    
    mapping = {
        ".ff.net.0.proj": ".mlp_fc1", ".ff.net.2": ".mlp_fc2",
        ".ff_context.net.0.proj": ".mlp_context_fc1", ".ff_context.net.2": ".mlp_context_fc2",
    }
    for src, dst in mapping.items():
        if src in name:
            alt = name.replace(src, dst)
            m = _get_module_by_name(model, alt)
            if m is not None:
                return alt, m
    
    # Debug output disabled for performance (only enable for troubleshooting)
    log.debug(_LOG_PREFIX, f"Module not found: {name}")
    return name, None


def _load_lora_state_dict(lora_state_dict_or_path: Union[str, Path, Dict[str, torch.Tensor]]) -> Dict[str, torch.Tensor]:
    # Load LoRA state dict from path or return existing dict.
    if isinstance(lora_state_dict_or_path, (str, Path)):
        path = Path(lora_state_dict_or_path)
        if path.suffix == ".safetensors":
            try:
                from safetensors import safe_open #type: ignore
                state_dict = {}
                with safe_open(path, framework="pt", device="cpu") as f:
                    for key in f.keys():
                        state_dict[key] = f.get_tensor(key)
                return state_dict
            except ImportError:
                log.warning("Qwen LoRA", "safetensors not available, falling back to torch.load")
                return torch.load(path, map_location="cpu")
        else:
            return torch.load(path, map_location="cpu")
    return lora_state_dict_or_path


def _fuse_qkv_lora(qkv_weights: Dict[str, torch.Tensor]) -> Tuple[
    Optional[torch.Tensor], Optional[torch.Tensor], Optional[torch.Tensor]]:
    # Fuse Q/K/V LoRA weights into a single QKV tensor.
    required_keys = ["Q_A", "Q_B", "K_A", "K_B", "V_A", "V_B"]
    if not all(k in qkv_weights for k in required_keys):
        return None, None, None
    
    A_q, A_k, A_v = qkv_weights["Q_A"], qkv_weights["K_A"], qkv_weights["V_A"]
    B_q, B_k, B_v = qkv_weights["Q_B"], qkv_weights["K_B"], qkv_weights["V_B"]
    
    if not (A_q.shape == A_k.shape == A_v.shape):
        log.warning("Qwen LoRA", f"Q/K/V LoRA A dimensions mismatch: {A_q.shape}, {A_k.shape}, {A_v.shape}")
        return None, None, None
    
    alpha_q, alpha_k, alpha_v = qkv_weights.get("Q_alpha"), qkv_weights.get("K_alpha"), qkv_weights.get("V_alpha")
    alpha_fused = None
    if alpha_q is not None and alpha_k is not None and alpha_v is not None and (
            alpha_q.item() == alpha_k.item() == alpha_v.item()):
        alpha_fused = alpha_q
    
    A_fused = torch.cat([A_q, A_k, A_v], dim=0)
    
    r = B_q.shape[1]
    out_q, out_k, out_v = B_q.shape[0], B_k.shape[0], B_v.shape[0]
    B_fused = torch.zeros(out_q + out_k + out_v, 3 * r, dtype=B_q.dtype, device=B_q.device)
    B_fused[:out_q, :r] = B_q
    B_fused[out_q: out_q + out_k, r: 2 * r] = B_k
    B_fused[out_q + out_k:, 2 * r:] = B_v
    
    return A_fused, B_fused, alpha_fused


def _handle_proj_out_split(lora_dict: Dict[str, Dict[str, torch.Tensor]], base_key: str, model: nn.Module) -> Tuple[
    Dict[str, Tuple[torch.Tensor, torch.Tensor, Optional[torch.Tensor]]], List[str]]:
    # Split single-block proj_out LoRA into two branches.
    result: Dict[str, Tuple[torch.Tensor, torch.Tensor, Optional[torch.Tensor]]] = {}
    consumed: List[str] = []
    m = re.search(r"single_transformer_blocks\.(\d+)", base_key)
    if not m or base_key not in lora_dict:
        return result, consumed
    
    block_idx = m.group(1)
    block = _get_module_by_name(model, f"single_transformer_blocks.{block_idx}")
    if block is None:
        return result, consumed
    
    A_full, B_full, alpha = lora_dict[base_key].get("A"), lora_dict[base_key].get("B"), lora_dict[base_key].get("alpha")
    if A_full is None or B_full is None:
        return result, consumed
    
    attn_to_out = getattr(getattr(block, "attn", None), "to_out", None)
    mlp_fc2 = getattr(block, "mlp_fc2", None)
    if attn_to_out is None or mlp_fc2 is None or not hasattr(attn_to_out, "in_features") or not hasattr(mlp_fc2, "in_features"):
        return result, consumed
    
    attn_in, mlp_in = attn_to_out.in_features, mlp_fc2.in_features
    if A_full.shape[1] != attn_in + mlp_in:
        log.warning("Qwen LoRA", f"{base_key}: A_full shape mismatch {A_full.shape} vs expected in_features {attn_in + mlp_in}")
        return result, consumed
    
    A_attn, A_mlp = A_full[:, :attn_in], A_full[:, attn_in:]
    result[f"single_transformer_blocks.{block_idx}.attn.to_out"] = (A_attn, B_full.clone(), alpha)
    result[f"single_transformer_blocks.{block_idx}.mlp_fc2"] = (A_mlp, B_full.clone(), alpha)
    consumed.append(base_key)
    return result, consumed


def _qwen_unpack_lowrank_weight(weight: torch.Tensor, down: bool = True) -> torch.Tensor:
    # Unpack Nunchaku low-rank weight (placeholder - uses nunchaku if available).
    try:
        from nunchaku.lora.flux.nunchaku_converter import unpack_lowrank_weight #type: ignore
        return unpack_lowrank_weight(weight, down=down)
    except ImportError:
        # Fallback: assume already unpacked
        return weight


def _qwen_pack_lowrank_weight(weight: torch.Tensor, down: bool = True) -> torch.Tensor:
    # Pack Nunchaku low-rank weight (placeholder - uses nunchaku if available).
    try:
        from nunchaku.lora.flux.nunchaku_converter import pack_lowrank_weight #type: ignore
        return pack_lowrank_weight(weight, down=down)
    except ImportError:
        # Fallback: assume packing not needed
        return weight


def _qwen_reorder_adanorm_lora_up(weight: torch.Tensor, splits: int = 6) -> torch.Tensor:
    # Reorder AdaNorm LoRA up weights (placeholder - uses nunchaku if available).
    try:
        from nunchaku.lora.flux.nunchaku_converter import reorder_adanorm_lora_up #type: ignore
        return reorder_adanorm_lora_up(weight, splits=splits)
    except ImportError:
        # Fallback: no reordering
        return weight


def _apply_lora_to_qwen_module(module: Any, A: torch.Tensor, B: torch.Tensor, module_name: str,
                                model: Any) -> None:
    # Append combined LoRA weights to a Qwen module.
    if A.ndim != 2 or B.ndim != 2:
        raise ValueError(f"{module_name}: A/B must be 2D, got {A.shape}, {B.shape}")
    if A.shape[1] != module.in_features:
        raise ValueError(f"{module_name}: A shape {A.shape} mismatch with in_features={module.in_features}")
    if B.shape[0] != module.out_features:
        raise ValueError(f"{module_name}: B shape {B.shape} mismatch with out_features={module.out_features}")
    
    pd, pu = module.proj_down.data, module.proj_up.data
    pd = _qwen_unpack_lowrank_weight(pd, down=True)
    pu = _qwen_unpack_lowrank_weight(pu, down=False)
    
    base_rank = pd.shape[0] if pd.shape[1] == module.in_features else pd.shape[1]
    
    if pd.shape[1] == module.in_features:  # [rank, in]
        new_proj_down = torch.cat([pd, A], dim=0)
        axis_down = 0
    else:  # [in, rank]
        new_proj_down = torch.cat([pd, A.T], dim=1)
        axis_down = 1
    
    new_proj_up = torch.cat([pu, B], dim=1)
    
    module.proj_down.data = _qwen_pack_lowrank_weight(new_proj_down, down=True)
    module.proj_up.data = _qwen_pack_lowrank_weight(new_proj_up, down=False)
    module.rank = base_rank + A.shape[0]
    
    if not hasattr(model, "_lora_slots"):
        model._lora_slots = {}
    slot = model._lora_slots.setdefault(module_name, {"base_rank": base_rank, "appended": 0, "axis_down": axis_down})
    slot["appended"] += A.shape[0]


def compose_qwen_loras_v2(
        model: torch.nn.Module,
        lora_configs: List[Tuple[Union[str, Path, Dict[str, torch.Tensor]], float]],
) -> None:
    # Compose multiple LoRAs into a Qwen model with individual strengths.
    #
    # Parameters
    # ----------
    # model : torch.nn.Module
    #     The Qwen model to apply LoRAs to
    # lora_configs : List[Tuple[Union[str, Path, dict], float]]
    #     List of (lora_path_or_dict, strength) tuples
    log.msg("Qwen LoRA", f"Composing {len(lora_configs)} LoRAs for Qwen model...")
    reset_qwen_lora_v2(model)
    
    aggregated_weights: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    
    # 1. Aggregate weights from all LoRAs
    for lora_path_or_dict, strength in lora_configs:
        lora_name = lora_path_or_dict if isinstance(lora_path_or_dict, str) else "dict"
        lora_state_dict = _load_lora_state_dict(lora_path_or_dict)
        
        lora_grouped: Dict[str, Dict[str, torch.Tensor]] = defaultdict(dict)
        for key, value in lora_state_dict.items():
            parsed = _classify_and_map_qwen_key(key)
            if parsed is None:
                continue
            
            group, base_key, comp, ab = parsed
            if group in ("qkv", "add_qkv") and comp is not None:
                lora_grouped[base_key][f"{comp}_{ab}"] = value
            else:
                lora_grouped[base_key][ab] = value
        
        # Process grouped weights for this LoRA
        processed_groups = {}
        special_handled = set()
        for base_key, lw in lora_grouped.items():
            if base_key in special_handled:
                continue
            
            if "qkv" in base_key:
                A, B, alpha = (lw.get("A"), lw.get("B"), lw.get("alpha")) if "A" in lw else _fuse_qkv_lora(lw)
            elif ".proj_out" in base_key and "single_transformer_blocks" in base_key:
                split_map, consumed_keys = _handle_proj_out_split(lora_grouped, base_key, model)
                processed_groups.update(split_map)
                special_handled.update(consumed_keys)
                continue
            else:
                A, B, alpha = lw.get("A"), lw.get("B"), lw.get("alpha")
            
            if A is not None and B is not None:
                processed_groups[base_key] = (A, B, alpha)
        
        for module_key, (A, B, alpha) in processed_groups.items():
            aggregated_weights[module_key].append(
                {"A": A, "B": B, "alpha": alpha, "strength": strength, "source": lora_name})
    
    # 2. Apply aggregated weights to the model
    applied_modules_count = 0
    
    for module_name, parts in aggregated_weights.items():
        resolved_name, module = _resolve_module_name(model, module_name)
        if module is None or not (hasattr(module, "proj_down") and hasattr(module, "proj_up")):
            continue
        
        all_A = []
        all_B_scaled = []
        for part in parts:
            A, B, alpha, strength = part["A"], part["B"], part["alpha"], part["strength"]
            r_lora = A.shape[0]
            scale_alpha = alpha.item() if alpha is not None else float(r_lora)
            scale = strength * (scale_alpha / max(1.0, float(r_lora)))
            
            if ".norm1.linear" in resolved_name or ".norm1_context.linear" in resolved_name:
                B = _qwen_reorder_adanorm_lora_up(B, splits=6)
            elif ".single_transformer_blocks." in resolved_name and ".norm.linear" in resolved_name:
                B = _qwen_reorder_adanorm_lora_up(B, splits=3)
            
            all_A.append(A.to(dtype=module.proj_down.dtype, device=module.proj_down.device))
            all_B_scaled.append((B * scale).to(dtype=module.proj_up.dtype, device=module.proj_up.device))
        
        if not all_A:
            continue
        
        final_A = torch.cat(all_A, dim=0)
        final_B = torch.cat(all_B_scaled, dim=1)
        
        _apply_lora_to_qwen_module(module, final_A, final_B, resolved_name, model)
        applied_modules_count += 1
    
    log.msg("Qwen LoRA", f"Applied LoRA compositions to {applied_modules_count} Qwen modules.")


def reset_qwen_lora_v2(model: Any) -> None:
    # Remove all appended LoRA weights from a Qwen model.
    if not hasattr(model, "_lora_slots") or not model._lora_slots:
        return
    
    for name, info in model._lora_slots.items():
        module: Any = _get_module_by_name(model, name)
        if module is None:
            continue
        
        base_rank = info["base_rank"]
        with torch.no_grad():
            pd = _qwen_unpack_lowrank_weight(module.proj_down.data, down=True)
            pu = _qwen_unpack_lowrank_weight(module.proj_up.data, down=False)
            
            if info.get("axis_down", 0) == 0:  # [rank, in]
                pd_reset = pd[:base_rank, :].clone()
            else:  # [in, rank]
                pd_reset = pd[:, :base_rank].clone()
            pu_reset = pu[:, :base_rank].clone()
            
            module.proj_down.data = _qwen_pack_lowrank_weight(pd_reset, down=True)
            module.proj_up.data = _qwen_pack_lowrank_weight(pu_reset, down=False)
            module.rank = base_rank
    
    model._lora_slots.clear()
    model._lora_strength = 1.0
    log.msg("Qwen LoRA", "All LoRA weights have been reset from the Qwen model.")


# ============================================================================
# ComfyQwenImageWrapper - Qwen Model Wrapper with LoRA Support
# ============================================================================

class ComfyQwenImageWrapper(nn.Module):
    # Wrapper for NunchakuQwenImageTransformer2DModel to support ComfyUI workflows with LoRA.
    #
    # This wrapper separates LoRA composition from the forward pass for maximum efficiency.
    # It detects changes to its `loras` attribute and recomposes the underlying model
    # lazily when the forward pass is executed.
    #
    # Attributes
    # ----------
    # model : NunchakuQwenImageTransformer2DModel
    #     The wrapped Qwen transformer model
    # loras : List[Tuple[Union[str, Path, dict], float]]
    #     List of (lora_path, strength) tuples to apply
    # cpu_offload_setting : str
    #     CPU offload mode: "auto", "enable", or "disable"
    # vram_margin_gb : float
    #     VRAM safety margin for auto mode (default: 4.0 GB)
    
    def __init__(
            self,
            model: Any,  # NunchakuQwenImageTransformer2DModel
            config: dict,
            customized_forward: Optional[Callable] = None,
            forward_kwargs: Optional[dict] = None,
            cpu_offload_setting: str = "auto",
            vram_margin_gb: float = 4.0
    ):
        super().__init__()
        self.model = model
        self.dtype = next(model.parameters()).dtype
        self.config = config
        self.loras: List[Tuple[Union[str, Path, dict], float]] = []
        self._applied_loras: Optional[List[Tuple[Union[str, Path, dict], float]]] = None
        
        self.cpu_offload_setting = cpu_offload_setting
        self.vram_margin_gb = vram_margin_gb
        
        log.msg("Qwen", f"CPU offload setting: '{cpu_offload_setting}' (VRAM margin: {vram_margin_gb}GB)")
        
        self.customized_forward = customized_forward
        self.forward_kwargs = forward_kwargs or {}
        
        self._prev_timestep = None
        self._cache_context = None
        
        # Track last seen device to detect CPU/GPU moves
        self._last_device = None
    
    def to_safely(self, device):
        # Safely move the model to the specified device.
        if hasattr(self.model, "to_safely"):
            self.model.to_safely(device)
        else:
            self.model.to(device)
        return self
    
    def forward(
            self,
            x,
            timestep,
            context=None,
            y=None,
            guidance=None,
            control=None,
            transformer_options=None,
            **kwargs,
    ):
        # Forward pass for the wrapped Qwen model.
        #
        # Detects changes to the `self.loras` list and recomposes the model
        # on-the-fly before inference.
        if transformer_options is None:
            transformer_options = {}
        
        if isinstance(timestep, torch.Tensor):
            if timestep.numel() == 1:
                timestep_float = timestep.item()
            else:
                timestep_float = timestep.flatten()[0].item()
        else:
            timestep_float = float(timestep)
        
        model_is_dirty = (
            not self.loras and
            hasattr(self.model, "_lora_slots") and self.model._lora_slots
        )
        
        # Deep comparison of LoRA stacks
        loras_changed = False
        if self._applied_loras is None or len(self._applied_loras) != len(self.loras):
            loras_changed = True
        else:
            for applied, current in zip(self._applied_loras, self.loras):
                if applied != current:
                    loras_changed = True
                    break
        
        # Detect device transition
        try:
            current_device = next(self.model.parameters()).device
        except Exception:
            current_device = None
        device_changed = (self._last_device != current_device)
        
        # Recompose LoRAs if needed
        if loras_changed or model_is_dirty or device_changed:
            reset_qwen_lora_v2(self.model)
            self._applied_loras = self.loras.copy()
            
            # Reset cache when LoRAs change
            if loras_changed:
                self._cache_context = None
                self._prev_timestep = None
                log.debug(_LOG_PREFIX, "Cache reset due to LoRA change")
            
            # Dynamic VRAM check for CPU offload
            offload_is_on = hasattr(self.model, "offload_manager") and self.model.offload_manager is not None
            should_enable_offload = offload_is_on
            
            if self.cpu_offload_setting == "auto" and not offload_is_on and self.loras:
                try:
                    import comfy.model_management #type: ignore
                    free_vram_gb = comfy.model_management.get_free_memory() / (1024 ** 3)
                    
                    if free_vram_gb < self.vram_margin_gb:
                        log.msg("Qwen LoRA",
                            f"Free VRAM is {free_vram_gb:.2f}GB (below safety margin of {self.vram_margin_gb}GB) "
                            f"and 'cpu_offload' is 'auto'. Force-enabling CPU offload for Qwen LoRA composition.")
                        should_enable_offload = True
                    else:
                        log.msg("Qwen LoRA",
                            f"Free VRAM is {free_vram_gb:.2f}GB (>= {self.vram_margin_gb}GB margin). "
                            f"Qwen LoRAs will be composed without enabling CPU offload.")
                except Exception as e:
                    log.error("Qwen LoRA", f"Error during VRAM check for Qwen LoRA offloading: {e}")
            
            # Compose LoRAs
            compose_qwen_loras_v2(self.model, self.loras)
            
            # Validate composition
            try:
                has_slots = hasattr(self.model, "_lora_slots") and bool(self.model._lora_slots)
            except Exception:
                has_slots = True
            if self.loras and not has_slots:
                log.warning("Qwen LoRA", "Qwen LoRA composition reported 0 target modules. Forcing reset and retry.")
                try:
                    reset_qwen_lora_v2(self.model)
                    compose_qwen_loras_v2(self.model, self.loras)
                except Exception as e:
                    log.error("Qwen LoRA", f"Qwen LoRA re-compose retry failed: {e}")
            
            # Rebuild offload manager if needed
            if should_enable_offload:
                if offload_is_on:
                    manager = self.model.offload_manager
                    if manager is not None:
                        offload_settings = {
                            "num_blocks_on_gpu": manager.num_blocks_on_gpu,
                            "use_pin_memory": manager.use_pin_memory,
                        }
                    else:
                        offload_settings = {
                            "num_blocks_on_gpu": 1,
                            "use_pin_memory": False,
                        }
                else:
                    offload_settings = {
                        "num_blocks_on_gpu": 1,
                        "use_pin_memory": False,
                    }
                    log.msg("Qwen LoRA", "Building new CPU offload manager for Qwen due to LoRA VRAM check.")
                
                self.model.set_offload(False)
                self.model.set_offload(True, **offload_settings)
            
            self._last_device = current_device
        
        # Execute model
        return self._execute_model(x, timestep, context, guidance, control, transformer_options, **kwargs)
    
    def _execute_model(self, x, timestep, context, guidance, control, transformer_options, **kwargs):
        # Helper function to run the Qwen model's forward pass.
        model_device = next(self.model.parameters()).device
        
        # Move input tensors to model device
        if x.device != model_device:
            x = x.to(model_device)
        if context is not None and context.device != model_device:
            context = context.to(model_device)
        
        input_is_5d = x.ndim == 5
        if input_is_5d:
            x = x.squeeze(2)
        
        if self.customized_forward:
            with torch.inference_mode():
                return self.customized_forward(
                    self.model,
                    hidden_states=x,
                    encoder_hidden_states=context,
                    timestep=timestep,
                    guidance=guidance if self.config.get("guidance_embed", False) else None,
                    control=control,
                    transformer_options=transformer_options,
                    **self.forward_kwargs,
                    **kwargs,
                )
        else:
            with torch.inference_mode():
                if x.ndim == 4:
                    x = x.unsqueeze(2)
                
                return self.model(
                    x,
                    timestep,
                    context,
                    guidance=guidance if self.config.get("guidance_embed", False) else None,
                    control=control,
                    transformer_options=transformer_options,
                    **kwargs,
                )



