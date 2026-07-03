
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


import json
import re
from pathlib import Path
from typing import Dict, Optional, Tuple
from collections import OrderedDict

try:
    import torch #type: ignore
    from safetensors.torch import load_file, save_file #type: ignore
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

from .logger import log


_LOG_PREFIX = "Weight Converter"


# Key mapping from HuggingFace to Mistral-native format
# Format: (hf_pattern, mistral_replacement)
HF_TO_MISTRAL_MAPPINGS = [
    # Language model layers
    (r"language_model\.model\.layers\.(\d+)\.self_attn\.q_proj\.(.*)", r"layers.\1.attention.wq.\2"),
    (r"language_model\.model\.layers\.(\d+)\.self_attn\.k_proj\.(.*)", r"layers.\1.attention.wk.\2"),
    (r"language_model\.model\.layers\.(\d+)\.self_attn\.v_proj\.(.*)", r"layers.\1.attention.wv.\2"),
    (r"language_model\.model\.layers\.(\d+)\.self_attn\.o_proj\.(.*)", r"layers.\1.attention.wo.\2"),
    (r"language_model\.model\.layers\.(\d+)\.mlp\.gate_proj\.(.*)", r"layers.\1.feed_forward.w1.\2"),
    (r"language_model\.model\.layers\.(\d+)\.mlp\.down_proj\.(.*)", r"layers.\1.feed_forward.w2.\2"),
    (r"language_model\.model\.layers\.(\d+)\.mlp\.up_proj\.(.*)", r"layers.\1.feed_forward.w3.\2"),
    (r"language_model\.model\.layers\.(\d+)\.input_layernorm\.(.*)", r"layers.\1.attention_norm.\2"),
    (r"language_model\.model\.layers\.(\d+)\.post_attention_layernorm\.(.*)", r"layers.\1.ffn_norm.\2"),
    
    # Top-level language model
    (r"language_model\.model\.embed_tokens\.weight", r"tok_embeddings.weight"),
    (r"language_model\.model\.norm\.weight", r"norm.weight"),
    (r"language_model\.lm_head\.weight", r"output.weight"),  # For models with untied embeddings
    
    # Vision tower layers
    (r"vision_tower\.transformer\.layers\.(\d+)\.attention\.q_proj\.(.*)", r"vision_encoder.transformer.layers.\1.attention.wq.\2"),
    (r"vision_tower\.transformer\.layers\.(\d+)\.attention\.k_proj\.(.*)", r"vision_encoder.transformer.layers.\1.attention.wk.\2"),
    (r"vision_tower\.transformer\.layers\.(\d+)\.attention\.v_proj\.(.*)", r"vision_encoder.transformer.layers.\1.attention.wv.\2"),
    (r"vision_tower\.transformer\.layers\.(\d+)\.attention\.o_proj\.(.*)", r"vision_encoder.transformer.layers.\1.attention.wo.\2"),
    (r"vision_tower\.transformer\.layers\.(\d+)\.feed_forward\.gate_proj\.(.*)", r"vision_encoder.transformer.layers.\1.feed_forward.w1.\2"),
    (r"vision_tower\.transformer\.layers\.(\d+)\.feed_forward\.down_proj\.(.*)", r"vision_encoder.transformer.layers.\1.feed_forward.w2.\2"),
    (r"vision_tower\.transformer\.layers\.(\d+)\.feed_forward\.up_proj\.(.*)", r"vision_encoder.transformer.layers.\1.feed_forward.w3.\2"),
    (r"vision_tower\.transformer\.layers\.(\d+)\.attention_norm\.(.*)", r"vision_encoder.transformer.layers.\1.attention_norm.\2"),
    (r"vision_tower\.transformer\.layers\.(\d+)\.ffn_norm\.(.*)", r"vision_encoder.transformer.layers.\1.ffn_norm.\2"),
    
    # Vision tower top-level
    (r"vision_tower\.ln_pre\.(.*)", r"vision_encoder.ln_pre.\1"),
    (r"vision_tower\.patch_conv\.(.*)", r"vision_encoder.patch_conv.\1"),
    
    # Multi-modal projector
    (r"multi_modal_projector\.patch_merger\.merging_layer\.(.*)", r"patch_merger.merging_layer.\1"),
    (r"multi_modal_projector\.norm\.(.*)", r"pre_mm_projector_norm.\1"),
    (r"multi_modal_projector\.linear_1\.(.*)", r"vision_language_adapter.w_in.\1"),
    (r"multi_modal_projector\.linear_2\.(.*)", r"vision_language_adapter.w_out.\1"),
]

