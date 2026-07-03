import comfy #type: ignore
import comfy.utils #type: ignore
import comfy.sd #type: ignore
import folder_paths #type: ignore
from typing import Any
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "LoraStack"
def is_nunchaku_flux_model(model: Any) -> bool:
    # Check if a model is a Nunchaku FLUX model by detecting ComfyFluxWrapper.
    #
    # Parameters
    # ----------
    # model : Any
    #     The model (ModelPatcher) to check.
    #
    # Returns
    # -------
    # bool
    #     True if the model has ComfyFluxWrapper, False otherwise.
    try:
        model_wrapper = model.model.diffusion_model  # type: ignore
        
        # Check if it's a ComfyFluxWrapper (handle torch.compile() optimized modules)
        if hasattr(model_wrapper, '_orig_mod'):
            # This is a torch._dynamo.eval_frame.OptimizedModule
            actual_wrapper = model_wrapper._orig_mod  # type: ignore
            wrapper_class_name = type(actual_wrapper).__name__
            return wrapper_class_name == 'ComfyFluxWrapper'
        else:
            wrapper_class_name = type(model_wrapper).__name__
            return wrapper_class_name == 'ComfyFluxWrapper'
    except Exception:
        return False


def is_nunchaku_qwen_model(model: Any) -> bool:
    # Check if a model is a Nunchaku Qwen model by detecting ComfyQwenImageWrapper.
    #
    # Parameters
    # ----------
    # model : Any
    #     The model (ModelPatcher) to check.
    #
    # Returns
    # -------
    # bool
    #     True if the model has ComfyQwenImageWrapper, False otherwise.
    try:
        model_wrapper = model.model.diffusion_model  # type: ignore
        
        # Check if it's a ComfyQwenImageWrapper
        if hasattr(model_wrapper, '_orig_mod'):
            # Handle torch.compile() optimized modules
            actual_wrapper = model_wrapper._orig_mod  # type: ignore
            wrapper_class_name = type(actual_wrapper).__name__
            return wrapper_class_name == 'ComfyQwenImageWrapper'
        else:
            wrapper_class_name = type(model_wrapper).__name__
            return wrapper_class_name == 'ComfyQwenImageWrapper'
    except Exception:
        return False


def is_nunchaku_zimage_model(model: Any) -> bool:
    # Check if a model is a Nunchaku ZImage model by detecting ZImageModelPatcher.
    #
    # Parameters
    # ----------
    # model : Any
    #     The model (ModelPatcher) to check.
    #
    # Returns
    # -------
    # bool
    #     True if the model is a ZImageModelPatcher, False otherwise.
    try:
        # ZImage uses ZImageModelPatcher directly, not a wrapper
        patcher_class_name = type(model).__name__
        return patcher_class_name == 'ZImageModelPatcher'
    except Exception:
        return False

def _build_lora_string(lora_params: list) -> str:
    # Build lora_names string from lora_params list.
    # Format: <lora:name:model_weight:clip_weight> or <lora:name:model_weight> when model-only
    # clip_weight=None signals model-only mode (no clip weight in output string)
    if not lora_params:
        return ""
    try:
        parts = []
        for tup in lora_params:
            if not isinstance(tup, (list, tuple)) or len(tup) < 3:
                continue
            lora_name, model_weight, clip_weight = tup[0], tup[1], tup[2]
            if clip_weight is None:
                parts.append(f"<lora:{lora_name}:{model_weight}>")
            else:
                parts.append(f"<lora:{lora_name}:{model_weight}:{clip_weight}>")
        return ' '.join(parts)
    except Exception:
        return ""


def _apply_lora_stack_standard(model, clip, lora_params):
    # Apply LoRAs to standard (non-Nunchaku) models using ComfyUI's loader.
    # Initialise the model and clip
    model_lora = model
    clip_lora = clip

    # Loop through the list
    for tup in lora_params:
        lora_name, strength_model, strength_clip = tup
        
        lora_path = folder_paths.get_full_path("loras", lora_name)
        lora = comfy.utils.load_torch_file(lora_path, safe_load=True)
        
        if strength_clip is None or clip_lora is None:
            # model-only mode: apply to model only, pass clip through unchanged
            if strength_clip is not None and clip_lora is None:
                log.warning(_LOG_PREFIX, f"LoRA '{lora_name}' has clip weight but no CLIP connected — applying to model only.")
            model_lora, _ = comfy.sd.load_lora_for_models(model_lora, None, lora, strength_model, 0.0)
        else:
            model_lora, clip_lora = comfy.sd.load_lora_for_models(model_lora, clip_lora, lora, strength_model, strength_clip)

    return (model_lora, clip_lora, _build_lora_string(lora_params))


