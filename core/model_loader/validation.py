# Validation and canonical file resolution for diffusion model-loader requests.

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import folder_paths  # type: ignore

from ..common import RESOLUTION_MAP
from ..config_store import get_config_value
from ..logger import log

_LOG_PREFIX = "ModelLoader"

SAFE_TENSOR_EXTENSIONS = frozenset({".safetensors", ".sft"})
GGUF_EXTENSIONS = frozenset({".gguf"})
LEGACY_MODEL_EXTENSIONS = frozenset({".ckpt", ".pt", ".pth", ".bin"})

MODEL_TYPES = (
    "Standard Checkpoint",
    "UNet Model",
    "Nunchaku Flux",
    "Nunchaku Qwen",
    "Nunchaku ZImage",
    "GGUF Model",
)
FEATURES = frozenset(
    {
        "templates",
        "clip",
        "vae",
        "audio_vae",
        "latent",
        "sampler",
        "lora",
        "model_sampling",
        "block_swap",
        "memory_cleanup",
        "integrity",
        "seed",
    }
)
MODEL_LOADER_FEATURES = frozenset(
    {"lora", "model_sampling", "block_swap", "memory_cleanup"}
)

DOWNLOAD_TARGET_ROLES = (
    "",
    "checkpoints",
    "diffusion_models",
    "diffusion_models_gguf",
    "unet",
    "vae",
    "text_encoders",
    "loras",
    "embeddings",
    "clip_vision",
)

CIVITAI_PRECISIONS = (
    "fp32",
    "fp16",
    "bf16",
    "mxfp8",
    "fp8_mixed",
    "fp8_scaled",
    "fp8",
    "int8",
    "nf4",
    "nvfp4",
    "int4",
)

CIVITAI_GGUF_QUANTIZATIONS = (
    "Q8_0",
    "Q6_K",
    "Q5_K_M",
    "Q5_K_S",
    "Q5_1",
    "Q5_0",
    "Q4_K_XL",
    "Q4_K_M",
    "Q4_K_S",
    "Q4_1",
    "Q4_0",
    "Q3_K_XL",
    "Q3_K_L",
    "Q3_K_M",
    "Q3_K_S",
    "Q2_K_XL",
    "Q2_K",
    "Q2_K_S",
    "IQ4_XS",
    "IQ4_KS",
    "IQ4_NL",
    "IQ3_M",
    "IQ3_S",
    "IQ3_XS",
    "IQ3_XXS",
    "IQ2_XS",
    "IQ2_XXS",
    "IQ2_S",
    "IQ2_M",
    "IQ1_S",
    "IQ1_M",
    "TQ2_0",
    "TQ1_0",
)

MODEL_PRECISION_OPTIONS = (
    "default",
    *CIVITAI_PRECISIONS,
    "fp8_e4m3fn",
    "gguf",
    "gguf_unquantized",
    *CIVITAI_GGUF_QUANTIZATIONS,
)

_MODEL_SELECTION = {
    "Standard Checkpoint": ("checkpoints", "ckpt_name", "model"),
    "UNet Model": ("diffusion_models", "unet_name", "model"),
    "Nunchaku Flux": ("diffusion_models", "nunchaku_name", "model"),
    "Nunchaku Qwen": ("diffusion_models", "qwen_name", "model"),
    "Nunchaku ZImage": ("diffusion_models", "zimage_name", "model"),
    "GGUF Model": ("diffusion_models_gguf", "gguf_name", "model_gguf"),
}

