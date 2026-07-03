from __future__ import annotations

# Shared utilities for Model Loader and Model Loader Pipe nodes
#
# Contains: folder registration, Nunchaku detection, LoRA application,
# model loading logic, BlockSwap application, schema input definitions

from typing import Any
import os

import torch  # type: ignore
import comfy  # type: ignore
import comfy.sd  # type: ignore
import comfy.utils  # type: ignore
import comfy.model_sampling  # type: ignore
import comfy.latent_formats  # type: ignore
import comfy.model_management  # type: ignore
import comfy.model_patcher  # type: ignore
import comfy.taesd.taesd  # type: ignore
import comfy.ldm.wan.vae2_2  # type: ignore
import folder_paths  # type: ignore

from comfy.ldm.models.autoencoder import AutoencoderKL, AutoencodingEngine  # type: ignore

from .common import cleanup_memory_before_load
from .logger import log
from comfy_api.latest import io  # type: ignore
from comfy.patcher_extension import CallbacksMP  # type: ignore

from .nunchaku_wrapper import (
    NUNCHAKU_AVAILABLE,
    detect_nunchaku_model,
    load_nunchaku_model,
    get_nunchaku_info
)

from .gguf_wrapper import (
    GGUF_AVAILABLE,
    detect_gguf_model,
    load_gguf_model,
    load_gguf_clip,
)


# ── Folder path registration (runs once on import) ───────────────────

if GGUF_AVAILABLE:
    base = folder_paths.folder_names_and_paths.get("diffusion_models_gguf", ([], {}))
    base = base[0] if isinstance(base[0], (list, set, tuple)) else []
    orig, _ = folder_paths.folder_names_and_paths.get("diffusion_models", ([], {}))
    folder_paths.folder_names_and_paths["diffusion_models_gguf"] = (orig or base, {".gguf"})

    # Add .gguf extension to clip folder
    if "clip" in folder_paths.folder_names_and_paths:
        _clip_data = folder_paths.folder_names_and_paths["clip"]
        _clip_paths, _clip_exts = _clip_data[0], _clip_data[1] if len(_clip_data) >= 2 else ([], {})
        if ".gguf" not in _clip_exts:
            _clip_exts = set(_clip_exts) if isinstance(_clip_exts, set) else set(_clip_exts.keys()) if isinstance(_clip_exts, dict) else set()
            _clip_exts.add(".gguf")
            folder_paths.folder_names_and_paths["clip"] = (_clip_paths, _clip_exts)
            if hasattr(folder_paths, 'filename_list_cache') and "clip" in folder_paths.filename_list_cache:
                del folder_paths.filename_list_cache["clip"]
            if hasattr(folder_paths, 'cache_helper'):
                folder_paths.cache_helper.clear()

    # Add .gguf extension to text_encoders folder
    if "text_encoders" in folder_paths.folder_names_and_paths:
        _te_data = folder_paths.folder_names_and_paths["text_encoders"]
        _te_paths, _te_exts = _te_data[0], _te_data[1] if len(_te_data) >= 2 else ([], {})
        if ".gguf" not in _te_exts:
            _te_exts = set(_te_exts) if isinstance(_te_exts, set) else set(_te_exts.keys()) if isinstance(_te_exts, dict) else set()
            _te_exts.add(".gguf")
            folder_paths.folder_names_and_paths["text_encoders"] = (_te_paths, _te_exts)
            if hasattr(folder_paths, 'filename_list_cache') and "text_encoders" in folder_paths.filename_list_cache:
                del folder_paths.filename_list_cache["text_encoders"]
            if hasattr(folder_paths, 'cache_helper'):
                folder_paths.cache_helper.clear()

for _folder_name in ["checkpoints", "diffusion_models"]:
    if _folder_name in folder_paths.folder_names_and_paths:
        _folder_data = folder_paths.folder_names_and_paths[_folder_name]
        if len(_folder_data) >= 2:
            _paths, _exts = _folder_data[0], _folder_data[1]
        else:
            continue
        _exts = set(_exts) if isinstance(_exts, set) else set(_exts.keys()) if isinstance(_exts, dict) else set()
        for _ext in [".safetensors", ".sft", ".ckpt", ".pt", ".bin"]:
            _exts.add(_ext)
        folder_paths.folder_names_and_paths[_folder_name] = (_paths, _exts)


# ── Feature options for Model Loader chip widget ──────────────────────

MODEL_LOADER_FEATURE_OPTIONS = [
    "lora",
    "model_sampling",
    "block_swap",
    "memory_cleanup",
]

MODEL_LOADER_DEFAULT_FEATURES = ["memory_cleanup"]


# ── Nunchaku detection ────────────────────────────────────────────────

def is_nunchaku_model(model: Any) -> bool:
    # Check if a model is a Nunchaku model (FLUX, Qwen, or ZImage) by detecting wrapper/patcher class
    if is_zimage_model(model):
        return True
    try:
        model_wrapper = model.model.diffusion_model
        if hasattr(model_wrapper, '_orig_mod'):
            actual_wrapper = model_wrapper._orig_mod
            wrapper_class_name = type(actual_wrapper).__name__
            return wrapper_class_name in ('ComfyFluxWrapper', 'ComfyQwenImageWrapper')
        else:
            wrapper_class_name = type(model_wrapper).__name__
            return wrapper_class_name in ('ComfyFluxWrapper', 'ComfyQwenImageWrapper')
    except Exception:
        return False


def is_zimage_model(model: Any) -> bool:
    # Check if a model is a Nunchaku ZImage model by detecting ZImageModelPatcher.
    # ZImage uses a custom ModelPatcher subclass directly, not a diffusion_model wrapper.
    try:
        return type(model).__name__ == 'ZImageModelPatcher'
    except Exception:
        return False


# ── LoRA application ──────────────────────────────────────────────────

def apply_loras(model: Any, clip: Any, lora_params: list) -> tuple:
    # Apply LoRAs to model (standard, Nunchaku Flux/Qwen, or ZImage).
    # Returns (modified_model, modified_clip).
    if not lora_params:
        return (model, clip)

    if is_zimage_model(model):
        log.msg("LoRA", "Detected Nunchaku ZImage model, applying LoRAs via standard ComfyUI loader")
        return _apply_loras_zimage(model, clip, lora_params)
    elif is_nunchaku_model(model):
        log.msg("LoRA", "Detected Nunchaku model, applying LoRAs via wrapper")
        return _apply_loras_nunchaku(model, clip, lora_params)
    else:
        log.msg("LoRA", "Applying LoRAs to standard model")
        return _apply_loras_standard(model, clip, lora_params)


def _apply_loras_standard(model: Any, clip: Any, lora_params: list) -> tuple:
    # Apply LoRAs to standard (non-Nunchaku) models using ComfyUI's loader
    model_lora = model
    clip_lora = clip

    for lora_name, model_weight in lora_params:
        lora_path = folder_paths.get_full_path("loras", lora_name)
        lora = comfy.utils.load_torch_file(lora_path, safe_load=True)
        model_lora, clip_lora = comfy.sd.load_lora_for_models(
            model_lora, clip_lora, lora, model_weight, model_weight
        )
        log.msg("LoRA", f"Applied {lora_name} with weight {model_weight}")

    return (model_lora, clip_lora)