def _apply_lora_stack_nunchaku_flux(model: Any, clip: Any, lora_params: list[Any]) -> tuple[Any, Any, str]:
    # Apply LoRAs to Nunchaku FLUX models via ComfyFluxWrapper.
    try:
        # Import required Nunchaku components
        from nunchaku.lora.flux import to_diffusers  # type: ignore
        from ..core.nunchaku_wrapper import ComfyFluxWrapper  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            f"Nunchaku not available for LoRA application: {e}\n"
            "Please install the 'nunchaku' pip package: pip install nunchaku"
        )

    # Get the model wrapper
    model_wrapper = model.model.diffusion_model  # type: ignore
    
    # Handle OptimizedModule case
    if hasattr(model_wrapper, '_orig_mod'):
        transformer = model_wrapper._orig_mod.model  # type: ignore
        
        # Create a new model structure manually for OptimizedModule
        ret_model = model.__class__(  # type: ignore
            model.model, model.load_device, model.offload_device,  # type: ignore
            model.size, model.weight_inplace_update  # type: ignore
        )
        ret_model.model = model.model  # type: ignore
        
        # Create a new ComfyFluxWrapper manually
        original_wrapper = model_wrapper._orig_mod  # type: ignore
        ret_model_wrapper = ComfyFluxWrapper(  # type: ignore
            transformer,
            config=original_wrapper.config,  # type: ignore
            pulid_pipeline=original_wrapper.pulid_pipeline,  # type: ignore
            customized_forward=original_wrapper.customized_forward,  # type: ignore
            forward_kwargs=original_wrapper.forward_kwargs,  # type: ignore
            ctx_for_copy=getattr(original_wrapper, 'ctx_for_copy', {}),  # type: ignore
        )
        
        # Copy internal state from original wrapper
        ret_model_wrapper._prev_timestep = original_wrapper._prev_timestep  # type: ignore
        ret_model_wrapper._cache_context = original_wrapper._cache_context  # type: ignore
        if hasattr(original_wrapper, '_original_time_text_embed'):
            ret_model_wrapper._original_time_text_embed = original_wrapper._original_time_text_embed  # type: ignore
        
        ret_model.model.diffusion_model = ret_model_wrapper  # type: ignore
    else:
        # Non-OptimizedModule case
        transformer = model_wrapper.model  # type: ignore
        
        # Create a new ModelPatcher with the same parameters
        ret_model = model.__class__(  # type: ignore
            model.model, model.load_device, model.offload_device,  # type: ignore
            model.size, model.weight_inplace_update  # type: ignore
        )
        
        # Create a new ComfyFluxWrapper manually
        original_wrapper = model_wrapper
        ret_model_wrapper = ComfyFluxWrapper(  # type: ignore
            transformer,
            config=original_wrapper.config,  # type: ignore
            pulid_pipeline=original_wrapper.pulid_pipeline,  # type: ignore
            customized_forward=original_wrapper.customized_forward,  # type: ignore
            forward_kwargs=original_wrapper.forward_kwargs,  # type: ignore
            ctx_for_copy=getattr(original_wrapper, 'ctx_for_copy', {}),  # type: ignore
        )
        
        # Copy internal state from original wrapper
        ret_model_wrapper._prev_timestep = original_wrapper._prev_timestep  # type: ignore
        ret_model_wrapper._cache_context = original_wrapper._cache_context  # type: ignore
        if hasattr(original_wrapper, '_original_time_text_embed'):
            ret_model_wrapper._original_time_text_embed = original_wrapper._original_time_text_embed  # type: ignore
        
        ret_model.model.diffusion_model = ret_model_wrapper  # type: ignore
    
    # Restore transformer to the original wrapper (important for original model integrity)
    if hasattr(model_wrapper, '_orig_mod'):
        model_wrapper._orig_mod.model = transformer  # type: ignore
    else:
        model_wrapper.model = transformer  # type: ignore
    
    # Set transformer to the new wrapper
    ret_model_wrapper.model = transformer  # type: ignore

    # Clear existing LoRA list in the new wrapper
    ret_model_wrapper.loras = []  # type: ignore

    # Track the maximum input channels needed
    max_in_channels = ret_model.model.model_config.unet_config["in_channels"]  # type: ignore

    # Add all LoRAs to the wrapper's LoRA list
    # lora_params format: [(lora_name, model_strength, clip_strength), ...]
    # For Nunchaku, we use model_strength as the LoRA strength
    lora_names_list = []
    for lora_name, model_strength, clip_strength in lora_params:
        lora_path = folder_paths.get_full_path_or_raise("loras", lora_name)
        ret_model_wrapper.loras.append((lora_path, model_strength))  # type: ignore
        lora_names_list.append(lora_name)

        # Check if input channels need to be updated
        sd = to_diffusers(lora_path)  # type: ignore
        if "transformer.x_embedder.lora_A.weight" in sd:
            new_in_channels = sd["transformer.x_embedder.lora_A.weight"].shape[1]
            assert new_in_channels % 4 == 0, f"Invalid LoRA input channels: {new_in_channels}"
            new_in_channels = new_in_channels // 4
            max_in_channels = max(max_in_channels, new_in_channels)

    # Update the model's input channels if needed
    if max_in_channels > ret_model.model.model_config.unet_config["in_channels"]:  # type: ignore
        ret_model.model.model_config.unet_config["in_channels"] = max_in_channels  # type: ignore

    # Generate string output with weights (Nunchaku uses model_strength only)
    # For Nunchaku Flux, CLIP is not modified (FLUX doesn't use separate CLIP)
    return (ret_model, clip, _build_lora_string(lora_params))