_ENUMS = {
    "weight_dtype": {"default", "fp8_e4m3fn", "fp8_e4m3fn_fast", "fp8_e5m2"},
    "data_type": {"bfloat16", "float16"},
    "attention": {"flash-attention2", "nunchaku-fp16"},
    "i2f_mode": {"enabled", "always"},
    "cpu_offload": {"auto", "enable", "disable"},
    "use_pin_memory": {"enable", "disable"},
    "gguf_dequant_dtype": {"default", "target", "float32", "float16", "bfloat16"},
    "gguf_patch_dtype": {"default", "target", "float32", "float16", "bfloat16"},
    "sampling_method": {
        "None",
        "SD3",
        "AuraFlow",
        "Flux",
        "Stable Cascade",
        "LCM",
        "ContinuousEDM",
        "ContinuousV",
        "LTXV",
    },
    "sampling_subtype": {
        "eps",
        "v_prediction",
        "edm",
        "edm_playground_v2.5",
        "cosmos_rflow",
    },
    "clip_source": {"Baked", "External", "External + Model File"},
    "vae_source": {"Baked", "External"},
    "audio_vae_source": {"Baked", "External"},
    "verify_file": {"off", "sidecar", "verify"},
    "template_action": {"None", "Load", "Save"},
    "clip_type": {
        "flux",
        "flux2",
        "sd3",
        "sdxl",
        "stable_cascade",
        "stable_audio",
        "hunyuan_dit",
        "mochi",
        "ltxv",
        "hunyuan_video",
        "pixart",
        "cosmos",
        "cogvideox",
        "lumina2",
        "wan",
        "hidream",
        "chroma",
        "ace",
        "omnigen2",
        "qwen_image",
        "ideogram4",
        "boogu",
        "krea2",
    },
    "download_target_role": set(DOWNLOAD_TARGET_ROLES),
    "model_precision": set(MODEL_PRECISION_OPTIONS),
}

_NUMERIC_BOUNDS = {
    "cache_threshold": (0.0, 1.0),
    "num_blocks_on_gpu": (1, 60),
    "stop_at_clip_layer": (-24, -1),
    "blocks_to_swap": (0, 100),
    "shift": (0.0, 100.0),
    "base_shift": (0.0, 100.0),
    "sampling_width": (16, 32768),
    "sampling_height": (16, 32768),
    "original_timesteps": (1, 1000),
    "sigma_max": (0.0, 1000.0),
    "sigma_min": (0.0, 1000.0),
    "width": (16, 32768),
    "height": (16, 32768),
    "batch_size": (1, 4096),
    "steps": (1, 10000),
    "cfg": (0.0, 1000.0),
    "flux_guidance": (0.0, 1000.0),
    "seed": (-3, 2**64 - 1),
    "lora_weight_1": (-10.0, 10.0),
    "lora_weight_2": (-10.0, 10.0),
    "lora_weight_3": (-10.0, 10.0),
}
_MAX_WORKFLOW_METADATA_BYTES = 1024 * 1024
_MAX_LATENT_PIXEL_BATCH = 256 * 1024 * 1024
_AIR_RE = re.compile(r"^urn:air:[^:\s]+:[^:\s]+:civitai:\d+@\d+(?:\+\d+)?$")


class LoaderValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedModelFile:
    role: str
    relative_path: str
    path: Path
    reference_type: str


def parse_features(value: Any, *, smart: bool) -> tuple[str, ...]:
    if isinstance(value, dict):
        if set(value) != {"__value__"}:
            raise LoaderValidationError("features wrapper is malformed")
        value = value["__value__"]
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, (list, tuple)):
        if not all(isinstance(item, str) for item in value):
            raise LoaderValidationError("features must contain only strings")
        items = [item.strip() for item in value if item.strip()]
    elif value is None:
        items = []
    else:
        raise LoaderValidationError("features must be a string or string array")

    allowed = FEATURES if smart else MODEL_LOADER_FEATURES
    unknown = sorted(set(items) - allowed)
    if unknown:
        raise LoaderValidationError(f"Unknown loader feature: {unknown[0]}")
    return tuple(dict.fromkeys(items))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _contains_symlink(candidate: Path, root: Path) -> bool:
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _extension_allowed(extension: str, reference_type: str) -> bool:
    if extension in SAFE_TENSOR_EXTENSIONS:
        return True
    if extension in GGUF_EXTENSIONS:
        return reference_type in {"model_gguf", "clip"}
    if (
        extension in LEGACY_MODEL_EXTENSIONS
        and get_config_value("allow_legacy_model_formats", False) is True
    ):
        log.warning(
            _LOG_PREFIX,
            f"Administrator override permits legacy model format '{extension}'",
        )
        return True
    return False


