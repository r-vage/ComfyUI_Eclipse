# backend_gguf.py - Unified GGUF Backend (llama-cpp-python)
#
# Handles all GGUF format models:
# - Vision models with MMProj (QwenVL, LLaVA)
# - Text-only LLMs
#
# Uses llama-cpp-python library for inference

import gc
import torch #type: ignore
import base64
from io import BytesIO
from pathlib import Path
from typing import Any, Optional
from .vlm_detection import tensor_to_pil
from .logger import log


_LOG_PREFIX = "GGUF"

# Common UTF-8 encoding artifacts (mojibake) from GGUF model outputs
_ENCODING_FIXES = {
    'âĢĻ': "'",   # Right single quotation mark (U+2019)
    'âĢľ': '"',   # Left double quotation mark (U+201C)
    'âĢĿ': '"',   # Right double quotation mark (U+201D)
    'âĢĺ': "'",   # Left single quotation mark (U+2018)
    'âĢ"': '—',   # Em dash (U+2014)
    'âĢ"': '–',   # En dash (U+2013)
    'âĢ¦': '…',   # Horizontal ellipsis (U+2026)
}


# ==============================================================================
# GGUF DETECTION
# ==============================================================================

def is_gguf_model(model_path: str) -> bool:
    # Check if model path is a GGUF format file.
    #
    # Detects GGUF models by:
    # - .gguf file extension
    # - Quantization markers (Q4_K_M, Q5_K_S, Q8_0, etc.)
    # - "gguf" in repo/path name
    #
    # Args:
    #     model_path: Path to the model file or directory
    #
    # Returns:
    #     True if this appears to be a GGUF model
    model_path_lower = model_path.lower()
    
    # Check .gguf extension
    has_gguf_ext = model_path_lower.endswith(".gguf")
    
    # Check for "gguf" in name (e.g., "model-GGUF" repos)
    has_gguf_name = "gguf" in model_path_lower
    
    # Check for GGUF quantization markers (Q4_K_M, Q5_K_S, Q8_0, etc.)
    gguf_quant_markers = [
        "_q4_", "_q5_", "_q6_", "_q8_", 
        "-q4-", "-q5-", "-q6-", "-q8-",
        "_k_m", "_k_s", "_k_l", 
        "q4_k", "q5_k", "q6_k", "q8_0", 
        ".q4_", ".q5_", ".q6_", ".q8_"
    ]
    has_gguf_quant = any(marker in model_path_lower for marker in gguf_quant_markers)
    
    return has_gguf_ext or has_gguf_name or has_gguf_quant


# ==============================================================================
# NOTE: v1 loading functions (load_gguf_model, _load_gguf_vision, _load_gguf_text)
# have been removed as they are no longer used.
# v2 uses backend_llamacpp_docker.py for GGUF model loading via Docker backend.
# The generation functions below are still used by v2.
# ==============================================================================


