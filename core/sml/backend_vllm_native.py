# Native vLLM integration for SML (Linux only).
#
# This module handles native vLLM loading on Linux where vLLM can be installed directly
# via pip without Docker. For Windows, use backend_vllm_docker.py instead.
#
# vLLM provides optimized inference for LLMs with:
# - Continuous batching
# - PagedAttention
# - Tensor parallelism
# - Native FP8 support
# ==============================================================================
# CRITICAL: Disable vLLM v1 engine and multiprocessing BEFORE importing vLLM
# This prevents the spawn/fork issue when vLLM is used as a library in ComfyUI
# where CUDA is already initialized before vLLM loads.
# vLLM v1 engine spawns a separate EngineCore process which re-executes main.py
# ==============================================================================
import os

os.environ["VLLM_USE_V1"] = "0"  # Force v0 engine (no multiprocessing)
os.environ["VLLM_ENABLE_V1_MULTIPROCESSING"] = (
    "0"  # Disable v1 multiprocessing if v1 is used
)
os.environ["VLLM_WORKER_MULTIPROC_METHOD"] = "fork"  # Use fork instead of spawn
import gc
import platform
import torch  # type: ignore
from pathlib import Path
from typing import Optional, Dict, Any, List

from .logger import log

_LOG_PREFIX = "vLLM Native"


# ==============================================================================
# PLATFORM CHECK
# ==============================================================================

IS_LINUX = platform.system() == "Linux"

if not IS_LINUX:
    log.warning(
        _LOG_PREFIX,
        "This module is for Linux only. Use backend_vllm_docker.py on Windows.",
    )


# ==============================================================================
# NATIVE vLLM AVAILABILITY CHECK
# ==============================================================================

VLLM_AVAILABLE = False
VLLM_VERSION = ""

try:
    import vllm  # type: ignore

    VLLM_AVAILABLE = True
    VLLM_VERSION = getattr(vllm, "__version__", "unknown")
    log.msg(_LOG_PREFIX, f"✓ vLLM {VLLM_VERSION} available")
except ImportError:
    log.warning(_LOG_PREFIX, "vLLM not installed. Install with: pip install vllm")


def is_vllm_available() -> bool:
    # Check if native vLLM is available
    return VLLM_AVAILABLE and IS_LINUX


# ==============================================================================
# vLLM MODEL LOADING (Native)
# ==============================================================================

# Cache for loaded vLLM models
# NOTE: Only ONE model is kept cached at a time to prevent VRAM accumulation
# in multi-node workflows. Loading a different model will evict the cached one.
_vllm_model_cache: Dict[str, Any] = {}


def _clear_vllm_cache_if_different(new_cache_key: str):
    # Clear vLLM cache if a different model is being loaded.
    #
    # This prevents VRAM accumulation when multiple SmartLoader nodes
    # use different models in the same workflow.
    global _vllm_model_cache

    if _vllm_model_cache and new_cache_key not in _vllm_model_cache:
        log.debug(
            _LOG_PREFIX,
            "Clearing previous vLLM model from cache (different model requested)",
        )
        # Unload all cached models
        for key in list(_vllm_model_cache.keys()):
            try:
                llm = _vllm_model_cache.pop(key)
                del llm
            except Exception as e:
                log.debug(_LOG_PREFIX, f"  Error clearing vLLM model {key}: {e}")

        # Force garbage collection
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()