# FP8 scale key mappings (HF uses different names for quantization scales)
FP8_SCALE_MAPPINGS = [
    (r"(.*)\.activation_scale", r"\1.qscale_act"),
    (r"(.*)\.weight_scale_inv", r"\1.qscale_weight"),
]


def convert_key_hf_to_mistral(hf_key: str) -> Optional[str]:
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
    
    # Check for HF format files
    has_hf_model = (model_dir / "model.safetensors").exists()
    has_sharded = any(model_dir.glob("model-*.safetensors"))
    
    # Check for consolidated (Mistral-native)
    has_consolidated = (model_dir / "consolidated.safetensors").exists()
    
    return (has_hf_model or has_sharded) and not has_consolidated


def is_mistral3_model(model_path: Path) -> bool:
    # Check if the model is a Mistral3/Pixtral model.
    config_path = Path(model_path) / "config.json"
    if not config_path.exists():
        return False
    
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        architectures = config.get("architectures", [])
        model_type = config.get("model_type", "")
        return (
            "Mistral3ForConditionalGeneration" in architectures or
            model_type == "mistral3" or
            config.get("vision_config", {}).get("model_type") == "pixtral"
        )
    except Exception:
        return False


def load_hf_weights(model_path: Path) -> Dict[str, "torch.Tensor"]:
    # Load weights from HuggingFace format (single or sharded).
    model_dir = Path(model_path)
    weights = {}
    
    # Check for single file
    single_file = model_dir / "model.safetensors"
    if single_file.exists():
        log.msg(_LOG_PREFIX, f"  Loading weights from model.safetensors...")
        weights = load_file(str(single_file))
        return weights
    
    # Check for sharded files
    shard_files = sorted(model_dir.glob("model-*.safetensors"))
    if shard_files:
        log.msg(_LOG_PREFIX, f"  Loading weights from {len(shard_files)} sharded files...")
        for shard_file in shard_files:
            log.debug(_LOG_PREFIX, f"    Loading {shard_file.name}...")
            shard_weights = load_file(str(shard_file))
            weights.update(shard_weights)
        return weights
    
    raise FileNotFoundError(f"No HuggingFace weight files found in {model_path}")


