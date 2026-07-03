# SmartLM VLM Loader
#
# Unified Transformers-based loader for Vision-Language Models.
# Handles Qwen VL, Mistral VL, LLaVA, Mllama, and compatible models
# via a single config-driven loader with quirk detection.

import os
import json
import torch  # type: ignore
from pathlib import Path
from typing import Any

from .logger import log
from .model_types import ModelType, _transformers_version, detect_vlm_model_type

_LOG_PREFIX = "VLMLoader"


# ============================================================================
# Transformers v5 Async Load Workaround
# ============================================================================
# Transformers v5 introduced async tensor loading which materializes full fp16
# tensors on GPU before quantization, doubling reserved VRAM and causing OOM
# on 16GB GPUs. Setting this env var restores v4 memory behavior.
# See: https://github.com/huggingface/transformers/issues/44387

if _transformers_version >= (5, 0):
    _prev = os.environ.get("HF_DEACTIVATE_ASYNC_LOAD")
    if _prev is None:
        os.environ["HF_DEACTIVATE_ASYNC_LOAD"] = "1"
        log.msg(_LOG_PREFIX, "Set HF_DEACTIVATE_ASYNC_LOAD=1 (transformers v5 async load workaround)")


# ============================================================================
# Transformers Version Compatibility
# ============================================================================

def get_dtype_kwarg_name() -> str:
    # Get the correct dtype parameter name for from_pretrained.
    #
    # In transformers v5, 'torch_dtype' is deprecated in favor of 'dtype'.
    # Returns 'dtype' for transformers >= 5.0, 'torch_dtype' otherwise.
    return "dtype" if _transformers_version >= (5, 0) else "torch_dtype"


# Cached at module load for performance
_DTYPE_KWARG_NAME = get_dtype_kwarg_name()

def dtype_kwarg() -> str:
    # Return the cached dtype kwarg name.
    return _DTYPE_KWARG_NAME


# ============================================================================
# LLaVA Custom Package Helpers
# ============================================================================

def _try_import_llava_class(arch_name: str):
    # Try to import a LLaVA model class from the custom llava pip package.
    #
    # Some LLaVA variants (LlavaLlamaForCausalLM, LlavaMistralForCausalLM) need
    # the external llava package. Standard HF models (llava-hf/*) use classes
    # already in the transformers library.
    #
    # Args:
    #     arch_name: Architecture class name from config.json architectures[0]
    #
    # Returns:
    #     Model class if found, None otherwise
    #
    # Raises:
    #     ValueError: If the custom llava package is required but not installed

    # Known standard transformers LLaVA classes — no custom package needed
    STANDARD_LLAVA_CLASSES = {
        "LlavaForConditionalGeneration", "LlavaNextForConditionalGeneration",
        "LlavaNextVideoForConditionalGeneration", "LlavaOnevisionForConditionalGeneration",
        "VipLlavaForConditionalGeneration", "VideoLlavaForConditionalGeneration",
    }

    if arch_name in STANDARD_LLAVA_CLASSES:
        return None  # Let the normal transformers resolution handle it

    # Map of custom llava classes to their import paths
    LLAVA_CUSTOM_IMPORTS = {
        "LlavaLlamaForCausalLM": ("llava.model.language_model.llava_llama", "LlavaLlamaForCausalLM"),
        "LlavaMistralForCausalLM": ("llava.model.language_model.llava_mistral", "LlavaMistralForCausalLM"),
        "LlavaQwenForCausalLM": ("llava.model.language_model.llava_qwen", "LlavaQwenForCausalLM"),
    }

    if arch_name in LLAVA_CUSTOM_IMPORTS:
        module_path, class_name = LLAVA_CUSTOM_IMPORTS[arch_name]
        try:
            import importlib
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            log.debug(_LOG_PREFIX, f"  Using {class_name} from llava package")
            return cls
        except (ImportError, AttributeError) as e:
            raise ValueError(
                f"This LLaVA model uses custom architecture '{arch_name}' which is not in standard transformers.\n\n"
                f"The custom 'llava' package is required but not installed or failed to import.\n"
                f"Error: {e}\n\n"
                f"Installation: pip install git+https://github.com/haotian-liu/LLaVA.git\n\n"
                f"Alternatively, use a standard LLaVA model that works with transformers, such as:\n"
                f"  - llava-hf/llava-1.5-7b-hf\n"
                f"  - llava-hf/llava-v1.6-mistral-7b-hf\n"
                f"  - llava-hf/llava-v1.6-vicuna-7b-hf"
            )

    # Unknown custom class — try generic import from llava.model
    try:
        from llava.model import LlavaLlamaForCausalLM as FallbackClass  # type: ignore
        log.debug(_LOG_PREFIX, f"  Using fallback LlavaLlamaForCausalLM from llava package for '{arch_name}'")
        return FallbackClass
    except ImportError:
        return None  # Will fall through to AutoModel fallback