def _apply_loras_zimage(model: Any, clip: Any, lora_params: list) -> tuple:
    # Apply LoRAs to Nunchaku ZImage models using standard ComfyUI loader.
    # ZImageModelPatcher overrides patch_weight_to_device() to handle SVDQ quantized layers,
    # so comfy.sd.load_lora_for_models() works correctly via add_patches().
    ret_model = model.clone()
    ret_clip = clip

    for lora_name, model_weight in lora_params:
        lora_path = folder_paths.get_full_path("loras", lora_name)
        if not lora_path:
            log.warning("LoRA", f"ZImage LoRA file not found: {lora_name}")
            continue
        try:
            lora = comfy.utils.load_torch_file(lora_path, safe_load=True)
            ret_model, ret_clip = comfy.sd.load_lora_for_models(ret_model, ret_clip, lora, model_weight, model_weight)
            log.msg("LoRA", f"Applied ZImage LoRA {lora_name} with weight {model_weight}")
        except Exception as e:
            log.error("LoRA", f"Failed to apply ZImage LoRA {lora_name}: {e}")

    return (ret_model, ret_clip)


def _apply_loras_nunchaku(model: Any, clip: Any, lora_params: list) -> tuple:
    # Apply LoRAs to Nunchaku models (FLUX or Qwen) via wrapper
    try:
        from .nunchaku_wrapper import ComfyFluxWrapper, ComfyQwenImageWrapper
    except ImportError as e:
        log.warning("LoRA", f"Nunchaku wrappers not available for LoRA application: {e}")
        return (model, clip)

    if ComfyFluxWrapper is None:
        log.warning("LoRA", "ComfyFluxWrapper is not available")
        return (model, clip)

    model_wrapper = model.model.diffusion_model

    if hasattr(model_wrapper, '_orig_mod'):
        actual_wrapper = model_wrapper._orig_mod
        wrapper_class_name = type(actual_wrapper).__name__
    else:
        actual_wrapper = model_wrapper
        wrapper_class_name = type(model_wrapper).__name__

    is_qwen = (wrapper_class_name == 'ComfyQwenImageWrapper')
    is_flux = (wrapper_class_name == 'ComfyFluxWrapper')

    if not (is_qwen or is_flux):
        log.warning("LoRA", f"Unknown wrapper type: {wrapper_class_name}")
        return (model, clip)

    # ── Qwen LoRA ──
    if is_qwen:
        log.msg("LoRA", "Applying LoRAs to Qwen model via ComfyQwenImageWrapper")
        if hasattr(model_wrapper, '_orig_mod'):
            wrapper = model_wrapper._orig_mod
        else:
            wrapper = model_wrapper
        wrapper.loras = []
        for lora_name, model_weight in lora_params:
            lora_path = folder_paths.get_full_path_or_raise("loras", lora_name)
            wrapper.loras.append((lora_path, model_weight))
            log.msg("LoRA", f"Applied Qwen LoRA {lora_name} with weight {model_weight}")
        return (model, clip)

    # ── Flux LoRA ──
    log.msg("LoRA", "Applying LoRAs to Flux model via ComfyFluxWrapper")

    try:
        from nunchaku.lora.flux import to_diffusers  # type: ignore
    except ImportError as e:
        log.warning("LoRA", f"nunchaku.lora.flux not available: {e}")
        return (model, clip)

    if hasattr(model_wrapper, '_orig_mod'):
        transformer = model_wrapper._orig_mod.model
        ret_model = model.__class__(
            model.model, model.load_device, model.offload_device,
            model.size, model.weight_inplace_update
        )
        ret_model.model = model.model
        original_wrapper = model_wrapper._orig_mod
        ret_model_wrapper = ComfyFluxWrapper(
            transformer,
            config=original_wrapper.config,
            pulid_pipeline=original_wrapper.pulid_pipeline,
            customized_forward=original_wrapper.customized_forward,
            forward_kwargs=original_wrapper.forward_kwargs,
            ctx_for_copy=getattr(original_wrapper, 'ctx_for_copy', {}),
        )
        ret_model_wrapper._prev_timestep = original_wrapper._prev_timestep
        ret_model_wrapper._cache_context = original_wrapper._cache_context
        if hasattr(original_wrapper, '_original_time_text_embed'):
            ret_model_wrapper._original_time_text_embed = original_wrapper._original_time_text_embed
        ret_model.model.diffusion_model = ret_model_wrapper
    else:
        transformer = model_wrapper.model
        ret_model = model.__class__(
            model.model, model.load_device, model.offload_device,
            model.size, model.weight_inplace_update
        )
        original_wrapper = model_wrapper
        ret_model_wrapper = ComfyFluxWrapper(
            transformer,
            config=original_wrapper.config,
            pulid_pipeline=original_wrapper.pulid_pipeline,
            customized_forward=original_wrapper.customized_forward,
            forward_kwargs=original_wrapper.forward_kwargs,
            ctx_for_copy=getattr(original_wrapper, 'ctx_for_copy', {}),
        )
        ret_model_wrapper._prev_timestep = original_wrapper._prev_timestep
        ret_model_wrapper._cache_context = original_wrapper._cache_context
        if hasattr(original_wrapper, '_original_time_text_embed'):
            ret_model_wrapper._original_time_text_embed = original_wrapper._original_time_text_embed
        ret_model.model.diffusion_model = ret_model_wrapper

    # Restore transformer to original wrapper
    if hasattr(model_wrapper, '_orig_mod'):
        model_wrapper._orig_mod.model = transformer
    else:
        model_wrapper.model = transformer

    ret_model_wrapper.model = transformer
    ret_model_wrapper.loras = []

    max_in_channels = ret_model.model.model_config.unet_config["in_channels"]

    for lora_name, model_weight in lora_params:
        lora_path = folder_paths.get_full_path_or_raise("loras", lora_name)
        ret_model_wrapper.loras.append((lora_path, model_weight))
        log.msg("LoRA", f"Applied Nunchaku LoRA {lora_name} with weight {model_weight}")
        sd = to_diffusers(lora_path)
        if "transformer.x_embedder.lora_A.weight" in sd:
            new_in_channels = sd["transformer.x_embedder.lora_A.weight"].shape[1]
            assert new_in_channels % 4 == 0, f"Invalid LoRA input channels: {new_in_channels}"
            new_in_channels = new_in_channels // 4
            max_in_channels = max(max_in_channels, new_in_channels)

    if max_in_channels > ret_model.model.model_config.unet_config["in_channels"]:
        ret_model.model.model_config.unet_config["in_channels"] = max_in_channels

    return (ret_model, clip)


# ── Latent channel / downscale detection ──────────────────────────────

LATENT_CHANNELS = 4
LATENT_DOWNSCALE = 8

def detect_latent_channels(vae_obj) -> int:
    # Infer latent channel count from a VAE-like object.
    try:
        if hasattr(vae_obj, 'channels') and isinstance(getattr(vae_obj, 'channels'), int):
            return getattr(vae_obj, 'channels')
        if hasattr(vae_obj, 'latent_channels') and isinstance(getattr(vae_obj, 'latent_channels'), int):
            return getattr(vae_obj, 'latent_channels')
        for attr in ('encoder', 'conv_in', 'down_blocks'):
            sub = getattr(vae_obj, attr, None)
            if sub is not None and hasattr(sub, 'weight'):
                return sub.weight.shape[0]
    except Exception:
        pass
    return LATENT_CHANNELS