def _apply_lora_stack_nunchaku_qwen(model: Any, clip: Any, lora_params: list[Any]) -> tuple[Any, Any, str]:
    # Apply LoRAs to Nunchaku Qwen models via ComfyQwenImageWrapper.
    try:
        # Import required Qwen wrapper
        from ..core.nunchaku_wrapper import ComfyQwenImageWrapper  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            f"Nunchaku Qwen wrapper not available for LoRA application: {e}\n"
            "Please ensure ComfyUI_Eclipse is properly installed."
        )

    # Get the model wrapper
    model_wrapper = model.model.diffusion_model  # type: ignore
    
    # Handle OptimizedModule case (if torch.compile() was used)
    if hasattr(model_wrapper, '_orig_mod'):
        transformer = model_wrapper._orig_mod.model  # type: ignore
        
        # Create a new model structure manually for OptimizedModule
        ret_model = model.__class__(  # type: ignore
            model.model, model.load_device, model.offload_device,  # type: ignore
            model.size, model.weight_inplace_update  # type: ignore
        )
        ret_model.model = model.model  # type: ignore
        
        # Create a new ComfyQwenImageWrapper manually
        original_wrapper = model_wrapper._orig_mod  # type: ignore
        ret_model_wrapper = ComfyQwenImageWrapper(  # type: ignore
            transformer,
            original_wrapper.config,  # type: ignore
            original_wrapper.customized_forward,  # type: ignore
            original_wrapper.forward_kwargs,  # type: ignore
            original_wrapper.cpu_offload_setting,  # type: ignore
            original_wrapper.vram_margin_gb  # type: ignore
        )
        
        # Copy internal state from original wrapper
        ret_model_wrapper._prev_timestep = original_wrapper._prev_timestep  # type: ignore
        ret_model_wrapper._cache_context = original_wrapper._cache_context  # type: ignore
        
        ret_model.model.diffusion_model = ret_model_wrapper  # type: ignore
    else:
        # Non-OptimizedModule case
        transformer = model_wrapper.model  # type: ignore
        
        # Create a new ModelPatcher with the same parameters
        ret_model = model.__class__(  # type: ignore
            model.model, model.load_device, model.offload_device,  # type: ignore
            model.size, model.weight_inplace_update  # type: ignore
        )
        
        # Create a new ComfyQwenImageWrapper manually
        original_wrapper = model_wrapper
        ret_model_wrapper = ComfyQwenImageWrapper(  # type: ignore
            transformer,
            original_wrapper.config,  # type: ignore
            original_wrapper.customized_forward,  # type: ignore
            original_wrapper.forward_kwargs,  # type: ignore
            original_wrapper.cpu_offload_setting,  # type: ignore
            original_wrapper.vram_margin_gb  # type: ignore
        )
        
        # Copy internal state from original wrapper
        ret_model_wrapper._prev_timestep = original_wrapper._prev_timestep  # type: ignore
        ret_model_wrapper._cache_context = original_wrapper._cache_context  # type: ignore
        
        ret_model.model.diffusion_model = ret_model_wrapper  # type: ignore
    
    # Restore transformer to the original wrapper (important for original model integrity)
    if hasattr(model_wrapper, '_orig_mod'):
        model_wrapper._orig_mod.model = transformer  # type: ignore
    else:
        model_wrapper.model = transformer  # type: ignore
    
    # Set transformer to the new wrapper
    ret_model_wrapper.model = transformer  # type: ignore

    # Clear existing LoRA list in the new wrapper
    ret_model_wrapper.loras = []  # type: ignore

    # Add all LoRAs to the wrapper's LoRA list
    # lora_params format: [(lora_name, model_strength, clip_strength), ...]
    # For Nunchaku Qwen, we use model_strength as the LoRA strength
    lora_names_list = []
    for lora_name, model_strength, clip_strength in lora_params:
        lora_path = folder_paths.get_full_path_or_raise("loras", lora_name)
        ret_model_wrapper.loras.append((lora_path, model_strength))  # type: ignore
        lora_names_list.append(lora_name)

    # For Nunchaku Qwen, CLIP is not modified (Qwen doesn't use separate CLIP)
    return (ret_model, clip, _build_lora_string(lora_params))


