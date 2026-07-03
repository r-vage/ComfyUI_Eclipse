# SmartLM Types - Core Enums and Type Definitions
#
# Single source of truth for all SmartLM enums and type-related functions.
# Used by loader_base.py and related modules.

from enum import Enum
from typing import List
import platform


# ============================================================================
# Platform Detection
# ============================================================================

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
IS_MACOS = platform.system() == "Darwin"


# ============================================================================
# Core Enums
# ============================================================================

class ModelType(Enum):
    # Model architecture types supported by SmartLM.
    QWENVL = "qwenvl"
    FLORENCE2 = "florence2"
    MISTRAL3 = "mistral3"  # Mistral3 vision-language models
    LLAVA = "llava"  # LLaVA vision-language models (high token usage ~2900/image)
    MLLAMA = "mllama"  # Llama 3.2 Vision (Meta's multimodal Llama)
    LLM = "llm"  # Text-only LLM (no vision)
    UNKNOWN = "unknown"


class ModelFamily(Enum):
    # Model families for UI categorization
    AUTO_DETECT = "Auto Detect"  # Auto-detect from config.json after download
    MISTRAL = "Mistral"
    QWEN = "Qwen"
    FLORENCE = "Florence"
    LLAVA = "LLaVA"  # LLaVA and Llama Vision models (auto-detects LLaVA vs Mllama at runtime)
    VLM = "VLM"  # Generic vision-language model (unknown family with vision)
    LLM_TEXT = "LLM (Text-Only)"
    WD14 = "WD14"  # SmilingWolf WD14 tagger models (ONNX)
    YOLO = "YOLO"  # Ultralytics YOLO detection models


class LoadingMethod(Enum):
    # Available model loading backends
    TRANSFORMERS = "Transformers"
    GGUF = "GGUF (llama-cpp-python)"
    WD14 = "WD14 Tagger"                # ONNX-based image tagger (SmilingWolf WD14 models)
    VLLM_DOCKER = "vLLM (Docker)"        # Cross-platform Docker-based vLLM
    VLLM_NATIVE = "vLLM (Native)"        # Linux only (pip install vllm)
    SGLANG_DOCKER = "SGLang (Docker)"    # Cross-platform Docker-based SGLang (alternative to vLLM)
    OLLAMA_DOCKER = "Ollama (Docker)"    # Docker-based Ollama (supports Mistral3 GGUF)
    LLAMACPP_DOCKER = "llama.cpp (Docker)"  # Docker-based llama.cpp server (supports Mistral3 GGUF)
    YOLO = "YOLO (Ultralytics)"               # Direct ultralytics inference (detection/segmentation)


# ============================================================================
# Transformers Version Detection
# ============================================================================

import transformers #type: ignore
_transformers_version: tuple[int, ...] = tuple(map(int, transformers.__version__.split('.')[:2])) if transformers.__version__[0].isdigit() else (4, 0)
if 'rc' in transformers.__version__.lower():
    _transformers_version = (5, 0)

# Florence-2 is now compatible with transformers v5 via accelerate-based manual loading
# (see florence2_wrapper.py _load_florence2_v5)
FLORENCE_COMPATIBLE = True

# Mistral3 (Ministral, Pixtral vision models) REQUIRES transformers v5+
# The Mistral3ForConditionalGeneration architecture doesn't exist in v4
# vLLM backend works with any transformers version
MISTRAL3_TRANSFORMERS_COMPATIBLE = _transformers_version >= (5, 0)


# ============================================================================
# Method Support Matrices
# ============================================================================

# Platform-specific vLLM method
_VLLM_METHOD = LoadingMethod.VLLM_DOCKER if IS_WINDOWS else LoadingMethod.VLLM_NATIVE
_SGLANG_METHOD = LoadingMethod.SGLANG_DOCKER  # SGLang is Docker-only on all platforms