def detect_latent_downscale(vae_obj) -> int:
    # Infer spatial downscale ratio from a VAE-like object.
    # Returns an integer ratio (default 8). Tuple/lambda values
    # (used by some video VAEs) are ignored in favour of the default.
    try:
        ratio = getattr(vae_obj, 'downscale_ratio', None)
        if isinstance(ratio, int) and ratio > 0:
            return ratio
    except Exception:
        pass
    return LATENT_DOWNSCALE


# ── LoRA helpers ──────────────────────────────────────────────────────

def collect_lora_params(kwargs: dict, lora_count: int) -> list[tuple]:
    # Collect enabled LoRA parameters from kwargs.
    # Returns list of (lora_name, model_weight) tuples.
    # Respects lora_switch_N booleans — if present and False, skip that slot.
    params = []
    for i in range(1, lora_count + 1):
        lora_switch = kwargs.get(f'lora_switch_{i}', True)
        if not lora_switch:
            continue
        lora_name = kwargs.get(f'lora_name_{i}', 'None')
        lora_weight = kwargs.get(f'lora_weight_{i}', 1.0)
        if lora_name not in (None, '', 'None'):
            params.append((lora_name, lora_weight))
    return params


def format_lora_string(lora_params: list[tuple]) -> str:
    # Generate LoRA metadata string for pipe.
    if not lora_params:
        return ""
    return ' '.join(f"<lora:{name}:{weight}:{weight}>" for name, weight in lora_params)


# ── Model sampling ────────────────────────────────────────────────────

def apply_model_sampling(model, sampling_method: str, shift: float, base_shift: float = 0.5,
                         width: int = 1024, height: int = 1024, original_timesteps: int = 50,
                         zsnr: bool = False, sampling_subtype: str = "eps",
                         sigma_max: float = 120.0, sigma_min: float = 0.002):
    # Apply model sampling configuration based on method.
    if sampling_method == "None" or not sampling_method:
        return model

    if sampling_method == "SD3":
        return _apply_sd3_sampling(model, shift=shift, multiplier=1000.0)
    elif sampling_method == "AuraFlow":
        return _apply_auraflow_sampling(model, shift=shift, multiplier=1.0)
    elif sampling_method == "Flux":
        return _apply_flux_sampling(model, max_shift=shift, base_shift=base_shift, width=width, height=height)
    elif sampling_method == "Stable Cascade":
        return _apply_stable_cascade_sampling(model, shift=shift)
    elif sampling_method == "LCM":
        return _apply_lcm_sampling(model, original_timesteps=original_timesteps, zsnr=zsnr)
    elif sampling_method == "ContinuousEDM":
        return _apply_continuous_edm_sampling(model, sampling_subtype=sampling_subtype, sigma_max=sigma_max, sigma_min=sigma_min)
    elif sampling_method == "ContinuousV":
        return _apply_continuous_v_sampling(model, sigma_max=sigma_max, sigma_min=sigma_min)
    elif sampling_method == "LTXV":
        return _apply_ltxv_sampling(model, max_shift=shift, base_shift=base_shift)
    else:
        log.warning("Model Sampling", f"Unknown sampling method '{sampling_method}', skipping")
        return model


def _apply_sd3_sampling(model, shift: float, multiplier: float = 1000.0):
    m = model.clone()
    sampling_base = comfy.model_sampling.ModelSamplingDiscreteFlow
    sampling_type = comfy.model_sampling.CONST

    class ModelSamplingAdvanced(sampling_base, sampling_type):  # type: ignore[misc,valid-type]
        pass

    model_sampling = ModelSamplingAdvanced(model.model.model_config)
    model_sampling.set_parameters(shift=shift, multiplier=multiplier)
    m.add_object_patch("model_sampling", model_sampling)
    log.msg("Model Sampling", f"Applied SD3 sampling: shift={shift}, multiplier={multiplier}")
    return m


def _apply_auraflow_sampling(model, shift: float, multiplier: float = 1.0):
    m = model.clone()
    sampling_base = comfy.model_sampling.ModelSamplingDiscreteFlow
    sampling_type = comfy.model_sampling.CONST

    class ModelSamplingAdvanced(sampling_base, sampling_type):  # type: ignore[misc,valid-type]
        pass

    model_sampling = ModelSamplingAdvanced(model.model.model_config)
    model_sampling.set_parameters(shift=shift, multiplier=multiplier)
    m.add_object_patch("model_sampling", model_sampling)
    log.msg("Model Sampling", f"Applied AuraFlow sampling: shift={shift}, multiplier={multiplier}")
    return m


def _apply_flux_sampling(model, max_shift: float, base_shift: float, width: int, height: int):
    m = model.clone()
    x1 = 256
    x2 = 4096
    mm = (max_shift - base_shift) / (x2 - x1)
    b = base_shift - mm * x1
    shift = (width * height / (8 * 8 * 2 * 2)) * mm + b

    sampling_base = comfy.model_sampling.ModelSamplingFlux
    sampling_type = comfy.model_sampling.CONST

    class ModelSamplingAdvanced(sampling_base, sampling_type):  # type: ignore[misc,valid-type]
        pass

    model_sampling = ModelSamplingAdvanced(model.model.model_config)
    model_sampling.set_parameters(shift=shift)
    m.add_object_patch("model_sampling", model_sampling)
    log.msg("Model Sampling", f"Applied Flux sampling: max_shift={max_shift}, base_shift={base_shift}, width={width}, height={height}, calculated_shift={shift:.4f}")
    return m


def _apply_stable_cascade_sampling(model, shift: float):
    m = model.clone()
    sampling_base = comfy.model_sampling.StableCascadeSampling
    sampling_type = comfy.model_sampling.EPS

    class ModelSamplingAdvanced(sampling_base, sampling_type):  # type: ignore[misc,valid-type]
        pass

    model_sampling = ModelSamplingAdvanced(model.model.model_config)
    model_sampling.set_parameters(shift=shift)
    m.add_object_patch("model_sampling", model_sampling)
    log.msg("Model Sampling", f"Applied Stable Cascade sampling: shift={shift}")
    return m