def clear_gguf_state_between_tasks(smart_lm_instance):
    # Clear GGUF model state between multi-task runs to prevent VRAM accumulation.
    #
    # This clears:
    # - KV cache (token state from previous generation)
    # - Image embeddings cached in chat handler (mtmd/clip vision encoder state)
    #
    # Unlike cleanup_gguf_model(), this does NOT close the model - it just resets state
    # so the model can be reused for the next task without memory buildup.
    #
    # Safe to call multiple times.
    model = None
    
    # Get the actual model object (may be wrapped)
    if hasattr(smart_lm_instance, 'model'):
        model = smart_lm_instance.model
    else:
        model = smart_lm_instance
    
    if model is None:
        return
    
    # 1. Clear KV cache using the proper reset method
    try:
        if hasattr(model, 'reset'):
            model.reset()
            log.debug(_LOG_PREFIX, "Cleared GGUF KV cache via model.reset()")
    except Exception as e:
        log.debug(_LOG_PREFIX, f"KV cache reset error (may be ok): {e}")
    
    # 2. Reset n_tokens counter to 0 (forces fresh context)
    try:
        if hasattr(model, 'n_tokens'):
            model.n_tokens = 0
    except Exception:
        pass
    
    # 3. Clear image embeddings from chat handler to free vision VRAM
    chat_handler = None
    if hasattr(model, '_sml_chat_handler') and model._sml_chat_handler is not None:
        chat_handler = model._sml_chat_handler
    elif hasattr(model, 'chat_handler') and model.chat_handler is not None:
        chat_handler = model.chat_handler
    elif hasattr(smart_lm_instance, 'chat_handler_ref') and smart_lm_instance.chat_handler_ref is not None:
        chat_handler = smart_lm_instance.chat_handler_ref
    
    if chat_handler is not None:
        # Clear cached image embeddings (mtmd for Qwen2.5-VL, clip for LLaVA)
        # These can accumulate 500MB+ per image batch
        for attr in ['image_embeds', '_image_embeds', 'embeds', '_last_image_embed', 
                     '_cached_embeds', '_vision_cache', '_image_cache']:
            if hasattr(chat_handler, attr):
                try:
                    embed = getattr(chat_handler, attr)
                    if embed is not None:
                        # If it's a tensor, explicitly delete
                        if hasattr(embed, 'cpu'):
                            del embed
                        setattr(chat_handler, attr, None)
                except Exception:
                    pass
        
        # Clear any cache dict
        if hasattr(chat_handler, '_cache'):
            try:
                if isinstance(chat_handler._cache, dict):
                    chat_handler._cache.clear()
            except Exception:
                pass
        
        # For mtmd handlers, clear the batch context if present
        if hasattr(chat_handler, '_mtmd_batch'):
            try:
                chat_handler._mtmd_batch = None
            except Exception:
                pass
        
        log.debug(_LOG_PREFIX, "Cleared chat handler image embeddings")
    
    # 4. Force garbage collection and VRAM cleanup
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


# ==============================================================================
# GENERATION - UNIFIED
# ==============================================================================

def generate_gguf(smart_lm_instance, model_type: str, image: Any, prompt: str,
                  max_tokens: int, temperature: float, top_p: float, top_k: int,
                  seed: int, repetition_penalty: float, frame_count: int = 8,
                  llm_mode: Optional[str] = None, vision_task: Optional[str] = None,
                  use_few_shot: bool = True,
                  min_p: float = 0.0, mirostat: int = 0,
                  mirostat_eta: float = 0.1, mirostat_tau: float = 5.0,
                  repeat_last_n: int = 64, stop_sequences=None,
                  **kwargs):
    # Unified GGUF generator.
    #
    # Args:
    #     smart_lm_instance: SmartLM instance with loaded model
    #     model_type: "vision" or "text"
    #     image: Image tensor (can be None for text-only)
    #     prompt: Input prompt
    #     max_tokens: Maximum tokens to generate
    #     temperature: Sampling temperature
    #     top_p: Top-p (nucleus) sampling
    #     top_k: Top-k sampling
    #     seed: Random seed (or None)
    #     repetition_penalty: Repetition penalty
    #     frame_count: Max frames for video (vision only)
    #     llm_mode: LLM mode key for few-shot examples (e.g., "tags_to_natural_language")
    #     vision_task: Task name for vision few-shot examples (e.g., "Detailed Description")
    #
    # Returns:
    #     Generated text string
    # Text-only with llm_mode: use LLM path for proper few-shot (even for vision models)
    if model_type == "text" or (image is None and llm_mode):
        return _generate_gguf_text(smart_lm_instance, prompt, max_tokens, 
                                   temperature, top_p, top_k, seed, 
                                   repetition_penalty, llm_mode, use_few_shot=use_few_shot,
                                   min_p=min_p, mirostat=mirostat,
                                   mirostat_eta=mirostat_eta, mirostat_tau=mirostat_tau,
                                   repeat_last_n=repeat_last_n, stop_sequences=stop_sequences)
    else:
        return _generate_gguf_vision(smart_lm_instance, image, prompt, max_tokens, 
                                     temperature, top_p, top_k, seed, 
                                     repetition_penalty, frame_count, vision_task,
                                     use_few_shot=use_few_shot,
                                     min_p=min_p, mirostat=mirostat,
                                     mirostat_eta=mirostat_eta, mirostat_tau=mirostat_tau,
                                     repeat_last_n=repeat_last_n, stop_sequences=stop_sequences,
                                     explicit_system_prompt=kwargs.get("system_prompt"))