def _apply_siglip_patch():
    # Monkey-patch the llava package to support SigLIP vision towers.
    #
    # The external llava package only supports CLIP by default, but some models
    # use SigLIP which is architecture-compatible with CLIP. This patch redirects
    # SigLIP models through CLIPVisionTower.
    #
    # No-op if the llava package is not installed.
    try:
        import llava.model.multimodal_encoder.builder as llava_builder  # type: ignore
        original_build_vision_tower = llava_builder.build_vision_tower

        def patched_build_vision_tower(vision_tower_cfg, **kwargs):
            vision_tower = getattr(vision_tower_cfg, 'mm_vision_tower',
                                   getattr(vision_tower_cfg, 'vision_tower', None))
            if vision_tower is None:
                vision_tower = vision_tower_cfg

            # Check if it's a SigLIP model
            if isinstance(vision_tower, str) and 'siglip' in vision_tower.lower():
                from llava.model.multimodal_encoder.clip_encoder import CLIPVisionTower  # type: ignore
                log.debug(_LOG_PREFIX, f"  Patching SigLIP vision tower: {vision_tower}")
                return CLIPVisionTower(vision_tower, args=vision_tower_cfg, **kwargs)

            return original_build_vision_tower(vision_tower_cfg, **kwargs)

        llava_builder.build_vision_tower = patched_build_vision_tower
        log.debug(_LOG_PREFIX, "  Applied SigLIP vision tower patch to llava package")
    except Exception as patch_error:
        log.debug(_LOG_PREFIX, f"  Could not patch llava for SigLIP (may not be needed): {patch_error}")


def _check_safetensors_prequantized(model_path: str) -> bool:
    # Check safetensors files for BitsAndBytes pre-quantization markers.
    #
    # Looks for .SCB (Scale Column Bias) or .CB weight keys, which indicate
    # the model was pre-quantized with bitsandbytes.
    #
    # Args:
    #     model_path: Path to the model directory
    #
    # Returns:
    #     True if pre-quantization markers found, False otherwise
    try:
        from safetensors import safe_open  # type: ignore
        safetensor_files = [f for f in os.listdir(model_path) if f.endswith('.safetensors')]
        for sf in safetensor_files[:1]:  # Only check first file
            with safe_open(os.path.join(model_path, sf), framework='pt') as f:
                keys = list(f.keys())
                if any('.SCB' in k or '.CB' in k for k in keys):
                    log.debug(_LOG_PREFIX, "  Detected SCB/CB weights in safetensors - pre-quantized with bitsandbytes")
                    return True
    except Exception as e:
        log.debug(_LOG_PREFIX, f"  Could not check safetensors for SCB: {e}")
    return False