def _apply_lcm_sampling(model, original_timesteps: int = 50, zsnr: bool = False):
    m = model.clone()

    class LCM(comfy.model_sampling.EPS):
        def calculate_denoised(self, sigma, model_output, model_input):
            timestep = self.timestep(sigma).view(sigma.shape[:1] + (1,) * (model_output.ndim - 1))
            sigma = sigma.view(sigma.shape[:1] + (1,) * (model_output.ndim - 1))
            x0 = model_input - model_output * sigma
            sigma_data = 0.5
            scaled_timestep = timestep * 10.0
            c_skip = sigma_data**2 / (scaled_timestep**2 + sigma_data**2)
            c_out = scaled_timestep / (scaled_timestep**2 + sigma_data**2) ** 0.5
            return c_out * x0 + c_skip * model_input

    class ModelSamplingDiscreteDistilled(comfy.model_sampling.ModelSamplingDiscrete):
        def __init__(self, model_config=None):
            super().__init__(model_config, zsnr=zsnr)
            self.original_timesteps = original_timesteps
            self.skip_steps = self.num_timesteps // self.original_timesteps
            sigmas_valid = torch.zeros((self.original_timesteps), dtype=torch.float32)
            for x in range(self.original_timesteps):
                sigmas_valid[self.original_timesteps - 1 - x] = self.sigmas[self.num_timesteps - 1 - x * self.skip_steps]
            self.set_sigmas(sigmas_valid)

        def timestep(self, sigma):
            log_sigma = sigma.log()
            dists = log_sigma.to(self.log_sigmas.device) - self.log_sigmas[:, None]
            return (dists.abs().argmin(dim=0).view(sigma.shape) * self.skip_steps + (self.skip_steps - 1)).to(sigma.device)

        def sigma(self, timestep):
            t = torch.clamp(((timestep.float().to(self.log_sigmas.device) - (self.skip_steps - 1)) / self.skip_steps).float(), min=0, max=(len(self.sigmas) - 1))
            low_idx = t.floor().long()
            high_idx = t.ceil().long()
            w = t.frac()
            log_sigma = (1 - w) * self.log_sigmas[low_idx] + w * self.log_sigmas[high_idx]
            return log_sigma.exp().to(timestep.device)

    sampling_base = ModelSamplingDiscreteDistilled
    sampling_type = LCM

    class ModelSamplingAdvanced(sampling_base, sampling_type):  # type: ignore[misc,valid-type]
        pass

    model_sampling = ModelSamplingAdvanced(model.model.model_config)
    m.add_object_patch("model_sampling", model_sampling)
    log.msg("Model Sampling", f"Applied LCM sampling: original_timesteps={original_timesteps}, zsnr={zsnr}")
    return m


def _apply_continuous_edm_sampling(model, sampling_subtype: str = "eps", sigma_max: float = 120.0, sigma_min: float = 0.002):
    m = model.clone()
    sampling_base = comfy.model_sampling.ModelSamplingContinuousEDM
    latent_format = None
    sigma_data = 1.0

    if sampling_subtype == "eps":
        sampling_type = comfy.model_sampling.EPS
    elif sampling_subtype == "edm" or sampling_subtype == "edm_playground_v2.5":
        sampling_type = comfy.model_sampling.EDM
        sigma_data = 0.5
        if sampling_subtype == "edm_playground_v2.5":
            latent_format = comfy.latent_formats.SDXL_Playground_2_5()
    elif sampling_subtype == "v_prediction":
        sampling_type = comfy.model_sampling.V_PREDICTION
    elif sampling_subtype == "cosmos_rflow":
        sampling_type = comfy.model_sampling.COSMOS_RFLOW
        sampling_base = comfy.model_sampling.ModelSamplingCosmosRFlow
    else:
        log.warning("Model Sampling", f"Unknown ContinuousEDM subtype '{sampling_subtype}', using eps")
        sampling_type = comfy.model_sampling.EPS

    class ModelSamplingAdvanced(sampling_base, sampling_type):  # type: ignore[misc,valid-type]
        pass

    model_sampling = ModelSamplingAdvanced(model.model.model_config)
    model_sampling.set_parameters(sigma_min, sigma_max, sigma_data)
    m.add_object_patch("model_sampling", model_sampling)
    if latent_format is not None:
        m.add_object_patch("latent_format", latent_format)
    log.msg("Model Sampling", f"Applied ContinuousEDM sampling: subtype={sampling_subtype}, sigma_max={sigma_max}, sigma_min={sigma_min}, sigma_data={sigma_data}")
    return m


def _apply_continuous_v_sampling(model, sigma_max: float = 500.0, sigma_min: float = 0.03):
    m = model.clone()
    sampling_type = comfy.model_sampling.V_PREDICTION
    sigma_data = 1.0

    class ModelSamplingAdvanced(comfy.model_sampling.ModelSamplingContinuousV, sampling_type):  # type: ignore[misc,valid-type]
        pass

    model_sampling = ModelSamplingAdvanced(model.model.model_config)
    model_sampling.set_parameters(sigma_min, sigma_max, sigma_data)
    m.add_object_patch("model_sampling", model_sampling)
    log.msg("Model Sampling", f"Applied ContinuousV sampling: sigma_max={sigma_max}, sigma_min={sigma_min}")
    return m


def _apply_ltxv_sampling(model, max_shift: float = 2.05, base_shift: float = 0.95):
    m = model.clone()
    tokens = 4096
    x1 = 1024
    x2 = 4096
    mm = (max_shift - base_shift) / (x2 - x1)
    b = base_shift - mm * x1
    shift = tokens * mm + b

    sampling_base = comfy.model_sampling.ModelSamplingFlux
    sampling_type = comfy.model_sampling.CONST

    class ModelSamplingAdvanced(sampling_base, sampling_type):  # type: ignore[misc,valid-type]
        pass

    model_sampling = ModelSamplingAdvanced(model.model.model_config)
    model_sampling.set_parameters(shift=shift)
    m.add_object_patch("model_sampling", model_sampling)
    log.msg("Model Sampling", f"Applied LTXV sampling: max_shift={max_shift}, base_shift={base_shift}, tokens={tokens}, calculated_shift={shift:.4f}")
    return m


# ── BlockSwap helper ──────────────────────────────────────────────────

def apply_blockswap(model, blocks_to_swap: int, offload_embeddings: bool,
                    log_prefix: str, is_nunchaku: bool = False,
                    is_qwen: bool = False, is_zimage: bool = False):
    # Apply block swap to offload transformer blocks to CPU.
    # Nunchaku models are skipped as they handle their own offloading.
    if model is None or blocks_to_swap <= 0:
        return model
    if is_nunchaku or is_qwen or is_zimage:
        return model

    # ComfyUI 0.18.0+ dynamic VRAM handles offloading natively
    if model.is_dynamic() and hasattr(model, 'backup_buffers'):
        log.msg(log_prefix, "BlockSwap: native dynamic VRAM active — not needed")
        return model

    from ..py.RvTools_BlockSwap import (
        _detect_block_groups, _count_blocks, _get_model_arch_name,
        _make_swap_callback,
    )
    diff_model = getattr(model.model, "diffusion_model", None)
    if diff_model is not None:
        groups = _detect_block_groups(diff_model)
        total = _count_blocks(groups)
        arch = _get_model_arch_name(model)
        if total > 0:
            actual = min(blocks_to_swap, total)
            log.msg(log_prefix, f"BlockSwap: {arch} — {total} blocks, "
                    f"will offload {actual} on next load")
            model = model.clone()
            model.add_callback(
                CallbacksMP.ON_LOAD,
                _make_swap_callback(blocks_to_swap, offload_embeddings),
            )
        else:
            log.warning(log_prefix, f"BlockSwap: {arch} has no recognized "
                        f"block structure — skipping")
    return model


# ── Pipe builder ──────────────────────────────────────────────────────