def _generate_gguf_vision(smart_lm_instance, image: Any, prompt: str, max_tokens: int,
                          temperature: float, top_p: float, top_k: int, seed: Optional[int],
                          repetition_penalty: float = 1.0, frame_count: int = 8,
                          vision_task: Optional[str] = None, use_few_shot: bool = True,
                          min_p: float = 0.0, mirostat: int = 0,
                          mirostat_eta: float = 0.1, mirostat_tau: float = 5.0,
                          repeat_last_n: int = 64, stop_sequences=None,
                          explicit_system_prompt: Optional[str] = None) -> str:
    # Generate with vision GGUF model using llama-cpp-python.
    #
    # Handles:
    # - Single images
    # - Video frames (multiple frames)
    # - Text-only prompts (no image)
    #
    # Note: LLaVA and similar vision models don't use system messages effectively.
    # The instruction must be placed in the user message WITH the image.
    
    # Set seed if provided (already clamped by JavaScript)
    if seed is not None:
        smart_lm_instance.model.set_seed(seed)
    
    # Parse prompt format: "system instruction\n\nuser_message" or just "prompt"
    # For vision models, the instruction goes in user message (not system message)
    # Eclipse 3.5+ passes system + user separately via explicit_system_prompt.
    instruction = ""
    user_message = ""  # Optional additional context from user_prompt widget

    if explicit_system_prompt is not None:
        instruction = (explicit_system_prompt or "").strip()
        user_message = (prompt or "").strip()
    elif "\n\n" in prompt:
        # Split into instruction and user message
        instruction, user_message = prompt.split("\n\n", 1)
        instruction = instruction.strip()
        user_message = user_message.strip()
    else:
        # No separator - use entire prompt as instruction
        instruction = prompt.strip()
    
    log.debug(_LOG_PREFIX, f"GGUF Instruction: {instruction[:80]}...")
    log.debug(_LOG_PREFIX, f"GGUF User message: {user_message[:80] if user_message else '(empty)'}...")
    
    # For vision GGUF models (LLaVA, etc.), instruction must be in user message
    # System messages are not well supported by these models
    messages: list[dict[str, Any]] = []
    
    # Inject vision few-shot examples if available (text-only examples to guide output style)
    if vision_task and use_few_shot:
        try:
            from .config_templates import get_vision_few_shot_messages
            few_shot = get_vision_few_shot_messages(vision_task)
            if few_shot:
                messages.extend(few_shot)
                log.debug(_LOG_PREFIX, f"  Added {len(few_shot)} vision few-shot messages for task '{vision_task}'")
        except Exception as e:
            log.warning(_LOG_PREFIX, f"Failed to load vision few-shot for GGUF: {e}")
    
    # Build the full prompt text: instruction + optional user message
    full_prompt = instruction
    if user_message:
        full_prompt = f"{instruction}\n\n{user_message}"
    
    # Track image count for error messages
    image_count = 0
    
    # Add image if provided
    if image is not None:
        # Handle video (multiple frames) or single image
        if len(image.shape) == 4 and image.shape[0] > 1:
            # For video, llama.cpp expects images in the content array - limit to frame_count
            total_frames = image.shape[0]
            actual_frame_count = min(frame_count, total_frames)
            image_count = actual_frame_count
            image_content = []
            
            # Add instruction text FIRST, then all images
            image_content.append({"type": "text", "text": full_prompt})
            
            # Keep the LAST actual_frame_count frames — preserves recent
            # context for chained / video workflows.
            start = total_frames - actual_frame_count
            for i in range(start, total_frames):
                pil_image = tensor_to_pil(image[i])
                if pil_image is None:
                    continue
                # Convert to base64 data URL for llama.cpp
                buffered = BytesIO()
                try:
                    pil_image.save(buffered, format="PNG")
                    img_str = base64.b64encode(buffered.getvalue()).decode()
                    image_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_str}"}
                    })
                finally:
                    # Cleanup memory per frame
                    buffered.close()
                    del pil_image, buffered
            
            # Force garbage collection after processing all frames
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            
            messages.append({
                "role": "user",
                "content": image_content
            })
        else:
            # Single image - instruction + image in user message
            image_count = 1
            pil_image = tensor_to_pil(image)
            if pil_image is None:
                raise ValueError("Failed to convert image tensor to PIL Image")
            buffered = BytesIO()
            try:
                pil_image.save(buffered, format="PNG")
                img_str = base64.b64encode(buffered.getvalue()).decode()
                
                # User content: instruction text + image
                # Instruction MUST be with image for LLaVA to follow it
                user_content = [
                    {"type": "text", "text": full_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_str}"}}
                ]
                
                messages.append({
                    "role": "user",
                    "content": user_content
                })
            finally:
                # Cleanup memory
                buffered.close()
                del pil_image, buffered
    else:
        # Text-only prompt (no image)
        messages.append({
            "role": "user",
            "content": full_prompt
        })
    
    # Generate response
    try:
        _extras = {}
        if min_p and min_p > 0.0: _extras["min_p"] = min_p
        if mirostat and mirostat > 0:
            _extras["mirostat_mode"] = mirostat
            _extras["mirostat_eta"] = mirostat_eta
            _extras["mirostat_tau"] = mirostat_tau
        if repeat_last_n != 64: _extras["repeat_last_n"] = repeat_last_n
        if stop_sequences: _extras["stop"] = stop_sequences
        response = smart_lm_instance.model.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            repeat_penalty=repetition_penalty,
            stream=False,
            **_extras,
        )
    except ValueError as e:
        error_msg = str(e)
        # Check for context overflow error
        if "Prompt exceeds n_ctx" in error_msg or "exceeds" in error_msg.lower() and "ctx" in error_msg.lower():
            # Parse the actual values from error message like "Prompt exceeds n_ctx: 34613 > 32768"
            import re
            match = re.search(r'(\d+)\s*>\s*(\d+)', error_msg)
            if match:
                used_tokens = int(match.group(1))
                max_ctx = int(match.group(2))
                excess = used_tokens - max_ctx
                raise RuntimeError(
                    f"Context overflow: {used_tokens:,} tokens > {max_ctx:,} max context\n\n"
                    f"Each image uses ~2,000-3,000 tokens. You have {excess:,} tokens over the limit.\n\n"
                    f"Solutions:\n"
                    f"  • Reduce number of images/frames (try {max(1, image_count - (excess // 2500))} or fewer)\n"
                    f"  • Increase context_size widget (if your GPU has enough VRAM)\n"
                    f"  • Use a shorter prompt"
                ) from e
            else:
                raise RuntimeError(
                    f"Context overflow: Prompt too large for model's context window.\n\n"
                    f"Each image uses ~2,000-3,000 tokens.\n\n"
                    f"Solutions:\n"
                    f"  • Reduce number of images/frames\n"
                    f"  • Increase context_size widget\n"
                    f"  • Use a shorter prompt"
                ) from e
        raise
    
    # Extract text from response
    text = response['choices'][0]['message']['content']
    
    # Clear messages to free base64 image data from memory
    messages.clear()
    del response
    
    # Clean up common formatting artifacts
    text = text.strip()
    # Remove leading colon and space (e.g., ": The video shows..." -> "The video shows...")
    if text.startswith(": "):
        text = text[2:]
    elif text.startswith(":"):
        text = text[1:].lstrip()
    
    # Fix common UTF-8 encoding artifacts (mojibake)
    for wrong, correct in _ENCODING_FIXES.items():
        text = text.replace(wrong, correct)
    
    # Strip thinking tags from "Thinker" models (e.g., Qwen3-VL-Thinking, DeepSeek-R1)
    from .common import strip_thinking_tags, strip_llm_prefixes
    text, _ = strip_thinking_tags(text)
    text = strip_llm_prefixes(text)
    
    return text


