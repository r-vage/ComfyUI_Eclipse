# Mistral Weight Format Converter
#
# Converts HuggingFace format Mistral3/Pixtral weights to Mistral-native format
# for use with vLLM Docker backend.
#
# HuggingFace format uses keys like:
#   - language_model.model.layers.0.self_attn.q_proj.weight
#   - vision_tower.transformer.layers.0.attention.q_proj.weight
#
# Mistral-native format uses keys like:
#   - layers.0.attention.wq.weight
#   - vision_encoder.transformer.layers.0.attention.wq.weight
import gc
import json
import os
import re
import shutil
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from safetensors import SafetensorError, safe_open  # type: ignore
    from safetensors.torch import load_file, save_file  # type: ignore

    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

from .json_store import JsonStoreError, locked_path, read_json_object, write_json_object
from .logger import log

_LOG_PREFIX = "Weight Converter"
_CONVERTER_VERSION = 2
_MANIFEST_SCHEMA_VERSION = 1
_MANIFEST_NAME = "consolidated.sml-conversion.json"
_INDEX_NAME = "consolidated.safetensors.index.json"
_MARKER_NAME = ".consolidated.sml-conversion.in-progress.json"
_LOCK_ANCHOR_NAME = ".consolidated.sml-conversion.transaction"
_TRANSACTION_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_DTYPE_BYTES = {
    "BOOL": 1,
    "I8": 1,
    "U8": 1,
    "F8_E4M3": 1,
    "F8_E5M2": 1,
    "I16": 2,
    "U16": 2,
    "F16": 2,
    "BF16": 2,
    "I32": 4,
    "U32": 4,
    "F32": 4,
    "I64": 8,
    "U64": 8,
    "F64": 8,
}


@dataclass(frozen=True)
class ConversionPlan:
    source_files: tuple[Path, ...]
    output_files: tuple[Path, ...]
    output_index: Path | None
    tensor_signatures: dict[str, tuple[str, tuple[int, ...]]]
    source_keys_by_file: dict[Path, tuple[str, ...]]
    output_file_by_key: dict[str, str]
    tensor_bytes: int
    peak_memory_bytes: int
    required_free_disk_bytes: int