def resolve_model_file(
    role: str,
    filename: str,
    *,
    reference_type: str,
) -> ResolvedModelFile:
    if not isinstance(filename, str) or not filename.strip() or filename == "None":
        raise LoaderValidationError(f"A {reference_type} file must be selected")
    if "\0" in filename or Path(filename).is_absolute():
        raise LoaderValidationError("Absolute or NUL-containing model paths are forbidden")

    normalized_name = filename.replace("\\", "/")
    posix_name = PurePosixPath(normalized_name)
    if any(part in {"", ".", ".."} for part in posix_name.parts):
        raise LoaderValidationError("Model path contains an unsafe component")

    role_candidates = [role]
    if role == "diffusion_models_gguf":
        role_candidates.append("diffusion_models")

    last_error = "Model file was not found in its declared folder role"
    for candidate_role in role_candidates:
        if candidate_role not in folder_paths.folder_names_and_paths:
            continue
        roots = [
            (
                Path(root).expanduser().absolute(),
                Path(root).expanduser().resolve(strict=True),
            )
            for root in folder_paths.get_folder_paths(candidate_role)
            if Path(root).expanduser().exists()
        ]
        try:
            full_path = folder_paths.get_full_path(candidate_role, normalized_name)
        except (KeyError, OSError, TypeError, ValueError):
            full_path = None
        if not full_path:
            continue

        lexical = Path(full_path).expanduser().absolute()
        matching_pair = next(
            (pair for pair in roots if _is_within(lexical, pair[0])),
            None,
        )
        if matching_pair is None:
            last_error = "Model path escapes its declared folder role"
            continue
        lexical_root, matching_root = matching_pair
        if _contains_symlink(lexical, lexical_root):
            raise LoaderValidationError("Symlinked model files and directories are forbidden")
        try:
            resolved = lexical.resolve(strict=True)
        except OSError as error:
            raise LoaderValidationError("Selected model file is unavailable") from error
        if not _is_within(resolved, matching_root):
            raise LoaderValidationError("Resolved model path escapes its declared folder role")
        if not resolved.is_file() or not os.access(resolved, os.R_OK):
            raise LoaderValidationError("Selected model must be a readable regular file")

        extension = resolved.suffix.lower()
        if not _extension_allowed(extension, reference_type):
            if extension in LEGACY_MODEL_EXTENSIONS:
                raise LoaderValidationError(
                    "Legacy model formats are disabled by the administrator"
                )
            raise LoaderValidationError(
                f"Unsupported {reference_type} extension '{extension or '<none>'}'"
            )
        return ResolvedModelFile(
            role=candidate_role,
            relative_path=resolved.relative_to(matching_root).as_posix(),
            path=resolved,
            reference_type=reference_type,
        )

    raise LoaderValidationError(last_error)


def resolve_clip_file(filename: str) -> ResolvedModelFile:
    first_error: LoaderValidationError | None = None
    for role in ("clip", "text_encoders"):
        try:
            return resolve_model_file(role, filename, reference_type="clip")
        except LoaderValidationError as error:
            if first_error is None:
                first_error = error
    raise first_error or LoaderValidationError("CLIP file was not found")


def _validate_enum(
    kwargs: Mapping[str, Any], key: str, allowed: Collection[str]
) -> None:
    if key in kwargs and kwargs[key] not in allowed:
        raise LoaderValidationError(f"Invalid {key}")


