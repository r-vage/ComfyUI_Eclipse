# Florence-2 Wrapper for Smart LML
#
# This module provides graceful loading support for Florence-2 models with fallback.
# Uses vendored Florence-2 implementation from extern/florence2/ (no external custom node dependency).
#
# Key Features:
# - Vendored Florence-2 model/config/processor from ComfyUI-Florence2
# - Graceful fallback to transformers AutoModel
# - Support for custom model implementations
# - Transformers v5 support via accelerate manual loading
# - Backward compatibility with transformers v4

import gc
import importlib.util
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch  # type: ignore
import transformers  # type: ignore

from .device import (
    auto_select_quantization,
    get_device_info,
    resolve_requested_device,
)
from .logger import log
from .model_types import _transformers_version as transformers_version

_LOG_PREFIX = "Florence-2"


# Transformers version detection (centralized in model_types)
_IS_V5 = transformers_version >= (5, 0)

# Florence-2 availability flags
FLORENCE2_CUSTOM_AVAILABLE = False
Florence2ForConditionalGeneration: Any | None = None
Florence2Config: Any | None = None
Florence2Processor: Any | None = None


@dataclass(frozen=True)
class FlorenceLoadPlan:
    # Effective Florence settings resolved before cache lookup and construction.
    requested_device: str
    requested_quantization: str
    requested_attention: str
    device: torch.device
    dtype: torch.dtype
    effective_quantization: str
    attention_candidates: tuple[str, ...]

    @property
    def dtype_name(self) -> str:
        return str(self.dtype).removeprefix("torch.")


@dataclass(frozen=True)
class FlorenceCheckpointLayout:
    files: tuple[Path, ...]
    declared_weight_map: dict[str, str]
    format_name: str