def _generate_gguf_text(smart_lm_instance, prompt: str, max_tokens: int,
                        temperature: float, top_p: float, top_k: int, seed: Optional[int],
                        repetition_penalty: float = 1.0, llm_mode: Optional[str] = None,
                        use_few_shot: bool = True,
                        min_p: float = 0.0, mirostat: int = 0,
                        mirostat_eta: float = 0.1, mirostat_tau: float = 5.0,
                        repeat_last_n: int = 64, stop_sequences=None) -> str:
    # Generate with text-only GGUF model using llama-cpp-python.
    #
    # Uses create_chat_completion() for text generation.
    # Prompt format: "system instruction\n\nuser content" or just "prompt"
    # llm_mode: If provided, loads few-shot examples for the task
    
    # Set seed if provided
    if seed is not None:
        smart_lm_instance.model.set_seed(seed)
    
    # Parse prompt format: "system instruction\n\nuser_content" or just "prompt"
    system_message = "You are a helpful assistant."
    if isinstance(prompt, list):
        prompt = "\n".join(str(item) for item in prompt)
    elif not isinstance(prompt, str):
        prompt = str(prompt or "")
    user_content = prompt
    
    if llm_mode:
        # llm_mode path: system prompt handled via get_system_prompt() below.
        # Don't parse \n\n — content may have paragraph breaks that aren't system/user splits.
        pass
    elif "\n\n" in prompt:
        # Legacy path: caller prepended system\n\nuser format
        parts = prompt.split("\n\n", 1)
        system_message = parts[0].strip()
        user_content = parts[1].strip() if len(parts) > 1 else ""
        
        # If user_content is empty after split, use the whole prompt as user content
        if not user_content:
            user_content = prompt
            system_message = "You are a helpful assistant."
    
    # Load few-shot examples if llm_mode is provided
    few_shot_examples: list[Any] = []
    instruction_template = ""
    if llm_mode:
        from .config_templates import get_llm_few_shot_examples
        from .tasks import get_system_prompt
        LLM_FEW_SHOT_EXAMPLES = get_llm_few_shot_examples()
        
        config = LLM_FEW_SHOT_EXAMPLES.get(llm_mode)
        if config:
            display_name = config.get("display_name", llm_mode)
        else:
            # No few-shot entry — derive display name for correct system prompt lookup
            display_name = llm_mode.replace("_", " ").title()
            config = {"display_name": display_name, "instruction_template": "", "examples": []}
            log.debug(_LOG_PREFIX, f"No few-shot config for '{llm_mode}', using task system prompt for '{display_name}'")
        
        examples_val = config.get("examples")
        if use_few_shot and isinstance(examples_val, list):
            few_shot_examples = examples_val
        
        inst_val = config.get("instruction_template", "")
        if isinstance(inst_val, list):
            instruction_template = "\n".join(str(item) for item in inst_val)
        else:
            instruction_template = str(inst_val or "")
        # Override system_message with authoritative source if available
        auth_system = get_system_prompt(display_name)
        if auth_system:
            system_message = auth_system
        log.debug(_LOG_PREFIX, f"GGUF Text - Loaded {len(few_shot_examples)} few-shot examples for mode '{llm_mode}' (use_few_shot={use_few_shot})")
    
    log.debug(_LOG_PREFIX, f"GGUF Text - System ({len(system_message)} chars): {system_message[:120]}...")
    log.debug(_LOG_PREFIX, f"GGUF Text - User ({len(user_content)} chars): {user_content[:120]}...")
    log.debug(_LOG_PREFIX, f"GGUF Text - Params: max_tokens={max_tokens}, temp={temperature}, top_p={top_p}, top_k={top_k}")
    
    # Build messages for chat completion
    messages = [{"role": "system", "content": system_message}]
    
    # Add few-shot examples if available
    if few_shot_examples:
        messages.extend(few_shot_examples)
        log.debug(_LOG_PREFIX, f"GGUF Text - Added {len(few_shot_examples)} few-shot messages")
    
    # Build user request with instruction template if available
    if llm_mode and llm_mode != "direct_chat" and instruction_template:
        req = instruction_template.replace("{prompt}", user_content) if "{prompt}" in instruction_template else f"{instruction_template} {user_content}"
        messages.append({"role": "user", "content": req})
    else:
        messages.append({"role": "user", "content": user_content})
    
    # Generate response
    try:
        _extras = {}
        if min_p and min_p > 0.0: _extras["min_p"] = min_p
        if mirostat and mirostat > 0:
            _extras["mirostat_mode"] = mirostat
            _extras["mirostat_eta"] = mirostat_eta
            _extras["mirostat_tau"] = mirostat_tau
        if repeat_last_n != 64: _extras["repeat_last_n"] = repeat_last_n
        if stop_sequences: _extras["stop"] = stop_sequences
        response = smart_lm_instance.model.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            repeat_penalty=repetition_penalty,
            stream=False,
            **_extras,
        )
        
        # Safely extract content from response
        text = ""
        if response and 'choices' in response and len(response['choices']) > 0:
            choice = response['choices'][0]
            if 'message' in choice and 'content' in choice['message']:
                text = choice['message']['content'] or ""
            else:
                log.warning(_LOG_PREFIX, f"GGUF response missing message/content. Choice: {choice}")
        else:
            log.warning(_LOG_PREFIX, f"GGUF response missing choices. Response: {response}")
        
        text = text.strip() if text else ""
        
        # Log response details for debugging
        finish_reason = response.get('choices', [{}])[0].get('finish_reason', 'unknown') if response else 'no response'
        usage = response.get('usage', {}) if response else {}
        log.debug(_LOG_PREFIX, f"GGUF Text - Response: {len(text)} chars, finish_reason={finish_reason}, usage={usage}")
        
        # Log warning if empty but model ran
        if not text:
            log.warning(_LOG_PREFIX, f"GGUF returned empty response. finish_reason={finish_reason}, usage={usage}")
            log.debug(_LOG_PREFIX, f"GGUF Full response: {response}")
        
        # Fix common UTF-8 encoding artifacts (mojibake)
        for wrong, correct in _ENCODING_FIXES.items():
            text = text.replace(wrong, correct)
        
        # Debug: log text before strip_thinking_tags
        log.debug(_LOG_PREFIX, f"GGUF Text - Before strip_thinking_tags ({len(text)} chars): {text[:200]}...")
        
        # Strip thinking tags from "Thinker" models (e.g., Qwen3-VL-Thinking, DeepSeek-R1)
        from .common import strip_thinking_tags, strip_llm_prefixes
        text, _ = strip_thinking_tags(text)
        text = strip_llm_prefixes(text)
        
        # Debug: log text after strip_thinking_tags
        log.debug(_LOG_PREFIX, f"GGUF Text - After strip_thinking_tags ({len(text)} chars): {text[:200] if text else '(empty)'}...")
        
        return text
        
    except Exception as e:
        log.error(_LOG_PREFIX, f"GGUF text generation error: {e}")
        raise