class _OmitType:
    # Sentinel for build_pipe() — keys with this value are excluded from the pipe dict
    __slots__ = ()
    def __repr__(self):
        return "OMIT"
    def __bool__(self):
        return False

OMIT = _OmitType()


def build_pipe(**kwargs) -> dict:
    # Build pipe dict, excluding keys with OMIT value.
    # Pass OMIT as a value to exclude that key from the pipe.
    return {k: v for k, v in kwargs.items() if not isinstance(v, _OmitType)}

def get_model_loader_inputs() -> list:
    # Returns the shared input list used by both Model Loader and Model Loader Pipe
    loras = ["None"] + folder_paths.get_filename_list("loras")

    return [
        io.String.Input("features", default=",".join(MODEL_LOADER_DEFAULT_FEATURES), socketless=True,
            tooltip="Comma-separated feature list. JS combo-chip replaces this widget.",
        ),
        io.Combo.Input("model_type", options=["Standard Checkpoint", "UNet Model", "Nunchaku Flux", "Nunchaku Qwen", "Nunchaku ZImage", "GGUF Model"], default="Standard Checkpoint", tooltip="Select model format"),
        io.Combo.Input("ckpt_name", options=["None"] + folder_paths.get_filename_list("checkpoints"), default="None", tooltip="Select checkpoint file"),
        io.Combo.Input("unet_name", options=["None"] + folder_paths.get_filename_list("diffusion_models"), default="None", tooltip="Select UNet diffusion model"),
        io.Combo.Input("nunchaku_name", options=["None"] + folder_paths.get_filename_list("diffusion_models"), default="None", tooltip="Select Nunchaku Flux model"),
        io.Combo.Input("qwen_name", options=["None"] + folder_paths.get_filename_list("diffusion_models"), default="None", tooltip="Select Nunchaku Qwen model"),
        io.Combo.Input("zimage_name", options=["None"] + folder_paths.get_filename_list("diffusion_models"), default="None", tooltip="Select Nunchaku ZImage model"),
        io.Combo.Input("gguf_name", options=["None"] + (folder_paths.get_filename_list("diffusion_models_gguf") if "diffusion_models_gguf" in folder_paths.folder_names_and_paths else []), default="None", tooltip="Select GGUF model"),
        io.Combo.Input("weight_dtype", options=["default", "fp8_e4m3fn", "fp8_e4m3fn_fast", "fp8_e5m2"], default="default", tooltip="Weight dtype for UNet model"),
        io.Combo.Input("data_type", options=["bfloat16", "float16"], default="bfloat16", tooltip="Model data type for Nunchaku"),
        io.Float.Input("cache_threshold", default=0.0, min=0.0, max=1.0, step=0.001, tooltip="Cache threshold for Nunchaku"),
        io.Combo.Input("attention", options=["flash-attention2", "nunchaku-fp16"], default="flash-attention2", tooltip="Attention implementation"),
        io.Combo.Input("i2f_mode", options=["enabled", "always"], default="enabled", tooltip="GEMM implementation"),
        io.Combo.Input("cpu_offload", options=["auto", "enable", "disable"], default="auto", tooltip="CPU offload"),
        io.Int.Input("num_blocks_on_gpu", default=30, min=1, max=60, step=1, tooltip="Blocks on GPU (Nunchaku Qwen/ZImage)"),
        io.Combo.Input("use_pin_memory", options=["enable", "disable"], default="enable", tooltip="Use pinned memory"),
        io.Combo.Input("gguf_dequant_dtype", options=["default", "target", "float32", "float16", "bfloat16"], default="default", tooltip="Dequantization dtype"),
        io.Combo.Input("gguf_patch_dtype", options=["default", "target", "float32", "float16", "bfloat16"], default="default", tooltip="LoRA patch dtype"),
        io.Boolean.Input("gguf_patch_on_device", default=False, label_on="yes", label_off="no", tooltip="Apply patches on GPU"),
        io.Boolean.Input("enable_clip_layer", default=True, label_on="yes", label_off="no", tooltip="Trim baked CLIP to specific layer (Standard Checkpoint only)"),
        io.Int.Input("stop_at_clip_layer", default=-2, min=-24, max=-1, step=1, tooltip="CLIP layer to stop at"),
        io.Combo.Input("lora_count", options=["1", "2", "3"], default="1", tooltip="Number of LoRA slots"),
        io.Boolean.Input("lora_switch_1", default=False, label_on="ON", label_off="OFF", tooltip="Enable LoRA 1"),
        io.Combo.Input("lora_name_1", options=loras, default="None", tooltip="LoRA 1 file"),
        io.Float.Input("lora_weight_1", default=1.0, min=-10.0, max=10.0, step=0.01, tooltip="LoRA 1 model weight"),
        io.Boolean.Input("lora_switch_2", default=False, label_on="ON", label_off="OFF", tooltip="Enable LoRA 2"),
        io.Combo.Input("lora_name_2", options=loras, default="None", tooltip="LoRA 2 file"),
        io.Float.Input("lora_weight_2", default=1.0, min=-10.0, max=10.0, step=0.01, tooltip="LoRA 2 model weight"),
        io.Boolean.Input("lora_switch_3", default=False, label_on="ON", label_off="OFF", tooltip="Enable LoRA 3"),
        io.Combo.Input("lora_name_3", options=loras, default="None", tooltip="LoRA 3 file"),
        io.Float.Input("lora_weight_3", default=1.0, min=-10.0, max=10.0, step=0.01, tooltip="LoRA 3 model weight"),
        io.Combo.Input("sampling_method", options=["None", "SD3", "AuraFlow", "Flux", "Stable Cascade", "LCM", "ContinuousEDM", "ContinuousV", "LTXV"], default="None", tooltip="Sampling method: SD3 (shift=3.0), AuraFlow (shift=1.73), Flux (max_shift=1.15), Stable Cascade (shift=2.0), LCM (distilled), ContinuousEDM/V (continuous sampling), LTXV (video)"),
        io.Combo.Input("sampling_subtype", options=["eps", "v_prediction", "edm", "edm_playground_v2.5", "cosmos_rflow"], default="eps", tooltip="Subtype for ContinuousEDM sampling"),
        io.Float.Input("shift", default=3.0, min=0.0, max=100.0, step=0.01, tooltip="Universal shift parameter (SD3: 3.0, AuraFlow: 1.73, Flux max_shift: 1.15, Stable Cascade: 2.0)"),
        io.Float.Input("base_shift", default=0.5, min=0.0, max=100.0, step=0.01, tooltip="Base shift for Flux/LTXV sampling (default: 0.5)"),
        io.Int.Input("sampling_width", default=1024, min=16, max=32768, step=8, tooltip="Width for Flux sampling shift calculation"),
        io.Int.Input("sampling_height", default=1024, min=16, max=32768, step=8, tooltip="Height for Flux sampling shift calculation"),
        io.Int.Input("original_timesteps", default=50, min=1, max=1000, step=1, tooltip="Original timesteps for LCM sampling (default: 50)"),
        io.Boolean.Input("zsnr", default=False, label_on="yes", label_off="no", tooltip="Enable zero-terminal SNR for LCM sampling"),
        io.Float.Input("sigma_max", default=120.0, min=0.0, max=1000.0, step=0.001, tooltip="Maximum sigma for ContinuousEDM/V sampling (EDM: 120.0, V: 500.0)"),
        io.Float.Input("sigma_min", default=0.002, min=0.0, max=1000.0, step=0.001, tooltip="Minimum sigma for ContinuousEDM/V sampling (EDM: 0.002, V: 0.03)"),
        io.Int.Input(
            "blocks_to_swap",
            default=5, min=0, max=100, step=1,
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
            "offload_embeddings", default=False, label_on="Yes", label_off="No",
            tooltip="Also offload embedding and projection layers for extra VRAM savings.",
        ),

        # --- Optional LTX2 text encoder (appended last so existing workflows keep widget order) ---
        io.Combo.Input(
            "ltx_text_encoder",
            options=["None"] + _get_clip_file_list(),
            default="None",
            tooltip=(
                "Optional LTX2/LTXV gemma text encoder (from the text_encoders/clip folder, "
                "GGUF or safetensors). When set, it is combined with the loaded Standard "
                "Checkpoint / UNet file's baked text-projection to build a correct LTXAV CLIP, "
                "overriding the (empty) baked CLIP. Leave as None for normal baked-CLIP behavior."
            ),
        ),
    ]


