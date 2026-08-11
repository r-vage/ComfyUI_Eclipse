# Shared Smart Model Loader execution pipeline.

from __future__ import annotations

import json
import os
from typing import Any

import comfy  # type: ignore
import comfy.samplers  # type: ignore
import comfy.sd  # type: ignore
import comfy.utils  # type: ignore
import folder_paths  # type: ignore
import torch  # type: ignore

from .. import RESOLUTION_MAP
from ..common import cleanup_memory_before_load
from ..gguf_wrapper import load_gguf_clip, load_gguf_model
from ..logger import log
from ..model_loader_common import (
    GGUF_AVAILABLE,
    LATENT_CHANNELS,
    LATENT_DOWNSCALE,
    NUNCHAKU_AVAILABLE,
    OMIT,
    apply_blockswap,
    apply_loras,
    apply_model_sampling,
    build_pipe,
    collect_lora_params,
    detect_latent_channels,
    detect_latent_downscale,
    format_lora_string,
    load_audio_vae_from_path,
    load_custom_vae,
)
from ..nunchaku_wrapper import load_nunchaku_model
from .integrity import verify_primary_model_integrity
from .lifecycle import with_loader_execution
from .loading import LoadRequest, LoadResult

_LOG_PREFIX = "Smart Model Loader"