# ==============================================================================
# UTILITY FUNCTIONS
# ==============================================================================

def get_gguf_info() -> dict:
    # Get information about llama-cpp-python availability and capabilities.
    #
    # Returns:
    #     Dict with 'available', 'version', 'gpu_offload' keys
    from .device import LLAMA_CPP_AVAILABLE
    
    info: dict[str, Any] = {
        "available": LLAMA_CPP_AVAILABLE,
        "version": None,
        "gpu_offload": False
    }
    
    if LLAMA_CPP_AVAILABLE:
        try:
            import llama_cpp #type: ignore
            info["version"] = getattr(llama_cpp, '__version__', 'unknown')
            
            # Check if GPU offloading is supported
            if hasattr(llama_cpp, 'llama_supports_gpu_offload'):
                try:
                    info["gpu_offload"] = llama_cpp.llama_supports_gpu_offload()
                except Exception:
                    pass
        except Exception:
            pass
    
    return info


def cleanup_chat_handler_vision(handler):
    # Cleanup vision model from a chat handler (mtmd for Qwen2.5-VL, CLIP for LLaVA).
    # CRITICAL: Must be called to free VRAM held by vision encoder (1-2GB+).
    # This function can be called standalone (for cache cleanup) or from cleanup_gguf_model.
    if handler is None:
        return
    
    # NEW VISION SYSTEM: mtmd context (Qwen2.5-VL uses this)
    if hasattr(handler, 'mtmd_ctx') and handler.mtmd_ctx is not None:
        log.debug(_LOG_PREFIX, "Freeing mtmd vision context (Qwen2.5-VL)...")
        try:
            # Try using exit_stack if available (context manager pattern)
            if hasattr(handler, '_exit_stack'):
                handler._exit_stack.close()
                log.debug(_LOG_PREFIX, "Closed _exit_stack")
            # Try direct mtmd_free call
            elif hasattr(handler, '_mtmd_cpp') and handler._mtmd_cpp is not None:
                if hasattr(handler._mtmd_cpp, 'mtmd_free'):
                    handler._mtmd_cpp.mtmd_free(handler.mtmd_ctx)
                    log.debug(_LOG_PREFIX, "Called mtmd_free()")
            handler.mtmd_ctx = None
        except Exception as e:
            log.debug(_LOG_PREFIX, f"mtmd cleanup error (may be ok): {e}")
    
    # Try llama_cpp's mtmd module directly
    try:
        from llama_cpp import mtmd_cpp #type: ignore
        if hasattr(mtmd_cpp, 'mtmd_free'):
            # Look for mtmd context in various locations
            for attr in ['mtmd_ctx', '_mtmd_ctx', 'ctx']:
                ctx = getattr(handler, attr, None)
                if ctx is not None:
                    log.debug(_LOG_PREFIX, f"Calling mtmd_free() on handler.{attr}")
                    try:
                        mtmd_cpp.mtmd_free(ctx)
                        setattr(handler, attr, None)
                    except Exception:
                        pass
    except ImportError:
        pass  # mtmd_cpp not available in this version
    
    # OLD VISION SYSTEM: CLIP context (legacy LLaVA)
    clip_attrs = ['clip_ctx', '_clip_ctx', 'clip_model', '_clip_model', '_llava_ctx']
    for attr in clip_attrs:
        if hasattr(handler, attr):
            clip_ctx = getattr(handler, attr, None)
            if clip_ctx is not None:
                log.debug(_LOG_PREFIX, f"Found CLIP context at handler.{attr}")
                try:
                    from llama_cpp import llava_cpp #type: ignore
                    if hasattr(llava_cpp, 'clip_free'):
                        log.debug(_LOG_PREFIX, "Calling clip_free() to release CLIP VRAM")
                        llava_cpp.clip_free(clip_ctx)
                except Exception as e:
                    log.debug(_LOG_PREFIX, f"clip_free failed: {e}")
                try:
                    setattr(handler, attr, None)
                except Exception:
                    pass
    
    # Call _clip_free method if available (some handlers have this)
    if hasattr(handler, '_clip_free') and callable(handler._clip_free):
        try:
            handler._clip_free()
            log.debug(_LOG_PREFIX, "Called handler._clip_free()")
        except Exception:
            pass
    
    # Clear any cached embeddings
    for attr in ['image_embeds', '_image_embeds', 'embeds', '_last_image_embed']:
        if hasattr(handler, attr):
            try:
                setattr(handler, attr, None)
            except Exception:
                pass
    if hasattr(handler, '_cache'):
        try:
            handler._cache.clear()
        except Exception:
            pass
    
    # For Qwen/Llava handlers that wrap another handler
    if hasattr(handler, '_llava_cpp') and handler._llava_cpp is not None:
        log.debug(_LOG_PREFIX, "Found _llava_cpp wrapper, cleaning up inner handler")
        cleanup_chat_handler_vision(handler._llava_cpp)
        try:
            handler._llava_cpp = None
        except Exception:
            pass
    
    # Some handlers have a 'handler' attribute pointing to inner handler
    if hasattr(handler, 'handler') and handler.handler is not None:
        cleanup_chat_handler_vision(handler.handler)
        try:
            handler.handler = None
        except Exception:
            pass