def _get_clip_file_list() -> list:
    # Combined clip + text_encoders file list (includes .gguf after registration above).
    clip_files = list(folder_paths.get_filename_list("clip"))
    if "text_encoders" in folder_paths.folder_names_and_paths:
        clip_files.extend(folder_paths.get_filename_list("text_encoders"))
    return sorted(set(clip_files))


# ── Model loading logic ──────────────────────────────────────────────

def load_model(log_prefix: str, **kwargs) -> tuple[Any, Any, Any, Any, str, str]:
    # Shared model loading logic.
    # Returns (model, clip, vae, audio_vae, checkpoint_name, lora_string).
    model_type = kwargs.get('model_type', 'Standard Checkpoint')
    ckpt_name = kwargs.get('ckpt_name', 'None')
    unet_name = kwargs.get('unet_name', 'None')
    nunchaku_name = kwargs.get('nunchaku_name', 'None')
    qwen_name = kwargs.get('qwen_name', 'None')
    zimage_name = kwargs.get('zimage_name', 'None')
    gguf_name = kwargs.get('gguf_name', 'None')
    weight_dtype = kwargs.get('weight_dtype', 'default')

    data_type = kwargs.get('data_type', 'bfloat16')
    cache_threshold = kwargs.get('cache_threshold', 0.0)
    attention = kwargs.get('attention', 'flash-attention2')
    i2f_mode = kwargs.get('i2f_mode', 'enabled')
    cpu_offload = kwargs.get('cpu_offload', 'auto')
    num_blocks_on_gpu = kwargs.get('num_blocks_on_gpu', 30)
    use_pin_memory = kwargs.get('use_pin_memory', 'enable')

    gguf_dequant_dtype = kwargs.get('gguf_dequant_dtype', 'default')
    gguf_patch_dtype = kwargs.get('gguf_patch_dtype', 'default')
    gguf_patch_on_device = kwargs.get('gguf_patch_on_device', False)

    enable_clip_layer = bool(kwargs.get('enable_clip_layer', True))
    stop_at_clip_layer = kwargs.get('stop_at_clip_layer', -2)

    # Parse features from chip widget
    features_raw = kwargs.get('features', MODEL_LOADER_DEFAULT_FEATURES)
    if isinstance(features_raw, dict) and '__value__' in features_raw:
        selected = features_raw['__value__']
    elif isinstance(features_raw, str):
        selected = [f.strip() for f in features_raw.split(',') if f.strip()]
    else:
        selected = list(features_raw) if features_raw else []
    selected_set = set(selected)

    configure_lora = "lora" in selected_set
    lora_count_int = int(kwargs.get('lora_count', '1'))

    configure_model_sampling = "model_sampling" in selected_set
    sampling_method = kwargs.get('sampling_method', 'None')
    sampling_subtype = kwargs.get('sampling_subtype', 'eps')
    shift = kwargs.get('shift', 3.0)
    base_shift = kwargs.get('base_shift', 0.5)
    sampling_width = kwargs.get('sampling_width', 1024)
    sampling_height = kwargs.get('sampling_height', 1024)
    original_timesteps = kwargs.get('original_timesteps', 50)
    zsnr = kwargs.get('zsnr', False)
    sigma_max = kwargs.get('sigma_max', 120.0)
    sigma_min = kwargs.get('sigma_min', 0.002)

    configure_blockswap = "block_swap" in selected_set
    blocks_to_swap = kwargs.get('blocks_to_swap', 10)
    offload_embeddings = kwargs.get('offload_embeddings', False)

    memory_cleanup = "memory_cleanup" in selected_set

    is_standard = (model_type == "Standard Checkpoint")
    is_unet = (model_type == "UNet Model")
    is_nunchaku = (model_type == "Nunchaku Flux")
    is_qwen = (model_type == "Nunchaku Qwen")
    is_zimage = (model_type == "Nunchaku ZImage")
    is_gguf = (model_type == "GGUF Model")

    loaded_model: Any = None
    loaded_clip = None
    loaded_vae = None
    checkpoint_name = ""

    safe_exts = {".safetensors", ".sft"}

    # ── Pre-Load Memory Cleanup ──

    if memory_cleanup:
        cleanup_memory_before_load()

    # ── Load Model ──

    if is_standard:
        if ckpt_name in (None, '', 'None'):
            raise ValueError("Please select a checkpoint file")

        ckpt_path = folder_paths.get_full_path("checkpoints", ckpt_name)
        if not ckpt_path or not os.path.isfile(ckpt_path):
            raise FileNotFoundError(f"Checkpoint not found: {ckpt_name}")

        _, ext = os.path.splitext(ckpt_path.lower())
        if ext not in safe_exts:
            log.warning(log_prefix, f"'{ckpt_name}' uses extension '{ext}'. Consider .safetensors for safety.")
        if not os.access(ckpt_path, os.R_OK):
            raise RuntimeError(f"Checkpoint file not readable: {ckpt_path}")

        loaded_ckpt = comfy.sd.load_checkpoint_guess_config(
            ckpt_path,
            output_vae=True,
            output_clip=True,
            embedding_directory=folder_paths.get_folder_paths("embeddings"),
        )

        checkpoint_name = ckpt_name
        ckpt_parts = loaded_ckpt[:3] if hasattr(loaded_ckpt, '__len__') and len(loaded_ckpt) >= 3 else None
        loaded_model = ckpt_parts[0] if ckpt_parts else loaded_ckpt

        # Extract baked CLIP
        if ckpt_parts and ckpt_parts[1]:
            base_clip = ckpt_parts[1]
            if enable_clip_layer:
                loaded_clip = base_clip.clone()
                loaded_clip.clip_layer(stop_at_clip_layer)
            else:
                loaded_clip = base_clip

        # Extract baked VAE
        if ckpt_parts and ckpt_parts[2]:
            loaded_vae = ckpt_parts[2]

    elif is_unet:
        if unet_name in (None, '', 'None'):
            raise ValueError("Please select a UNet model file")

        unet_path = folder_paths.get_full_path("diffusion_models", unet_name)
        if not unet_path or not os.path.isfile(unet_path):
            raise FileNotFoundError(f"UNet model not found: {unet_name}")

        _, ext = os.path.splitext(unet_path.lower())
        if ext not in safe_exts:
            log.warning(log_prefix, f"'{unet_name}' uses extension '{ext}'. Consider .safetensors.")
        if not os.access(unet_path, os.R_OK):
            raise RuntimeError(f"UNet file not readable: {unet_path}")

        model_options: dict[str, Any] = {}
        if weight_dtype == "fp8_e4m3fn":
            model_options["dtype"] = torch.float8_e4m3fn
        elif weight_dtype == "fp8_e4m3fn_fast":
            model_options["dtype"] = torch.float8_e4m3fn
            model_options["fp8_optimizations"] = True
        elif weight_dtype == "fp8_e5m2":
            model_options["dtype"] = torch.float8_e5m2

        loaded_model = comfy.sd.load_diffusion_model(unet_path, model_options=model_options)
        checkpoint_name = unet_name

    elif is_nunchaku:
        if nunchaku_name in (None, '', 'None'):
            raise ValueError("Please select a Nunchaku model file")

        nunchaku_path = folder_paths.get_full_path("diffusion_models", nunchaku_name)
        if not nunchaku_path or not os.path.isfile(nunchaku_path):
            raise FileNotFoundError(f"Nunchaku model not found: {nunchaku_name}")

        _, ext = os.path.splitext(nunchaku_path.lower())
        if ext not in safe_exts:
            log.warning(log_prefix, f"'{nunchaku_name}' uses extension '{ext}'. Consider .safetensors.")
        if not os.access(nunchaku_path, os.R_OK):
            raise RuntimeError(f"Nunchaku file not readable: {nunchaku_path}")

        if not NUNCHAKU_AVAILABLE:
            raise RuntimeError("Nunchaku support not available — install the 'nunchaku' pip package")

        loaded_model = load_nunchaku_model(
            model_path=nunchaku_path,
            device=None,
            dtype=None,
            cpu_offload=(cpu_offload == "enable" or cpu_offload == "auto"),
            cache_threshold=cache_threshold,
            attention=attention,
            data_type=data_type,
            i2f_mode=i2f_mode,
            model_type="flux"
        )
        checkpoint_name = nunchaku_name

    elif is_qwen:
        if qwen_name in (None, '', 'None'):
            raise ValueError("Please select a Nunchaku Qwen model file")

        qwen_path = folder_paths.get_full_path("diffusion_models", qwen_name)
        if not qwen_path or not os.path.isfile(qwen_path):
            raise FileNotFoundError(f"Nunchaku Qwen model not found: {qwen_name}")

        _, ext = os.path.splitext(qwen_path.lower())
        if ext not in safe_exts:
            log.warning(log_prefix, f"'{qwen_name}' uses extension '{ext}'. Consider .safetensors.")
        if not os.access(qwen_path, os.R_OK):
            raise RuntimeError(f"Qwen file not readable: {qwen_path}")

        if not NUNCHAKU_AVAILABLE:
            raise RuntimeError("Nunchaku support not available — install the 'nunchaku' pip package")

        loaded_model = load_nunchaku_model(
            model_path=qwen_path,
            device=None,
            dtype=None,
            cpu_offload=(cpu_offload == "enable" or cpu_offload == "auto"),
            num_blocks_on_gpu=num_blocks_on_gpu,
            use_pin_memory=(use_pin_memory == "enable"),
            model_type="qwen"
        )
        checkpoint_name = qwen_name

    elif is_zimage:
        if zimage_name in (None, '', 'None'):
            raise ValueError("Please select a Nunchaku ZImage model file")

        zimage_path = folder_paths.get_full_path("diffusion_models", zimage_name)
        if not zimage_path or not os.path.isfile(zimage_path):
            raise FileNotFoundError(f"Nunchaku ZImage model not found: {zimage_name}")

        _, ext = os.path.splitext(zimage_path.lower())
        if ext not in safe_exts:
            log.warning(log_prefix, f"'{zimage_name}' uses extension '{ext}'. Consider .safetensors.")
        if not os.access(zimage_path, os.R_OK):
            raise RuntimeError(f"ZImage file not readable: {zimage_path}")

        if not NUNCHAKU_AVAILABLE:
            raise RuntimeError("Nunchaku support not available — install the 'nunchaku' pip package")

        loaded_model = load_nunchaku_model(
            model_path=zimage_path,
            device=None,
            dtype=None,
            cpu_offload=(cpu_offload == "enable" or cpu_offload == "auto"),
            num_blocks_on_gpu=num_blocks_on_gpu,
            use_pin_memory=(use_pin_memory == "enable"),
            model_type="zimage"
        )
        checkpoint_name = zimage_name

    elif is_gguf:
        if gguf_name in (None, '', 'None'):
            raise ValueError("Please select a GGUF model file")

        gguf_path = folder_paths.get_full_path("diffusion_models", gguf_name)
        if not gguf_path or not os.path.isfile(gguf_path):
            raise FileNotFoundError(f"GGUF model not found: {gguf_name}")

        if not gguf_path.lower().endswith('.gguf'):
            log.warning(log_prefix, f"'{gguf_name}' doesn't have .gguf extension")
        if not os.access(gguf_path, os.R_OK):
            raise RuntimeError(f"GGUF file not readable: {gguf_path}")

        if not GGUF_AVAILABLE:
            raise RuntimeError("GGUF support not available — install the 'gguf' pip package")

        loaded_model = load_gguf_model(
            model_path=gguf_path,
            dequant_dtype=gguf_dequant_dtype,
            patch_dtype=gguf_patch_dtype,
            patch_on_device=gguf_patch_on_device
        )
        checkpoint_name = gguf_name

    else:
        raise ValueError(f"Invalid model_type: {model_type}")

    # ── Optional LTX2 gemma + baked-projection CLIP ──
    # Combines an external gemma text encoder with the loaded checkpoint/UNet
    # file (whose baked text_embedding_projection completes the LTXAV recipe).
    ltx_te = kwargs.get('ltx_text_encoder', 'None')
    if ltx_te not in (None, '', 'None'):
        if is_standard or is_unet:
            gemma_path = folder_paths.get_full_path("clip", ltx_te) or folder_paths.get_full_path("text_encoders", ltx_te)
            model_file_path = (folder_paths.get_full_path("checkpoints", ckpt_name) if is_standard
                               else folder_paths.get_full_path("diffusion_models", unet_name))
            if not gemma_path:
                log.warning(log_prefix, f"LTX text encoder '{ltx_te}' not found — keeping baked CLIP")
            elif not model_file_path:
                log.warning(log_prefix, "LTX text encoder set but model file path unavailable — keeping baked CLIP")
            else:
                clip_paths = [gemma_path, model_file_path]
                try:
                    if any(p.lower().endswith('.gguf') for p in clip_paths):
                        if not GGUF_AVAILABLE:
                            raise ImportError("GGUF text encoder selected but the 'gguf' pip package is not installed.")
                        loaded_clip = load_gguf_clip(clip_paths=clip_paths, clip_type=comfy.sd.CLIPType.LTXV)
                    else:
                        loaded_clip = comfy.sd.load_clip(
                            ckpt_paths=clip_paths,
                            embedding_directory=folder_paths.get_folder_paths("embeddings"),
                            clip_type=comfy.sd.CLIPType.LTXV,
                        )
                    log.msg(log_prefix, f"Built LTXAV CLIP from '{ltx_te}' + model-file projection")
                except Exception as e:
                    log.warning(log_prefix, f"Failed to build LTXAV CLIP: {e} — keeping baked CLIP")
        else:
            log.warning(log_prefix, "LTX text encoder is only supported for Standard Checkpoint / UNet Model — ignoring")

    # ── Extract baked audio VAE (LTX2 all-in-one checkpoints/UNet) ──
    loaded_audio_vae = None
    if is_standard or is_unet:
        _mfp = (folder_paths.get_full_path("checkpoints", ckpt_name) if is_standard
                else folder_paths.get_full_path("diffusion_models", unet_name))
        if _mfp and safetensors_has_audio_vae(_mfp):
            try:
                loaded_audio_vae = load_audio_vae_from_path(_mfp)
                if loaded_audio_vae is not None:
                    log.msg(log_prefix, "Extracted baked audio VAE from model file")
            except Exception as e:
                log.warning(log_prefix, f"Failed to extract baked audio VAE: {e}")

    # ── Apply LoRAs ──

    lora_params = []
    if configure_lora:
        for i in range(1, lora_count_int + 1):
            lora_name = kwargs.get(f'lora_name_{i}', 'None')
            lora_weight = kwargs.get(f'lora_weight_{i}', 1.0)
            if lora_name not in (None, '', 'None'):
                lora_params.append((lora_name, lora_weight))

        if lora_params:
            log.msg("LoRA", f"Applying {len(lora_params)} LoRA(s)...")
            loaded_model, loaded_clip = apply_loras(loaded_model, loaded_clip, lora_params)

    lora_string = ""
    if lora_params:
        lora_string = ' '.join(f"<lora:{name}:{weight}:{weight}>" for name, weight in lora_params)

    # ── Apply Model Sampling ──

    if configure_model_sampling and loaded_model is not None:
        loaded_model = apply_model_sampling(
            loaded_model,
            sampling_method=sampling_method,
            shift=shift,
            base_shift=base_shift,
            width=sampling_width,
            height=sampling_height,
            original_timesteps=original_timesteps,
            zsnr=zsnr,
            sampling_subtype=sampling_subtype,
            sigma_max=sigma_max,
            sigma_min=sigma_min
        )

    # ── Apply BlockSwap ──

    if configure_blockswap and loaded_model is not None and blocks_to_swap > 0:
        # Nunchaku models handle their own offloading
        if not (is_nunchaku or is_qwen or is_zimage):
            # ComfyUI 0.18.0+ dynamic VRAM handles offloading natively
            if loaded_model.is_dynamic() and hasattr(loaded_model, 'backup_buffers'):
                log.msg(log_prefix, "BlockSwap: native dynamic VRAM active — not needed")
            else:
                from ..py.RvTools_BlockSwap import (
                    _detect_block_groups, _count_blocks, _get_model_arch_name,
                    _make_swap_callback,
                )
                diff_model = getattr(loaded_model.model, "diffusion_model", None)
                if diff_model is not None:
                    groups = _detect_block_groups(diff_model)
                    total = _count_blocks(groups)
                    arch = _get_model_arch_name(loaded_model)
                    if total > 0:
                        actual = min(blocks_to_swap, total)
                        log.msg(log_prefix, f"BlockSwap: {arch} — {total} blocks, "
                                f"will offload {actual} on next load")
                        loaded_model = loaded_model.clone()
                        loaded_model.add_callback(
                            CallbacksMP.ON_LOAD,
                            _make_swap_callback(blocks_to_swap, offload_embeddings),
                        )
                    else:
                        log.warning(log_prefix, f"BlockSwap: {arch} has no recognized "
                                    f"block structure — skipping")

    # ── Validate ──

    if loaded_model is None:
        if is_gguf:
            ext_hint = "Ensure the 'gguf' pip package is installed."
        elif is_nunchaku or is_qwen or is_zimage:
            ext_hint = "Ensure the 'nunchaku' pip package is installed."
        else:
            ext_hint = ""
        raise RuntimeError(
            f"Failed to load {model_type} model. Check the console log above for details.\n"
            f"The model could not be loaded — ensure the file exists and is not corrupted. {ext_hint}"
        )

    return (loaded_model, loaded_clip, loaded_vae, loaded_audio_vae, checkpoint_name, lora_string)


