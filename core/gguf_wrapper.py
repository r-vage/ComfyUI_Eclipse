# GGUF Model Wrapper for Smart Loader Plus
#
# This module provides detection and loading support for GGUF quantized models.
# GGUF models are quantized diffusion models (INT4/INT8) that require special loading.
#
# Key Features:
# - Automatic detection via .gguf file extension
# - Graceful fallback when 'gguf' pip package is not installed
# - Support for dequantization and patch dtype control
# - Compatible with ComfyUI ModelPatcher interface via GGUFModelPatcher

import os
import inspect
from typing import Optional, Any, Callable
import torch #type: ignore

from .logger import log


_LOG_PREFIX = "GGUF"


log.debug(_LOG_PREFIX, "Module loading started...")

# Import GGUF from vendored extern package - no external custom node dependency
GGUF_AVAILABLE = False
GGMLOps: Optional[Any] = None
gguf_sd_loader: Optional[Callable[..., tuple[dict, dict]]] = None
gguf_clip_loader: Optional[Callable[[str], dict]] = None
GGUFModelPatcher: Optional[Any] = None

try:
    from ..extern.gguf.ops import GGMLOps as _GGMLOps
    from ..extern.gguf.loader import gguf_sd_loader as _gguf_sd_loader
    from ..extern.gguf.loader import gguf_clip_loader as _gguf_clip_loader
    from ..extern.gguf.nodes import GGUFModelPatcher as _GGUFModelPatcher

    GGMLOps = _GGMLOps
    gguf_sd_loader = _gguf_sd_loader
    gguf_clip_loader = _gguf_clip_loader
    GGUFModelPatcher = _GGUFModelPatcher

    GGUF_AVAILABLE = True
    log.msg(_LOG_PREFIX, "✓ GGUF components imported successfully")
except ImportError as e:
    log.warning(_LOG_PREFIX, f"GGUF not available (install 'gguf' pip package): {e}")
except Exception as e:
    log.error(_LOG_PREFIX, f"GGUF import error: {type(e).__name__}: {e}")

# ComfyUI imports
comfy: Any = None
folder_paths: Any = None
try:
    import comfy.sd #type: ignore
    import comfy.utils #type: ignore
    import comfy.model_management #type: ignore
    import folder_paths #type: ignore
    import comfy #type: ignore
except ImportError:
    pass


def is_gguf_available() -> bool:
    # Check if GGUF support is available.
    #
    # Returns:
    #     True if GGUF support is available (requires 'gguf' pip package)
    return GGUF_AVAILABLE


def detect_gguf_model(model_path: str) -> bool:
    # Detect if a model file is in GGUF format.
    #
    # Args:
    #     model_path: Path to model file
    #
    # Returns:
    #     True if file has .gguf extension
    if not model_path:
        return False
    
    return model_path.lower().endswith('.gguf')


def load_gguf_model(
    model_path: str,
    dequant_dtype: str = "default",
    patch_dtype: str = "default",
    patch_on_device: bool = False,
) -> object:
    # Load a GGUF quantized model.
    #
    # Args:
    #     model_path: Path to .gguf model file
    #     dequant_dtype: Dequantization dtype (default/target/float32/float16/bfloat16)
    #     patch_dtype: LoRA patch dtype (default/target/float32/float16/bfloat16)
    #     patch_on_device: Apply LoRA patches on GPU (faster but uses more VRAM)
    #
    # Returns:
    #     GGUFModelPatcher object
    #
    # Raises:
    #     ImportError: If GGUF support is not available
    #     ValueError: If model file not found or invalid parameters
    #     RuntimeError: If model loading fails
    
    # Check if GGUF is available
    if not GGUF_AVAILABLE:
        raise ImportError(
            "GGUF support not available.\n\n"
            "The 'gguf' pip package is required to load GGUF models.\n\n"
            "Installation:\n"
            "  pip install --upgrade gguf\n\n"
            "Then restart ComfyUI.\n\n"
            "Alternatively, use a standard (non-quantized) model."
        )
    
    # Validate model file exists
    if not os.path.exists(model_path):
        raise ValueError(f"Model file not found: {model_path}")
    
    # Validate file extension
    if not detect_gguf_model(model_path):
        raise ValueError(f"Not a GGUF model file (expected .gguf extension): {model_path}")
    
    log.msg(_LOG_PREFIX, f"Loading quantized model: {os.path.basename(model_path)}")
    log.msg(_LOG_PREFIX, f"  Dequant dtype: {dequant_dtype}")
    log.msg(_LOG_PREFIX, f"  Patch dtype: {patch_dtype}")
    log.msg(_LOG_PREFIX, f"  Patch on device: {patch_on_device}")
    
    # Type guards for mypy
    if GGMLOps is None or gguf_sd_loader is None or GGUFModelPatcher is None:
        raise ImportError("GGUF components not loaded properly")
    
    try:
        # Create custom ops with dtype settings
        ops = GGMLOps()
        
        # Set dequantization dtype
        if dequant_dtype == "default":
            ops.Linear.dequant_dtype = None  # type: ignore
        elif dequant_dtype == "target":
            ops.Linear.dequant_dtype = "target"  # type: ignore
        else:
            ops.Linear.dequant_dtype = getattr(torch, dequant_dtype)  # type: ignore
        
        # Set patch dtype
        if patch_dtype == "default":
            ops.Linear.patch_dtype = None  # type: ignore
        elif patch_dtype == "target":
            ops.Linear.patch_dtype = "target"  # type: ignore
        else:
            ops.Linear.patch_dtype = getattr(torch, patch_dtype)  # type: ignore
        
        # Load state dict from GGUF file
        log.msg(_LOG_PREFIX, "Loading state dict from GGUF file...")
        sd, extra = gguf_sd_loader(model_path)
        
        # Load diffusion model with custom operations
        # Pass metadata if load_diffusion_model_state_dict supports it
        log.msg(_LOG_PREFIX, "Loading diffusion model...")
        kwargs = {}
        valid_params = inspect.signature(comfy.sd.load_diffusion_model_state_dict).parameters
        if "metadata" in valid_params:
            kwargs["metadata"] = extra.get("metadata", {})
        
        model = comfy.sd.load_diffusion_model_state_dict(
            sd, 
            model_options={"custom_operations": ops},
            **kwargs,
        )
        
        if model is None:
            raise RuntimeError(f"Could not detect model type of: {model_path}")
        
        # Wrap in GGUF model patcher
        log.msg(_LOG_PREFIX, "Wrapping in GGUFModelPatcher...")
        model = GGUFModelPatcher.clone(model)  # type: ignore
        model.patch_on_device = patch_on_device  # type: ignore
        
        log.msg(_LOG_PREFIX, f"✓ Model loaded successfully: {os.path.basename(model_path)}")
        
        return model
        
    except Exception as e:
        raise RuntimeError(
            f"Failed to load GGUF model '{os.path.basename(model_path)}':\n{e}\n\n"
            f"This might indicate:\n"
            f"  - Corrupted model file\n"
            f"  - Incompatible GGUF version\n"
            f"  - Unsupported model architecture\n"
            f"  - Missing gguf Python package (pip install --upgrade gguf)\n"
        )