# Method -> Families support matrix (what families does this method support?)
METHOD_SUPPORT_V2 = {
    LoadingMethod.TRANSFORMERS: [ModelFamily.MISTRAL, ModelFamily.QWEN, ModelFamily.FLORENCE, ModelFamily.LLAVA, ModelFamily.VLM, ModelFamily.LLM_TEXT],
    # Note: Mistral GGUF disabled in llama.cpp local - mistral3 architecture not yet supported by llama-cpp-python
    # LLaVA GGUF supported via Llava16ChatHandler with mmproj file (Mllama not supported in GGUF)
    LoadingMethod.GGUF: [ModelFamily.QWEN, ModelFamily.LLAVA, ModelFamily.LLM_TEXT],
    _VLLM_METHOD: [ModelFamily.MISTRAL, ModelFamily.QWEN, ModelFamily.VLM, ModelFamily.LLM_TEXT],
    _SGLANG_METHOD: [ModelFamily.MISTRAL, ModelFamily.QWEN, ModelFamily.VLM, ModelFamily.LLM_TEXT],  # SGLang supports same as vLLM
    # Docker backends with full Mistral3/GGUF support
    # LLaVA family includes LLaVA and Mllama (Llama 3.2 Vision) - auto-detected inside container
    # VLM = generic vision models (Phi-vision, Gemma3, etc.)
    LoadingMethod.OLLAMA_DOCKER: [ModelFamily.MISTRAL, ModelFamily.QWEN, ModelFamily.LLAVA, ModelFamily.VLM, ModelFamily.LLM_TEXT],
    LoadingMethod.LLAMACPP_DOCKER: [ModelFamily.MISTRAL, ModelFamily.QWEN, ModelFamily.LLAVA, ModelFamily.VLM, ModelFamily.LLM_TEXT],
    # WD14 Tagger: ONNX classifiers, no model families (has its own model registry)
    LoadingMethod.WD14: [],
    # YOLO: Ultralytics detection, self-contained (has its own model registry)
    LoadingMethod.YOLO: [],
}


# ============================================================================
# UI Helper Functions
# ============================================================================

def get_loading_method_list() -> List[str]:
    # Get list of all loading method names for UI dropdown.
    # Platform-specific filtering:
    # - vLLM (Docker): Available on ALL platforms with Docker (Windows, Linux, Mac)
    # - vLLM (Native): Only available on Linux (native pip install vllm)
    methods = []
    for method in LoadingMethod:
        # Filter platform-specific vLLM methods
        # Docker vLLM is available on all platforms
        # Native vLLM only works on Linux
        if method == LoadingMethod.VLLM_NATIVE and not IS_LINUX:
            continue  # Skip Native vLLM on non-Linux (Windows/Mac don't support native vLLM)
        methods.append(method.value)
    return methods


def get_supported_families(method: LoadingMethod) -> List[str]:
    # Get list of supported families for a loading method
    families = METHOD_SUPPORT_V2.get(method, [])
    return [family.value for family in families]


# ============================================================================
# Model Type Detection Functions
# ============================================================================