def _validate_number(kwargs: Mapping[str, Any], key: str, minimum: float, maximum: float) -> None:
    if key not in kwargs:
        return
    value = kwargs[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{key} must be numeric")
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise LoaderValidationError(f"{key} is outside the supported range")


def _validate_count(kwargs: Mapping[str, Any], key: str, minimum: int, maximum: int) -> int:
    raw = kwargs.get(key, str(minimum))
    if not isinstance(raw, (str, int)) or isinstance(raw, bool):
        raise LoaderValidationError(f"{key} must be an integer")
    try:
        value = int(raw)
    except ValueError as error:
        raise LoaderValidationError(f"{key} must be an integer") from error
    if str(value) != str(raw) or not minimum <= value <= maximum:
        raise LoaderValidationError(f"{key} is outside the supported range")
    return value


def _validate_json_container(
    kwargs: Mapping[str, Any],
    key: str,
    expected_type: type[dict | list],
    *,
    maximum_items: int,
) -> dict[Any, Any] | list[Any]:
    value = kwargs.get(key, expected_type())
    if isinstance(value, str):
        if len(value.encode("utf-8")) > _MAX_WORKFLOW_METADATA_BYTES:
            raise LoaderValidationError(f"{key} exceeds the supported size")
        try:
            value = json.loads(value) if value.strip() else expected_type()
        except json.JSONDecodeError as error:
            raise LoaderValidationError(f"{key} must contain valid JSON") from error
    if not isinstance(value, expected_type):
        raise LoaderValidationError(
            f"{key} must be a JSON {'object' if expected_type is dict else 'array'}"
        )
    if len(value) > maximum_items:
        raise LoaderValidationError(f"{key} contains too many entries")
    try:
        serialized_size = len(
            json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
        )
    except (TypeError, ValueError) as error:
        raise LoaderValidationError(f"{key} must contain JSON-compatible values") from error
    if serialized_size > _MAX_WORKFLOW_METADATA_BYTES:
        raise LoaderValidationError(f"{key} exceeds the supported size")
    return value


def validate_loader_request(
    kwargs: Mapping[str, Any],
    *,
    smart: bool,
) -> tuple[tuple[str, ...], dict[str, ResolvedModelFile]]:
    if not isinstance(kwargs, Mapping):
        raise LoaderValidationError("Loader request must be an object")

    model_type = kwargs.get("model_type", "Standard Checkpoint")
    if model_type not in MODEL_TYPES:
        raise LoaderValidationError("Invalid model_type")
    features = parse_features(kwargs.get("features"), smart=smart)

    for key, allowed in _ENUMS.items():
        _validate_enum(kwargs, key, allowed)
    for key, (minimum, maximum) in _NUMERIC_BOUNDS.items():
        _validate_number(kwargs, key, minimum, maximum)
    for key in (
        "gguf_patch_on_device",
        "enable_clip_layer",
        "offload_embeddings",
        "zsnr",
        "lora_switch_1",
        "lora_switch_2",
        "lora_switch_3",
    ):
        if key in kwargs and not isinstance(kwargs[key], bool):
            raise LoaderValidationError(f"{key} must be true or false")

    lora_count = _validate_count(kwargs, "lora_count", 1, 3)
    if smart:
        clip_count = _validate_count(kwargs, "clip_count", 1, 4)
        _validate_json_container(
            kwargs, "expected_hashes", dict, maximum_items=256
        )
        download_locators = _validate_json_container(
            kwargs, "download_locators", list, maximum_items=128
        )
        if not all(isinstance(locator, dict) for locator in download_locators):
            raise LoaderValidationError("download_locators must contain JSON objects")
        air_or_hash = kwargs.get("air_or_hash", "")
        if not isinstance(air_or_hash, str) or len(air_or_hash) > 512:
            raise LoaderValidationError("air_or_hash is malformed")
        if air_or_hash and not (
            re.fullmatch(r"[0-9a-fA-F]{64}", air_or_hash)
            or _AIR_RE.fullmatch(air_or_hash)
        ):
            raise LoaderValidationError("air_or_hash must be a SHA-256 or CivitAI AIR")
        resolution = kwargs.get(
            "resolution", "1024x1024 (1:1 XL/SD3/Flux/HiDream)"
        )
        if resolution != "Custom" and resolution not in RESOLUTION_MAP:
            raise LoaderValidationError("Invalid resolution")
        for key in ("template_name", "new_template_name"):
            value = kwargs.get(key)
            if value is not None and (
                not isinstance(value, str) or len(value.encode("utf-8")) > 512
            ):
                raise LoaderValidationError(f"{key} is malformed")
        if "sampler" in features:
            import comfy.samplers  # type: ignore

            if kwargs.get("sampler_name", "euler") not in comfy.samplers.KSampler.SAMPLERS:
                raise LoaderValidationError("Invalid sampler_name")
            if kwargs.get("scheduler", "normal") not in comfy.samplers.KSampler.SCHEDULERS:
                raise LoaderValidationError("Invalid scheduler")
    else:
        clip_count = 0

    role, field, reference_type = _MODEL_SELECTION[model_type]
    resolved_files = {
        field: resolve_model_file(
            role,
            kwargs.get(field, "None"),
            reference_type=reference_type,
        )
    }

    selected = set(features)
    external_only_model = model_type in {
        "Nunchaku Flux",
        "Nunchaku Qwen",
        "Nunchaku ZImage",
        "GGUF Model",
    }
    if smart and external_only_model:
        if "clip" in selected and kwargs.get("clip_source", "Baked") == "Baked":
            raise LoaderValidationError(
                f"{model_type} requires an external CLIP when the clip feature is enabled"
            )
        if "vae" in selected and kwargs.get("vae_source", "Baked") == "Baked":
            raise LoaderValidationError(
                f"{model_type} requires an external VAE when the vae feature is enabled"
            )
        if "audio_vae" in selected and kwargs.get("audio_vae_source", "Baked") == "Baked":
            raise LoaderValidationError(
                f"{model_type} requires an external audio VAE when audio_vae is enabled"
            )
        if "latent" in selected and "vae" not in selected:
            raise LoaderValidationError(
                f"{model_type} requires the vae feature to create a latent"
            )
    if "lora" in selected:
        for index in range(1, lora_count + 1):
            if kwargs.get(f"lora_switch_{index}", True) is False:
                continue
            name = kwargs.get(f"lora_name_{index}", "None")
            if name not in (None, "", "None"):
                resolved_files[f"lora_name_{index}"] = resolve_model_file(
                    "loras", name, reference_type="lora"
                )

    if smart and "clip" in selected and kwargs.get("clip_source", "Baked") != "Baked":
        for index in range(1, clip_count + 1):
            name = kwargs.get(f"clip_name{index}", "None")
            if name in (None, "", "None"):
                raise LoaderValidationError(
                    f"External CLIP selection {index} is required"
                )
            resolved_files[f"clip_name{index}"] = resolve_clip_file(name)

    if smart and "vae" in selected and kwargs.get("vae_source", "Baked") == "External":
        resolved_files["vae_name"] = resolve_model_file(
            "vae", kwargs.get("vae_name", "None"), reference_type="vae"
        )
    if smart and "audio_vae" in selected and kwargs.get("audio_vae_source", "External") == "External":
        resolved_files["audio_vae_name"] = resolve_model_file(
            "vae", kwargs.get("audio_vae_name", "None"), reference_type="audio_vae"
        )

    ltx_text_encoder = kwargs.get("ltx_text_encoder", "None")
    if not smart and ltx_text_encoder not in (None, "", "None"):
        resolved_files["ltx_text_encoder"] = resolve_clip_file(ltx_text_encoder)

    if kwargs.get("sigma_min", 0.002) > kwargs.get("sigma_max", 120.0):
        raise LoaderValidationError("sigma_min cannot exceed sigma_max")
    if "latent" in selected:
        width = kwargs.get("width", 1024)
        height = kwargs.get("height", 1024)
        if width % 8 or height % 8:
            raise LoaderValidationError("Latent width and height must be divisible by 8")
        if width * height * kwargs.get("batch_size", 1) > _MAX_LATENT_PIXEL_BATCH:
            raise LoaderValidationError("Requested latent batch exceeds the safety limit")

    return features, resolved_files