# Key mapping from HuggingFace to Mistral-native format
# Format: (hf_pattern, mistral_replacement)
HF_TO_MISTRAL_MAPPINGS = [
    # Language model layers
    (
        r"language_model\.model\.layers\.(\d+)\.self_attn\.q_proj\.(.*)",
        r"layers.\1.attention.wq.\2",
    ),
    (
        r"language_model\.model\.layers\.(\d+)\.self_attn\.k_proj\.(.*)",
        r"layers.\1.attention.wk.\2",
    ),
    (
        r"language_model\.model\.layers\.(\d+)\.self_attn\.v_proj\.(.*)",
        r"layers.\1.attention.wv.\2",
    ),
    (
        r"language_model\.model\.layers\.(\d+)\.self_attn\.o_proj\.(.*)",
        r"layers.\1.attention.wo.\2",
    ),
    (
        r"language_model\.model\.layers\.(\d+)\.mlp\.gate_proj\.(.*)",
        r"layers.\1.feed_forward.w1.\2",
    ),
    (
        r"language_model\.model\.layers\.(\d+)\.mlp\.down_proj\.(.*)",
        r"layers.\1.feed_forward.w2.\2",
    ),
    (
        r"language_model\.model\.layers\.(\d+)\.mlp\.up_proj\.(.*)",
        r"layers.\1.feed_forward.w3.\2",
    ),
    (
        r"language_model\.model\.layers\.(\d+)\.input_layernorm\.(.*)",
        r"layers.\1.attention_norm.\2",
    ),
    (
        r"language_model\.model\.layers\.(\d+)\.post_attention_layernorm\.(.*)",
        r"layers.\1.ffn_norm.\2",
    ),
    # Top-level language model
    (r"language_model\.model\.embed_tokens\.weight", r"tok_embeddings.weight"),
    (r"language_model\.model\.norm\.weight", r"norm.weight"),
    (
        r"language_model\.lm_head\.weight",
        r"output.weight",
    ),  # For models with untied embeddings
    # Vision tower layers
    (
        r"vision_tower\.transformer\.layers\.(\d+)\.attention\.q_proj\.(.*)",
        r"vision_encoder.transformer.layers.\1.attention.wq.\2",
    ),
    (
        r"vision_tower\.transformer\.layers\.(\d+)\.attention\.k_proj\.(.*)",
        r"vision_encoder.transformer.layers.\1.attention.wk.\2",
    ),
    (
        r"vision_tower\.transformer\.layers\.(\d+)\.attention\.v_proj\.(.*)",
        r"vision_encoder.transformer.layers.\1.attention.wv.\2",
    ),
    (
        r"vision_tower\.transformer\.layers\.(\d+)\.attention\.o_proj\.(.*)",
        r"vision_encoder.transformer.layers.\1.attention.wo.\2",
    ),
    (
        r"vision_tower\.transformer\.layers\.(\d+)\.feed_forward\.gate_proj\.(.*)",
        r"vision_encoder.transformer.layers.\1.feed_forward.w1.\2",
    ),
    (
        r"vision_tower\.transformer\.layers\.(\d+)\.feed_forward\.down_proj\.(.*)",
        r"vision_encoder.transformer.layers.\1.feed_forward.w2.\2",
    ),
    (
        r"vision_tower\.transformer\.layers\.(\d+)\.feed_forward\.up_proj\.(.*)",
        r"vision_encoder.transformer.layers.\1.feed_forward.w3.\2",
    ),
    (
        r"vision_tower\.transformer\.layers\.(\d+)\.attention_norm\.(.*)",
        r"vision_encoder.transformer.layers.\1.attention_norm.\2",
    ),
    (
        r"vision_tower\.transformer\.layers\.(\d+)\.ffn_norm\.(.*)",
        r"vision_encoder.transformer.layers.\1.ffn_norm.\2",
    ),
    # Vision tower top-level
    (r"vision_tower\.ln_pre\.(.*)", r"vision_encoder.ln_pre.\1"),
    (r"vision_tower\.patch_conv\.(.*)", r"vision_encoder.patch_conv.\1"),
    # Multi-modal projector
    (
        r"multi_modal_projector\.patch_merger\.merging_layer\.(.*)",
        r"patch_merger.merging_layer.\1",
    ),
    (r"multi_modal_projector\.norm\.(.*)", r"pre_mm_projector_norm.\1"),
    (r"multi_modal_projector\.linear_1\.(.*)", r"vision_language_adapter.w_in.\1"),
    (r"multi_modal_projector\.linear_2\.(.*)", r"vision_language_adapter.w_out.\1"),
]

# FP8 scale key mappings (HF uses different names for quantization scales)
FP8_SCALE_MAPPINGS = [
    (r"(.*)\.activation_scale", r"\1.qscale_act"),
    (r"(.*)\.weight_scale_inv", r"\1.qscale_weight"),
]


def convert_key_hf_to_mistral(hf_key: str) -> str | None:
    # Convert a single HuggingFace key to Mistral-native format.
    # First apply FP8 scale mappings
    converted_key = hf_key
    for pattern, replacement in FP8_SCALE_MAPPINGS:
        converted_key = re.sub(pattern, replacement, converted_key)

    # Then apply main key mappings
    for pattern, replacement in HF_TO_MISTRAL_MAPPINGS:
        new_key = re.sub(pattern, replacement, converted_key)
        if new_key != converted_key:
            return new_key

    # If no mapping found, return None
    return None


def is_hf_format_model(model_path: Path) -> bool:
    # Check if the model uses HuggingFace weight format.
    model_dir = Path(model_path)
    has_hf_model = (model_dir / "model.safetensors").exists()
    has_sharded = any(model_dir.glob("model-*.safetensors"))
    return (has_hf_model or has_sharded) and not has_complete_mistral_weights(
        model_dir
    )


def is_mistral3_model(model_path: Path) -> bool:
    # Check if the model is a Mistral3/Pixtral model.
    config_path = Path(model_path) / "config.json"
    if not config_path.exists():
        return False

    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        architectures = config.get("architectures", [])
        model_type = config.get("model_type", "")
        return (
            "Mistral3ForConditionalGeneration" in architectures
            or model_type == "mistral3"
            or config.get("vision_config", {}).get("model_type") == "pixtral"
        )
    except (OSError, json.JSONDecodeError, TypeError):
        return False