def load_vllm(
    model_path: str,
    quantization: Optional[str] = None,
    context_size: Optional[int] = None,
    gpu_memory_utilization: Optional[float] = None,
    trust_remote_code: bool = False,
    use_torch_compile: bool = False,
) -> Optional[Dict[str, Any]]:
    # Load model via native vLLM (Linux only).
    #
    # This function uses vLLM's LLM class directly for optimized inference.
    # Only ONE model is cached at a time - loading a different model will
    # evict the previous one to prevent VRAM accumulation in multi-node workflows.
    #
    # Args:
    #     model_path: Full path to model folder
    #     quantization: Quantization method (bitsandbytes, awq, gptq, fp8, or None)
    #     context_size: Maximum context window size (max_model_len in vLLM)
    #     gpu_memory_utilization: GPU memory utilization (0.0-1.0)
    #
    # Returns:
    #     Dict with vLLM model info, or None if unavailable
    if not is_vllm_available():
        log.warning(_LOG_PREFIX, "Not available on this platform")
        return None

    try:
        from vllm import LLM, SamplingParams  # type: ignore
    except ImportError as e:
        log.error(_LOG_PREFIX, f"Failed to import vLLM: {e}")
        return None

    model_name = Path(model_path).name

    # Build cache key that includes quantization and context_size
    cache_key = (
        f"{model_path}:{quantization or 'none'}:{context_size or 'auto'}:"
        f"compile={use_torch_compile}"
    )

    # Clear cache if loading a different model (prevents VRAM accumulation)
    _clear_vllm_cache_if_different(cache_key)

    # Check if model is already loaded with same settings
    if cache_key in _vllm_model_cache:
        log.debug(_LOG_PREFIX, f"Using cached vLLM model: {model_name}")
        llm = _vllm_model_cache[cache_key]
    else:
        log.msg(_LOG_PREFIX, f"Loading model: {model_name}")

        # Detect WSL environment
        is_wsl = False
        try:
            with open("/proc/version", "r") as f:
                is_wsl = "microsoft" in f.read().lower() or "wsl" in f.read().lower()
        except Exception:
            pass

        # Build LLM kwargs
        llm_kwargs: Dict[str, Any] = {
            "model": model_path,
            "dtype": "auto",  # Let vLLM auto-detect (supports FP8 natively)
            "seed": 0,  # Explicit seed (None is deprecated in v0.13)
            "allowed_local_media_path": "/",  # Allow loading local images for vision models
            "trust_remote_code": trust_remote_code,  # Required for newer model architectures (Mistral 3/Pixtral) — caller-controlled
            "disable_log_stats": True,  # Reduce log spam
            # Native vLLM shares the ComfyUI CUDA process. Keep CUDA graph capture
            # opt-in, matching the safer Docker backend default.
            "enforce_eager": not use_torch_compile,
        }

        # ==============================================================================
        # MISTRAL NATIVE FORMAT DETECTION
        # Mistral models distributed in native format have consolidated.safetensors
        # and require special loading parameters (config_format, load_format, tokenizer_mode)
        # HuggingFace-converted models (model.safetensors with language_model.* prefix)
        # are broken in vLLM 0.12+ due to weight mapping issues with Pixtral loader
        # NOTE: Mistral native format is incompatible with bitsandbytes quantization
        #       (load_format conflicts: "mistral" vs "bitsandbytes")
        # ==============================================================================
        model_dir = Path(model_path)
        consolidated_path = model_dir / "consolidated.safetensors"
        params_json_path = model_dir / "params.json"  # Mistral native format indicator
        config_json_path = model_dir / "config.json"

        # Check for Mistral native format (consolidated.safetensors or params.json)
        is_mistral_native = consolidated_path.exists() or params_json_path.exists()

        # Also check config.json for Mistral3/Pixtral vision models
        # These need enforce_eager to avoid CUDA graph crashes
        is_mistral3_vision = False
        if config_json_path.exists():
            try:
                import json

                with open(config_json_path, "r") as f:
                    config = json.load(f)
                architectures = config.get("architectures", [])
                model_type = config.get("model_type", "")
                vision_type = config.get("vision_config", {}).get("model_type", "")
                is_mistral3_vision = (
                    "Mistral3ForConditionalGeneration" in architectures
                    or model_type == "mistral3"
                    or vision_type == "pixtral"
                )
                if is_mistral3_vision:
                    log.debug(
                        _LOG_PREFIX,
                        "  Detected Mistral3/Pixtral vision model from config.json",
                    )
            except Exception as e:
                log.debug(_LOG_PREFIX, f"  Could not read config.json: {e}")

        # Fallback: check model name for ministral/pixtral
        model_name_lower = Path(model_path).name.lower()
        if "ministral" in model_name_lower or "pixtral" in model_name_lower:
            is_mistral3_vision = True
            log.debug(_LOG_PREFIX, "  Detected Mistral3/Pixtral from model name")

        # Check if bitsandbytes quantization is requested or might be auto-selected
        # Note: 'auto' quantization might select bitsandbytes based on VRAM, which conflicts with mistral load_format
        is_bitsandbytes = quantization and quantization.lower() == "bitsandbytes"
        is_auto_quant = quantization and quantization.lower() == "auto"

        # Also check if auto-quantization will likely need bitsandbytes (model too big for VRAM)
        needs_quantization = False
        if is_auto_quant and is_mistral_native:
            try:
                import torch  # type: ignore

                # Estimate model size from consolidated.safetensors
                model_size_gb = (
                    consolidated_path.stat().st_size / (1024**3)
                    if consolidated_path.exists()
                    else 0
                )
                if torch.cuda.is_available():
                    free_vram_gb = (
                        torch.cuda.get_device_properties(0).total_memory
                        / (1024**3)
                        * 0.85
                    )
                    # If model (in bf16) doesn't fit, quantization will be needed
                    needs_quantization = (
                        model_size_gb * 2
                    ) > free_vram_gb  # bf16 = 2x safetensors size
                    log.debug(
                        _LOG_PREFIX,
                        f"  Auto-quant check: model={model_size_gb:.1f}GB, free_vram={free_vram_gb:.1f}GB, needs_quant={needs_quantization}",
                    )
            except Exception as e:
                log.debug(_LOG_PREFIX, f"  Auto-quant check failed: {e}")
                needs_quantization = True  # Assume we might need quantization

        if (
            is_mistral_native
            and not is_bitsandbytes
            and not (is_auto_quant and needs_quantization)
        ):
            log.msg(
                _LOG_PREFIX,
                "✓ Detected Mistral native format (consolidated.safetensors)",
            )
            llm_kwargs["config_format"] = "mistral"
            llm_kwargs["load_format"] = "mistral"
            llm_kwargs["tokenizer_mode"] = "mistral"
            log.debug(
                _LOG_PREFIX,
                "  Using Mistral native loading: config_format=mistral, load_format=mistral, tokenizer_mode=mistral",
            )
        elif is_mistral_native and (
            is_bitsandbytes or (is_auto_quant and needs_quantization)
        ):
            # Bitsandbytes requires load_format="bitsandbytes", can't use mistral native format
            log.warning(
                _LOG_PREFIX,
                "⚠ Mistral native format detected but quantization needed - using HuggingFace shards",
            )
            log.warning(
                _LOG_PREFIX,
                "  Note: Model will load from model-*.safetensors shards instead of consolidated.safetensors",
            )
            # Don't set mistral format options - let it try standard HuggingFace loading

        # Disable CUDA graphs on WSL (causes crashes during graph capture)
        # Also disable for Mistral3/Pixtral vision models (CUDA graph crashes with exit code 139)
        if not use_torch_compile:
            log.debug(
                _LOG_PREFIX,
                "  Eager execution enabled (torch compile is off)",
            )
        elif is_wsl:
            llm_kwargs["enforce_eager"] = True
            log.debug(
                _LOG_PREFIX,
                "  WSL detected: enabling enforce_eager to disable CUDA graphs",
            )
        elif is_mistral3_vision:
            llm_kwargs["enforce_eager"] = True
            log.debug(
                _LOG_PREFIX,
                "  Mistral3/Pixtral vision model: enabling enforce_eager to avoid CUDA graph crashes",
            )

        # Set max_model_len (context_size)
        if context_size and context_size > 0:
            llm_kwargs["max_model_len"] = context_size
            log.debug(_LOG_PREFIX, f"  Context size: {context_size}")
        else:
            llm_kwargs["max_model_len"] = 8192  # Reasonable default
            log.debug(_LOG_PREFIX, "  Context size: 8192 (default)")

        # Set GPU memory utilization
        if gpu_memory_utilization and 0 < gpu_memory_utilization <= 1.0:
            llm_kwargs["gpu_memory_utilization"] = gpu_memory_utilization
        else:
            llm_kwargs["gpu_memory_utilization"] = 0.85  # Leave some VRAM for ComfyUI
        log.debug(
            _LOG_PREFIX,
            f"  GPU memory utilization: {llm_kwargs['gpu_memory_utilization']}",
        )

        # Handle quantization
        # vLLM supports: bitsandbytes, awq, gptq, squeezellm, fp8
        if quantization and quantization.lower() not in (
            "none",
            "auto",
            "fp16",
            "bf16",
            "fp32",
        ):
            llm_kwargs["quantization"] = quantization.lower()
            log.msg(_LOG_PREFIX, f"  Quantization: {quantization}")

        try:
            # Load model with vLLM
            # Note: We disable v1 engine and multiprocessing via env vars at module load time
            llm = LLM(**llm_kwargs)

            # Cache for reuse
            _vllm_model_cache[cache_key] = llm
            log.msg(_LOG_PREFIX, "✓ Model loaded successfully")

        except Exception as e:
            log.error(_LOG_PREFIX, f"Failed to load model: {e}")
            return None

    return {
        "model": llm,
        "model_path": model_path,
        "model_name": model_name,
        "backend": "vllm_native",
    }