# ── External VAE loader ─────────────────────────────────────────────
# Mirrors upstream comfy_extras VAELoader.load_vae() exactly: reads the
# safetensors metadata and forwards it to comfy.sd.VAE (some VAEs read
# "config" from metadata to set scale/shift/architecture, see
# comfy/sd.py VAE.__init__). Any architecture-specific handling lives in
# upstream comfy.sd.VAE — no Eclipse-side branches.

def load_custom_vae(vae_name: str, disable_offload: bool | None = None) -> "comfy.sd.VAE":
    # Load an external VAE file by name. Returns a stock comfy.sd.VAE.
    # Set disable_offload=True to force a full load (skip the offload pass);
    # leaving it None preserves the upstream per-VAE default.
    vae_path = folder_paths.get_full_path_or_raise("vae", vae_name)
    sd, metadata = comfy.utils.load_torch_file(vae_path, return_metadata=True)
    vae = comfy.sd.VAE(sd=sd, metadata=metadata)
    vae.throw_exception_if_invalid()
    if disable_offload is not None:
        vae.disable_offload = disable_offload
    return vae


def load_audio_vae_from_path(vae_path: str) -> "comfy.sd.VAE | None":
    # Load an LTXV/LTX2 audio VAE from a file path. Audio VAEs ship as
    # checkpoints carrying `audio_vae.` + `vocoder.` prefixed weights (they may
    # also be baked into a Standard Checkpoint alongside the image VAE).
    # Filtering + remapping those keys mirrors ComfyUI's LTXVAudioVAELoader.
    # Returns None when the file contains no audio VAE keys (so callers can warn).
    sd, metadata = comfy.utils.load_torch_file(vae_path, return_metadata=True)
    audio_sd = comfy.utils.state_dict_prefix_replace(
        sd, {"audio_vae.": "autoencoder.", "vocoder.": "vocoder."}, filter_keys=True
    )
    if not audio_sd:
        return None
    vae = comfy.sd.VAE(sd=audio_sd, metadata=metadata)
    vae.throw_exception_if_invalid()
    return vae


def safetensors_has_audio_vae(path: str) -> bool:
    # Cheap header-only peek (no tensor data) to detect a baked LTX audio VAE.
    # Reads just the safetensors JSON header (~1-16ms regardless of file size).
    if not path or not path.lower().endswith((".safetensors", ".sft")):
        return False
    try:
        import struct, json
        with open(path, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            header = json.loads(f.read(n))
        return any(
            k.startswith("vocoder.") or k.startswith("audio_vae.")
            for k in header if k != "__metadata__"
        )
    except Exception:
        return False