def get_model_family_from_name(model_name: str, has_vision: bool = False) -> ModelFamily:
    # Detect model family from model name/path by reading config.json if available.
    # When has_vision=True (user explicitly set), prefer vision-capable families
    # even when name-based detection would return LLM_TEXT (e.g. Ollama models
    # without "vl" in their name like qwen3.5-abliterated).
    import json
    from pathlib import Path
    
    model_path = Path(model_name)
    model_lower = model_name.lower()
    
    # WD14 early detection — these models have ONNX config.json that misleads LLM family detection
    if "wd14" in model_lower or "wd-" in model_lower or "tagger" in model_lower:
        return ModelFamily.WD14
    
    # GGUF files don't have config.json - skip to name-based detection
    is_gguf = model_lower.endswith('.gguf')
    
    # Try to read config.json for accurate detection (non-GGUF only)
    config_file = None
    if not is_gguf:
        if model_path.is_dir():
            config_file = model_path / "config.json"
        elif model_path.is_file():
            # Single file - check parent folder (but not for GGUF)
            config_file = model_path.parent / "config.json"
    
    if config_file and config_file.exists():
        try:
            config = json.loads(config_file.read_text(encoding='utf-8'))
            
            # Get model_type and architectures from config
            model_type = config.get("model_type", "").lower()
            architectures = [a.lower() for a in config.get("architectures", [])]
            
            # Check for vision capabilities
            has_vision = (
                "vision_config" in config or
                "image_size" in config or
                "visual" in str(config.get("architectures", [])).lower() or
                "vl" in model_type or
                "vision" in model_type or
                any("vision" in a or "vl" in a or "image" in a for a in architectures)
            )
            
            # Florence-2 detection
            if "florence" in model_type or any("florence" in a for a in architectures):
                return ModelFamily.FLORENCE
            
            # Qwen detection
            if "qwen" in model_type or any("qwen" in a for a in architectures):
                if has_vision or "vl" in model_type:
                    return ModelFamily.QWEN
                else:
                    return ModelFamily.LLM_TEXT
            
            # Mistral/Pixtral detection
            if "mistral" in model_type or "pixtral" in model_type or any("mistral" in a or "pixtral" in a for a in architectures):
                if has_vision or "pixtral" in model_type:
                    return ModelFamily.MISTRAL
                else:
                    return ModelFamily.LLM_TEXT
            
            # LLaVA detection (from config.json architecture)
            if "llava" in model_type or any("llava" in a for a in architectures):
                return ModelFamily.LLAVA
            
            # Mllama (Llama 3.2 Vision) detection - returns LLAVA family (auto-detected at runtime)
            # Mllama uses MllamaForConditionalGeneration architecture
            if "mllama" in model_type or any("mllama" in a for a in architectures):
                return ModelFamily.LLAVA  # Consolidated into LLAVA family
            
            # Generic Llama with vision capabilities -> LLAVA family (auto-detected at runtime)
            if ("llama" in model_type or any("llama" in a for a in architectures)) and has_vision:
                return ModelFamily.LLAVA  # Could be LLaVA or Mllama - detected at runtime
            
            # Generic vision model check
            if has_vision:
                # Try to match to known vision families by name
                if "qwen" in model_lower:
                    return ModelFamily.QWEN
                elif "mistral" in model_lower or "pixtral" in model_lower:
                    return ModelFamily.MISTRAL
                elif "florence" in model_lower:
                    return ModelFamily.FLORENCE
                else:
                    # Unknown vision model — route through generic VLM path
                    return ModelFamily.VLM
            
            # No vision - it's a text-only LLM
            return ModelFamily.LLM_TEXT
            
        except Exception:
            pass  # Fall through to name-based detection
    
    # Fallback: Name-based detection for GGUF or when config.json is not available
    if "llava" in model_lower:
        # LLaVA models (vision) - e.g., llava-hf/llava-1.5-7b-hf
        return ModelFamily.LLAVA
    elif "mllama" in model_lower or ("llama" in model_lower and ("3.2" in model_lower or "3-2" in model_lower) and "vision" in model_lower):
        # Mllama (Llama 3.2 Vision) - e.g., meta-llama/Llama-3.2-11B-Vision-Instruct
        # Returns LLAVA family - actual type detected at runtime
        return ModelFamily.LLAVA
    elif "mistral" in model_lower or "ministral" in model_lower:
        # Distinguish vision vs text-only Mistral models by name
        is_vision_model = (
            "ministral-3" in model_lower or 
            "mistral-small-3" in model_lower or
            "pixtral" in model_lower or
            has_vision
        )
        if is_vision_model:
            return ModelFamily.MISTRAL
        else:
            return ModelFamily.LLM_TEXT
    elif "qwen" in model_lower:
        if "vl" in model_lower or "vision" in model_lower or has_vision:
            return ModelFamily.QWEN
        else:
            return ModelFamily.LLM_TEXT
    elif "florence" in model_lower:
        return ModelFamily.FLORENCE
    elif "wd14" in model_lower or "wd-" in model_lower or "tagger" in model_lower:
        return ModelFamily.WD14
    else:
        # User explicitly set has_vision — unknown family but vision-capable
        if has_vision:
            return ModelFamily.VLM
        # If path doesn't exist locally (Docker/Ollama model name), we can't reliably
        # determine family from name alone. Return AUTO_DETECT so the template's saved
        # family takes precedence and self-correction doesn't override it.
        if not is_gguf and not model_path.is_dir() and not model_path.is_file():
            return ModelFamily.AUTO_DETECT
        return ModelFamily.LLM_TEXT