# ==============================================================================
# vLLM GENERATION (Native)
# ==============================================================================


def generate_vllm(
    smart_lm_instance,
    prompt: str,
    image_paths: Optional[list] = None,
    max_tokens: int = 512,
    temperature: float = 0.7,
    top_p: float = 0.9,
    top_k: int = 50,
    seed: Optional[int] = None,
    llm_mode: Optional[str] = None,
    instruction_template: str = "",
    repetition_penalty: float = 1.0,
    vision_task: Optional[str] = None,
    use_few_shot: bool = True,
    **kwargs,
) -> str | tuple[str, str]:
    # Generate text using native vLLM.
    #
    # Supports both:
    # - Vision models with image_paths
    # - Text-only LLM with llm_mode for few-shot examples
    #
    # Args:
    #     smart_lm_instance: SmartLM instance with loaded vLLM model
    #     prompt: Text prompt
    #     image_paths: List of image paths for vision models (max 16 for video frames)
    #     max_tokens: Maximum tokens to generate
    #     temperature: Sampling temperature
    #     top_p: Top-p (nucleus) sampling
    #     top_k: Top-k sampling
    #     seed: Random seed for reproducibility
    #     llm_mode: LLM mode key for few-shot examples (text-only models)
    #     instruction_template: Custom instruction template (text-only models)
    #     repetition_penalty: Repetition penalty
    #     **kwargs: Additional arguments
    #
    # Returns:
    #     Generated text string (or tuple (cleaned, raw) for LLM mode)
    if (
        not hasattr(smart_lm_instance, "vllm_model")
        or smart_lm_instance.vllm_model is None
    ):
        raise RuntimeError("vLLM model not loaded. Call load_vllm first.")

    try:
        from vllm import SamplingParams  # type: ignore
    except ImportError as e:
        raise RuntimeError(f"vLLM not available: {e}")

    llm = smart_lm_instance.vllm_model

    # Build sampling params - only include seed if it's a valid integer
    # vLLM doesn't accept seed=None, it must be omitted or a valid int
    sampling_kwargs: Dict[str, Any] = {
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,  # vLLM supports top_k sampling
    }

    # vLLM SamplingParams supports repetition_penalty natively
    if repetition_penalty and repetition_penalty != 1.0:
        sampling_kwargs["repetition_penalty"] = repetition_penalty

    # min_p / stop are also native to vLLM SamplingParams
    min_p = kwargs.get("min_p", 0.0)
    if min_p and min_p > 0.0:
        sampling_kwargs["min_p"] = min_p
    stop_sequences = kwargs.get("stop_sequences")
    if stop_sequences:
        sampling_kwargs["stop"] = stop_sequences

    # Only add seed if it's a valid integer (not None)
    if seed is not None and isinstance(seed, int):
        sampling_kwargs["seed"] = seed

    log.debug(
        _LOG_PREFIX,
        f"SamplingParams: max_tokens={max_tokens}, temp={temperature}, top_p={top_p}, top_k={top_k}, rep_pen={repetition_penalty}, seed={seed}",
    )
    sampling_params = SamplingParams(**sampling_kwargs)

    # Handle images for vision models (video frames are passed as images, max 16)
    if image_paths:
        # vLLM 0.12+ vision model support
        # Use chat API which handles image placeholders automatically
        try:
            log.debug(
                _LOG_PREFIX, f"Vision generation with {len(image_paths)} image(s)"
            )

            # Eclipse 3.5+ passes system + user separately via system_prompt kwarg.
            # Legacy callers may still send a combined "system\n\nuser" string.
            system_prompt = kwargs.get("system_prompt")
            user_message = ""

            if system_prompt is not None:
                user_message = (prompt or "").strip()
            elif "\n\n" in prompt:
                parts = prompt.split("\n\n", 1)  # Split only on first \n\n
                system_prompt = parts[0].strip()
                if len(parts) > 1:
                    remaining = parts[1].strip()
                    if remaining.startswith("Additional context:"):
                        user_message = remaining.replace(
                            "Additional context:", ""
                        ).strip()
                    elif remaining:
                        user_message = remaining
                log.debug(
                    _LOG_PREFIX,
                    f"  Parsed - System: {system_prompt[:50] if system_prompt else 'None'}..., User: {user_message[:50] if user_message else 'empty'}...",
                )
            else:
                # No separator - use entire prompt as user message (Custom task)
                user_message = prompt

            # Build content list with images and optional text
            content = []
            for img_path in image_paths:
                log.debug(_LOG_PREFIX, f"Adding image: {img_path}")
                # vLLM 0.12+ requires file:// URL for local files
                content.append(
                    {"type": "image_url", "image_url": {"url": f"file://{img_path}"}}
                )

            # Add user text if provided
            user_content = (
                user_message
                if user_message
                else "Please follow the instructions above for this image."
            )
            content.append({"type": "text", "text": user_content})

            # Build chat message with system role if we have a system prompt
            conversation = []
            if system_prompt:
                conversation.append({"role": "system", "content": system_prompt})

            # Inject text-only few-shot examples to guide output style (no prefixes, uncensored)
            if vision_task and use_few_shot:
                from .config_templates import get_vision_few_shot_messages

                few_shot = get_vision_few_shot_messages(vision_task)
                if few_shot:
                    conversation.extend(few_shot)

            conversation.append({"role": "user", "content": content})

            log.debug(_LOG_PREFIX, f"Using chat API for vision")
            outputs = llm.chat(conversation, sampling_params=sampling_params)

        except Exception as e:
            log.warning(
                _LOG_PREFIX,
                f"Vision generation failed ({type(e).__name__})",
            )
            log.debug(_LOG_PREFIX, f"Vision error details: {type(e).__name__}: {e}")
            # Fall back to text-only
            log.warning(_LOG_PREFIX, "Falling back to text-only generation")
            outputs = llm.generate([prompt], sampling_params=sampling_params)
    elif llm_mode:
        # Text-only LLM with few-shot examples
        from .config_templates import get_llm_few_shot_examples
        from .tasks import get_system_prompt

        LLM_FEW_SHOT_EXAMPLES = get_llm_few_shot_examples()

        config = LLM_FEW_SHOT_EXAMPLES.get(llm_mode)
        if config:
            display_name = config.get("display_name", llm_mode)
        else:
            # No few-shot entry — derive display name for correct system prompt lookup
            display_name = llm_mode.replace("_", " ").title()
            config = {
                "display_name": display_name,
                "instruction_template": "",
                "examples": [],
            }
            log.debug(
                _LOG_PREFIX,
                f"No few-shot config for '{llm_mode}', using task system prompt for '{display_name}'",
            )

        # Get system_prompt from prompt_defaults (authoritative source)
        system_prompt = get_system_prompt(display_name)
        if not system_prompt:
            system_prompt = "You are a helpful assistant."

        examples_val = config.get("examples", []) if use_few_shot else []
        examples = examples_val if isinstance(examples_val, list) else []
        template_val = (
            instruction_template
            if instruction_template
            else config.get("instruction_template", "")
        )
        if isinstance(template_val, list):
            template = "\n".join(str(item) for item in template_val)
        else:
            template = str(template_val or "")

        if isinstance(prompt, list):
            prompt = "\n".join(str(item) for item in prompt)
        elif not isinstance(prompt, str):
            prompt = str(prompt or "")

        log.debug(
            _LOG_PREFIX,
            f"  LLM mode: display_name={display_name}, {len(examples)} examples (use_few_shot={use_few_shot})",
        )

        # Build messages: system + (optional examples) + user request
        messages = [{"role": "system", "content": system_prompt}]

        # Add few-shot examples only if available for this task
        if examples:
            messages.extend(examples)

        # Build user request
        if llm_mode != "direct_chat" and template:
            req = (
                template.replace("{prompt}", prompt)
                if "{prompt}" in template
                else f"{template} {prompt}"
            )
            messages.append({"role": "user", "content": req})
        else:
            messages.append({"role": "user", "content": prompt})

        log.debug(_LOG_PREFIX, f"Using chat API for LLM")
        outputs = llm.chat(messages, sampling_params=sampling_params)
    else:
        # Simple text-only generation (no llm_mode)
        log.debug(_LOG_PREFIX, "Text-only generation")
        outputs = llm.generate([prompt], sampling_params=sampling_params)

    # Extract generated text
    if outputs and len(outputs) > 0:
        result = outputs[0].outputs[0].text

        from .common import clean_model_output

        cleaned_result, raw_result = clean_model_output(result)

        # For LLM mode, return tuple (cleaned, raw) for compatibility
        if llm_mode:
            return cleaned_result, raw_result

        return cleaned_result

    return "" if not llm_mode else ("", "")