def _resize_lm_head_if_needed(model, quantization: str) -> None:
    # Check and resize lm_head if it doesn't match input embedding size.
    #
    # Mllama models have 128264 embedding tokens (includes 8 image tokens)
    # but config.vocab_size is 128256. Without resize, the model generates
    # garbage tokens or crashes.
    #
    # For BnB 4-bit quantized models: dequantizes old weights, creates new
    # fp16 Linear layer, copies + initializes new token weights.
    # For non-quantized: uses model.resize_token_embeddings().
    #
    # Args:
    #     model: The loaded model instance
    #     quantization: Current quantization mode
    input_embeddings = model.get_input_embeddings()
    if input_embeddings is None:
        return

    input_size = input_embeddings.weight.shape[0]
    lm_head = model.lm_head if hasattr(model, 'lm_head') else model.get_output_embeddings()
    if lm_head is None:
        return

    output_size = lm_head.out_features if hasattr(lm_head, 'out_features') else model.config.vocab_size
    log.debug(_LOG_PREFIX, f"  Vocab check: embeddings={input_size}, lm_head={output_size}")

    if output_size >= input_size:
        return  # No mismatch

    log.msg(_LOG_PREFIX, f"Resizing lm_head: {output_size} -> {input_size} (fixing image token mismatch)")
    try:
        import torch.nn as nn  # type: ignore

        old_lm_head = model.lm_head
        in_features = old_lm_head.in_features

        # Check if this is a BitsAndBytes quantized layer
        is_bnb_quantized = hasattr(old_lm_head, 'weight') and hasattr(old_lm_head.weight, 'quant_state')

        if is_bnb_quantized:
            log.debug(_LOG_PREFIX, "  lm_head is BnB quantized, dequantizing and resizing")
            import bitsandbytes.functional as bnb_f  # type: ignore

            # Dequantize the weights
            old_weight = bnb_f.dequantize_4bit(
                old_lm_head.weight.data,
                old_lm_head.weight.quant_state
            )

            # Create new fp16 lm_head with correct size
            new_lm_head = nn.Linear(in_features, input_size, bias=False,
                                    dtype=torch.float16, device="cuda:0")

            # Copy existing weights and initialize new ones
            with torch.no_grad():
                new_lm_head.weight.data[:output_size, :] = old_weight.half()
                mean_weight = old_weight.mean(dim=0, keepdim=True).half()
                new_lm_head.weight.data[output_size:, :] = mean_weight.expand(
                    input_size - output_size, -1)

            model.lm_head = new_lm_head

            # Update config.vocab_size so beam search uses correct shape
            model.config.vocab_size = input_size
            if hasattr(model.config, 'text_config') and model.config.text_config is not None:
                model.config.text_config.vocab_size = input_size

            log.msg(_LOG_PREFIX, "✓ lm_head resized (dequantized fp16)")
        else:
            # Non-quantized model - use standard resize
            model.resize_token_embeddings(input_size)
            log.msg(_LOG_PREFIX, "✓ Token embeddings resized")

    except Exception as e:
        log.warning(_LOG_PREFIX, f"Could not resize lm_head: {e}")
        import traceback
        traceback.print_exc()


# ============================================================================
# Unified VLM Loader
# ============================================================================