def cleanup_gguf_model(smart_lm_instance):
    # Properly cleanup GGUF model and free VRAM.
    #
    # CRITICAL: Must cleanup chat_handler separately as it holds CLIP model in VRAM.
    # The chat_handler (Qwen25VLChatHandler, Llava16ChatHandler, etc.) loads the
    # vision encoder (mmproj) which can use 1-2GB+ of VRAM.
    #
    # Qwen2.5-VL uses mtmd (multimodal) context - must call mtmd_free()
    # Legacy LLaVA uses clip context - must call clip_free()
    log.debug(_LOG_PREFIX, "Cleaning up GGUF model and freeing VRAM...")
    
    model = None
    
    # Get the actual model object (may be wrapped)
    if hasattr(smart_lm_instance, 'model'):
        model = smart_lm_instance.model
    else:
        model = smart_lm_instance
    
    # Cleanup chat handler stored on the model (our custom reference)
    if model is not None and hasattr(model, '_sml_chat_handler') and model._sml_chat_handler is not None:
        try:
            log.debug(_LOG_PREFIX, "Cleaning up GGUF chat_handler (vision encoder)")
            chat_handler = model._sml_chat_handler
            cleanup_chat_handler_vision(chat_handler)
            model._sml_chat_handler = None
            del chat_handler
        except Exception as e:
            log.warning(_LOG_PREFIX, f"Error cleaning up chat_handler: {e}")
    
    # Legacy: cleanup chat_handler_ref if present (old method)
    if hasattr(smart_lm_instance, 'chat_handler_ref') and smart_lm_instance.chat_handler_ref is not None:
        try:
            cleanup_chat_handler_vision(smart_lm_instance.chat_handler_ref)
            smart_lm_instance.chat_handler_ref = None
        except Exception:
            pass
    
    # Cleanup the Llama model's internal chat_handler reference
    if model is not None and hasattr(model, 'chat_handler') and model.chat_handler is not None:
        try:
            log.debug(_LOG_PREFIX, "Cleaning up Llama internal chat_handler")
            cleanup_chat_handler_vision(model.chat_handler)
            model.chat_handler = None
        except Exception:
            pass
    
    # Cleanup main Llama model - need to call close() to properly release resources
    # IMPORTANT: Never call __del__ directly or llama_free on _ctx - let close() handle it
    # Double-free errors cause immediate crashes
    if model is not None:
        try:
            # Only use close() method - it handles all internal cleanup safely
            if hasattr(model, 'close') and callable(model.close):
                log.debug(_LOG_PREFIX, "Calling model.close()")
                model.close()
            # After close(), the model is cleaned up - don't try to free _ctx again
            # Just null out references to help garbage collector
            if hasattr(model, '_ctx'):
                model._ctx = None
            if hasattr(model, '_model'):
                model._model = None
        except Exception as e:
            log.warning(_LOG_PREFIX, f"Error cleaning up Llama model: {e}")
    
    # Clear the reference in the wrapper
    if hasattr(smart_lm_instance, 'model'):
        try:
            smart_lm_instance.model = None
        except Exception:
            pass
    
    # Force garbage collection multiple times to ensure cleanup
    for _ in range(3):
        gc.collect()
    
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        # Also try ipc_collect for shared memory
        try:
            torch.cuda.ipc_collect()
        except Exception:
            pass
    
    log.msg(_LOG_PREFIX, "✓ GGUF cleanup complete")


# ==============================================================================
# EXPORTS
# ==============================================================================

__all__ = [
    # Detection
    'is_gguf_model',
    # Generation
    'generate_gguf',
    # Utilities
    'get_gguf_info',
    'cleanup_gguf_model',
    'clear_gguf_state_between_tasks',
    'cleanup_chat_handler_vision',
]
