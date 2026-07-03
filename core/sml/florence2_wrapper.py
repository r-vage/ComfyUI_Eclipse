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

from typing import Optional, Any
from pathlib import Path
import os
import torch #type: ignore
import transformers #type: ignore

from .logger import log


_LOG_PREFIX = "Florence-2"


# Transformers version detection (centralized in model_types)
from .model_types import _transformers_version as transformers_version
_IS_V5 = transformers_version >= (5, 0)

# Florence-2 availability flags
FLORENCE2_CUSTOM_AVAILABLE = False
Florence2ForConditionalGeneration: Optional[Any] = None
Florence2Config: Optional[Any] = None
Florence2Processor: Optional[Any] = None

# Import custom Florence-2 implementation from vendored extern package
# These classes work with both v4 and v5 — on v5 we use them with init_empty_weights
try:
    from ...extern.florence2.modeling_florence2 import Florence2ForConditionalGeneration as _F2Model
    from ...extern.florence2.configuration_florence2 import Florence2Config as _F2Config
    from ...extern.florence2.processing_florence2 import Florence2Processor as _F2Processor

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


def _load_florence2_v5(model_path: str, attn_impl: str, dtype: torch.dtype, device: torch.device) -> Any:
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

    if not FLORENCE2_CUSTOM_AVAILABLE or not Florence2Config or not Florence2ForConditionalGeneration:
        raise RuntimeError("Florence-2 custom classes not available. Cannot load with v5 method.")

    from accelerate import init_empty_weights #type: ignore
    from accelerate.utils import set_module_tensor_to_device #type: ignore

    log.msg(_LOG_PREFIX, f"Loading with v5 method (accelerate): {model_path}")

    # Load config and set attention mode
    config = Florence2Config.from_pretrained(model_path)
    config._attn_implementation = attn_impl

    # Create empty model shell
    with init_empty_weights():
        model = Florence2ForConditionalGeneration(config)

    # Find and load weights
    checkpoint_path = os.path.join(model_path, "model.safetensors")
    if not os.path.exists(checkpoint_path):
        checkpoint_path = os.path.join(model_path, "pytorch_model.bin")
    if not os.path.exists(checkpoint_path):
        raise FileNotFoundError(f"No model weights found at {model_path} (tried model.safetensors and pytorch_model.bin)")

    from comfy.utils import load_torch_file #type: ignore
    state_dict = load_torch_file(checkpoint_path)

    # Handle shared embedding keys — language_model encoder/decoder embeddings
    # may share weights via language_model.model.shared.weight
    key_mapping = {}
    if "language_model.model.shared.weight" in state_dict:
        key_mapping["language_model.model.encoder.embed_tokens.weight"] = "language_model.model.shared.weight"
        key_mapping["language_model.model.decoder.embed_tokens.weight"] = "language_model.model.shared.weight"

    # Populate model parameters from state dict
    missing_keys = []
    for name, _param in model.named_parameters():
        actual_key = key_mapping.get(name, name)
        if actual_key in state_dict:
            set_module_tensor_to_device(model, name, device, value=state_dict[actual_key].to(dtype))
        else:
            missing_keys.append(name)

    if missing_keys:
        # Expected: lm_head.weight is tied to decoder embeddings (resolved by tie_weights below)
        log.debug(_LOG_PREFIX, f"{len(missing_keys)} tied weight(s) resolved by tie_weights()")
        for key in missing_keys[:5]:
            log.debug(_LOG_PREFIX, f"  Tied: {key}")

    # Tie shared embeddings
    model.language_model.tie_weights()
    model = model.eval().to(dtype).to(device)

    log.msg(_LOG_PREFIX, f"✓ Loaded with v5 method, attention={attn_impl}, dtype={dtype}")
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
        raise RuntimeError("Florence-2 custom classes not available. Cannot create processor with v5 method.")

    import json
    from tokenizers import Tokenizer as HFTokenizer #type: ignore
    from tokenizers import AddedToken as TokAddedToken #type: ignore
    from transformers import CLIPImageProcessor, BartTokenizerFast #type: ignore

    # Create image processor with Florence-2 standard settings
    image_processor = CLIPImageProcessor(
        do_resize=True,
        size={"height": 768, "width": 768},
        resample=3,  # BICUBIC
        do_center_crop=False,
        do_rescale=True,
        rescale_factor=1/255.0,
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
        tok_obj.add_special_tokens([
            TokAddedToken(t, special=True, normalized=False) for t in added_token_strs
        ])

    tokenizer = BartTokenizerFast(
        tokenizer_object=tok_obj,
        bos_token="<s>", eos_token="</s>", unk_token="<unk>",
        pad_token="<pad>", mask_token="<mask>",
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
    trust_remote_code = bool(load_kwargs.pop('trust_remote_code', False))

    # Determine if loading from local path or remote
    is_local = Path(model_path).exists()
    source = "local" if is_local else "remote"
    
    # Verify model integrity if loading from local cache
    if is_local:
        from .model_files import verify_model_integrity
        # Try to get repo_id from load_kwargs if available (for hash lookup from HuggingFace)
        repo_id = load_kwargs.pop('repo_id', '') if isinstance(load_kwargs, dict) else ''
        if not verify_model_integrity(Path(model_path), repo_id):
            raise RuntimeError(f"Florence-2 model integrity check failed for {model_path}. The model may be corrupted. Please delete and re-download.")
    
    # CRITICAL: Resolve "auto" attention mode BEFORE trying any loading
    # Florence-2 doesn't support "auto" - must be resolved to specific mode
    requested_attn = load_kwargs.get('attn_implementation', 'auto')
    
    if requested_attn == 'auto':
        # Check if flash-attn is available
        try:
            import flash_attn #type: ignore
            load_kwargs['attn_implementation'] = 'flash_attention_2'
            requested_attn = 'flash_attention_2'
            log.msg(_LOG_PREFIX, "Auto mode: Selected flash_attention_2 (flash-attn available)")
        except ImportError:
            # Fall back to sdpa (PyTorch built-in, good performance)
            load_kwargs['attn_implementation'] = 'sdpa'
            requested_attn = 'sdpa'
            log.msg(_LOG_PREFIX, "Auto mode: Selected sdpa (flash-attn not available)")

    # ========================================================================
    # Transformers v5+ path: Use accelerate-based manual loading
    # ========================================================================
    if _IS_V5 and FLORENCE2_CUSTOM_AVAILABLE:
        # BnB quantization not supported with v5 accelerate loading path
        if 'quantization_config' in load_kwargs:
            log.warning(_LOG_PREFIX, "BitsAndBytes quantization not supported with transformers v5 Florence-2 loading — loading without quantization")
            load_kwargs.pop('quantization_config', None)
            load_kwargs.pop('device_map', None)

        # Extract dtype from load_kwargs (torch_dtype or dtype key)
        dtype = load_kwargs.get('torch_dtype', load_kwargs.get('dtype', torch.float16))
        if dtype == "auto" or dtype is None:
            dtype = torch.float16

        # Determine target device
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        return _load_florence2_v5(model_path, requested_attn, dtype, device)

    # ========================================================================
    # Transformers v4 path: Custom from_pretrained or AutoModel fallback
    # ========================================================================

    # Try custom implementation first
    if FLORENCE2_CUSTOM_AVAILABLE and Florence2ForConditionalGeneration:
        try:
            log.msg(_LOG_PREFIX, f"Loading from {source} with custom implementation: {model_path}")
            model = Florence2ForConditionalGeneration.from_pretrained(
                model_path,
                local_files_only=is_local,  # Prevent online lookup for local models
                **load_kwargs
            )
            log.debug(_LOG_PREFIX, "Loaded with custom implementation")
            return model
        except Exception as e:
            log.warning(_LOG_PREFIX, f"Custom implementation failed: {e}")
            log.warning(_LOG_PREFIX, "Falling back to AutoModel...")
    
    # Fallback to AutoModel (v4 only)
    from transformers import AutoModelForCausalLM #type: ignore
    log.msg(_LOG_PREFIX, f"Loading from {source} with AutoModelForCausalLM: {model_path}")
    
    # Apply workaround context manager if needed (for transformers < 4.51.0)
    if transformers.__version__ < '4.51.0':
        from unittest.mock import patch
        from transformers.dynamic_module_utils import get_imports #type: ignore
        
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
        
        log.msg(_LOG_PREFIX, f"Applying flash_attn workaround for transformers {transformers.__version__}")
        load_context = patch("transformers.dynamic_module_utils.get_imports", fixed_get_imports)
    else:
        from contextlib import nullcontext
        load_context = nullcontext()
    
    with load_context:
        if requested_attn == 'flash_attention_2':
            try:
                log.msg(_LOG_PREFIX, "Attempting Flash Attention 2...")
                model = AutoModelForCausalLM.from_pretrained(
                    model_path,
                    trust_remote_code=trust_remote_code,
                    local_files_only=is_local,
                    **load_kwargs
                )
                log.msg(_LOG_PREFIX, "✓ Loaded with Flash Attention 2")
                return model
            except (ValueError, ImportError) as e:
                if "does not support Flash Attention 2.0" in str(e) or "flash_attn" in str(e):
                    log.warning(_LOG_PREFIX, "Flash Attention 2 not supported by cached model code")
                    log.error(_LOG_PREFIX, "Your Florence-2 model uses outdated cached code from HuggingFace")
                    
                    cache_hint = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "modules", "transformers_modules")
                    model_name = Path(model_path).name if is_local else model_path.split('/')[-1]
                    
                    log.error(_LOG_PREFIX, "To update: Delete cached folder and restart ComfyUI:")
                    log.error(_LOG_PREFIX, f"  Location: {cache_hint}/{model_name}")
                    log.warning(_LOG_PREFIX, "Falling back to SDPA (still faster than eager mode)")
                    
                    load_kwargs['attn_implementation'] = 'sdpa'
                else:
                    raise
        
        # Load with requested attention mode (or fallback to sdpa)
        try:
            model = AutoModelForCausalLM.from_pretrained(
                model_path,
                trust_remote_code=trust_remote_code,
                local_files_only=is_local,
                **load_kwargs
            )
        except AttributeError as e:
            if '_supports_sdpa' in str(e):
                log.warning(_LOG_PREFIX, f"Model lacks SDPA support attribute, falling back to eager attention")
                load_kwargs['attn_implementation'] = 'eager'
                model = AutoModelForCausalLM.from_pretrained(
                    model_path,
                    trust_remote_code=trust_remote_code,
                    local_files_only=is_local,
                    **load_kwargs
                )
            else:
                raise
    
    # Add _supports_sdpa to model class if not present (custom models from HF may lack it)
    if not hasattr(type(model), '_supports_sdpa'):
        log.warning(_LOG_PREFIX, f"Adding _supports_sdpa=True to {type(model).__name__}")
        type(model)._supports_sdpa = True
    
    # Also patch language_model subcomponent if it exists (for Florence2ForConditionalGeneration)
    if hasattr(model, 'language_model') and not hasattr(type(model.language_model), '_supports_sdpa'):
        log.warning(_LOG_PREFIX, f"Adding _supports_sdpa=True to {type(model.language_model).__name__}")
        type(model.language_model)._supports_sdpa = True
    
    attn_used = load_kwargs.get('attn_implementation', 'auto')
    log.msg(_LOG_PREFIX, f"✓ Loaded with AutoModel from {source}, attention={attn_used}")
    return model


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
    has_processor_module = is_local and (model_path_obj / "processing_florence2.py").exists()
    local_files_only = has_processor_module  # Only force local if we have all required files
    
    # Try custom processor first
    if FLORENCE2_CUSTOM_AVAILABLE and Florence2Processor:
        try:
            processor = Florence2Processor.from_pretrained(
                model_path,
                local_files_only=local_files_only,
                **kwargs
            )
            return processor
        except Exception as e:
            log.warning(_LOG_PREFIX, f"Custom processor failed: {e}, using AutoProcessor")
    
    # v4 fallback: Use AutoProcessor — caller controls trust_remote_code via kwarg
    trust_remote_code = bool(kwargs.pop('trust_remote_code', False))
    from transformers import AutoProcessor #type: ignore
    processor = AutoProcessor.from_pretrained(
        model_path,
        trust_remote_code=trust_remote_code,
        local_files_only=local_files_only,
        **kwargs
    )
    return processor


# Export public API
__all__ = [
    'FLORENCE2_CUSTOM_AVAILABLE',
    'Florence2ForConditionalGeneration',
    'Florence2Config',
    'Florence2Processor',
    'load_florence2_model',
    'load_florence2_processor',
]