def convert_weights_to_mistral(
    model_path: str,
    output_path: Optional[str] = None,
    dry_run: bool = False
) -> Tuple[bool, str]:
    # Convert HuggingFace format weights to Mistral-native format.
    #
    # Args:
    #     model_path: Path to the model directory
    #     output_path: Optional custom output path. If None, saves as consolidated.safetensors in model_path
    #     dry_run: If True, only analyze without writing files
    #
    # Returns:
    #     Tuple of (success: bool, message: str)
    if not TORCH_AVAILABLE:
        return False, "PyTorch and safetensors are required for weight conversion"
    
    model_dir = Path(model_path)
    
    # Validate model
    if not model_dir.exists():
        return False, f"Model path does not exist: {model_path}"
    
    if not is_mistral3_model(model_dir):
        return False, "Not a Mistral3/Pixtral model (based on config.json)"
    
    if not is_hf_format_model(model_dir):
        # Check if already converted
        if (model_dir / "consolidated.safetensors").exists():
            return True, "Model already has consolidated.safetensors (Mistral-native format)"
        return False, "Model does not have HuggingFace format weights"
    
    log.msg(_LOG_PREFIX, f"Converting HuggingFace weights to Mistral-native format...")
    log.msg(_LOG_PREFIX, f"  Model: {model_dir.name}")
    
    try:
        # Load HF weights
        hf_weights = load_hf_weights(model_dir)
        log.msg(_LOG_PREFIX, f"  Loaded {len(hf_weights)} weight tensors")
        
        # Convert keys
        mistral_weights = OrderedDict()
        unmapped_keys = []
        mapped_count = 0
        
        for hf_key, tensor in hf_weights.items():
            mistral_key = convert_key_hf_to_mistral(hf_key)
            if mistral_key:
                mistral_weights[mistral_key] = tensor
                mapped_count += 1
                log.debug(_LOG_PREFIX, f"    {hf_key} -> {mistral_key}")
            else:
                unmapped_keys.append(hf_key)
        
        log.msg(_LOG_PREFIX, f"  Converted {mapped_count} keys")
        
        if unmapped_keys:
            log.msg(_LOG_PREFIX, f"  Warning: {len(unmapped_keys)} keys could not be mapped:")
            for key in unmapped_keys[:5]:
                log.msg(_LOG_PREFIX, f"    - {key}")
            if len(unmapped_keys) > 5:
                log.msg(_LOG_PREFIX, f"    ... and {len(unmapped_keys) - 5} more")
        
        # Check if this is an FP8 model - these CANNOT be properly converted
        # Mistral-native FP8 format requires 'fake_quantizer.qscale_act' tensors
        # that contain attention score calibration values unique to each layer.
        # HuggingFace FP8 format doesn't have these, and they cannot be computed
        # without recalibrating the model.
        is_fp8 = any("qscale_act" in k for k in mistral_weights.keys())
        if is_fp8:
            log.error(_LOG_PREFIX, "⚠️  This is an FP8 quantized HuggingFace model.")
            log.error(_LOG_PREFIX, "    FP8 HuggingFace models cannot be converted to Mistral-native format")
            log.error(_LOG_PREFIX, "    because they're missing attention calibration tensors (fake_quantizer)")
            log.error(_LOG_PREFIX, "    that are required by vLLM's Mistral loader.")
            log.error(_LOG_PREFIX, "")
            log.error(_LOG_PREFIX, "  Options:")
            log.error(_LOG_PREFIX, "    1. Use the BF16 version of this model (if available)")
            log.error(_LOG_PREFIX, "    2. Use the original Mistral-native FP8 model (non-abliterated)")
            log.error(_LOG_PREFIX, "    3. Use 'Transformers' backend with transformers>=5.0")
            return False, "FP8 HuggingFace models cannot be converted (missing attention calibration data)"
        
        if dry_run:
            return True, f"Dry run: Would convert {mapped_count} keys ({len(unmapped_keys)} unmapped)"
        
        # Save as consolidated.safetensors
        output_file = Path(output_path) if output_path else model_dir / "consolidated.safetensors"
        log.msg(_LOG_PREFIX, f"  Saving to {output_file.name}...")
        
        # Calculate size
        total_bytes = sum(t.numel() * t.element_size() for t in mistral_weights.values())
        size_gb = total_bytes / (1024**3)
        log.msg(_LOG_PREFIX, f"  Output size: {size_gb:.2f} GB")
        
        save_file(mistral_weights, str(output_file))
        log.msg(_LOG_PREFIX, f"✓ Successfully created {output_file.name}")
        
        # Generate SHA256 hash file
        try:
            from .model_files import calculate_file_hash
            log.msg(_LOG_PREFIX, f"  Generating SHA256 hash...")
            hash_value = calculate_file_hash(output_file, show_progress=True)
            hash_file = output_file.with_suffix(".safetensors.sha256")
            with open(hash_file, "w") as f:
                f.write(hash_value)
            log.msg(_LOG_PREFIX, f"✓ Created {hash_file.name}")
        except ImportError:
            # Fallback to inline hash calculation if model_files not available
            import hashlib
            log.msg(_LOG_PREFIX, f"  Generating SHA256 hash...")
            sha256_hash = hashlib.sha256()
            with open(output_file, "rb") as f:
                while chunk := f.read(8192 * 1024):
                    sha256_hash.update(chunk)
            hash_value = sha256_hash.hexdigest()
            hash_file = output_file.with_suffix(".safetensors.sha256")
            with open(hash_file, "w") as f:
                f.write(hash_value)
            log.msg(_LOG_PREFIX, f"✓ Created {hash_file.name}")
        except Exception as e:
            log.msg(_LOG_PREFIX, f"  Warning: Could not create hash file: {e}")
        
        return True, f"Successfully converted {mapped_count} weight tensors to {output_file.name}"
        
    except Exception as e:
        log.error(_LOG_PREFIX, f"Failed to convert weights: {e}")
        return False, f"Conversion failed: {str(e)}"


# CLI interface for manual conversion
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python mistral_weight_converter.py <model_path> [--dry-run]")
        print("")
        print("Converts HuggingFace format Mistral3/Pixtral weights to Mistral-native format")
        print("for use with vLLM Docker backend.")
        sys.exit(1)
    
    model_path = sys.argv[1]
    dry_run = "--dry-run" in sys.argv
    
    success, message = convert_weights_to_mistral(model_path, dry_run=dry_run)
    print(message)
    sys.exit(0 if success else 1)