def _read_config(model_dir: Path) -> dict[str, Any]:
    config_path = model_dir / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read Mistral config.json: {error}") from error
    if not isinstance(config, dict):
        raise TypeError("Mistral config.json root must be an object")
    return config


def _safe_relative_file(model_dir: Path, filename: object) -> Path:
    if not isinstance(filename, str) or not filename or "\x00" in filename:
        raise TypeError("Weight index filenames must be non-empty strings")
    relative = Path(filename.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe weight index filename: {filename!r}")
    resolved = (model_dir / relative).resolve(strict=False)
    try:
        resolved.relative_to(model_dir.resolve(strict=False))
    except ValueError as error:
        raise ValueError(f"Weight shard escapes the model directory: {filename!r}") from error
    return model_dir / relative


def _source_weight_inventory(model_dir: Path) -> tuple[tuple[Path, ...], dict[str, str]]:
    single_file = model_dir / "model.safetensors"
    index_path = model_dir / "model.safetensors.index.json"
    declared_weights: dict[str, str] = {}
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Could not read Hugging Face weight index: {error}") from error
        weight_map = index.get("weight_map") if isinstance(index, dict) else None
        if not isinstance(weight_map, dict) or not weight_map:
            raise ValueError("Hugging Face weight index has no weight_map")
        shard_paths = set()
        for key, filename in weight_map.items():
            if not isinstance(key, str) or not key:
                raise ValueError("Hugging Face weight index contains an invalid key")
            shard_path = _safe_relative_file(model_dir, filename)
            if not shard_path.is_file():
                raise FileNotFoundError(f"Missing Hugging Face shard: {shard_path.name}")
            declared_weights[key] = shard_path.relative_to(model_dir).as_posix()
            shard_paths.add(shard_path)
        return tuple(sorted(shard_paths)), declared_weights
    if single_file.is_file():
        return (single_file,), declared_weights
    shards = tuple(sorted(model_dir.glob("model-*.safetensors")))
    if shards:
        return shards, declared_weights
    raise FileNotFoundError(f"No Hugging Face Safetensors weights found in {model_dir}")


def _tensor_nbytes(dtype: str, shape: tuple[int, ...]) -> int:
    element_size = _DTYPE_BYTES.get(dtype)
    if element_size is None:
        raise ValueError(f"Unsupported Safetensors dtype during conversion: {dtype}")
    elements = 1
    for dimension in shape:
        if dimension < 0:
            raise ValueError(f"Invalid tensor shape: {shape}")
        elements *= dimension
    return elements * element_size


def _required_mistral_keys(config: dict[str, Any]) -> set[str]:
    text_config = config.get("text_config")
    if not isinstance(text_config, dict):
        text_config = config
    layer_count = text_config.get("num_hidden_layers")
    if not isinstance(layer_count, int) or layer_count <= 0:
        raise ValueError("Mistral config does not define a positive text layer count")

    required = {"tok_embeddings.weight", "norm.weight"}
    for layer in range(layer_count):
        prefix = f"layers.{layer}"
        required.update(
            {
                f"{prefix}.attention.wq.weight",
                f"{prefix}.attention.wk.weight",
                f"{prefix}.attention.wv.weight",
                f"{prefix}.attention.wo.weight",
                f"{prefix}.feed_forward.w1.weight",
                f"{prefix}.feed_forward.w2.weight",
                f"{prefix}.feed_forward.w3.weight",
                f"{prefix}.attention_norm.weight",
                f"{prefix}.ffn_norm.weight",
            }
        )

    vision_config = config.get("vision_config")
    if isinstance(vision_config, dict):
        vision_layers = vision_config.get("num_hidden_layers")
        if not isinstance(vision_layers, int) or vision_layers <= 0:
            raise ValueError("Pixtral config does not define a positive vision layer count")
        required.update(
            {
                "vision_encoder.ln_pre.weight",
                "vision_encoder.patch_conv.weight",
                "patch_merger.merging_layer.weight",
                "pre_mm_projector_norm.weight",
                "vision_language_adapter.w_in.weight",
                "vision_language_adapter.w_out.weight",
            }
        )
        for layer in range(vision_layers):
            prefix = f"vision_encoder.transformer.layers.{layer}"
            required.update(
                {
                    f"{prefix}.attention.wq.weight",
                    f"{prefix}.attention.wk.weight",
                    f"{prefix}.attention.wv.weight",
                    f"{prefix}.attention.wo.weight",
                    f"{prefix}.feed_forward.w1.weight",
                    f"{prefix}.feed_forward.w2.weight",
                    f"{prefix}.feed_forward.w3.weight",
                    f"{prefix}.attention_norm.weight",
                    f"{prefix}.ffn_norm.weight",
                }
            )
    return required


def _native_output_files(model_dir: Path) -> tuple[Path, ...]:
    index_path = model_dir / _INDEX_NAME
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        weight_map = index.get("weight_map") if isinstance(index, dict) else None
        if not isinstance(weight_map, dict) or not weight_map:
            return ()
        try:
            files = {_safe_relative_file(model_dir, value) for value in weight_map.values()}
        except (TypeError, ValueError):
            return ()
        if not files or any(not path.is_file() for path in files):
            return ()
        return tuple(sorted(files))
    single_file = model_dir / "consolidated.safetensors"
    return (single_file,) if single_file.is_file() else ()


def _native_key_inventory(files: tuple[Path, ...]) -> set[str]:
    keys: set[str] = set()
    for file_path in files:
        with safe_open(str(file_path), framework="pt", device="cpu") as handle:
            shard_keys = set(handle.keys())
        if keys.intersection(shard_keys):
            raise ValueError(f"Duplicate native tensor key in {file_path.name}")
        keys.update(shard_keys)
    return keys


def _manifest_matches(model_dir: Path) -> bool:
    manifest_path = model_dir / _MANIFEST_NAME
    if not manifest_path.is_file():
        return False
    try:
        manifest = read_json_object(manifest_path)
    except (JsonStoreError, OSError):
        return False
    if (
        manifest.get("schema_version") != _MANIFEST_SCHEMA_VERSION
        or manifest.get("converter_version") != _CONVERTER_VERSION
    ):
        return False
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        return False
    output_paths = []
    for record in outputs:
        if not isinstance(record, dict):
            return False
        try:
            output = _safe_relative_file(model_dir, record.get("filename"))
            output_stat = output.stat()
        except (OSError, TypeError, ValueError):
            return False
        if (
            output_stat.st_size != record.get("size_bytes")
            or output_stat.st_mtime_ns != record.get("mtime_ns")
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", "")))
        ):
            return False
        output_paths.append(output)
    if len(set(output_paths)) != len(output_paths):
        return False

    index_record = manifest.get("index")
    if len(output_paths) > 1:
        if not isinstance(index_record, dict):
            return False
        try:
            index_path = _safe_relative_file(model_dir, index_record.get("filename"))
            index_stat = index_path.stat()
        except (OSError, TypeError, ValueError):
            return False
        if (
            index_path.name != _INDEX_NAME
            or index_stat.st_size != index_record.get("size_bytes")
            or index_stat.st_mtime_ns != index_record.get("mtime_ns")
            or not re.fullmatch(r"[0-9a-f]{64}", str(index_record.get("sha256", "")))
        ):
            return False
    elif index_record is not None:
        return False

    return set(_native_output_files(model_dir)) == set(output_paths)


def has_complete_mistral_weights(model_path: Path) -> bool:
    model_dir = Path(model_path)
    if (model_dir / _MANIFEST_NAME).is_file():
        return _manifest_matches(model_dir)
    if (model_dir / _MARKER_NAME).is_file():
        return False
    files = _native_output_files(model_dir)
    if not files:
        return False
    try:
        config = _read_config(model_dir)
        keys = _native_key_inventory(files)
        return _required_mistral_keys(config).issubset(keys)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _build_conversion_plan(model_dir: Path, output_path: str | None) -> ConversionPlan:
    config = _read_config(model_dir)
    quantization = config.get("quantization_config")
    quantization_method = (
        str(quantization.get("quant_method", "")).lower()
        if isinstance(quantization, dict)
        else ""
    )
    if "fp8" in quantization_method:
        raise ValueError(
            "FP8 Hugging Face weights cannot be converted because Mistral-native "
            "attention calibration tensors are not available"
        )

    source_files, declared_weights = _source_weight_inventory(model_dir)
    if output_path and len(source_files) != 1:
        raise ValueError("A custom output_path is supported only for an unsharded source")
    if output_path:
        custom_output = Path(output_path)
        if custom_output.parent.resolve(strict=False) != model_dir.resolve(strict=False):
            raise ValueError("Transactional conversion output must stay in the model directory")
        if custom_output.name != "consolidated.safetensors":
            raise ValueError(
                "Transactional conversion output must be named consolidated.safetensors"
            )
        output_files = (custom_output,)
    elif len(source_files) == 1:
        output_files = (model_dir / "consolidated.safetensors",)
    else:
        count = len(source_files)
        output_files = tuple(
            model_dir / f"consolidated-{index:05d}-of-{count:05d}.safetensors"
            for index in range(1, count + 1)
        )

    signatures: dict[str, tuple[str, tuple[int, ...]]] = {}
    source_keys_by_file: dict[Path, tuple[str, ...]] = {}
    output_file_by_key: dict[str, str] = {}
    tensor_bytes = 0
    max_shard_bytes = 0
    observed_source_keys: set[str] = set()
    fp8_scale_seen = False

    for source_file, output_file in zip(source_files, output_files):
        shard_keys = []
        shard_bytes = 0
        with safe_open(str(source_file), framework="pt", device="cpu") as handle:
            source_key_names = handle.keys()
            for source_key in source_key_names:
                if source_key in observed_source_keys:
                    raise ValueError(f"Duplicate Hugging Face tensor key: {source_key}")
                observed_source_keys.add(source_key)
                if declared_weights:
                    declared_file = declared_weights.get(source_key)
                    relative_source = source_file.relative_to(model_dir).as_posix()
                    if declared_file != relative_source:
                        raise ValueError(
                            f"Weight index maps {source_key} to {declared_file!r}, "
                            f"but it is stored in {relative_source!r}"
                        )
                target_key = convert_key_hf_to_mistral(source_key)
                if target_key is None:
                    raise ValueError(f"Required Hugging Face key has no Mistral mapping: {source_key}")
                if target_key in signatures:
                    raise ValueError(f"Multiple Hugging Face keys map to {target_key}")
                tensor_slice = handle.get_slice(source_key)
                dtype = tensor_slice.get_dtype()
                shape = tuple(tensor_slice.get_shape())
                signatures[target_key] = (dtype, shape)
                output_file_by_key[target_key] = output_file.name
                shard_bytes += _tensor_nbytes(dtype, shape)
                shard_keys.append(source_key)
                fp8_scale_seen = fp8_scale_seen or source_key.endswith(
                    (".activation_scale", ".weight_scale_inv")
                ) or dtype.startswith("F8_")
        source_keys_by_file[source_file] = tuple(shard_keys)
        tensor_bytes += shard_bytes
        max_shard_bytes = max(max_shard_bytes, shard_bytes)

    if declared_weights and observed_source_keys != set(declared_weights):
        missing = sorted(set(declared_weights) - observed_source_keys)
        raise ValueError(f"Weight index declares missing tensor keys: {missing[:5]}")
    if fp8_scale_seen:
        raise ValueError(
            "FP8 Hugging Face weights cannot be converted because Mistral-native "
            "attention calibration tensors are not available"
        )
    missing_required = sorted(_required_mistral_keys(config) - set(signatures))
    if missing_required:
        raise ValueError(f"Converted weights would miss required keys: {missing_required[:8]}")

    peak_memory = max_shard_bytes + max(max_shard_bytes // 4, 512 * 1024**2)
    required_disk = tensor_bytes + max(tensor_bytes // 20, 512 * 1024**2)
    output_index = model_dir / _INDEX_NAME if len(output_files) > 1 else None
    return ConversionPlan(
        source_files=source_files,
        output_files=output_files,
        output_index=output_index,
        tensor_signatures=signatures,
        source_keys_by_file=source_keys_by_file,
        output_file_by_key=output_file_by_key,
        tensor_bytes=tensor_bytes,
        peak_memory_bytes=peak_memory,
        required_free_disk_bytes=required_disk,
    )


def _format_gib(value: int) -> str:
    return f"{value / 1024**3:.2f} GiB"


def _validate_resources(model_dir: Path, plan: ConversionPlan) -> None:
    try:
        import psutil  # type: ignore

        available_memory = int(psutil.virtual_memory().available)
    except ImportError:
        available_memory = 0
    free_disk = shutil.disk_usage(model_dir).free
    if available_memory and available_memory < plan.peak_memory_bytes:
        raise RuntimeError(
            "Insufficient available RAM for transactional conversion: "
            f"need about {_format_gib(plan.peak_memory_bytes)}, "
            f"have {_format_gib(available_memory)}"
        )
    if free_disk < plan.required_free_disk_bytes:
        raise RuntimeError(
            "Insufficient free disk for transactional conversion: "
            f"need about {_format_gib(plan.required_free_disk_bytes)}, "
            f"have {_format_gib(free_disk)}"
        )


def _fsync_directory(directory: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _safe_marker_file(model_dir: Path, filename: object, transaction_id: str) -> Path:
    path = _safe_relative_file(model_dir, filename)
    if transaction_id not in path.name and not path.name.startswith("consolidated"):
        raise ValueError(f"Unexpected conversion transaction file: {path.name}")
    return path


def _recover_interrupted_conversion(model_dir: Path) -> None:
    marker_path = model_dir / _MARKER_NAME
    if not marker_path.is_file():
        return
    marker = read_json_object(marker_path)
    transaction_id = marker.get("transaction_id")
    if not isinstance(transaction_id, str) or not _TRANSACTION_PATTERN.fullmatch(transaction_id):
        raise RuntimeError("Malformed Mistral conversion marker requires manual review")
    if _manifest_matches(model_dir):
        marker_path.unlink(missing_ok=True)
        _fsync_directory(model_dir)
        return

    for filename in marker.get("temporary_files", []):
        temporary = _safe_marker_file(model_dir, filename, transaction_id)
        temporary.unlink(missing_ok=True)
    for filename in marker.get("owned_outputs", []):
        output = _safe_marker_file(model_dir, filename, transaction_id)
        if not output.exists():
            continue
        quarantine = model_dir / f".{output.name}.incomplete-{transaction_id}"
        if quarantine.exists():
            raise RuntimeError(f"Conversion quarantine already exists: {quarantine.name}")
        os.replace(output, quarantine)
    marker_path.unlink(missing_ok=True)
    _fsync_directory(model_dir)
    log.warning(
        _LOG_PREFIX,
        "Recovered an interrupted Mistral conversion; incomplete outputs were quarantined",
    )


def _validate_output_file(
    output_file: Path,
    expected: dict[str, tuple[str, tuple[int, ...]]],
) -> None:
    with safe_open(str(output_file), framework="pt", device="cpu") as handle:
        actual_keys = set(handle.keys())
        if actual_keys != set(expected):
            missing = sorted(set(expected) - actual_keys)
            unexpected = sorted(actual_keys - set(expected))
            raise ValueError(
                f"Converted shard key mismatch; missing={missing[:5]}, "
                f"unexpected={unexpected[:5]}"
            )
        for key, signature in expected.items():
            tensor_slice = handle.get_slice(key)
            actual = (tensor_slice.get_dtype(), tuple(tensor_slice.get_shape()))
            if actual != signature:
                raise ValueError(
                    f"Converted tensor metadata mismatch for {key}: "
                    f"expected {signature}, got {actual}"
                )


def _quarantine_owned_outputs(model_dir: Path, marker: dict[str, Any]) -> None:
    transaction_id = marker["transaction_id"]
    for filename in marker.get("temporary_files", []):
        try:
            _safe_marker_file(model_dir, filename, transaction_id).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    for filename in marker.get("owned_outputs", []):
        try:
            output = _safe_marker_file(model_dir, filename, transaction_id)
            if output.exists():
                quarantine = model_dir / f".{output.name}.incomplete-{transaction_id}"
                if not quarantine.exists():
                    os.replace(output, quarantine)
        except (OSError, ValueError):
            pass
    _fsync_directory(model_dir)


def convert_weights_to_mistral(
    model_path: str,
    output_path: str | None = None,
    dry_run: bool = False,
    allow_conversion: bool = False,
) -> tuple[bool, str]:
    if not TORCH_AVAILABLE:
        return False, "PyTorch and safetensors are required for weight conversion"

    model_dir = Path(model_path)
    if not model_dir.is_dir():
        return False, f"Model path does not exist: {model_path}"
    if not is_mistral3_model(model_dir):
        return False, "Not a Mistral3/Pixtral model (based on config.json)"
    if has_complete_mistral_weights(model_dir):
        return True, "Model already has complete Mistral-native weights"
    if not ((model_dir / "model.safetensors").is_file() or any(model_dir.glob("model-*.safetensors"))):
        return False, "Model does not have HuggingFace format weights"

    try:
        plan = _build_conversion_plan(model_dir, output_path)
        if dry_run:
            return (
                True,
                (
                    f"Dry run: {len(plan.tensor_signatures)} tensors, "
                    f"{len(plan.source_files)} source shard(s), "
                    f"{_format_gib(plan.tensor_bytes)} output, "
                    f"~{_format_gib(plan.peak_memory_bytes)} peak RAM, "
                    f"~{_format_gib(plan.required_free_disk_bytes)} free disk required"
                ),
            )
        if not allow_conversion:
            return (
                False,
                (
                    "Mistral weight conversion is disabled. Set "
                    "vllm.allow_mistral_weight_conversion=true in docker_config.json "
                    "after reviewing the estimated RAM and disk requirements."
                ),
            )
        _validate_resources(model_dir, plan)
    except (
        JsonStoreError,
        MemoryError,
        OSError,
        RuntimeError,
        SafetensorError,
        TypeError,
        ValueError,
    ) as error:
        log.error(_LOG_PREFIX, f"Conversion preflight failed: {error}")
        return False, f"Conversion preflight failed: {error}"

    log.msg(_LOG_PREFIX, "Converting Hugging Face weights transactionally...")
    log.msg(
        _LOG_PREFIX,
        f"  {len(plan.source_files)} shard(s), {_format_gib(plan.tensor_bytes)} output, "
        f"~{_format_gib(plan.peak_memory_bytes)} peak RAM",
    )

    lock_anchor = model_dir / _LOCK_ANCHOR_NAME
    marker_path = model_dir / _MARKER_NAME
    manifest_path = model_dir / _MANIFEST_NAME
    transaction_id = uuid.uuid4().hex
    temporary_outputs = tuple(
        model_dir / f".{output.name}.{transaction_id}.tmp"
        for output in plan.output_files
    )
    temporary_index = (
        model_dir / f".{_INDEX_NAME}.{transaction_id}.tmp"
        if plan.output_index is not None
        else None
    )
    temporary_index_lock = (
        temporary_index.with_name(f".{temporary_index.name}.lock")
        if temporary_index is not None
        else None
    )
    marker = {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "temporary_files": [path.name for path in temporary_outputs]
        + ([temporary_index.name] if temporary_index is not None else [])
        + ([temporary_index_lock.name] if temporary_index_lock is not None else []),
        "owned_outputs": [path.name for path in plan.output_files]
        + ([plan.output_index.name] if plan.output_index is not None else []),
    }

    try:
        with locked_path(lock_anchor):
            _recover_interrupted_conversion(model_dir)
            if has_complete_mistral_weights(model_dir):
                return True, "Model already has complete Mistral-native weights"
            occupied = [
                path.name
                for path in (*plan.output_files,)
                if path.exists()
            ]
            if plan.output_index is not None and plan.output_index.exists():
                occupied.append(plan.output_index.name)
            if occupied:
                raise RuntimeError(
                    "Refusing to overwrite unverified existing native files: "
                    + ", ".join(occupied)
                )

            write_json_object(marker_path, marker)
            from .model_files import (
                calculate_file_hash,
                get_or_create_local_file_hash,
            )

            source_records = []
            source_stats = {}
            for source_file in plan.source_files:
                source_stat = source_file.stat()
                source_stats[source_file] = (
                    source_stat.st_size,
                    source_stat.st_mtime_ns,
                )
                source_records.append(
                    {
                        "filename": source_file.relative_to(model_dir).as_posix(),
                        "sha256": get_or_create_local_file_hash(source_file),
                        "size_bytes": source_stat.st_size,
                        "mtime_ns": source_stat.st_mtime_ns,
                    }
                )

            output_records = []
            index_record = None
            for source_file, temporary_output, final_output in zip(
                plan.source_files,
                temporary_outputs,
                plan.output_files,
            ):
                log.msg(_LOG_PREFIX, f"  Converting {source_file.name}...")
                source_weights = load_file(str(source_file), device="cpu")
                converted = OrderedDict()
                for source_key in plan.source_keys_by_file[source_file]:
                    target_key = convert_key_hf_to_mistral(source_key)
                    if target_key is None:
                        raise RuntimeError(f"Mapping disappeared for {source_key}")
                    converted[target_key] = source_weights[source_key]
                save_file(converted, str(temporary_output))
                expected = {
                    key: plan.tensor_signatures[key]
                    for key, filename in plan.output_file_by_key.items()
                    if filename == final_output.name
                }
                _validate_output_file(temporary_output, expected)
                output_records.append(
                    {
                        "filename": final_output.name,
                        "sha256": calculate_file_hash(temporary_output),
                    }
                )
                del converted
                del source_weights
                gc.collect()

            if temporary_index is not None:
                index = {
                    "metadata": {"total_size": plan.tensor_bytes},
                    "weight_map": plan.output_file_by_key,
                }
                write_json_object(temporary_index, index)
                parsed_index = read_json_object(temporary_index)
                if parsed_index.get("weight_map") != plan.output_file_by_key:
                    raise RuntimeError("Converted shard index failed readback validation")
                index_record = {
                    "filename": plan.output_index.name,
                    "sha256": calculate_file_hash(temporary_index),
                }
                temporary_index_lock.unlink(missing_ok=True)

            for source_file, expected_stat in source_stats.items():
                current_stat = source_file.stat()
                if (current_stat.st_size, current_stat.st_mtime_ns) != expected_stat:
                    raise RuntimeError(
                        f"Source shard changed during conversion: {source_file.name}"
                    )

            for temporary_output, final_output in zip(
                temporary_outputs,
                plan.output_files,
            ):
                if final_output.exists():
                    raise RuntimeError(f"Output appeared during conversion: {final_output.name}")
                os.replace(temporary_output, final_output)
            if temporary_index is not None and plan.output_index is not None:
                if plan.output_index.exists():
                    raise RuntimeError(f"Output appeared during conversion: {plan.output_index.name}")
                os.replace(temporary_index, plan.output_index)
            _fsync_directory(model_dir)

            for record in output_records:
                output_stat = (model_dir / record["filename"]).stat()
                record["size_bytes"] = output_stat.st_size
                record["mtime_ns"] = output_stat.st_mtime_ns
            if index_record is not None and plan.output_index is not None:
                index_stat = plan.output_index.stat()
                index_record["size_bytes"] = index_stat.st_size
                index_record["mtime_ns"] = index_stat.st_mtime_ns
            manifest = {
                "schema_version": _MANIFEST_SCHEMA_VERSION,
                "converter_version": _CONVERTER_VERSION,
                "completed_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "mapping": "hf_mistral3_to_mistral_native_v2",
                "inputs": source_records,
                "outputs": output_records,
                "index": index_record,
                "tensor_count": len(plan.tensor_signatures),
                "tensor_bytes": plan.tensor_bytes,
                "unmapped_keys": [],
            }
            write_json_object(manifest_path, manifest)
            if not has_complete_mistral_weights(model_dir):
                raise RuntimeError("Committed Mistral conversion failed final validation")
            marker_path.unlink(missing_ok=True)
            _fsync_directory(model_dir)
        return (
            True,
            (
                f"Converted {len(plan.tensor_signatures)} tensors into "
                f"{len(plan.output_files)} validated Mistral shard(s)"
            ),
        )
    except (
        JsonStoreError,
        MemoryError,
        OSError,
        RuntimeError,
        SafetensorError,
        TypeError,
        ValueError,
    ) as error:
        _quarantine_owned_outputs(model_dir, marker)
        log.error(_LOG_PREFIX, f"Failed to convert weights: {error}")
        return False, f"Conversion failed: {error}"


# CLI interface for manual conversion
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(
            "Usage: python mistral_weight_converter.py <model_path> "
            "[--dry-run | --confirm-conversion]"
        )
        print()
        print(
            "Converts HuggingFace format Mistral3/Pixtral weights to Mistral-native format"
        )
        print("for use with vLLM Docker backend.")
        sys.exit(1)

    model_path = sys.argv[1]
    dry_run = "--dry-run" in sys.argv
    allow_conversion = "--confirm-conversion" in sys.argv

    success, message = convert_weights_to_mistral(
        model_path,
        dry_run=dry_run,
        allow_conversion=allow_conversion,
    )
    print(message)
    sys.exit(0 if success else 1)