def unload_vllm(smart_lm_instance=None, model_path: Optional[str] = None):
    # Unload vLLM model from cache and free GPU memory.
    #
    # Args:
    #     smart_lm_instance: SmartLM instance to clear
    #     model_path: Specific model path to unload, or None to unload all
    global _vllm_model_cache
    import gc
    import torch  # type: ignore

    models_to_delete = []

    if model_path:
        # Find cache keys containing this model path
        for key in list(_vllm_model_cache.keys()):
            if model_path in key:
                models_to_delete.append(key)
    else:
        models_to_delete = list(_vllm_model_cache.keys())

    # Delete models from cache
    for key in models_to_delete:
        if key in _vllm_model_cache:
            llm = _vllm_model_cache.pop(key)
            # Try to delete the LLM engine properly
            try:
                del llm
            except Exception:
                pass
            log.debug(_LOG_PREFIX, f"Unloaded model: {key}")

    if not models_to_delete:
        log.debug(_LOG_PREFIX, "No models to unload")
    else:
        log.debug(_LOG_PREFIX, f"Unloaded {len(models_to_delete)} model(s)")

    if smart_lm_instance is not None:
        if (
            hasattr(smart_lm_instance, "vllm_model")
            and smart_lm_instance.vllm_model is not None
        ):
            try:
                del smart_lm_instance.vllm_model
            except Exception:
                pass
        smart_lm_instance.vllm_model = None
        smart_lm_instance.vllm_model_path = None
        smart_lm_instance.is_vllm = False
        smart_lm_instance.is_vllm_native = False

    # Force garbage collection and CUDA memory cleanup
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        log.msg(_LOG_PREFIX, "GPU memory cleared")


# ==============================================================================
# MODULE EXPORTS
# ==============================================================================

__all__ = [
    # Availability
    "VLLM_AVAILABLE",
    "VLLM_VERSION",
    "IS_LINUX",
    "is_vllm_available",
    # Loading
    "load_vllm",
    "unload_vllm",
    # Generation
    "generate_vllm",
]