def load_vlm_transformers(
    model_path: str,
    quantization: str,
    attn_impl: str,
    is_prequantized: bool,
    quant_type: str,
    keep_model_loaded: bool,
    resolved_quantization: str,
    resolved_attention: str,
    **kwargs
) -> tuple:
    # Unified Transformers loader for vision-language models.
    #
    # Handles Qwen VL, Mistral VL, and any model using the AutoModelForVision2Seq
    # pattern. Replaces the separate Qwen and Mistral loading blocks with a single
    # config-driven loader that detects model-specific quirks from config.json.
    #
    # Quirk handling:
    #   - Mistral config patching (transformers < 5.0): model_type/tie_word_embeddings fixes
    #   - FP8: FineGrainedFP8Config for transformers 5.0+, error for older versions
    #   - Tokenizer fixes: TokenizersBackend → PreTrainedTokenizerFast
    #   - Chat template fallback: Copy from tokenizer if processor is missing it
    #
    # Args:
    #     model_path: Path to local model directory
    #     quantization: Resolved quantization mode (4bit, 8bit, fp16, bf16, fp32, auto)
    #     attn_impl: Attention implementation (flash_attention_2, sdpa, eager, or None)
    #     is_prequantized: Whether model is pre-quantized (detected earlier)
    #     quant_type: Pre-quantization type (fp8, gptq, awq, etc.)
    #     keep_model_loaded: Whether to cache model for reuse
    #     resolved_quantization: Original quantization for cache key
    #     resolved_attention: Original attention mode for cache key
    #     **kwargs: Additional options (use_torch_compile, etc.)
    #
    # Returns:
    #     Tuple of (model, processor, ModelType)
    import transformers  # type: ignore
    from .model_cache import (
        get_transformers_cache_key, set_cached_transformers_model
    )

    model_name = Path(model_path).name
    config_path = Path(model_path) / "config.json"
    config_data = {}

    # Read config.json for architecture detection and quirk handling
    if config_path.exists():
        try:
            config_data = json.loads(config_path.read_text(encoding='utf-8'))
        except Exception as e:
            log.debug(_LOG_PREFIX, f"  Could not read config.json: {e}")

    # Detect model type for quirk handling and return value
    model_type_result = detect_vlm_model_type(config_data)
    config_model_type = config_data.get("model_type", "").lower()
    architectures = config_data.get("architectures", [])
    arch_str = architectures[0] if architectures else ""
    arch_str_lower = arch_str.lower()
    is_mistral_type = any(k in config_model_type for k in ("mistral", "ministral", "pixtral"))
    is_mllama_type = "mllama" in config_model_type or "mllama" in arch_str_lower
    is_llava_type = "llava" in config_model_type or "llava" in arch_str_lower
    # Qwen3.5 hybrid (linear_attention + full_attention, Mamba-style SSM): flash_attention_2
    # produces NaN/Inf logits → CUDA multinomial assert. SDPA is required.
    is_qwen3_5_type = "qwen3_5" in config_model_type or "qwen3_5" in arch_str_lower

    # ================================================================
    # Step 1: Quirk detection from config.json
    # ================================================================

    # Mllama: flash_attention_2 not supported — MllamaVisionAttention lacks is_causal
    if is_mllama_type and attn_impl == "flash_attention_2":
        attn_impl = "sdpa"
        log.debug(_LOG_PREFIX, "  Mllama: flash_attention_2 not supported for vision module, using sdpa")

    # Qwen3.5: flash_attention_2 incompatible with hybrid linear-attention/Mamba layers
    # → produces NaN logits and crashes generation. Force sdpa.
    if is_qwen3_5_type and attn_impl == "flash_attention_2":
        attn_impl = "sdpa"
        log.warning(_LOG_PREFIX, "Qwen3.5 hybrid architecture: flash_attention_2 produces NaN logits, forcing sdpa")

    # BnB skip modules for vision models — prevents quantizing vision encoder
    # These module names are harmless no-ops for Qwen/Mistral (no matching modules)
    bnb_skip_modules = []
    if is_llava_type or is_mllama_type:
        bnb_skip_modules = ["vision_tower", "multi_modal_projector", "vision_model"]
        if is_llava_type:
            bnb_skip_modules.append("image_newline")

    # Device map override for Mllama — multi-GPU auto-sharding has issues
    # with Mllama's cross-attention architecture
    device_map_override = None  # None = use unified loader defaults
    if is_mllama_type:
        device_map_override = "pinned"  # Signal to use {"":0} for quantized, None for non-quantized

    # ================================================================
    # Step 1b: Resolve model class from config.json architectures
    # ================================================================
    ModelClass = None

    # Try AutoModelForVision2Seq first (transformers < 5.0)
    try:
        from transformers import AutoModelForVision2Seq  # type: ignore
        ModelClass = AutoModelForVision2Seq
        log.debug(_LOG_PREFIX, "  Using AutoModelForVision2Seq")
    except ImportError:
        # transformers >= 5.0: resolve from config.json architectures
        if architectures:
            class_name = architectures[0]

            # Handle known architecture overrides
            if class_name == "Mistral3Model":
                class_name = "Mistral3ForConditionalGeneration"
                log.debug(_LOG_PREFIX, f"  Override: Mistral3Model -> {class_name} (for generation)")

            try:
                ModelClass = getattr(transformers, class_name)
                log.debug(_LOG_PREFIX, f"  Using model class: {class_name}")
            except AttributeError:
                log.debug(_LOG_PREFIX, f"Class '{class_name}' not in transformers {transformers.__version__}")

    # LLaVA custom package fallback — some LLaVA variants need the external llava pip package
    # because their model classes (LlavaLlamaForCausalLM, etc.) aren't in standard transformers
    if ModelClass is None and is_llava_type and arch_str:
        ModelClass = _try_import_llava_class(arch_str)

    # LLaVA standard class fallback chain
    if ModelClass is None and is_llava_type:
        try:
            from transformers import LlavaNextForConditionalGeneration  # type: ignore
            ModelClass = LlavaNextForConditionalGeneration
            log.debug(_LOG_PREFIX, "  Using LlavaNextForConditionalGeneration (LLaVA 1.6+)")
        except ImportError:
            try:
                from transformers import LlavaForConditionalGeneration  # type: ignore
                ModelClass = LlavaForConditionalGeneration
                log.debug(_LOG_PREFIX, "  Using LlavaForConditionalGeneration")
            except ImportError:
                pass

    # Mllama explicit class (for transformers < 5.0 that has no AutoModelForVision2Seq support)
    if ModelClass is None and is_mllama_type:
        try:
            from transformers import MllamaForConditionalGeneration  # type: ignore
            ModelClass = MllamaForConditionalGeneration
            log.debug(_LOG_PREFIX, "  Using MllamaForConditionalGeneration")
        except ImportError:
            pass

    # Final fallback to AutoModel
    if ModelClass is None:
        from transformers import AutoModel  # type: ignore
        ModelClass = AutoModel
        log.warning(_LOG_PREFIX, "  Using AutoModel fallback (may not support generation)")

    log.msg(_LOG_PREFIX, f"Loading VLM ({ModelClass.__name__}, {quantization}, {attn_impl})")

    # ================================================================
    # Step 2: FP8 validation
    # ================================================================
    is_fp8_model = is_prequantized and quant_type == "fp8"
    has_native_fp8 = _transformers_version >= (5, 0)

    if is_fp8_model and not has_native_fp8:
        raise ValueError(
            f"FP8 model '{model_name}' requires transformers >= 5.0 (you have {transformers.__version__}).\n\n"
            "Options:\n"
            "  1. Upgrade: pip install transformers>=5.0\n"
            "  2. Use 'vLLM (Docker)' loading method (recommended for FP8)\n"
            "  3. Download the non-FP8 version\n"
            "  4. Use a GGUF quantized version with 'GGUF (llama-cpp-python)' method"
        )
    elif is_fp8_model:
        log.msg(_LOG_PREFIX, f"Loading FP8 model with transformers {transformers.__version__} native support")

    # ================================================================
    # Step 3: Mistral-specific config patching (transformers < 5.0 only)
    #
    # Mistral3/Ministral3 models need config.json patches for transformers < 5.0:
    #   - text_config.model_type: mistral3/ministral3 → mistral (creates MistralModel backbone)
    #   - tie_word_embeddings: True → False (prevents accelerate IndexError)
    # Transformers 5.0+ has native support, so patching is skipped (and would BREAK things).
    # ================================================================
    config_backup_path = Path(model_path) / "config.json.smartlm_backup"
    config_patched = False
    original_tie_word_embeddings = True

    # Cleanup stale backups from earlier versions
    if has_native_fp8 and config_backup_path.exists():
        try:
            import shutil
            shutil.move(str(config_backup_path), str(config_path))
            log.warning(_LOG_PREFIX, "Restored original config.json from backup (was corrupted by earlier patching)")
        except Exception:
            pass

    # Read original tie_word_embeddings state (needed for lm_head tying later)
    if "tie_word_embeddings" in config_data:
        original_tie_word_embeddings = config_data.get("tie_word_embeddings", True)
    elif "text_config" in config_data and "tie_word_embeddings" in config_data.get("text_config", {}):
        original_tie_word_embeddings = config_data["text_config"].get("tie_word_embeddings", True)
    log.debug(_LOG_PREFIX, f"  Original tie_word_embeddings: {original_tie_word_embeddings}")

    # Only patch for Mistral-type models on transformers < 5.0
    if is_mistral_type and not has_native_fp8 and config_path.exists():
        log.debug(_LOG_PREFIX, "  Legacy mode (transformers < 5.0): applying Mistral config patches")
        try:
            needs_patch = False

            if "text_config" in config_data:
                text_model_type = config_data["text_config"].get("model_type", "")
                if text_model_type in ("mistral3", "ministral3"):
                    config_data["text_config"]["model_type"] = "mistral"
                    needs_patch = True
                    log.debug(_LOG_PREFIX, f"  Patching text_config.model_type: {text_model_type} -> mistral")

                if config_data["text_config"].get("tie_word_embeddings", True):
                    config_data["text_config"]["tie_word_embeddings"] = False
                    needs_patch = True
                    log.debug(_LOG_PREFIX, "  Patching text_config.tie_word_embeddings: False")

            if config_data.get("tie_word_embeddings", True):
                config_data["tie_word_embeddings"] = False
                needs_patch = True
                log.debug(_LOG_PREFIX, "  Patching tie_word_embeddings: False")

            if needs_patch:
                import shutil
                shutil.copy(config_path, config_backup_path)
                config_path.write_text(json.dumps(config_data, indent=2))
                config_patched = True
                log.debug(_LOG_PREFIX, f"  Config backed up to: {config_backup_path.name}")
        except Exception as e:
            log.debug(_LOG_PREFIX, f"  Could not patch config: {e}")
    elif is_mistral_type and has_native_fp8:
        log.debug(_LOG_PREFIX, "  Transformers 5.0+ detected: skipping Mistral config patches (native support)")

    # Fix tokenizer_config.json if it has invalid tokenizer class (applies to all models)
    tokenizer_config_path = Path(model_path) / "tokenizer_config.json"
    if tokenizer_config_path.exists():
        try:
            tokenizer_data = json.loads(tokenizer_config_path.read_text())
            if tokenizer_data.get("tokenizer_class") == "TokenizersBackend":
                tokenizer_data["tokenizer_class"] = "PreTrainedTokenizerFast"
                tokenizer_config_path.write_text(json.dumps(tokenizer_data, indent=2))
                log.debug(_LOG_PREFIX, "  Fixed tokenizer_class: TokenizersBackend -> PreTrainedTokenizerFast")
        except Exception:
            pass

    # ================================================================
    # Step 4: Build load kwargs and load model
    # ================================================================
    # `trust_remote_code` defaults to False (safe). Set to True only when the
    # registry entry (or the runtime chip) explicitly allows it for this model.
    trust_remote_code = bool(kwargs.get("trust_remote_code", False))
    load_kwargs: dict[str, Any] = {
        "low_cpu_mem_usage": True,
        "trust_remote_code": trust_remote_code,
    }
    if attn_impl:
        load_kwargs["attn_implementation"] = attn_impl

    log.debug(_LOG_PREFIX, f"  quantization={quantization}, ModelClass={ModelClass.__name__}, is_fp8={is_fp8_model}")

    # Additional pre-quantization check via safetensors SCB/CB markers (LLaVA/Mllama)
    if not is_prequantized and (is_llava_type or is_mllama_type):
        if _check_safetensors_prequantized(model_path):
            is_prequantized = True
            quant_type = "bnb"
            log.msg(_LOG_PREFIX, "Pre-quantized BnB model detected via safetensors markers")

    # Apply SigLIP vision tower monkey-patch if llava package is installed
    if is_llava_type:
        _apply_siglip_patch()

    # Determine device_map based on override and quantization.
    # device_map="auto" lets accelerate use all available GPU memory before
    # offloading to CPU. The improved auto_select_quantization safety margin
    # prevents selecting 8bit/4bit when VRAM is too tight, so offloading
    # should not trigger in practice for BnB modes.
    # NOTE: We intentionally do NOT set max_memory — that was the old cause of
    # forced CPU offloading which segfaulted with BnB int8 CUDA kernels.
    def _get_device_map(quant_mode):
        if device_map_override == "pinned":
            # Mllama: use {"":0} for quantized/pre-quantized, None for non-quantized
            if quant_mode in ("4bit", "8bit") or is_prequantized:
                return {"": 0}
            return None
        return "auto"  # Default for all other VLMs

    # Suppress accelerate's informational "meta device" warnings during loading —
    # expected when device_map="auto" offloads some params to CPU
    import logging
    _accel_logger = logging.getLogger("accelerate.big_modeling")
    _prev_accel_level = _accel_logger.level
    _accel_logger.setLevel(logging.ERROR)

    try:
        if is_prequantized and not is_fp8_model:
            # Pre-quantized BnB model (SCB/CB or quantization_config in config.json)
            if quantization in ["4bit", "8bit"]:
                log.warning(_LOG_PREFIX, f"Model is pre-quantized ({quant_type}), ignoring {quantization} request")
            load_kwargs["device_map"] = _get_device_map("prequantized")
            log.debug(_LOG_PREFIX, f"  Loading pre-quantized model (device_map={load_kwargs['device_map']})")
            model = ModelClass.from_pretrained(model_path, **load_kwargs)

        elif quantization == "4bit":
            from transformers import BitsAndBytesConfig  # type: ignore

            load_kwargs["device_map"] = _get_device_map("4bit")

            bnb_kwargs: dict[str, Any] = {
                "load_in_4bit": True,
                "bnb_4bit_compute_dtype": torch.float16,
                "bnb_4bit_quant_type": "nf4",
                "bnb_4bit_use_double_quant": True,
            }
            if bnb_skip_modules:
                bnb_kwargs["llm_int8_skip_modules"] = bnb_skip_modules

            # VRAM advisory: BnB 4-bit with low_cpu_mem_usage=True + device_map="auto"
            # quantizes shard-by-shard, so transient peak is usually just one shard,
            # not the full fp16 size. Warn only if free VRAM is below the *quantized*
            # 4-bit footprint (~25% of fp16 + overhead). CPU offloading is NOT viable
            # (bnb ≤0.47 + accelerate ≥1.12 incompatibilities) but accelerate decides.
            try:
                from .model_files import calculate_model_size
                free_bytes, _ = torch.cuda.mem_get_info(0)
                free_gb = free_bytes / (1024 ** 3)
                model_gb = calculate_model_size(Path(model_path))
                quantized_4bit_gb = model_gb * 0.55  # matches device.py estimate
                if model_gb > 0.0 and free_gb > 0.0 and free_gb < quantized_4bit_gb:
                    log.warning(_LOG_PREFIX,
                                f"4-bit loading: free VRAM={free_gb:.1f}GB below quantized "
                                f"footprint ≈{quantized_4bit_gb:.1f}GB. May OOM or fail with "
                                f"CPU-offload error. Try GGUF backend or a smaller model.")
            except Exception:
                pass

            load_kwargs["quantization_config"] = BitsAndBytesConfig(**bnb_kwargs)
            log.msg(_LOG_PREFIX, f"Loading 4bit model (all-on-GPU, device_map={load_kwargs['device_map']})")
            model = ModelClass.from_pretrained(model_path, **load_kwargs)

        elif quantization == "8bit":
            from transformers import BitsAndBytesConfig  # type: ignore

            load_kwargs["device_map"] = _get_device_map("8bit")

            bnb_kwargs: dict[str, Any] = {"load_in_8bit": True}
            if bnb_skip_modules:
                bnb_kwargs["llm_int8_skip_modules"] = bnb_skip_modules

            # VRAM advisory (8-bit): warn only when free VRAM is below the quantized
            # 8-bit footprint (~85% of fp16 with overhead). Shard-by-shard loading
            # via low_cpu_mem_usage handles transient peaks in most cases.
            try:
                from .model_files import calculate_model_size
                free_bytes, _ = torch.cuda.mem_get_info(0)
                free_gb = free_bytes / (1024 ** 3)
                model_gb = calculate_model_size(Path(model_path))
                quantized_8bit_gb = model_gb * 0.85  # matches device.py estimate
                if model_gb > 0.0 and free_gb > 0.0 and free_gb < quantized_8bit_gb:
                    log.warning(_LOG_PREFIX,
                                f"8-bit loading: free VRAM={free_gb:.1f}GB below quantized "
                                f"footprint ≈{quantized_8bit_gb:.1f}GB. May OOM or fail with "
                                f"CPU-offload error. Try GGUF backend or a smaller model.")
            except Exception:
                pass

            load_kwargs["quantization_config"] = BitsAndBytesConfig(**bnb_kwargs)
            log.msg(_LOG_PREFIX, f"Loading 8bit model (all-on-GPU, device_map={load_kwargs['device_map']})")
            model = ModelClass.from_pretrained(model_path, **load_kwargs)

        elif is_fp8_model:
            # FP8 requires transformers 5.0+ (validated in Step 2)
            try:
                from transformers import FineGrainedFP8Config  # type: ignore
                load_kwargs["device_map"] = _get_device_map("fp8")
                load_kwargs["quantization_config"] = FineGrainedFP8Config(dequantize=True)
                log.msg(_LOG_PREFIX, "Loading FP8 with dequantize=True (BF16 conversion)")
                model = ModelClass.from_pretrained(model_path, **load_kwargs)
            except ImportError:
                # Fallback: load FP8 natively without explicit config
                load_kwargs["device_map"] = _get_device_map("fp8")
                log.msg(_LOG_PREFIX, "FineGrainedFP8Config not available, loading FP8 natively")
                model = ModelClass.from_pretrained(model_path, **load_kwargs)

        else:
            # Non-quantized: auto/fp16/bf16/fp32
            dtype_map = {
                "fp16": torch.float16,
                "bf16": torch.bfloat16,
                "fp32": torch.float32,
                "auto": "auto",
            }
            dm = _get_device_map("none")
            load_kwargs["device_map"] = dm
            load_kwargs[dtype_kwarg()] = dtype_map.get(quantization, "auto")
            log.debug(_LOG_PREFIX, f"  Loading with device_map={dm}, dtype={load_kwargs[dtype_kwarg()]}")
            model = ModelClass.from_pretrained(model_path, **load_kwargs)
            # Move to GPU explicitly when device_map=None (Mllama non-quantized)
            # When device_map is set, accelerate handles placement — do not call .to()
            if dm is None and torch.cuda.is_available():
                if not (hasattr(model, 'hf_device_map') and model.hf_device_map is not None):
                    model = model.to("cuda")  # type: ignore

        log.debug(_LOG_PREFIX, "  Model loaded successfully")

        # Manual lm_head tying — ONLY for Mistral with legacy config patching
        # When we patched tie_word_embeddings=False, the checkpoint doesn't include
        # lm_head.weight, so we must tie it manually to embed_tokens.weight.
        # Transformers 5.0+ handles this natively, so skip.
        if config_patched and original_tie_word_embeddings:
            if hasattr(model, 'lm_head') and hasattr(model, 'model'):
                if hasattr(model.model, 'language_model') and hasattr(model.model.language_model, 'embed_tokens'):
                    model.lm_head.weight = model.model.language_model.embed_tokens.weight
                    log.debug(_LOG_PREFIX, "  Manually tied lm_head.weight to embed_tokens.weight (legacy mode)")
        elif has_native_fp8 and is_mistral_type:
            log.debug(_LOG_PREFIX, "  Transformers 5.0+ handles tie_word_embeddings natively, skipping manual tying")

        # Post-load: lm_head resize check (Mllama vocab_size mismatch fix)
        if is_mllama_type:
            _resize_lm_head_if_needed(model, quantization)

    except Exception as e:
        log.error(_LOG_PREFIX, f"ERROR loading VLM model: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        # Restore accelerate logger level
        _accel_logger.setLevel(_prev_accel_level)
        # Restore original config if we patched it
        if config_patched and config_backup_path.exists():
            try:
                import shutil
                shutil.move(str(config_backup_path), str(config_path))
                log.debug(_LOG_PREFIX, "  Restored original config.json")
            except Exception:
                pass

    # ================================================================
    # Step 5: Load processor
    # ================================================================
    from transformers import AutoProcessor  # type: ignore
    processor = AutoProcessor.from_pretrained(model_path)
    log.debug(_LOG_PREFIX, f"  Using AutoProcessor: {type(processor).__name__}")

    # Chat template fallback from tokenizer (useful for all models)
    if not hasattr(processor, 'chat_template') or processor.chat_template is None:
        try:
            from transformers import AutoTokenizer  # type: ignore
            tokenizer = AutoTokenizer.from_pretrained(model_path)
            if hasattr(tokenizer, 'chat_template') and tokenizer.chat_template:
                processor.chat_template = tokenizer.chat_template
                log.debug(_LOG_PREFIX, "  Copied chat_template from tokenizer to processor")
        except Exception:
            pass

    # ================================================================
    # Step 6: torch.compile + caching
    # ================================================================
    use_torch_compile = kwargs.get('use_torch_compile', False)
    is_quantized = quantization in ["4bit", "8bit"] or is_fp8_model
    if use_torch_compile and not is_quantized and torch.cuda.is_available():
        try:
            model = torch.compile(model, mode="reduce-overhead")
            log.msg(_LOG_PREFIX, "✓ Applied torch.compile optimization")
        except Exception as e:
            log.warning(_LOG_PREFIX, f"torch.compile failed: {e}")
    elif use_torch_compile and is_quantized:
        log.debug(_LOG_PREFIX, "  torch.compile skipped (not compatible with quantization)")

    if keep_model_loaded:
        cache_key = get_transformers_cache_key(model_path, resolved_quantization, resolved_attention)
        set_cached_transformers_model(cache_key, model, processor, model_type_result)

    return model, processor, model_type_result
