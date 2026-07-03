from __future__ import annotations

# Smart Loader - Streamlined Model Loader with Integrated LoRA Support
#
# Streamlined model loader supporting multiple model formats and quantization methods:
# - Standard Checkpoints (.safetensors, .ckpt)
# - UNet-only models
# - GGUF quantized models (INT4/INT8 quantization)
#
# Features:
# - Automatic model type detection
# - Format-specific loading options (cache, attention, offload)
# - Template system for saving/loading configurations with intelligent field filtering
# - Model-only LoRA support with up to 3 slots
# - Graceful fallback when extensions are not installed
# - Comprehensive VRAM management and cleanup
# - Auto-fill template names for easy updates
# - No latent or sampler configuration (use separate nodes for those)

from typing import Any
import os
import time

import torch  # type: ignore
import comfy  # type: ignore
import comfy.sd  # type: ignore
import comfy.utils  # type: ignore
import folder_paths  # type: ignore
import comfy.model_management as mm  # type: ignore

from ...core import CATEGORY, RESOLUTION_PRESETS, RESOLUTION_MAP
from ...core.common import cleanup_memory_before_load
from ...core.logger import log
from ...core.model_loader_common import (
    GGUF_AVAILABLE,
    collect_lora_params, format_lora_string,
    apply_loras, apply_blockswap, build_pipe, OMIT,
    load_custom_vae,
)
from comfy_api.latest import io  # type: ignore

_LOG_PREFIX = "Smart Loader Basic v2"

from ...core.gguf_wrapper import (
    detect_gguf_model,
    load_gguf_model,
    load_gguf_clip,
)

MAX_RESOLUTION = 32768
UNET_DOWNSAMPLE = 8

_support_messages_printed = False

if not _support_messages_printed:
    _support_messages_printed = True
    if GGUF_AVAILABLE:
        log.debug(_LOG_PREFIX, "✓ GGUF support available")