def _apply_lora_stack_nunchaku_zimage(model, clip, lora_params):
    # Apply LoRAs to ZImage model using standard ComfyUI LoRA loading.
    # ZImageModelPatcher overrides patch_weight_to_device() to handle SVDQ quantized layers,
    # so we can use comfy.sd.load_lora_for_models() which calls add_patches() internally.
    #
    # Args:
    #     model: The input model (ZImageModelPatcher instance)
    #     clip: The CLIP model (may be None for ZImage)
    #     lora_params: List of (lora_name, model_strength, clip_strength) tuples
    #
    # Returns:
    #     tuple: (modified_model, modified_clip, lora_string)
    
    import comfy.sd #type: ignore
    import comfy.utils #type: ignore
    
    # Clone the model to avoid modifying the original
    ret_model = model.clone()
    ret_clip = clip
    
    lora_names_list = []
    
    # Apply each LoRA in the stack
    for lora_name, model_strength, clip_strength in lora_params:
        if lora_name == "None":
            continue
            
        # Get the LoRA file path
        lora_path = folder_paths.get_full_path("loras", lora_name)
        
        if lora_path is None:
            log.warning(_LOG_PREFIX, f"LoRA file not found: {lora_name}")
            continue
        
        # Load and apply the LoRA using ComfyUI's standard method
        # This works with ZImageModelPatcher because it overrides patch_weight_to_device()
        # to handle SVDQ quantized layers (fused QKV, fused w13, backup/restore)
        try:
            lora_data = comfy.utils.load_torch_file(lora_path)
            
            if clip_strength is None:
                # model_only_lora mode: apply to model only, pass clip through unchanged
                ret_model, _ = comfy.sd.load_lora_for_models(
                    ret_model, 
                    None, 
                    lora_data, 
                    model_strength, 
                    0.0
                )
                log.msg(_LOG_PREFIX, f"Applied ZImage LoRA: {lora_name} (model: {model_strength}, clip: skipped)")
            else:
                ret_model, ret_clip = comfy.sd.load_lora_for_models(
                    ret_model, 
                    ret_clip, 
                    lora_data, 
                    model_strength, 
                    clip_strength
                )
                log.msg(_LOG_PREFIX, f"Applied ZImage LoRA: {lora_name} (model: {model_strength}, clip: {clip_strength})")
            lora_names_list.append(lora_name)
        except Exception as e:
            log.error(_LOG_PREFIX, f"Failed to load LoRA {lora_name}: {str(e)}")
            continue
    
    # ZImage models may or may not have separate CLIP
    return (ret_model, ret_clip, _build_lora_string(lora_params))


class RvTools_LoraStack_Apply(io.ComfyNode):

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Lora Stack apply [Eclipse]",
            display_name="Lora Stack apply",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            inputs=[
                io.Model.Input("model"),
                io.Clip.Input("clip", optional=True),
                io.Custom("LORA_STACK").Input("lora_stack"),
            ],
            outputs=[
                io.Model.Output("MODEL"),
                io.Clip.Output("CLIP"),
                io.String.Output("lora_names"),
            ],
        )

    @classmethod
    def execute(cls, model, clip=None, lora_stack=None):
 
        # Initialise the list
        lora_params = list()
 
        # Extend lora_params with lora-stack items 
        if lora_stack:
            lora_params.extend(lora_stack)
        else:
            return io.NodeOutput(model, clip, "")

        # Check if this is a Nunchaku Qwen model
        if is_nunchaku_qwen_model(model):
            log.msg(_LOG_PREFIX, "Detected Nunchaku Qwen model, applying LoRAs via ComfyQwenImageWrapper")
            return io.NodeOutput(*_apply_lora_stack_nunchaku_qwen(model, clip, lora_params))
        # Check if this is a Nunchaku ZImage model
        elif is_nunchaku_zimage_model(model):
            log.msg(_LOG_PREFIX, "Detected Nunchaku ZImage model, applying LoRAs via ZImageModelPatcher")
            return io.NodeOutput(*_apply_lora_stack_nunchaku_zimage(model, clip, lora_params))
        # Check if this is a Nunchaku Flux model
        elif is_nunchaku_flux_model(model):
            log.msg(_LOG_PREFIX, "Detected Nunchaku Flux model, applying LoRAs via ComfyFluxWrapper")
            return io.NodeOutput(*_apply_lora_stack_nunchaku_flux(model, clip, lora_params))
        else:
            # Standard model - use ComfyUI's load_lora_for_models
            return io.NodeOutput(*_apply_lora_stack_standard(model, clip, lora_params))