def _safe_checkpoint_shard(model_dir: Path, filename: object) -> Path:
    if not isinstance(filename, str) or not filename or "\x00" in filename:
        raise TypeError("Florence checkpoint shard names must be non-empty strings")
    relative = Path(filename.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe Florence checkpoint shard path: {filename!r}")
    resolved = (model_dir / relative).resolve(strict=False)
    try:
        resolved.relative_to(model_dir.resolve(strict=False))
    except ValueError as error:
        raise ValueError(f"Florence checkpoint shard escapes model folder: {filename!r}") from error
    if not resolved.is_file():
        raise FileNotFoundError(f"Missing Florence checkpoint shard: {relative.as_posix()}")
    return resolved


def _read_checkpoint_index(
    model_dir: Path,
    index_path: Path,
    *,
    expected_suffix: str,
    format_name: str,
) -> FlorenceCheckpointLayout:
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read Florence checkpoint index: {error}") from error
    weight_map = index.get("weight_map") if isinstance(index, dict) else None
    if not isinstance(weight_map, dict) or not weight_map:
        raise ValueError(f"Florence checkpoint index has no weight_map: {index_path.name}")

    declared = {}
    files = set()
    for key, filename in weight_map.items():
        if not isinstance(key, str) or not key:
            raise TypeError("Florence checkpoint index keys must be non-empty strings")
        shard = _safe_checkpoint_shard(model_dir, filename)
        if shard.suffix.lower() != expected_suffix:
            raise ValueError(
                f"Florence {format_name} index references incompatible shard: {shard.name}"
            )
        declared[key] = shard.relative_to(model_dir).as_posix()
        files.add(shard)
    return FlorenceCheckpointLayout(
        files=tuple(sorted(files)),
        declared_weight_map=declared,
        format_name=format_name,
    )


def _resolve_florence_checkpoint(model_path: str) -> FlorenceCheckpointLayout:
    model_dir = Path(model_path).resolve(strict=False)
    safetensors_index = model_dir / "model.safetensors.index.json"
    safetensors_single = model_dir / "model.safetensors"
    pytorch_index = model_dir / "pytorch_model.bin.index.json"
    pytorch_single = model_dir / "pytorch_model.bin"

    if safetensors_index.is_file():
        return _read_checkpoint_index(
            model_dir,
            safetensors_index,
            expected_suffix=".safetensors",
            format_name="Safetensors",
        )
    if safetensors_single.is_file():
        return FlorenceCheckpointLayout(
            files=(safetensors_single,),
            declared_weight_map={},
            format_name="Safetensors",
        )
    if pytorch_index.is_file():
        return _read_checkpoint_index(
            model_dir,
            pytorch_index,
            expected_suffix=".bin",
            format_name="restricted PyTorch",
        )
    if pytorch_single.is_file():
        return FlorenceCheckpointLayout(
            files=(pytorch_single,),
            declared_weight_map={},
            format_name="restricted PyTorch",
        )
    raise FileNotFoundError(
        "No Florence-2 model weights found; expected model.safetensors, "
        "model.safetensors.index.json, pytorch_model.bin, or "
        "pytorch_model.bin.index.json"
    )


def _populate_florence_checkpoint(
    model: Any,
    model_path: str,
    *,
    device: torch.device,
    dtype: torch.dtype,
    set_module_tensor_to_device: Any,
) -> tuple[str, ...]:
    from comfy.utils import load_torch_file  # type: ignore

    layout = _resolve_florence_checkpoint(model_path)
    if layout.format_name == "restricted PyTorch":
        log.warning(
            _LOG_PREFIX,
            "Loading Florence-2 PyTorch .bin weights with restricted weights-only loading; "
            "Safetensors is preferred.",
        )

    expected_state = model.state_dict(keep_vars=True)
    expected_keys = set(expected_state)
    declared_keys = set(layout.declared_weight_map)
    available_keys = declared_keys if declared_keys else None
    shared_key = "language_model.model.shared.weight"
    shared_aliases = {
        "language_model.model.encoder.embed_tokens.weight",
        "language_model.model.decoder.embed_tokens.weight",
    }
    allowed_tied_missing = {"language_model.lm_head.weight"}
    loaded_targets = set()
    seen_keys = set()

    for shard in layout.files:
        state_dict = load_torch_file(
            str(shard),
            safe_load=True,
            device=torch.device("cpu"),
        )
        if not isinstance(state_dict, dict):
            raise TypeError(f"Florence checkpoint shard is not a state dictionary: {shard.name}")
        if available_keys is None:
            available_keys = set(state_dict)

        shard_relative = shard.relative_to(Path(model_path).resolve(strict=False)).as_posix()
        for key, tensor in state_dict.items():
            if not isinstance(key, str) or not isinstance(tensor, torch.Tensor):
                raise TypeError(f"Invalid Florence state entry in {shard.name}: {key!r}")
            if key in seen_keys:
                raise ValueError(f"Duplicate Florence checkpoint key across shards: {key}")
            seen_keys.add(key)
            if layout.declared_weight_map:
                declared_shard = layout.declared_weight_map.get(key)
                if declared_shard != shard_relative:
                    raise ValueError(
                        f"Florence index maps {key} to {declared_shard!r}, "
                        f"but the tensor is stored in {shard_relative!r}"
                    )

            targets = [key] if key in expected_keys else []
            if key == shared_key:
                targets.extend(
                    alias
                    for alias in shared_aliases
                    if alias in expected_keys and alias not in available_keys
                )
            if not targets:
                raise ValueError(f"Unexpected Florence checkpoint key: {key}")

            for target in targets:
                expected = expected_state[target]
                if tuple(tensor.shape) != tuple(expected.shape):
                    raise ValueError(
                        f"Florence tensor shape mismatch for {target}: "
                        f"expected {tuple(expected.shape)}, got {tuple(tensor.shape)}"
                    )
                if expected.is_floating_point() and not tensor.is_floating_point():
                    raise ValueError(
                        f"Florence tensor dtype mismatch for {target}: "
                        f"expected floating weights, got {tensor.dtype}"
                    )
                set_module_tensor_to_device(
                    model,
                    target,
                    device,
                    value=tensor.to(dtype) if tensor.is_floating_point() else tensor,
                )
                loaded_targets.add(target)
        del state_dict

    if declared_keys and seen_keys != declared_keys:
        missing_declared = sorted(declared_keys - seen_keys)
        unexpected = sorted(seen_keys - declared_keys)
        raise ValueError(
            "Florence checkpoint index mismatch; "
            f"missing={missing_declared[:5]}, unexpected={unexpected[:5]}"
        )

    missing = expected_keys - loaded_targets
    unresolved_missing = sorted(missing - allowed_tied_missing)
    if unresolved_missing:
        raise ValueError(f"Florence checkpoint is missing required keys: {unresolved_missing[:8]}")

    model.language_model.tie_weights()
    unresolved_meta = sorted(
        name
        for name, tensor in model.state_dict(keep_vars=True).items()
        if tensor.device.type == "meta"
    )
    if unresolved_meta:
        raise ValueError(
            f"Florence checkpoint left unresolved meta tensors: {unresolved_meta[:8]}"
        )
    return tuple(sorted(missing))


def _is_flash_attention_available() -> bool:
    return importlib.util.find_spec("flash_attn") is not None


def _is_sdpa_available() -> bool:
    return hasattr(torch.nn.functional, "scaled_dot_product_attention")


def _resolve_attention_candidates(
    requested_attention: str, device: torch.device
) -> tuple[str, ...]:
    requested = requested_attention or "auto"
    supported = {"auto", "flash_attention_2", "sdpa", "eager"}
    if requested not in supported:
        raise ValueError(
            f"Unsupported Florence-2 attention mode '{requested_attention}'."
        )

    if requested == "flash_attention_2":
        if device.type != "cuda":
            raise RuntimeError(
                "Florence-2 Flash Attention 2 requires a CUDA/ROCm device."
            )
        if not _is_flash_attention_available():
            raise RuntimeError(
                "Florence-2 Flash Attention 2 was requested but flash-attn is not available."
            )
        return (requested,)
    if requested == "sdpa":
        if not _is_sdpa_available():
            raise RuntimeError(
                "Florence-2 SDPA was requested but this PyTorch build does not provide it."
            )
        return (requested,)
    if requested == "eager":
        return (requested,)

    candidates = []
    if device.type == "cuda" and _is_flash_attention_available():
        candidates.append("flash_attention_2")
    if _is_sdpa_available():
        candidates.append("sdpa")
    candidates.append("eager")
    return tuple(candidates)


def resolve_florence_load_plan(
    *,
    requested_device: str,
    requested_quantization: str,
    requested_attention: str,
    model_size_gb: float,
    device_info: dict[str, Any] | None = None,
) -> FlorenceLoadPlan:
    # Resolve truthful Florence device/precision/attention before cache lookup.
    automatic_device = requested_device == "auto"
    if automatic_device:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = resolve_requested_device(requested_device)
    if device.type == "mps":
        raise RuntimeError(
            "Florence-2 on MPS is not qualified for the vendored Transformers "
            "loader. Choose CPU, or CUDA/ROCm when available."
        )

    quantization = (requested_quantization or "auto").lower()
    valid_quantization = {"auto", "none", "fp16", "bf16", "fp32", "4bit", "8bit"}
    if quantization not in valid_quantization:
        raise ValueError(
            f"Unsupported Florence-2 quantization '{requested_quantization}'."
        )

    info = device_info if device_info is not None else get_device_info(device)
    if device.type == "cuda":
        if _IS_V5 and quantization in {"4bit", "8bit"}:
            raise RuntimeError(
                "BitsAndBytes 4-bit/8-bit quantization is not supported by the "
                "Transformers v5 Florence-2 compatibility loader. Free accelerator "
                "memory, choose CPU, or use native FP16/BF16/FP32 explicitly."
            )

        if quantization == "auto":
            selected = auto_select_quantization(
                model_name="Florence-2",
                estimated_size_gb=model_size_gb,
                device_info=info,
                target_device=device,
                log_decision=not automatic_device,
            )
            if _IS_V5 and selected in {"4bit", "8bit"}:
                if not automatic_device:
                    raise RuntimeError(
                        "BitsAndBytes 4-bit/8-bit quantization is not supported by the "
                        "Transformers v5 Florence-2 compatibility loader, and native "
                        "FP16 does not fit the current accelerator-memory budget. "
                        "Release VRAM or select CPU."
                    )
                free_memory = info["gpu"]["free_memory"]
                log.warning(
                    _LOG_PREFIX,
                    "Auto device: native CUDA/ROCm FP16 does not fit the current "
                    f"accelerator-memory budget ({free_memory:.1f} GB free); "
                    "falling back to CPU FP32.",
                )
                device = torch.device("cpu")
            else:
                effective_quantization = selected
                if automatic_device:
                    free_memory = info["gpu"]["free_memory"]
                    log.msg(
                        _LOG_PREFIX,
                        "Auto device selected CUDA/ROCm with native "
                        f"{selected.upper()} ({free_memory:.1f} GB free).",
                    )
        elif quantization == "none":
            effective_quantization = "fp16"
        else:
            effective_quantization = quantization

        if device.type == "cuda":
            dtype_map = {
                "fp16": torch.float16,
                "bf16": torch.bfloat16,
                "fp32": torch.float32,
                "4bit": torch.float16,
                "8bit": torch.float16,
            }
            dtype = dtype_map[effective_quantization]

    if device.type == "cpu":
        if quantization not in {"auto", "none", "fp32"}:
            raise RuntimeError(
                f"Florence-2 {quantization} is not qualified on CPU. "
                "Use automatic or FP32 precision."
            )

        if model_size_gb > 0:
            available = info["system_memory"]["available"]
            safety_margin = max(2.5, model_size_gb * 0.25)
            required_fp32 = model_size_gb * 2.0 * 1.3
            if required_fp32 > available - safety_margin:
                raise RuntimeError(
                    "Florence-2 FP32 does not fit the requested CPU memory "
                    f"budget (need about {required_fp32:.1f} GB, "
                    f"{available:.1f} GB available before headroom)."
                )
        dtype = torch.float32
        effective_quantization = "fp32"
        if automatic_device:
            log.msg(_LOG_PREFIX, "Auto device selected CPU with FP32 precision.")

    return FlorenceLoadPlan(
        requested_device=requested_device,
        requested_quantization=requested_quantization,
        requested_attention=requested_attention,
        device=device,
        dtype=dtype,
        effective_quantization=effective_quantization,
        attention_candidates=_resolve_attention_candidates(
            requested_attention, device
        ),
    )


def _cleanup_failed_florence_load(device: torch.device) -> None:
    # Release partial construction state before trying a compatible attention mode.
    gc.collect()
    if device.type == "cuda" and torch.cuda.is_available():
        torch.cuda.empty_cache()
    elif device.type == "mps" and hasattr(torch, "mps"):
        try:
            torch.mps.empty_cache()
        except (AttributeError, RuntimeError) as error:
            log.debug(_LOG_PREFIX, f"Could not clear MPS cache after failed load: {error}")


def _is_attention_compatibility_error(error: Exception, attention: str) -> bool:
    message = str(error).lower()
    attention_markers = {
        "flash_attention_2": ("flash", "flash_attn"),
        "sdpa": ("sdpa", "scaled_dot_product", "attention"),
        "eager": ("eager", "attention"),
    }
    incompatibility_markers = (
        "unsupported",
        "not support",
        "not implemented",
        "unavailable",
        "requires",
        "cannot use",
        "incompatible",
    )
    has_attention_marker = any(
        marker in message for marker in attention_markers.get(attention, ())
    )
    typed_compatibility_error = isinstance(
        error, (ImportError, AttributeError, NotImplementedError)
    )
    return has_attention_marker and (
        typed_compatibility_error
        or any(marker in message for marker in incompatibility_markers)
    )


def _record_effective_settings(
    model: Any,
    *,
    device: torch.device,
    dtype: torch.dtype,
    quantization: str,
    attention: str,
) -> Any:
    model._sml_effective_device = str(device)
    model._sml_effective_dtype = str(dtype).removeprefix("torch.")
    model._sml_effective_quantization = quantization
    model._sml_effective_attention = attention
    return model


def _finalize_loaded_florence(
    model: Any,
    *,
    device: torch.device,
    dtype: torch.dtype,
    quantization: str,
    attention: str,
) -> Any:
    if quantization not in {"4bit", "8bit"}:
        model = model.eval().to(dtype).to(device)
    _record_effective_settings(
        model,
        device=device,
        dtype=dtype,
        quantization=quantization,
        attention=attention,
    )
    log.msg(
        _LOG_PREFIX,
        "Effective settings: "
        f"device={device}, precision={quantization}, "
        f"dtype={str(dtype).removeprefix('torch.')}, attention={attention}",
    )
    return model

# Import custom Florence-2 implementation from vendored extern package
# These classes work with both v4 and v5 — on v5 we use them with init_empty_weights
try:
    from ...extern.florence2.configuration_florence2 import Florence2Config as _F2Config
    from ...extern.florence2.modeling_florence2 import (
        Florence2ForConditionalGeneration as _F2Model,
    )
    from ...extern.florence2.processing_florence2 import (
        Florence2Processor as _F2Processor,
    )

    Florence2ForConditionalGeneration = _F2Model
    Florence2Config = _F2Config
    Florence2Processor = _F2Processor

    FLORENCE2_CUSTOM_AVAILABLE = True
    log.msg(_LOG_PREFIX, "✓ Custom Florence-2 classes imported successfully")
except ImportError as e:
    log.warning(_LOG_PREFIX, f"Could not import custom Florence-2: {e}")
    log.warning(_LOG_PREFIX, "Will fall back to transformers AutoModel")
except Exception as e:
    log.warning(_LOG_PREFIX, f"Could not import custom Florence-2: {e}")
    log.warning(_LOG_PREFIX, "Will fall back to transformers AutoModel")


def _load_florence2_v5(
    model_path: str, attn_impl: str, dtype: torch.dtype, device: torch.device
) -> Any:
    # Load Florence-2 model using accelerate for transformers v5+.
    # Uses init_empty_weights + manual state dict loading to bypass v5 from_pretrained issues.
    #
    # This approach matches ComfyUI-Florence2's load_model() function.
    #
    # Args:
    #     model_path: Path to local model directory
    #     attn_impl: Attention implementation ('sdpa', 'flash_attention_2', 'eager')
    #     dtype: Target dtype (torch.float16, torch.bfloat16, etc.)
    #     device: Target device
    #
    # Returns:
    #     Loaded Florence-2 model

    if (
        not FLORENCE2_CUSTOM_AVAILABLE
        or not Florence2Config
        or not Florence2ForConditionalGeneration
    ):
        raise RuntimeError(
            "Florence-2 custom classes not available. Cannot load with v5 method."
        )

    from accelerate import init_empty_weights  # type: ignore
    from accelerate.utils import set_module_tensor_to_device  # type: ignore

    log.msg(_LOG_PREFIX, f"Loading with v5 method (accelerate): {model_path}")

    # Load config and set attention mode
    config = Florence2Config.from_pretrained(model_path)
    config._attn_implementation = attn_impl

    # Create empty model shell
    with init_empty_weights():
        model = Florence2ForConditionalGeneration(config)

    tied_keys = _populate_florence_checkpoint(
        model,
        model_path,
        device=device,
        dtype=dtype,
        set_module_tensor_to_device=set_module_tensor_to_device,
    )
    if tied_keys:
        log.debug(
            _LOG_PREFIX, f"{len(tied_keys)} tied weight(s) resolved by tie_weights()"
        )
        for key in tied_keys[:5]:
            log.debug(_LOG_PREFIX, f"  Tied: {key}")
    model = model.eval().to(dtype).to(device)

    log.msg(
        _LOG_PREFIX, f"✓ Loaded with v5 method, attention={attn_impl}, dtype={dtype}"
    )
    return model


def _load_florence2_processor_v5(model_path: str) -> Any:
    # Load Florence-2 processor for transformers v5+.
    # Constructs processor manually from CLIPImageProcessor + BartTokenizerFast
    # to bypass v5 AutoProcessor/from_pretrained issues.
    #
    # Args:
    #     model_path: Path to local model directory
    #
    # Returns:
    #     Florence2Processor instance

    if not FLORENCE2_CUSTOM_AVAILABLE or not Florence2Processor:
        raise RuntimeError(
            "Florence-2 custom classes not available. Cannot create processor with v5 method."
        )

    from tokenizers import AddedToken as TokAddedToken  # type: ignore
    from tokenizers import Tokenizer as HFTokenizer  # type: ignore
    from transformers import BartTokenizerFast, CLIPImageProcessor  # type: ignore

    # Create image processor with Florence-2 standard settings
    image_processor = CLIPImageProcessor(
        do_resize=True,
        size={"height": 768, "width": 768},
        resample=3,  # BICUBIC
        do_center_crop=False,
        do_rescale=True,
        rescale_factor=1 / 255.0,
        do_normalize=True,
        image_mean=[0.485, 0.456, 0.406],
        image_std=[0.229, 0.224, 0.225],
    )
    image_processor.image_seq_length = 577

    # Create tokenizer by loading tokenizer.json directly via the tokenizers library.
    # BartTokenizerFast.from_pretrained() crashes on transformers v5 because
    # _extra_special_tokens stores Florence-2's 1024 task tokens as raw dicts
    # instead of AddedToken objects, causing the Rust tokenizer backend to reject them.
    model_dir = Path(model_path)
    tok_obj = HFTokenizer.from_file(str(model_dir / "tokenizer.json"))

    # Add Florence-2 task/location tokens from added_tokens.json
    # CRITICAL: tokens must be sorted by their expected ID before adding, because
    # the tokenizers library assigns sequential IDs starting from current vocab size.
    # Without sorting, token-to-ID mapping is scrambled → CUDA index-out-of-bounds crash.
    added_tokens_file = model_dir / "added_tokens.json"
    added_token_strs = []
    if added_tokens_file.exists():
        with open(added_tokens_file, encoding="utf-8") as f:
            added_tokens_dict = json.load(f)
        sorted_tokens = sorted(added_tokens_dict.items(), key=lambda x: x[1])
        added_token_strs = [t for t, _ in sorted_tokens]
        tok_obj.add_special_tokens(
            [TokAddedToken(t, special=True, normalized=False) for t in added_token_strs]
        )

    tokenizer = BartTokenizerFast(
        tokenizer_object=tok_obj,
        bos_token="<s>",
        eos_token="</s>",
        unk_token="<unk>",
        pad_token="<pad>",
        mask_token="<mask>",
    )
    # Set additional_special_tokens as instance attribute — transformers v5 removed this
    # from SPECIAL_TOKENS_ATTRIBUTES, but vendored Florence2Processor.__init__ needs it
    tokenizer.additional_special_tokens = added_token_strs

    # Build processor from vendored Florence2Processor
    processor = Florence2Processor(image_processor=image_processor, tokenizer=tokenizer)
    log.msg(_LOG_PREFIX, "✓ Loaded processor with v5 method (manual construction)")
    return processor


def load_florence2_model(model_path: str, **load_kwargs) -> Any:
    # Load Florence-2 model with custom implementation if available, fallback to AutoModel.
    # Supports both local model paths and HuggingFace repo IDs.
    # On transformers v5+, uses accelerate-based manual loading.
    #
    # Args:
    #     model_path: Path to local model directory or HuggingFace repo ID
    #     **load_kwargs: Additional arguments for from_pretrained (dtype, device_map, etc.)
    #         Also accepts `trust_remote_code` (bool, default False) — passed to
    #         HuggingFace from_pretrained to allow auto_map/modeling_*.py execution.
    #
    # Returns:
    #     Loaded Florence-2 model

    # Extract trust_remote_code from load_kwargs (default False = safe). When False,
    # the v4 AutoModel fallback path still requires True for Florence-2 to load at
    # all (architecture not in transformers core) — caller controls this via the
    # registry flag or the runtime chip.
    trust_remote_code = bool(load_kwargs.pop("trust_remote_code", False))
    repo_id = load_kwargs.pop("repo_id", "")
    revision = load_kwargs.pop("revision", None)
    expected_sha256_map = load_kwargs.pop("expected_sha256_map", None)
    target_device_value = load_kwargs.pop("device", None)
    if target_device_value is None:
        target_device_value = "cuda" if torch.cuda.is_available() else "cpu"
    target_device = resolve_requested_device(target_device_value)
    requested_device_mode = load_kwargs.pop(
        "requested_device_mode", str(target_device_value)
    )
    requested_attention_mode = load_kwargs.pop(
        "requested_attention_mode",
        load_kwargs.get("attn_implementation", "auto"),
    )
    supplied_candidates = load_kwargs.pop("attention_candidates", None)
    attention_candidates = (
        tuple(supplied_candidates)
        if supplied_candidates
        else _resolve_attention_candidates(requested_attention_mode, target_device)
    )
    effective_quantization = load_kwargs.pop("effective_quantization", "fp16")
    requested_quantization_mode = load_kwargs.pop(
        "requested_quantization_mode", effective_quantization
    )

    # Determine if loading from local path or remote
    is_local = Path(model_path).exists()
    source = "local" if is_local else "remote"

    # Verify model integrity if loading from local cache
    if is_local:
        from .model_files import verify_model_integrity

        if not verify_model_integrity(
            Path(model_path),
            repo_id,
            revision=revision,
            expected_sha256_map=expected_sha256_map,
        ):
            raise RuntimeError(
                f"Florence-2 model integrity check failed for {model_path}. The model may be corrupted. Please delete and re-download."
            )

    # Florence-2 cannot consume the literal "auto" value. The caller supplies
    # target-aware candidates resolved before cache lookup.
    requested_attn = attention_candidates[0]
    load_kwargs["attn_implementation"] = requested_attn
    dtype = load_kwargs.get("torch_dtype", load_kwargs.get("dtype", torch.float16))
    if dtype == "auto" or dtype is None:
        dtype = torch.float16

    # ========================================================================
    # Transformers v5+ path: Use accelerate-based manual loading
    # ========================================================================
    if _IS_V5 and FLORENCE2_CUSTOM_AVAILABLE:
        # Never turn a requested/cache-keyed BnB model into native precision.
        if "quantization_config" in load_kwargs:
            raise RuntimeError(
                "BitsAndBytes quantization is not supported with the Transformers "
                "v5 Florence-2 compatibility loader."
            )

        for index, attention in enumerate(attention_candidates):
            try:
                model = _load_florence2_v5(
                    model_path, attention, dtype, target_device
                )
                _record_effective_settings(
                    model,
                    device=target_device,
                    dtype=dtype,
                    quantization=effective_quantization,
                    attention=attention,
                )
                log.msg(
                    _LOG_PREFIX,
                    "Effective settings: "
                    f"device={target_device}, precision={effective_quantization}, "
                    f"dtype={str(dtype).removeprefix('torch.')}, attention={attention} "
                    f"(requested: device={requested_device_mode}, "
                    f"precision={requested_quantization_mode}, "
                    f"attention={requested_attention_mode})",
                )
                return model
            except Exception as error:
                can_retry = (
                    requested_attention_mode == "auto"
                    and index + 1 < len(attention_candidates)
                    and _is_attention_compatibility_error(error, attention)
                )
                if not can_retry:
                    raise
                next_attention = attention_candidates[index + 1]
                log.warning(
                    _LOG_PREFIX,
                    f"{attention} is incompatible with this Florence-2 load; "
                    f"retrying with {next_attention}",
                )
                _cleanup_failed_florence_load(target_device)

        raise RuntimeError("No compatible Florence-2 attention mode was available.")

    # ========================================================================
    # Transformers v4 path: Custom from_pretrained or AutoModel fallback
    # ========================================================================

    # Try custom implementation first
    if FLORENCE2_CUSTOM_AVAILABLE and Florence2ForConditionalGeneration:
        try:
            log.msg(
                _LOG_PREFIX,
                f"Loading from {source} with custom implementation: {model_path}",
            )
            model = Florence2ForConditionalGeneration.from_pretrained(
                model_path,
                local_files_only=is_local,  # Prevent online lookup for local models
                **load_kwargs,
            )
            log.debug(_LOG_PREFIX, "Loaded with custom implementation")
        except Exception as e:
            log.warning(_LOG_PREFIX, f"Custom implementation failed: {e}")
            log.warning(_LOG_PREFIX, "Falling back to AutoModel...")
        else:
            return _finalize_loaded_florence(
                model,
                device=target_device,
                dtype=dtype,
                quantization=effective_quantization,
                attention=requested_attn,
            )

    # Fallback to AutoModel (v4 only)
    from transformers import AutoModelForCausalLM  # type: ignore

    log.msg(
        _LOG_PREFIX, f"Loading from {source} with AutoModelForCausalLM: {model_path}"
    )

    # Apply workaround context manager if needed (for transformers < 4.51.0)
    if transformers.__version__ < "4.51.0":
        from unittest.mock import patch

        from transformers.dynamic_module_utils import get_imports  # type: ignore

        def fixed_get_imports(filename):
            # Workaround for unnecessary flash_attn requirement
            imports = []
            try:
                if not str(filename).endswith("modeling_florence2.py"):
                    return get_imports(filename)
                imports = get_imports(filename)
                if "flash_attn" in imports:
                    imports.remove("flash_attn")
            except Exception:
                pass
            return imports

        log.msg(
            _LOG_PREFIX,
            f"Applying flash_attn workaround for transformers {transformers.__version__}",
        )
        load_context = patch(
            "transformers.dynamic_module_utils.get_imports", fixed_get_imports
        )
    else:
        from contextlib import nullcontext

        load_context = nullcontext()

    with load_context:
        if requested_attn == "flash_attention_2":
            try:
                log.msg(_LOG_PREFIX, "Attempting Flash Attention 2...")
                model = AutoModelForCausalLM.from_pretrained(
                    model_path,
                    trust_remote_code=trust_remote_code,
                    local_files_only=is_local,
                    **load_kwargs,
                )
                log.msg(_LOG_PREFIX, "✓ Loaded with Flash Attention 2")
                return _finalize_loaded_florence(
                    model,
                    device=target_device,
                    dtype=dtype,
                    quantization=effective_quantization,
                    attention="flash_attention_2",
                )
            except (ValueError, ImportError) as e:
                if "does not support Flash Attention 2.0" in str(
                    e
                ) or "flash_attn" in str(e):
                    if requested_attention_mode != "auto":
                        raise
                    log.warning(
                        _LOG_PREFIX,
                        "Flash Attention 2 not supported by cached model code",
                    )
                    log.error(
                        _LOG_PREFIX,
                        "Your Florence-2 model uses outdated cached code from HuggingFace",
                    )

                    cache_hint = os.path.join(
                        os.path.expanduser("~"),
                        ".cache",
                        "huggingface",
                        "modules",
                        "transformers_modules",
                    )
                    model_name = (
                        Path(model_path).name if is_local else model_path.split("/")[-1]
                    )

                    log.error(
                        _LOG_PREFIX,
                        "To update: Delete cached folder and restart ComfyUI:",
                    )
                    log.error(_LOG_PREFIX, f"  Location: {cache_hint}/{model_name}")
                    log.warning(
                        _LOG_PREFIX,
                        "Falling back to SDPA (still faster than eager mode)",
                    )

                    load_kwargs["attn_implementation"] = "sdpa"
                else:
                    raise

        # Load with requested attention mode (or fallback to sdpa)
        try:
            model = AutoModelForCausalLM.from_pretrained(
                model_path,
                trust_remote_code=trust_remote_code,
                local_files_only=is_local,
                **load_kwargs,
            )
        except AttributeError as e:
            if "_supports_sdpa" in str(e):
                if requested_attention_mode != "auto":
                    raise
                log.warning(
                    _LOG_PREFIX,
                    "Model lacks SDPA support attribute, falling back to eager attention",
                )
                load_kwargs["attn_implementation"] = "eager"
                model = AutoModelForCausalLM.from_pretrained(
                    model_path,
                    trust_remote_code=trust_remote_code,
                    local_files_only=is_local,
                    **load_kwargs,
                )
            else:
                raise

    # Add _supports_sdpa to model class if not present (custom models from HF may lack it)
    if not hasattr(type(model), "_supports_sdpa"):
        log.warning(
            _LOG_PREFIX, f"Adding _supports_sdpa=True to {type(model).__name__}"
        )
        type(model)._supports_sdpa = True

    # Also patch language_model subcomponent if it exists (for Florence2ForConditionalGeneration)
    if hasattr(model, "language_model") and not hasattr(
        type(model.language_model), "_supports_sdpa"
    ):
        log.warning(
            _LOG_PREFIX,
            f"Adding _supports_sdpa=True to {type(model.language_model).__name__}",
        )
        type(model.language_model)._supports_sdpa = True

    attn_used = load_kwargs.get("attn_implementation", "auto")
    log.msg(
        _LOG_PREFIX, f"✓ Loaded with AutoModel from {source}, attention={attn_used}"
    )
    return _finalize_loaded_florence(
        model,
        device=target_device,
        dtype=dtype,
        quantization=effective_quantization,
        attention=attn_used,
    )


def load_florence2_processor(model_path: str, **kwargs) -> Any:
    # Load Florence-2 processor with custom implementation if available, fallback to AutoProcessor.
    # Supports both local model paths and HuggingFace repo IDs.
    # On transformers v5+, constructs processor manually.
    #
    # Args:
    #     model_path: Path to local model directory or HuggingFace repo ID
    #     **kwargs: Additional arguments for from_pretrained
    #
    # Returns:
    #     Loaded Florence-2 processor

    # ========================================================================
    # Transformers v5+ path: Manual processor construction
    # ========================================================================
    if _IS_V5 and FLORENCE2_CUSTOM_AVAILABLE:
        return _load_florence2_processor_v5(model_path)

    # ========================================================================
    # Transformers v4 path: Custom from_pretrained or AutoProcessor fallback
    # ========================================================================

    # Determine if loading from local path or remote
    model_path_obj = Path(model_path)
    is_local = model_path_obj.exists()

    # Check if the local folder has the required dynamic module file for the processor
    # If not (e.g., models from comfyui-florence2 node), we need to allow online lookup
    has_processor_module = (
        is_local and (model_path_obj / "processing_florence2.py").exists()
    )
    local_files_only = (
        has_processor_module  # Only force local if we have all required files
    )

    # Try custom processor first
    if FLORENCE2_CUSTOM_AVAILABLE and Florence2Processor:
        try:
            processor = Florence2Processor.from_pretrained(
                model_path, local_files_only=local_files_only, **kwargs
            )
            return processor
        except Exception as e:
            log.warning(
                _LOG_PREFIX, f"Custom processor failed: {e}, using AutoProcessor"
            )

    # v4 fallback: Use AutoProcessor — caller controls trust_remote_code via kwarg
    trust_remote_code = bool(kwargs.pop("trust_remote_code", False))
    from transformers import AutoProcessor  # type: ignore

    processor = AutoProcessor.from_pretrained(
        model_path,
        trust_remote_code=trust_remote_code,
        local_files_only=local_files_only,
        **kwargs,
    )
    return processor


# Export public API
__all__ = [
    "FLORENCE2_CUSTOM_AVAILABLE",
    "Florence2ForConditionalGeneration",
    "Florence2Config",
    "Florence2Processor",
    "load_florence2_model",
    "load_florence2_processor",
]