def load_gguf_clip(
    clip_paths: list,
    clip_type: Any,
) -> object:
    # Load CLIP text encoder(s) that may include GGUF files.
    # Mirrors the CLIPLoaderGGUF pattern from extern/gguf/nodes.py.
    #
    # Args:
    #     clip_paths: List of paths to CLIP model files (can be .gguf or .safetensors)
    #     clip_type: comfy.sd.CLIPType enum value
    #
    # Returns:
    #     CLIP object with GGUFModelPatcher wrapping
    #
    # Raises:
    #     ImportError: If GGUF support is not available
    #     RuntimeError: If CLIP loading fails

    if not GGUF_AVAILABLE:
        raise ImportError(
            "GGUF support not available.\n\n"
            "The 'gguf' pip package is required to load GGUF text encoders.\n\n"
            "Installation:\n"
            "  pip install --upgrade gguf\n\n"
            "Then restart ComfyUI."
        )

    if GGMLOps is None or gguf_clip_loader is None or GGUFModelPatcher is None:
        raise ImportError("GGUF components not loaded properly")

    try:
        # Load state dicts — use gguf_clip_loader for .gguf files, torch for others
        clip_data = []
        for p in clip_paths:
            if p.lower().endswith('.gguf'):
                log.msg(_LOG_PREFIX, f"Loading GGUF CLIP: {os.path.basename(p)}")
                sd = gguf_clip_loader(p)
            else:
                log.msg(_LOG_PREFIX, f"Loading standard CLIP: {os.path.basename(p)}")
                sd = comfy.utils.load_torch_file(p, safe_load=True)
            clip_data.append(sd)

        # Load text encoder with GGUF custom operations
        clip = comfy.sd.load_text_encoder_state_dicts(
            clip_type=clip_type,
            state_dicts=clip_data,
            model_options={
                "custom_operations": GGMLOps,
                "initial_device": comfy.model_management.text_encoder_offload_device()
            },
            embedding_directory=folder_paths.get_folder_paths("embeddings"),
        )

        # Wrap patcher for GGUF compatibility
        clip.patcher = GGUFModelPatcher.clone(clip.patcher)

        log.msg(_LOG_PREFIX, f"✓ CLIP loaded successfully ({len(clip_paths)} file(s))")
        return clip

    except Exception as e:
        raise RuntimeError(
            f"Failed to load GGUF CLIP:\n{e}\n\n"
            f"This might indicate:\n"
            f"  - Corrupted text encoder file\n"
            f"  - Incompatible GGUF version\n"
            f"  - Missing gguf Python package (pip install --upgrade gguf)\n"
        )


# Export public API
__all__ = [
    'is_gguf_available',
    'detect_gguf_model', 
    'load_gguf_model',
    'load_gguf_clip',
    'GGUF_AVAILABLE',
]

log.msg(_LOG_PREFIX, f"Module loaded. GGUF available: {GGUF_AVAILABLE}")