def detect_model_type(template_info: dict) -> ModelType:
    # Detect model type from template configuration
    model_type = template_info.get("model_type", "").lower()
    repo_id = template_info.get("repo_id", "").lower()
    local_path = template_info.get("local_path", "").lower()
    mmproj_path = template_info.get("mmproj_path", "")
    
    # Explicit LLM type
    if model_type == "llm":
        return ModelType.LLM
    
    # Explicit mllama type (Llama 3.2 Vision) - check BEFORE llava
    if model_type == "mllama":
        return ModelType.MLLAMA
    
    # Mllama detection from repo_id (with proper parentheses for precedence)
    if "mllama" in repo_id or ("llama-3.2" in repo_id and "vision" in repo_id):
        return ModelType.MLLAMA
    if "llama3.2" in repo_id and "vision" in repo_id:
        return ModelType.MLLAMA
    
    # Mistral3 detection (Ministral-3, Mistral-Small-3, Pixtral, etc.)
    if model_type == "mistral3":
        return ModelType.MISTRAL3
    if "mistral" in repo_id or "ministral" in repo_id or "pixtral" in repo_id:
        # Check if it's a vision model (Ministral-3, Mistral-Small-3, Pixtral) vs text-only
        if "ministral-3" in repo_id or "ministral3" in repo_id or "mistral-small-3" in repo_id or "pixtral" in repo_id:
            return ModelType.MISTRAL3
        # Text-only Mistral - return LLM
        return ModelType.LLM
    
    # QwenVL detection - check for vision indicators
    if model_type == "qwenvl":
        return ModelType.QWENVL
    if "qwen" in repo_id:
        # Only return QWENVL if it's a vision model
        if "vl" in repo_id or "vision" in repo_id:
            return ModelType.QWENVL
        # Text-only Qwen - return LLM
        return ModelType.LLM
    
    # Florence-2 detection
    if model_type == "florence2" or "florence" in repo_id:
        return ModelType.FLORENCE2
    
    # LLaVA detection (after mllama check to avoid false matches)
    if model_type == "llava" or "llava" in repo_id:
        return ModelType.LLAVA
    
    # Text-only GGUF detection: GGUF file without mmproj
    if local_path.endswith(".gguf") and not mmproj_path:
        return ModelType.LLM
    
    return ModelType.UNKNOWN


def detect_vlm_model_type(config_data: dict) -> ModelType:
    # Detect ModelType from a model's config.json data.
    #
    # Uses model_type and architectures fields to determine the correct
    # ModelType for generation routing. Used by the unified VLM loader
    # when loading via Transformers backend.
    #
    # Args:
    #     config_data: Parsed config.json dict from the model directory
    #
    # Returns:
    #     ModelType enum value (QWENVL, MISTRAL3, LLAVA, MLLAMA, FLORENCE2, or QWENVL as fallback)
    from .logger import log

    model_type = config_data.get("model_type", "").lower()
    architectures = config_data.get("architectures", [])
    arch_str = architectures[0].lower() if architectures else ""

    if "qwen" in model_type or "qwen" in arch_str:
        return ModelType.QWENVL
    elif any(k in model_type for k in ("mistral", "ministral", "pixtral")) or \
         any(k in arch_str for k in ("mistral", "ministral", "pixtral")):
        return ModelType.MISTRAL3
    elif "mllama" in model_type or "mllama" in arch_str:
        return ModelType.MLLAMA
    elif "llava" in model_type or "llava" in arch_str:
        return ModelType.LLAVA
    elif "florence" in model_type or "florence" in arch_str:
        return ModelType.FLORENCE2
    else:
        # Unknown VLM architecture - QWENVL uses the most generic loading pattern
        log.debug("Types", f"  Unknown VLM type (model_type='{model_type}', arch='{arch_str}'), defaulting to QWENVL")
        return ModelType.QWENVL


def is_mistral3_vision_model(model_path: str) -> bool:
    # Check if a model is a Mistral3/Pixtral vision model.
    #
    # These models have specific limitations:
    # - Don't support BitsAndBytes quantization in vLLM
    # - Require transformers v5+ for Transformers backend
    # - Need --load-format mistral with consolidated.safetensors
    #
    # Args:
    #     model_path: Path to the model directory or model name
    #
    # Returns:
    #     True if model is a Mistral3/Pixtral vision model
    import json
    from pathlib import Path
    
    model_dir = Path(model_path)
    model_lower = model_path.lower()
    
    # Try config.json first (most accurate)
    config_file = model_dir / "config.json" if model_dir.is_dir() else None
    
    if config_file and config_file.exists():
        try:
            config = json.loads(config_file.read_text(encoding='utf-8'))
            
            # Check architecture and model_type
            architectures = config.get("architectures", [])
            model_type = config.get("model_type", "")
            vision_config = config.get("vision_config", {})
            
            # Mistral3ForConditionalGeneration is the vision model architecture
            if "Mistral3ForConditionalGeneration" in architectures:
                return True
            
            # Check for mistral3 model type with vision config
            if model_type == "mistral3" and vision_config:
                return True
            
            # Check for pixtral vision encoder
            if vision_config.get("model_type") == "pixtral":
                return True
                
        except Exception:
            pass  # Fall through to name-based detection
    
    # Fallback: Name-based detection
    # Ministral-3, Mistral-Small-3, and Pixtral are vision models
    if any(pattern in model_lower for pattern in ["ministral-3", "ministral3", "mistral-small-3", "pixtral"]):
        return True
    
    return False