@with_loader_execution
def execute_smart_request(**kwargs):
    # Validate every workflow value and selected file before cleanup or loading.
    request = LoadRequest.from_kwargs(kwargs, smart=True)
    selected = list(request.features)
    selected_set = set(selected)

    # Map features to boolean flags (same names as Plus v2 used)
    configure_clip = "clip" in selected_set
    configure_vae = "vae" in selected_set
    configure_audio_vae = "audio_vae" in selected_set
    configure_latent = "latent" in selected_set
    configure_sampler = "sampler" in selected_set
    configure_model_only_lora = "lora" in selected_set
    configure_model_sampling = "model_sampling" in selected_set
    configure_blockswap = "block_swap" in selected_set
    memory_cleanup = "memory_cleanup" in selected_set
    configure_seed = "seed" in selected_set

    # Extract all parameters
    verify_integrity = str(kwargs.get("verify_file") or "off")
    expected_hashes_raw = kwargs.get("expected_hashes", {})
    air_or_hash = str(kwargs.get("air_or_hash", "") or "").strip()
    _download_locators_raw = kwargs.get("download_locators", [])

    if verify_integrity not in {"off", "sidecar", "verify"}:
        verify_integrity = "off"

    expected_hashes: dict[str, Any] = {}
    if isinstance(expected_hashes_raw, dict):
        expected_hashes = expected_hashes_raw
    elif isinstance(expected_hashes_raw, str) and expected_hashes_raw.strip():
        try:
            parsed_hashes = json.loads(expected_hashes_raw)
            if isinstance(parsed_hashes, dict):
                expected_hashes = parsed_hashes
        except (json.JSONDecodeError, TypeError, ValueError):
            log.warning(_LOG_PREFIX, "expected_hashes is not valid JSON; ignoring.")

    # Parsed for forward compatibility and template persistence; runtime use is handled in JS
    # + /eclipse/civitai/download endpoint for locator-only (filename-free) requests.
    if isinstance(_download_locators_raw, str) and _download_locators_raw.strip():
        try:
            _parsed_locators = json.loads(_download_locators_raw)
            if not isinstance(_parsed_locators, list):
                _parsed_locators = []
        except (json.JSONDecodeError, TypeError, ValueError):
            _parsed_locators = []

    model_type = kwargs.get("model_type", "Standard Checkpoint")
    ckpt_name = kwargs.get("ckpt_name", "None")
    unet_name = kwargs.get("unet_name", "None")
    nunchaku_name = kwargs.get("nunchaku_name", "None")
    qwen_name = kwargs.get("qwen_name", "None")
    zimage_name = kwargs.get("zimage_name", "None")
    gguf_name = kwargs.get("gguf_name", "None")
    weight_dtype = kwargs.get("weight_dtype", "default")

    data_type = kwargs.get("data_type", "bfloat16")
    cache_threshold = kwargs.get("cache_threshold", 0.0)
    attention = kwargs.get("attention", "flash-attention2")
    i2f_mode = kwargs.get("i2f_mode", "enabled")
    cpu_offload = kwargs.get("cpu_offload", "auto")
    num_blocks_on_gpu = kwargs.get("num_blocks_on_gpu", 30)
    use_pin_memory = kwargs.get("use_pin_memory", "enable")

    gguf_dequant_dtype = kwargs.get("gguf_dequant_dtype", "default")
    gguf_patch_dtype = kwargs.get("gguf_patch_dtype", "default")
    gguf_patch_on_device = kwargs.get("gguf_patch_on_device", False)

    blocks_to_swap = kwargs.get("blocks_to_swap", 10)
    offload_embeddings = kwargs.get("offload_embeddings", False)

    sampling_method = kwargs.get("sampling_method", "None")
    sampling_subtype = kwargs.get("sampling_subtype", "eps")
    shift = kwargs.get("shift", 3.0)
    base_shift = kwargs.get("base_shift", 0.5)
    sampling_width = kwargs.get("sampling_width", 1024)
    sampling_height = kwargs.get("sampling_height", 1024)
    original_timesteps = kwargs.get("original_timesteps", 50)
    zsnr = kwargs.get("zsnr", False)
    sigma_max = kwargs.get("sigma_max", 120.0)
    sigma_min = kwargs.get("sigma_min", 0.002)

    clip_source = kwargs.get("clip_source", "Baked")
    clip_count = kwargs.get("clip_count", "1")
    clip_name1 = kwargs.get("clip_name1", "None")
    clip_name2 = kwargs.get("clip_name2", "None")
    clip_name3 = kwargs.get("clip_name3", "None")
    clip_name4 = kwargs.get("clip_name4", "None")
    clip_type = kwargs.get("clip_type", "flux")
    enable_clip_layer = kwargs.get("enable_clip_layer", True)
    stop_at_clip_layer = kwargs.get("stop_at_clip_layer", -2)

    vae_source = kwargs.get("vae_source", "Baked")
    vae_name = kwargs.get("vae_name", "None")

    audio_vae_source = kwargs.get("audio_vae_source", "External")
    audio_vae_name = kwargs.get("audio_vae_name", "None")

    resolution = kwargs.get("resolution", "1024x1024 (1:1 XL/SD3/Flux/HiDream)")
    width = kwargs.get("width", 1024)
    height = kwargs.get("height", 1024)
    batch_size = kwargs.get("batch_size", 1)

    lora_count = kwargs.get("lora_count", "1")

    sampler_name = kwargs.get("sampler_name", "euler")
    scheduler = kwargs.get("scheduler", "normal")
    steps = kwargs.get("steps", 20)
    cfg = round(kwargs.get("cfg", 8.0), 2)
    flux_guidance = round(kwargs.get("flux_guidance", 3.5), 2)

    seed = int(kwargs.get("seed", 0))

    # Normalize
    enable_clip_layer = bool(enable_clip_layer)
    clip_count_int = int(clip_count)
    lora_count_int = int(lora_count)

    is_standard = model_type == "Standard Checkpoint"
    is_unet = model_type == "UNet Model"
    is_nunchaku = model_type == "Nunchaku Flux"
    is_qwen = model_type == "Nunchaku Qwen"
    is_zimage = model_type == "Nunchaku ZImage"
    is_gguf = model_type == "GGUF Model"
    use_baked_clip = clip_source == "Baked"
    use_baked_vae = vae_source == "Baked"

    loaded_model = None
    loaded_clip = None
    loaded_vae = None
    ckpt_parts = None
    checkpoint_name = ""

    safe_exts = {".safetensors", ".sft"}

    # ============================================================
    # STEP 0: Integrity check pre-pass (sidecar / verify)
    # ============================================================

    if verify_integrity in {"sidecar", "verify"}:
        verify_primary_model_integrity(
            request.files.values(),
            mode=verify_integrity,
            expected_hashes=expected_hashes,
            air_or_hash=air_or_hash,
        )

    # ============================================================
    # STEP 0: Pre-Load Memory Cleanup
    # ============================================================

    if memory_cleanup:
        cleanup_memory_before_load()

    # ============================================================
    # STEP 1: Load Model
    # ============================================================

    if is_standard:
        if ckpt_name in (None, "", "None"):
            raise ValueError("Please select a checkpoint file")

        ckpt_path = str(request.files["ckpt_name"].path)
        if not ckpt_path or not os.path.isfile(ckpt_path):
            raise FileNotFoundError(f"Checkpoint not found: {ckpt_name}")

        _, ext = os.path.splitext(ckpt_path.lower())
        if ext not in safe_exts:
            log.warning(
                _LOG_PREFIX,
                f"'{ckpt_name}' uses extension '{ext}'. Consider .safetensors for safety.",
            )

        if not os.access(ckpt_path, os.R_OK):
            raise RuntimeError(f"Checkpoint file not readable: {ckpt_path}")

        loaded_ckpt = comfy.sd.load_checkpoint_guess_config(
            ckpt_path,
            output_vae=use_baked_vae if configure_vae else False,
            output_clip=use_baked_clip if configure_clip else False,
            embedding_directory=folder_paths.get_folder_paths("embeddings"),
        )

        checkpoint_name = ckpt_name
        ckpt_parts = (
            loaded_ckpt[:3]
            if hasattr(loaded_ckpt, "__len__") and len(loaded_ckpt) >= 3
            else None
        )
        loaded_model = ckpt_parts[0] if ckpt_parts else loaded_ckpt

    elif is_nunchaku:
        if nunchaku_name in (None, "", "None"):
            raise ValueError("Please select a Nunchaku model file")

        nunchaku_path = str(request.files["nunchaku_name"].path)
        if not nunchaku_path or not os.path.isfile(nunchaku_path):
            raise FileNotFoundError(f"Nunchaku model not found: {nunchaku_name}")

        _, ext = os.path.splitext(nunchaku_path.lower())
        if ext not in safe_exts:
            log.warning(
                _LOG_PREFIX,
                f"'{nunchaku_name}' uses extension '{ext}'. Consider .safetensors.",
            )

        if not os.access(nunchaku_path, os.R_OK):
            raise RuntimeError(f"Nunchaku file not readable: {nunchaku_path}")

        if not NUNCHAKU_AVAILABLE:
            raise RuntimeError(
                "Nunchaku support not available — install the 'nunchaku' pip package"
            )
        log.msg("Nunchaku Flux", f"Loading quantized model: {nunchaku_name}")
        loaded_model = load_nunchaku_model(
            model_path=nunchaku_path,
            device=None,
            dtype=None,
            cpu_offload=(cpu_offload == "enable" or cpu_offload == "auto"),
            cache_threshold=cache_threshold,
            attention=attention,
            data_type=data_type,
            i2f_mode=i2f_mode,
            model_type="flux",
        )
        checkpoint_name = nunchaku_name

    elif is_qwen:
        if qwen_name in (None, "", "None"):
            raise ValueError("Please select a Nunchaku Qwen model file")

        qwen_path = str(request.files["qwen_name"].path)
        if not qwen_path or not os.path.isfile(qwen_path):
            raise FileNotFoundError(f"Nunchaku Qwen model not found: {qwen_name}")

        _, ext = os.path.splitext(qwen_path.lower())
        if ext not in safe_exts:
            log.warning(
                _LOG_PREFIX,
                f"'{qwen_name}' uses extension '{ext}'. Consider .safetensors.",
            )

        if not os.access(qwen_path, os.R_OK):
            raise RuntimeError(f"Qwen file not readable: {qwen_path}")

        if not NUNCHAKU_AVAILABLE:
            raise RuntimeError(
                "Nunchaku support not available — install the 'nunchaku' pip package"
            )
        checkpoint_name = qwen_name
        loaded_model = load_nunchaku_model(
            model_path=qwen_path,
            device=None,
            dtype=None,
            cpu_offload=(cpu_offload == "enable" or cpu_offload == "auto"),
            num_blocks_on_gpu=num_blocks_on_gpu,
            use_pin_memory=(use_pin_memory == "enable"),
            model_type="qwen",
        )

    elif is_zimage:
        if zimage_name in (None, "", "None"):
            raise ValueError("Please select a Nunchaku ZImage model file")

        zimage_path = str(request.files["zimage_name"].path)
        if not zimage_path or not os.path.isfile(zimage_path):
            raise FileNotFoundError(
                f"Nunchaku ZImage model not found: {zimage_name}"
            )

        _, ext = os.path.splitext(zimage_path.lower())
        if ext not in safe_exts:
            log.warning(
                _LOG_PREFIX,
                f"'{zimage_name}' uses extension '{ext}'. Consider .safetensors.",
            )

        if not os.access(zimage_path, os.R_OK):
            raise RuntimeError(f"ZImage file not readable: {zimage_path}")

        if not NUNCHAKU_AVAILABLE:
            raise RuntimeError(
                "Nunchaku support not available — install the 'nunchaku' pip package"
            )
        checkpoint_name = zimage_name
        loaded_model = load_nunchaku_model(
            model_path=zimage_path,
            device=None,
            dtype=None,
            cpu_offload=(cpu_offload == "enable" or cpu_offload == "auto"),
            num_blocks_on_gpu=num_blocks_on_gpu,
            use_pin_memory=(use_pin_memory == "enable"),
            model_type="zimage",
        )

    elif is_gguf:
        if gguf_name in (None, "", "None"):
            raise ValueError("Please select a GGUF model file")

        gguf_path = str(request.files["gguf_name"].path)
        if not gguf_path or not os.path.isfile(gguf_path):
            raise FileNotFoundError(f"GGUF model not found: {gguf_name}")

        if not gguf_path.lower().endswith(".gguf"):
            log.warning(_LOG_PREFIX, f"'{gguf_name}' doesn't have .gguf extension")

        if not os.access(gguf_path, os.R_OK):
            raise RuntimeError(f"GGUF file not readable: {gguf_path}")

        if not GGUF_AVAILABLE:
            raise RuntimeError(
                "GGUF support not available — install the 'gguf' pip package"
            )
        checkpoint_name = gguf_name
        loaded_model = load_gguf_model(
            model_path=gguf_path,
            dequant_dtype=gguf_dequant_dtype,
            patch_dtype=gguf_patch_dtype,
            patch_on_device=gguf_patch_on_device,
        )

    elif is_unet:
        if unet_name in (None, "", "None"):
            raise ValueError("Please select a UNet model file")

        unet_path = str(request.files["unet_name"].path)
        if not unet_path or not os.path.isfile(unet_path):
            raise FileNotFoundError(f"UNet model not found: {unet_name}")

        _, ext = os.path.splitext(unet_path.lower())
        if ext not in safe_exts:
            log.warning(
                _LOG_PREFIX,
                f"'{unet_name}' uses extension '{ext}'. Consider .safetensors.",
            )

        if not os.access(unet_path, os.R_OK):
            raise RuntimeError(f"UNet file not readable: {unet_path}")

        needs_baked_clip = configure_clip and use_baked_clip
        needs_baked_vae = configure_vae and use_baked_vae
        needs_vae_for_latent = configure_latent and not configure_vae

        if needs_baked_clip or needs_baked_vae or needs_vae_for_latent:
            loaded_ckpt = comfy.sd.load_checkpoint_guess_config(
                unet_path,
                output_vae=(needs_baked_vae or needs_vae_for_latent),
                output_clip=needs_baked_clip,
                embedding_directory=folder_paths.get_folder_paths("embeddings"),
            )
            ckpt_parts = (
                loaded_ckpt[:3]
                if hasattr(loaded_ckpt, "__len__") and len(loaded_ckpt) >= 3
                else None
            )
            loaded_model = ckpt_parts[0] if ckpt_parts else loaded_ckpt
            checkpoint_name = unet_name
        else:
            model_options = {}
            if weight_dtype == "fp8_e4m3fn":
                model_options["dtype"] = torch.float8_e4m3fn
            elif weight_dtype == "fp8_e4m3fn_fast":
                model_options["dtype"] = torch.float8_e4m3fn
                model_options["fp8_optimizations"] = True
            elif weight_dtype == "fp8_e5m2":
                model_options["dtype"] = torch.float8_e5m2
            loaded_model = comfy.sd.load_diffusion_model(
                unet_path, model_options=model_options
            )
            checkpoint_name = unet_name

    else:
        raise ValueError(
            "Invalid model_type. Choose 'Standard Checkpoint', 'UNet Model', 'Nunchaku Flux', 'Nunchaku Qwen', 'Nunchaku ZImage', or 'GGUF Model'"
        )

    # ============================================================
    # STEP 2: Load CLIP (if configured)
    # ============================================================

    if configure_clip:
        if use_baked_clip:
            if is_nunchaku or is_qwen or is_zimage or is_gguf:
                if is_nunchaku:
                    model_label = "Nunchaku Flux"
                elif is_qwen:
                    model_label = "Nunchaku Qwen"
                elif is_zimage:
                    model_label = "Nunchaku ZImage"
                else:
                    model_label = "GGUF"
                log.warning(
                    model_label,
                    "Quantized models don't contain baked CLIP - please use External CLIP",
                )
            elif ckpt_parts and ckpt_parts[1]:
                base_clip = ckpt_parts[1]
                if enable_clip_layer:
                    loaded_clip = base_clip.clone()
                    loaded_clip.clip_layer(stop_at_clip_layer)
                else:
                    loaded_clip = base_clip
            else:
                log.warning(
                    _LOG_PREFIX, "Baked CLIP requested but not found in checkpoint"
                )

        else:
            clip_paths = []
            clip_names = [clip_name1, clip_name2, clip_name3, clip_name4]

            for i in range(clip_count_int):
                clip_name = clip_names[i] if i < len(clip_names) else "None"
                if clip_name not in (None, "", "None"):
                    clip_path = str(request.files[f"clip_name{i + 1}"].path)
                    if clip_path and os.path.isfile(clip_path):
                        clip_paths.append(clip_path)
                    else:
                        log.warning(
                            _LOG_PREFIX,
                            f"CLIP file '{clip_name}' not found, skipping",
                        )

            # 'External + Model File': append the loaded model file so a baked
            # text-projection (LTXAV gemma recipe) is detected by comfy.sd.load_clip.
            if clip_source == "External + Model File":
                model_file_path = None
                if is_standard and ckpt_name not in (None, "", "None"):
                    model_file_path = str(request.files["ckpt_name"].path)
                elif is_unet and unet_name not in (None, "", "None"):
                    model_file_path = str(request.files["unet_name"].path)
                if model_file_path and os.path.isfile(model_file_path):
                    if model_file_path.lower().endswith(".gguf"):
                        log.warning(
                            _LOG_PREFIX,
                            "GGUF model files can't be combined into CLIP loading; ignoring model file. Use a standalone projection file instead.",
                        )
                    else:
                        clip_paths.append(model_file_path)
                        log.msg(
                            _LOG_PREFIX,
                            "Appending model file to CLIP loader (LTXAV projection recipe)",
                        )
                else:
                    log.warning(
                        _LOG_PREFIX,
                        "'External + Model File' selected but no Standard Checkpoint / UNet file is available to combine with CLIP",
                    )

            if not clip_paths:
                raise ValueError(
                    "No valid CLIP files found. Please select at least one CLIP model"
                )

            # Resolve clip type dynamically to prevent AttributeError on older ComfyUI installations
            resolved_clip_type = comfy.sd.CLIPType.STABLE_DIFFUSION
            if clip_type != "sdxl":
                upper_name = clip_type.upper()
                if hasattr(comfy.sd.CLIPType, upper_name):
                    resolved_clip_type = getattr(comfy.sd.CLIPType, upper_name)
                else:
                    log.warning(
                        _LOG_PREFIX,
                        f"ComfyUI CLIPType does not support '{upper_name}', falling back to STABLE_DIFFUSION",
                    )

            # Check if any CLIP file is GGUF — requires special loading path
            has_gguf_clip = any(p.lower().endswith(".gguf") for p in clip_paths)

            if has_gguf_clip:
                if not GGUF_AVAILABLE:
                    raise ImportError(
                        "GGUF text encoder selected but GGUF support is not available. Install the 'gguf' pip package."
                    )
                loaded_clip = load_gguf_clip(
                    clip_paths=clip_paths,
                    clip_type=resolved_clip_type,
                )
            else:
                loaded_clip = comfy.sd.load_clip(
                    ckpt_paths=clip_paths,
                    embedding_directory=folder_paths.get_folder_paths("embeddings"),
                    clip_type=resolved_clip_type,
                )

    # ============================================================
    # STEP 3: Load VAE
    # ============================================================

    needs_vae_for_latent = configure_latent and not configure_vae

    if configure_vae or needs_vae_for_latent:
        if use_baked_vae or needs_vae_for_latent:
            if is_nunchaku:
                if needs_vae_for_latent:
                    log.warning(
                        "Nunchaku",
                        "Nunchaku models don't contain baked VAE - please enable 'vae' feature and use External VAE",
                    )
            elif ckpt_parts and ckpt_parts[2]:
                loaded_vae = ckpt_parts[2]
            else:
                if needs_vae_for_latent:
                    raise ValueError(
                        "Cannot create latent: Model has no baked VAE. "
                        "Please enable 'vae' feature and set vae_source to 'External', "
                        "or disable 'latent' feature."
                    )
                log.warning(
                    _LOG_PREFIX, "Baked VAE requested but not found in model"
                )

        elif configure_vae and not use_baked_vae:
            if vae_name in (None, "", "None"):
                log.warning(_LOG_PREFIX, "External VAE requested but none selected")
            else:
                loaded_vae = load_custom_vae(vae_name)

    # ============================================================
    # STEP 3.5: Load Audio VAE (LTXV/LTX2)
    # ============================================================

    loaded_audio_vae = None

    if configure_audio_vae:
        if audio_vae_source == "Baked":
            # Extract the audio VAE baked into the loaded model file. LTX2
            # all-in-one files carry audio_vae./vocoder. keys whether they
            # live in checkpoints (Standard Checkpoint) or diffusion_models
            # (UNet Model). GGUF files can't be read by load_torch_file here.
            baked_model_path = None
            if is_standard and ckpt_name not in (None, "", "None"):
                baked_model_path = str(request.files["ckpt_name"].path)
            elif is_unet and unet_name not in (None, "", "None"):
                baked_model_path = str(request.files["unet_name"].path)
            if baked_model_path:
                loaded_audio_vae = load_audio_vae_from_path(baked_model_path)
                if loaded_audio_vae is None:
                    log.warning(
                        _LOG_PREFIX,
                        f"No baked audio VAE (audio_vae./vocoder. keys) found in '{os.path.basename(baked_model_path)}'",
                    )
            else:
                log.warning(
                    _LOG_PREFIX,
                    "Baked audio VAE requires a Standard Checkpoint or UNet Model file - select 'External' instead",
                )
        else:
            # External audio VAE file (ships as a checkpoint).
            if audio_vae_name in (None, "", "None"):
                log.warning(
                    _LOG_PREFIX, "External audio VAE requested but none selected"
                )
            else:
                audio_vae_path = request.files["audio_vae_name"].path
                loaded_audio_vae = load_audio_vae_from_path(str(audio_vae_path))
                if loaded_audio_vae is None:
                    log.warning(
                        _LOG_PREFIX,
                        f"No audio VAE weights (audio_vae./vocoder. keys) found in '{audio_vae_name}'",
                    )

    # ============================================================
    # STEP 4: Apply LoRAs
    # ============================================================

    lora_params = (
        collect_lora_params(kwargs, lora_count_int)
        if configure_model_only_lora
        else []
    )

    if lora_params:
        log.msg("LoRA", f"Applying {len(lora_params)} LoRA(s)...")
        loaded_model, loaded_clip = apply_loras(
            loaded_model, loaded_clip, lora_params
        )

    lora_string = format_lora_string(lora_params)

    # ============================================================
    # STEP 4.5: Apply Model Sampling
    # ============================================================

    if configure_model_sampling and loaded_model is not None:
        flux_width = sampling_width
        flux_height = sampling_height

        if configure_latent and sampling_method == "Flux":
            if resolution != "Custom" and resolution in RESOLUTION_MAP:
                auto_width, auto_height = RESOLUTION_MAP[resolution]
                flux_width = auto_width
                flux_height = auto_height
            else:
                flux_width = width
                flux_height = height
            log.msg(
                "Model Sampling",
                f"Auto-filled Flux dimensions from latent: {flux_width}x{flux_height}",
            )

        loaded_model = apply_model_sampling(
            loaded_model,
            sampling_method=sampling_method,
            shift=shift,
            base_shift=base_shift,
            width=flux_width,
            height=flux_height,
            original_timesteps=original_timesteps,
            zsnr=zsnr,
            sampling_subtype=sampling_subtype,
            sigma_max=sigma_max,
            sigma_min=sigma_min,
        )

    # ============================================================
    # STEP 4.6: Apply Block Swap
    # ============================================================

    if configure_blockswap:
        loaded_model = apply_blockswap(
            loaded_model,
            blocks_to_swap,
            offload_embeddings,
            _LOG_PREFIX,
            is_nunchaku=is_nunchaku,
            is_qwen=is_qwen,
            is_zimage=is_zimage,
        )

    # ============================================================
    # STEP 5: Create Latent Tensor
    # ============================================================

    latent_tensor = None
    detected_downscale = LATENT_DOWNSCALE
    final_width = width
    final_height = height

    if configure_latent:
        if resolution != "Custom" and resolution in RESOLUTION_MAP:
            final_width, final_height = RESOLUTION_MAP[resolution]

        detected_channels = LATENT_CHANNELS
        if loaded_vae:
            detected_channels = detect_latent_channels(loaded_vae)
            detected_downscale = detect_latent_downscale(loaded_vae)

        latent_tensor = torch.zeros(
            [
                batch_size,
                detected_channels,
                final_height // detected_downscale,
                final_width // detected_downscale,
            ],
            device="cpu",
        )

    # ============================================================
    # STEP 6: Construct output pipe
    # ============================================================

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

    pipe = build_pipe(
        model=loaded_model,
        model_name=checkpoint_name,
        is_nunchaku=is_nunchaku,
        lora_names=lora_string,
        clip=loaded_clip if configure_clip else OMIT,
        vae=loaded_vae if configure_vae else OMIT,
        audio_vae=(
            loaded_audio_vae
            if (configure_audio_vae and loaded_audio_vae is not None)
            else OMIT
        ),
        latent=(
            {
                "samples": latent_tensor,
                "downscale_ratio_spacial": detected_downscale,
            }
            if (configure_latent and latent_tensor is not None)
            else OMIT
        ),
        width=final_width if configure_latent else OMIT,
        height=final_height if configure_latent else OMIT,
        batch_size=batch_size if configure_latent else OMIT,
        vae_name=(
            vae_name
            if (not use_baked_vae and vae_name not in (None, "", "None"))
            else ""
        ),
        clip_skip=(
            stop_at_clip_layer
            if (is_standard and use_baked_clip and enable_clip_layer)
            else OMIT
        ),
        configure_sampler=configure_sampler,
        sampler_name=sampler_name if configure_sampler else OMIT,
        scheduler=scheduler if configure_sampler else OMIT,
        steps=steps if configure_sampler else OMIT,
        cfg=cfg if configure_sampler else OMIT,
        flux_guidance=flux_guidance if configure_sampler else OMIT,
        _allow_overwrite=False if configure_sampler else OMIT,
        seed=seed if configure_seed else OMIT,
    )

    return LoadResult(
        model=loaded_model,
        clip=loaded_clip,
        vae=loaded_vae,
        audio_vae=loaded_audio_vae,
        model_name=checkpoint_name,
        lora_names=lora_string,
        pipe=pipe,
    )