class RvLoader_SmartLoader_Basic_v2(io.ComfyNode):

    @classmethod
    def define_schema(cls):
        weight_dtype_options = ["default", "fp8_e4m3fn", "fp8_e4m3fn_fast", "fp8_e5m2"]
        loras = ["None"] + folder_paths.get_filename_list("loras")

        # Get available CLIP files from both clip and text_encoders folders (deduplicated)
        clip_files = list(folder_paths.get_filename_list("clip"))
        if "text_encoders" in folder_paths.folder_names_and_paths:
            clip_files.extend(folder_paths.get_filename_list("text_encoders"))
        clips = ["None"] + sorted(set(clip_files))

        return io.Schema(
            node_id="Smart Loader Basic v2 [Eclipse]",
            display_name="⚠ Smart Loader Basic v2",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            inputs=[
                io.Combo.Input("model_type", options=["Standard Checkpoint", "UNet Model", "GGUF Model"], default="Standard Checkpoint", tooltip="Select model type"),
                io.Combo.Input("ckpt_name", options=["None"] + folder_paths.get_filename_list("checkpoints"), default="None", tooltip="Select checkpoint file"),
                io.Combo.Input("unet_name", options=["None"] + folder_paths.get_filename_list("diffusion_models"), default="None", tooltip="Select UNet diffusion model"),
                io.Combo.Input("gguf_name", options=["None"] + (folder_paths.get_filename_list("diffusion_models_gguf") if "diffusion_models_gguf" in folder_paths.folder_names_and_paths else []), default="None", tooltip="Select GGUF model"),
                io.Combo.Input("weight_dtype", options=weight_dtype_options, default="default", tooltip="Weight dtype for UNet model"),
                io.Combo.Input("gguf_dequant_dtype", options=["default", "target", "float32", "float16", "bfloat16"], default="default", tooltip="Dequantization dtype"),
                io.Combo.Input("gguf_patch_dtype", options=["default", "target", "float32", "float16", "bfloat16"], default="default", tooltip="LoRA patch dtype"),
                io.Boolean.Input("gguf_patch_on_device", default=False, label_on="yes", label_off="no", tooltip="Apply patches on GPU"),
                io.Boolean.Input("configure_clip", default=True, label_on="yes", label_off="no", tooltip="Enable CLIP configuration"),
                io.Boolean.Input("configure_vae", default=True, label_on="yes", label_off="no", tooltip="Enable VAE configuration"),
                io.Boolean.Input("configure_model_only_lora", default=False, label_on="yes", label_off="no", tooltip="Enable model-only LoRA configuration"),
                io.Boolean.Input("configure_blockswap", default=False, label_on="yes", label_off="no", tooltip="Enable block swap — offload transformer blocks to CPU for VRAM savings"),
                io.Int.Input(
                    "blocks_to_swap",
                    default=10,
                    min=0,
                    max=100,
                    step=1,
                    tooltip=(
                        "Number of transformer blocks to offload from GPU to CPU. "
                        "Higher = more VRAM saved but slower inference. "
                        "Suggested ~value (max total blocks): "
                        "flux/chroma ~10 (max 57), sd3 ~8 (max 24-38), "
                        "wan ~10 (max 30-40), hunyuan-video ~10 (max 60), "
                        "ltxv ~6 (max 28), cosmos ~8 (max 28-36), "
                        "zimage ~10 (max 30), qwenimage ~20 (max 60), "
                        "mochi ~10 (max 48), hidream ~10 (max 48). "
                        "Set to 0 to disable."
                    ),
                ),
                io.Boolean.Input(
                    "offload_embeddings",
                    default=False,
                    label_on="Yes",
                    label_off="No",
                    tooltip=(
                        "Also offload embedding and projection layers (text_embedding, "
                        "img_emb, time_in, etc.). Saves a small amount of extra VRAM."
                    ),
                ),
                io.Combo.Input("clip_source", options=["Baked", "External"], default="Baked", tooltip="CLIP source"),
                io.Combo.Input("clip_count", options=["1", "2", "3", "4"], default="1", tooltip="Number of CLIP models"),
                io.Combo.Input("clip_name1", options=clips, default="None", tooltip="Primary CLIP model"),
                io.Combo.Input("clip_name2", options=clips, default="None", tooltip="Secondary CLIP model"),
                io.Combo.Input("clip_name3", options=clips, default="None", tooltip="Third CLIP model"),
                io.Combo.Input("clip_name4", options=clips, default="None", tooltip="Fourth CLIP model"),
                io.Combo.Input("clip_type", options=["flux", "flux2", "sd3", "sdxl", "stable_cascade", "stable_audio", "hunyuan_dit", "mochi", "ltxv", "hunyuan_video", "pixart", "cosmos", "cogvideox", "lumina2", "wan", "hidream", "chroma", "ace", "omnigen2", "qwen_image", "hunyuan_image", "hunyuan_video_15", "ovis", "kandinsky5", "kandinsky5_image", "newbie", "lens", "longcat_image", "pixeldit", "ideogram4", "boogu", "krea2"], default="flux", tooltip="CLIP architecture type"),
                io.Boolean.Input("enable_clip_layer", default=True, label_on="yes", label_off="no", tooltip="Trim CLIP to specific layer"),
                io.Int.Input("stop_at_clip_layer", default=-2, min=-24, max=-1, step=1, tooltip="CLIP layer to stop at"),
                io.Combo.Input("vae_source", options=["Baked", "External"], default="Baked", tooltip="VAE source"),
                io.Combo.Input("vae_name", options=["None"] + folder_paths.get_filename_list("vae"), default="None", tooltip="External VAE file"),
                io.Combo.Input("lora_count", options=["1", "2", "3"], default="1", tooltip="Number of LoRA slots to configure"),
                io.Boolean.Input("lora_switch_1", default=False, label_on="ON", label_off="OFF", tooltip="Enable LoRA 1"),
                io.Combo.Input("lora_name_1", options=loras, default="None", tooltip="LoRA 1 file"),
                io.Float.Input("lora_weight_1", default=1.0, min=-10.0, max=10.0, step=0.01, tooltip="LoRA 1 model weight"),
                io.Boolean.Input("lora_switch_2", default=False, label_on="ON", label_off="OFF", tooltip="Enable LoRA 2"),
                io.Combo.Input("lora_name_2", options=loras, default="None", tooltip="LoRA 2 file"),
                io.Float.Input("lora_weight_2", default=1.0, min=-10.0, max=10.0, step=0.01, tooltip="LoRA 2 model weight"),
                io.Boolean.Input("lora_switch_3", default=False, label_on="ON", label_off="OFF", tooltip="Enable LoRA 3"),
                io.Combo.Input("lora_name_3", options=loras, default="None", tooltip="LoRA 3 file"),
                io.Float.Input("lora_weight_3", default=1.0, min=-10.0, max=10.0, step=0.01, tooltip="LoRA 3 model weight"),
                io.Boolean.Input("memory_cleanup", default=True, label_on="yes", label_off="no", tooltip="Perform memory cleanup before loading"),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def validate_inputs(cls, **kwargs):
        # Accept **kwargs so ComfyUI skips built-in combo validation.
        # This prevents "Value not in list" errors for stale filenames
        # in saved workflows (e.g. LoRA files that were moved/deleted).
        # Actual file existence is validated at execution time.
        return True

    @classmethod
    def execute(cls, **kwargs):
        # Extract all parameters
        model_type = kwargs.get('model_type', 'Standard Checkpoint')
        ckpt_name = kwargs.get('ckpt_name', 'None')
        unet_name = kwargs.get('unet_name', 'None')
        gguf_name = kwargs.get('gguf_name', 'None')
        weight_dtype = kwargs.get('weight_dtype', 'default')
        
        gguf_dequant_dtype = kwargs.get('gguf_dequant_dtype', 'default')
        gguf_patch_dtype = kwargs.get('gguf_patch_dtype', 'default')
        gguf_patch_on_device = kwargs.get('gguf_patch_on_device', False)
        
        configure_clip = kwargs.get('configure_clip', True)
        configure_vae = kwargs.get('configure_vae', True)
        configure_model_only_lora = kwargs.get('configure_model_only_lora', False)
        configure_blockswap = kwargs.get('configure_blockswap', False)
        blocks_to_swap = kwargs.get('blocks_to_swap', 10)
        offload_embeddings = kwargs.get('offload_embeddings', False)
        
        clip_source = kwargs.get('clip_source', 'Baked')
        clip_count = kwargs.get('clip_count', '1')
        clip_name1 = kwargs.get('clip_name1', 'None')
        clip_name2 = kwargs.get('clip_name2', 'None')
        clip_name3 = kwargs.get('clip_name3', 'None')
        clip_name4 = kwargs.get('clip_name4', 'None')
        clip_type = kwargs.get('clip_type', 'flux')
        enable_clip_layer = kwargs.get('enable_clip_layer', True)
        stop_at_clip_layer = kwargs.get('stop_at_clip_layer', -2)
        
        vae_source = kwargs.get('vae_source', 'Baked')
        vae_name = kwargs.get('vae_name', 'None')
        
        lora_count = kwargs.get('lora_count', '1')
        
        memory_cleanup = kwargs.get('memory_cleanup', True)
        
        # Normalize inputs
        configure_clip = bool(configure_clip)
        configure_vae = bool(configure_vae)
        configure_model_only_lora = bool(configure_model_only_lora)
        enable_clip_layer = bool(enable_clip_layer)
        clip_count_int = int(clip_count)
        lora_count_int = int(lora_count)
        
        is_standard = (model_type == "Standard Checkpoint")
        is_unet = (model_type == "UNet Model")
        is_gguf = (model_type == "GGUF Model")
        use_baked_clip = (clip_source == "Baked")
        use_baked_vae = (vae_source == "Baked")
        
        loaded_model = None
        loaded_clip = None
        loaded_vae = None
        ckpt_parts = None
        checkpoint_name = ""
        
        safe_exts = {".safetensors", ".sft"}
        
        # ============================================================
        # STEP 0: Pre-Load Memory Cleanup
        # ============================================================
        
        if memory_cleanup:
            cleanup_memory_before_load()
        
        # ============================================================
        # STEP 1: Load Model (Standard Checkpoint, UNet, or GGUF)
        # ============================================================
        
        if is_standard:
            # Load standard checkpoint
            if ckpt_name in (None, '', 'None'):
                raise ValueError("Please select a checkpoint file")
            
            ckpt_path = folder_paths.get_full_path("checkpoints", ckpt_name)
            if not ckpt_path or not os.path.isfile(ckpt_path):
                raise FileNotFoundError(f"Checkpoint not found: {ckpt_name}")
            
            _, ext = os.path.splitext(ckpt_path.lower())
            if ext not in safe_exts:
                log.warning(_LOG_PREFIX, f"'{ckpt_name}' uses extension '{ext}'. Consider .safetensors for safety.")
            
            if not os.access(ckpt_path, os.R_OK):
                raise RuntimeError(f"Checkpoint file not readable: {ckpt_path}")
            
            # Load checkpoint with conditional outputs
            loaded_ckpt = comfy.sd.load_checkpoint_guess_config(
                ckpt_path,
                output_vae=use_baked_vae if configure_vae else False,
                output_clip=use_baked_clip if configure_clip else False,
                embedding_directory=folder_paths.get_folder_paths("embeddings")
            )
            
            # Extract checkpoint parts
            checkpoint_name = ckpt_name
            ckpt_parts = loaded_ckpt[:3] if hasattr(loaded_ckpt, '__len__') and len(loaded_ckpt) >= 3 else None
            loaded_model = ckpt_parts[0] if ckpt_parts else loaded_ckpt
            
        elif is_gguf:
            # ============================================================
            # STEP 1E: Load GGUF Quantized Model
            # ============================================================
            
            if gguf_name in (None, '', 'None'):
                raise ValueError("Please select a GGUF model file")
            
            gguf_path = folder_paths.get_full_path("diffusion_models", gguf_name)
            if not gguf_path or not os.path.isfile(gguf_path):
                raise FileNotFoundError(f"GGUF model not found: {gguf_name}")
            
            if not gguf_path.lower().endswith('.gguf'):
                log.warning(_LOG_PREFIX, f"'{gguf_name}' doesn't have .gguf extension")
            
            if not os.access(gguf_path, os.R_OK):
                raise RuntimeError(f"GGUF file not readable: {gguf_path}")
            
            if not GGUF_AVAILABLE:
                log.warning("GGUF", "GGUF support not available - install the 'gguf' pip package")
                log.msg("GGUF", "Run: pip install gguf")
                loaded_model = None
                checkpoint_name = ""
            else:
                # Load GGUF model
                checkpoint_name = gguf_name
                
                log.msg("GGUF", f"Loading on device: {mm.get_torch_device()}")
                try:
                    loaded_model = load_gguf_model(
                        model_path=gguf_path,
                        dequant_dtype=gguf_dequant_dtype,
                        patch_dtype=gguf_patch_dtype,
                        patch_on_device=gguf_patch_on_device
                    )
                    
                except Exception as e:
                    log.error("GGUF", f"Failed to load model '{gguf_name}': {e}")
                    loaded_model = None
                    checkpoint_name = ""
            
        elif is_unet:
            # ============================================================
            # STEP 1F: Load Standard UNet Model
            # ============================================================
            
            if unet_name in (None, '', 'None'):
                raise ValueError("Please select a UNet model file")
            
            unet_path = folder_paths.get_full_path("diffusion_models", unet_name)
            if not unet_path or not os.path.isfile(unet_path):
                raise FileNotFoundError(f"UNet model not found: {unet_name}")
            
            _, ext = os.path.splitext(unet_path.lower())
            if ext not in safe_exts:
                log.warning(_LOG_PREFIX, f"'{unet_name}' uses extension '{ext}'. Consider .safetensors.")
            
            if not os.access(unet_path, os.R_OK):
                raise RuntimeError(f"UNet file not readable: {unet_path}")
            
            # Check if we need baked components (CLIP or VAE)
            needs_baked_clip = configure_clip and use_baked_clip
            needs_baked_vae = configure_vae and use_baked_vae
            
            if needs_baked_clip or needs_baked_vae:
                # Try to load as checkpoint to extract baked components
                try:
                    loaded_ckpt = comfy.sd.load_checkpoint_guess_config(
                        unet_path,
                        output_vae=needs_baked_vae,
                        output_clip=needs_baked_clip,
                        embedding_directory=folder_paths.get_folder_paths("embeddings"),
                    )
                    
                    ckpt_parts = loaded_ckpt[:3] if hasattr(loaded_ckpt, '__len__') and len(loaded_ckpt) >= 3 else None
                    loaded_model = ckpt_parts[0] if ckpt_parts else loaded_ckpt
                    checkpoint_name = unet_name
                    
                    
                except Exception as e:
                    # If checkpoint loading fails, fall back to diffusion model loading
                    log.msg("UNet", f"File doesn't contain baked components: {e}")
                    
                    # Configure model options
                    model_options: dict[str, Any] = {}
                    if weight_dtype == "fp8_e4m3fn":
                        model_options["dtype"] = torch.float8_e4m3fn
                    elif weight_dtype == "fp8_e4m3fn_fast":
                        model_options["dtype"] = torch.float8_e4m3fn
                        model_options["fp8_optimizations"] = True
                    elif weight_dtype == "fp8_e5m2":
                        model_options["dtype"] = torch.float8_e5m2
                    
                    log.msg("UNet", f"Target device: {mm.get_torch_device()}")
                    loaded_model = comfy.sd.load_diffusion_model(unet_path, model_options=model_options)
                    checkpoint_name = unet_name
                    
            else:
                # No baked components needed - use standard diffusion model loading
                model_options = {}
                if weight_dtype == "fp8_e4m3fn":
                    model_options["dtype"] = torch.float8_e4m3fn
                elif weight_dtype == "fp8_e4m3fn_fast":
                    model_options["dtype"] = torch.float8_e4m3fn
                    model_options["fp8_optimizations"] = True
                elif weight_dtype == "fp8_e5m2":
                    model_options["dtype"] = torch.float8_e5m2
                
                loaded_model = comfy.sd.load_diffusion_model(unet_path, model_options=model_options)
                checkpoint_name = unet_name
        
        else:
            raise ValueError("Invalid model_type. Choose 'Standard Checkpoint', 'UNet Model', or 'GGUF Model'")
        
        # ============================================================
        # STEP 2: Load CLIP (if configured)
        # ============================================================
        
        if configure_clip:
            if use_baked_clip:
                # Use baked CLIP from checkpoint (or UNet if it has one)
                # Note: GGUF models don't have baked CLIP
                if is_gguf:
                    log.warning("GGUF", "Quantized models don't contain baked CLIP - please use External CLIP")
                elif ckpt_parts and ckpt_parts[1]:
                    base_clip = ckpt_parts[1]
                    if enable_clip_layer:
                        loaded_clip = base_clip.clone()
                        loaded_clip.clip_layer(stop_at_clip_layer)
                    else:
                        loaded_clip = base_clip
                else:
                    log.warning(_LOG_PREFIX, "Baked CLIP requested but not found in checkpoint")
            
            else:
                # Load external CLIP files
                clip_paths = []
                clip_names = [clip_name1, clip_name2, clip_name3, clip_name4]
                
                for i in range(clip_count_int):
                    clip_name = clip_names[i] if i < len(clip_names) else "None"
                    if clip_name not in (None, '', 'None'):
                        clip_path = folder_paths.get_full_path("clip", clip_name)
                        if clip_path and os.path.isfile(clip_path):
                            clip_paths.append(clip_path)
                        else:
                            log.warning(_LOG_PREFIX, f"CLIP file '{clip_name}' not found, skipping")
                
                if not clip_paths:
                    raise ValueError("No valid CLIP files found. Please select at least one CLIP model")
                
                # Resolve clip type dynamically to prevent AttributeError on older ComfyUI installations
                resolved_clip_type = comfy.sd.CLIPType.STABLE_DIFFUSION
                if clip_type != "sdxl":
                    upper_name = clip_type.upper()
                    if hasattr(comfy.sd.CLIPType, upper_name):
                        resolved_clip_type = getattr(comfy.sd.CLIPType, upper_name)
                    else:
                        log.warning(_LOG_PREFIX, f"ComfyUI CLIPType does not support '{upper_name}', falling back to STABLE_DIFFUSION")
                
                has_gguf_clip = any(p.lower().endswith('.gguf') for p in clip_paths)

                if has_gguf_clip:
                    if not GGUF_AVAILABLE:
                        raise ImportError("GGUF text encoder selected but GGUF support is not available. Install the 'gguf' pip package.")
                    loaded_clip = load_gguf_clip(
                        clip_paths=clip_paths,
                        clip_type=resolved_clip_type,
                    )
                else:
                    loaded_clip = comfy.sd.load_clip(
                        ckpt_paths=clip_paths,
                        embedding_directory=folder_paths.get_folder_paths("embeddings"),
                        clip_type=resolved_clip_type
                    )
                
        
        # ============================================================
        # STEP 3: Load VAE (if configured)
        # ============================================================
        
        if configure_vae:
            if use_baked_vae:
                # Use baked VAE from checkpoint (or UNet if it has one)
                # Note: GGUF models don't have baked VAE
                if is_gguf:
                    log.warning("GGUF", "Quantized models don't contain baked VAE - please use External VAE")
                elif ckpt_parts and ckpt_parts[2]:
                    loaded_vae = ckpt_parts[2]
                else:
                    log.warning(_LOG_PREFIX, "Baked VAE requested but not found in model")
            
            else:
                # Load external VAE file
                if vae_name in (None, '', 'None'):
                    log.warning(_LOG_PREFIX, "External VAE requested but none selected")
                else:
                    try:
                        loaded_vae = load_custom_vae(vae_name)
                    except Exception as e:
                        log.warning(_LOG_PREFIX, f"Failed to load VAE '{vae_name}': {e}")
        
        # ============================================================
        # STEP 4: Apply LoRAs (if configured)
        # ============================================================
        
        # Collect and apply LoRAs
        lora_params = collect_lora_params(kwargs, lora_count_int) if configure_model_only_lora else []
        
        if lora_params:
            log.msg("LoRA", f"Applying {len(lora_params)} LoRA(s)...")
            loaded_model, loaded_clip = apply_loras(loaded_model, loaded_clip, lora_params)
        
        lora_string = format_lora_string(lora_params)
        
        # ============================================================
        # STEP 4.5: Apply Block Swap (if configured)
        # ============================================================
        
        if configure_blockswap:
            loaded_model = apply_blockswap(
                loaded_model, blocks_to_swap, offload_embeddings, _LOG_PREFIX
            )
        
        # ============================================================
        # STEP 5: Construct output pipe (no latent or sampler)
        # ============================================================
        
        if loaded_model is None:
            ext_hint = "Ensure the 'gguf' pip package is installed." if is_gguf else ""
            raise RuntimeError(
                f"Failed to load {model_type} model. Check the console log above for details.\n"
                f"The model could not be loaded — ensure the file exists and is not corrupted. {ext_hint}"
            )
        
        # Build pipe conditionally — omit keys for disabled features so
        # ConcatMulti won't overwrite existing values from other pipes.
        # Note: Basic v2 intentionally has NO is_nunchaku key (no Nunchaku support)
        pipe = build_pipe(
            model=loaded_model,
            model_name=checkpoint_name,
            lora_names=lora_string,
            clip=loaded_clip if configure_clip else OMIT,
            vae=loaded_vae if configure_vae else OMIT,
            vae_name=vae_name if (not use_baked_vae and vae_name not in (None, '', 'None')) else "",
            clip_skip=stop_at_clip_layer if (is_standard and use_baked_clip and enable_clip_layer) else OMIT,
        )
        
        return io.NodeOutput(pipe)
