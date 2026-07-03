# SmartLM Templates - Configuration Management
#
# Handles config and context operations:
# - Config value loading/saving
# - TemplateContext for loader pipelines
# - Prompt configuration loading
#
# Used by the SmartLM core loader and related modules (shared utilities).

import json
from pathlib import Path
from typing import Dict, Any, Optional

from .logger import log

_LOG_PREFIX = ""


# ============================================================================
# Template Context Class
# ============================================================================

class TemplateContext:
    # Context object for template-related operations.
    #
    # Holds all template/widget values in one place, avoiding the need to
    # pass many individual parameters through call chains.
    #
    # Thread-safe: Each execution creates its own context instance.
    #
    # Usage:
    #     # From template dict
    #     ctx = TemplateContext.from_template_info(template_info)
    #     
    #     # From widget values
    #     ctx = TemplateContext.from_widgets(
    #         model_family="LLaVA",
    #         model_type="llava",
    #         loading_method="Ollama (Docker)",
    #     )
    #     
    #     # Update values
    #     ctx.update(has_vision=True, ollama_model="local_llava:latest")
    
    def __init__(self):
        # Core template fields
        self.model_family: str = ""
        self.model_type: str = ""
        self.loading_method: str = ""
        
        # Model source
        self.repo_id: str = ""
        self.local_path: str = ""
        self.ollama_model: str = ""
        self.model_source: str = ""  # "huggingface", "local", "ollama"
        
        # Vision support
        self.mmproj_path: str = ""
        self.mmproj_url: str = ""
        self.has_vision: bool = False
        
        # Configuration
        self.quantization: str = "auto"
        self.attention_mode: str = "auto"
        self.context_size: int = 8192
        self.max_tokens: int = 1024
        
        # Metadata
        self.original_filename: str = ""
        self.quantized: bool = False
        self.default_task: str = ""
        self.default_text_input: str = ""
        
    @classmethod
    def from_template_info(cls, template_info: Dict[str, Any]) -> "TemplateContext":
        # Create context from a template_info dictionary
        ctx = cls()
        if not template_info:
            return ctx
        
        # Map dict keys to context attributes
        ctx.model_family = template_info.get("model_family", "")
        ctx.model_type = template_info.get("model_type", "")
        ctx.loading_method = template_info.get("loading_method", "")
        ctx.repo_id = template_info.get("repo_id", "")
        ctx.local_path = template_info.get("local_path", "")
        ctx.ollama_model = template_info.get("ollama_model", "")
        ctx.model_source = template_info.get("model_source", "")
        ctx.mmproj_path = template_info.get("mmproj_path", "")
        ctx.mmproj_url = template_info.get("mmproj_url", "")
        ctx.quantization = template_info.get("quantization", "auto")
        ctx.attention_mode = template_info.get("attention_mode", "auto")
        ctx.context_size = template_info.get("context_size", 8192)
        ctx.max_tokens = template_info.get("max_tokens", 1024)
        ctx.quantized = template_info.get("quantized", False)
        ctx.default_task = template_info.get("default_task", "")
        ctx.default_text_input = template_info.get("default_text_input", "")
        ctx.has_vision = template_info.get("has_vision", False)
        
        return ctx
    
    @classmethod
    def from_widgets(
        cls,
        model_family: str = "",
        model_type: str = "",
        loading_method: str = "",
        **kwargs,
    ) -> "TemplateContext":
        # Create context from widget values
        ctx = cls()
        ctx.model_family = model_family
        ctx.model_type = model_type
        ctx.loading_method = loading_method
        
        # Apply any additional kwargs
        for key, value in kwargs.items():
            if hasattr(ctx, key):
                setattr(ctx, key, value)
        
        return ctx
    
    def update(self, **kwargs) -> "TemplateContext":
        # Update context values. Returns self for chaining
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        return self
    
    def to_dict(self) -> Dict[str, Any]:
        # Convert context to dictionary (for compatibility)
        return {
            "model_family": self.model_family,
            "model_type": self.model_type,
            "loading_method": self.loading_method,
            "repo_id": self.repo_id,
            "local_path": self.local_path,
            "ollama_model": self.ollama_model,
            "model_source": self.model_source,
            "mmproj_path": self.mmproj_path,
            "mmproj_url": self.mmproj_url,
            "quantization": self.quantization,
            "attention_mode": self.attention_mode,
            "context_size": self.context_size,
            "max_tokens": self.max_tokens,
            "quantized": self.quantized,
            "default_task": self.default_task,
            "default_text_input": self.default_text_input,
            "has_vision": self.has_vision,
        }

# ============================================================================
# Directory Constants and Paths
# ============================================================================

NODE_DIR = Path(__file__).parent.parent.parent

# Config directories
REPO_CONFIG_DIR = NODE_DIR / "config"

# Config cache for get_config_value (avoids repeated file I/O)
_config_cache: Dict[str, Any] = {}
_config_cache_time: float = 0.0
_CONFIG_CACHE_TTL: float = 5.0  # Cache for 5 seconds

def get_config_value(key: str, default: Any = None) -> Any:
    # Get a configuration value from config.json (cached)
    import time
    global _config_cache, _config_cache_time
    
    current_time = time.time()
    
    # Check if cache is valid
    if current_time - _config_cache_time < _CONFIG_CACHE_TTL and _config_cache:
        return _config_cache.get(key, default)
    
    # Reload config from file
    config_path = NODE_DIR / "config.json"
    try:
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                _config_cache = json.load(f)
                _config_cache_time = current_time
                return _config_cache.get(key, default)
    except Exception:
        pass
    return default


def invalidate_config_cache():
    # Invalidate config cache (call after updating config)
    global _config_cache_time
    _config_cache_time = 0.0


def update_config_value(key: str, value, nested_key: Optional[str] = None) -> bool:
    # Update a configuration value in config.json.
    #
    # Args:
    #     key: Top-level key in config
    #     value: Value to set (or dict to merge if nested_key is None)
    #     nested_key: Optional nested key within the top-level dict
    #
    # Returns:
    #     bool: True if successful
    invalidate_config_cache()  # Clear cache before update
    config_path = NODE_DIR / "config.json"
    try:
        # Load existing config
        config = {}
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        
        # Update value
        if nested_key:
            if key not in config:
                config[key] = {}
            if not isinstance(config[key], dict):
                config[key] = {}
            config[key][nested_key] = value
        else:
            config[key] = value
        
        # Save config
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
        
        return True
    except Exception as e:
        log.error(_LOG_PREFIX, f"Config failed to update {key}: {e}")
        return False


def ensure_config_exists() -> bool:
    # Ensure config.json exists.
    # If missing, copies from config.json.example (shipped with the repo).
    # This allows users to edit their config without git conflicts on pull/update.
    #
    # Returns:
    #     bool: True if file was created, False if it already existed
    import shutil

    # Migrate: rename old smartlml_config.json → config.json (one-time)
    old_config_path = NODE_DIR / "smartlml_config.json"
    config_path = NODE_DIR / "config.json"
    if old_config_path.exists() and not config_path.exists():
        old_config_path.rename(config_path)
        log.msg(_LOG_PREFIX, "Migrated smartlml_config.json → config.json")

    if not config_path.exists():
        example_path = NODE_DIR / "config.json.example"
        try:
            if example_path.exists():
                shutil.copy2(example_path, config_path)
                log.msg(_LOG_PREFIX, "Created config.json from .example template")
            else:
                # Fallback: create minimal defaults if .example is missing
                default_config = {
                    "_comments": {
                        "description": "SML ComfyUI Node Configuration",
                        "log_level_options": "error | warning | info | debug",
                        "llm_models_path": "Relative path from ComfyUI models folder (e.g., 'LLM').",
                        "llm_models_absolute_path": "REQUIRED FOR DOCKER: Full absolute path to LLM models folder."
                    },
                    "log_level": "warning",
                    "llm_models_path": "LLM",
                    "llm_models_absolute_path": "",
                    "retry_download_attempts": 2,
                    "hf_token": "",
                    "few_shot_training_file": "llm_few_shot_training_nsfw.json"
                }
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, indent=2)
                log.msg(_LOG_PREFIX, "Created default config.json (no .example found)")
            return True
        except Exception as e:
            log.error(_LOG_PREFIX, f"Failed to create config.json: {e}")
            return False
    return False


def get_llm_models_path() -> Path:
    # Get the LLM models directory path from config (for Python file scanning).
    #
    # The path can be:
    # - Relative: Will be joined with ComfyUI's models_dir (e.g., "LLM" -> models/LLM)
    # - Absolute: Will be used directly (e.g., "D:/MyModels/LLM")
    #
    # Returns:
    #     Path to LLM models directory
    import folder_paths #type: ignore
    
    llm_path = get_config_value("llm_models_path", "LLM")
    path = Path(llm_path)
    
    # If absolute path, use directly
    if path.is_absolute():
        return path
    
    # Otherwise, join with ComfyUI models directory
    return Path(folder_paths.models_dir) / llm_path


def get_llm_models_absolute_path() -> str:
    # Get the absolute path to LLM models directory (required for Docker).
    #
    # Docker containers need the complete absolute path for volume mounts.
    # This value MUST be configured in config.json for Docker backends to work.
    #
    # Returns:
    #     Absolute path string to LLM models directory
    #
    # Raises:
    #     ValueError: If path is not configured or is empty
    abs_path = get_config_value("llm_models_absolute_path", "")
    
    if not abs_path:
        # Try to derive from relative path as fallback
        derived = get_llm_models_path()
        if derived.exists():
            return str(derived.resolve())
        raise ValueError(
            "llm_models_absolute_path not configured in config.json.\n"
            "Docker backends require the full absolute path to your LLM models folder.\n"
            "Example: \"D:/AI/ComfyUI/models/LLM\" or \"/home/user/models/LLM\""
        )
    
    return abs_path


def initialize_llm_paths() -> bool:
    # Initialize LLM model paths in config.json at startup.
    #
    # This function:
    # 1. Detects the correct LLM models folder based on llm_models_path config
    # 2. Creates the folder if it doesn't exist (for model downloads)
    # 3. Updates llm_models_absolute_path to always match the derived path
    #
    # The absolute path is ALWAYS derived from llm_models_path + ComfyUI models dir.
    # If user changes llm_models_path (e.g., "LLM" -> "MyModels"), the absolute path
    # will be updated accordingly on next startup.
    #
    # Called early in __init__.py before any other SmartLM operations.
    #
    # Returns:
    #     bool: True if paths were updated, False if no update was needed
    import folder_paths #type: ignore
    
    # Get current values from config (fallback to "LLM" if empty or not set)
    current_relative = get_config_value("llm_models_path", "LLM")
    if not current_relative or not current_relative.strip():
        current_relative = "LLM"
        # Also update the config to have the default value
        update_config_value("llm_models_path", "LLM")
        log.debug(_LOG_PREFIX, "llm_models_path was empty, using default 'LLM'")
    
    current_absolute = get_config_value("llm_models_absolute_path", "")
    
    # Derive the expected absolute path from ComfyUI's models directory
    relative_path = Path(current_relative)
    if relative_path.is_absolute():
        # User has set an absolute path as llm_models_path, use it directly
        expected_path = relative_path
    else:
        # Normal case: relative path from models folder (e.g., "LLM")
        expected_path = Path(folder_paths.models_dir) / current_relative
    
    expected_absolute = str(expected_path.resolve())
    
    # Create the directory if it doesn't exist (needed for model downloads)
    if not expected_path.exists():
        try:
            expected_path.mkdir(parents=True, exist_ok=True)
            log.msg(_LOG_PREFIX, f"Created LLM models folder: {expected_absolute}")
        except Exception as e:
            log.warning(_LOG_PREFIX, f"Could not create LLM models folder '{expected_absolute}': {e}")
    
    # Normalize paths for comparison (handle different separators)
    def normalize_path(p: str) -> str:
        return str(Path(p).resolve()) if p else ""
    
    current_absolute_normalized = normalize_path(current_absolute)
    expected_absolute_normalized = normalize_path(expected_absolute)
    
    # Check if update is needed
    if current_absolute_normalized == expected_absolute_normalized:
        log.debug(_LOG_PREFIX, f"LLM paths already configured correctly: {expected_absolute}")
        return False
    
    # Update the absolute path in config (always sync with derived path)
    success = update_config_value("llm_models_absolute_path", expected_absolute)
    
    if success:
        log.msg(_LOG_PREFIX, f"Updated LLM models path: {expected_absolute}")
    else:
        log.warning(_LOG_PREFIX, f"Failed to update LLM models path in config")
    
    return success


# ============================================================================
# Auto Template Generation (for imported models)
# ============================================================================

def infer_model_family_from_name(model_name: str) -> str:
    # Infer model family from model name for template generation.
    #
    # Used when auto-generating templates for locally imported models
    # (e.g., GGUF files imported into Ollama).
    #
    # Args:
    #     model_name: The model name or filename
    #
    # Returns:
    #     Model family string for template display
    name_lower = model_name.lower()
    
    if "mistral" in name_lower or "ministral" in name_lower:
        return "Mistral"
    elif "qwen" in name_lower:
        return "Qwen"
    elif "llava" in name_lower or "llama" in name_lower:
        return "LLaVA"
    elif "florence" in name_lower:
        return "Florence"
    elif "phi" in name_lower:
        return "Phi"
    elif "gemma" in name_lower:
        return "Gemma"
    elif "deepseek" in name_lower:
        return "DeepSeek"
    else:
        return "LLM (Text-Only)"


def infer_model_type_from_name(model_name: str, model_path: str | None = None) -> str:
    # Infer model type from model name and optionally from config.json.
    #
    # This determines the prompt format/chat template to use.
    # When model_path is provided and contains config.json, that takes priority.
    #
    # Args:
    #     model_name: The model name or filename
    #     model_path: Optional path to model directory (for config.json detection)
    #
    # Returns:
    #     Model type string for internal processing
    from pathlib import Path
    
    # Try config.json detection first if model_path provided
    if model_path:
        model_dir = Path(model_path)
        config_file = model_dir / "config.json" if model_dir.is_dir() else None
        
        if config_file and config_file.exists():
            try:
                config = json.loads(config_file.read_text(encoding='utf-8'))
                
                # Get model_type and architectures from config
                cfg_model_type = config.get("model_type", "").lower()
                architectures = [a.lower() for a in config.get("architectures", [])]
                
                # Mllama detection (Llama 3.2 Vision)
                if "mllama" in cfg_model_type or any("mllama" in a for a in architectures):
                    return "mllama"
                
                # LLaVA detection
                if "llava" in cfg_model_type or any("llava" in a for a in architectures):
                    return "llava"
                
                # Florence-2 detection
                if "florence" in cfg_model_type or any("florence" in a for a in architectures):
                    return "florence2"
                
                # Qwen detection
                if "qwen" in cfg_model_type or any("qwen" in a for a in architectures):
                    has_vision = "vl" in cfg_model_type or "vision_config" in config
                    return "qwenvl" if has_vision else "qwen"
                
                # Mistral/Pixtral detection
                if "mistral" in cfg_model_type or "pixtral" in cfg_model_type or any("mistral" in a or "pixtral" in a for a in architectures):
                    has_vision = "pixtral" in cfg_model_type or "vision_config" in config
                    return "mistral3" if has_vision else "mistral"
                
                # Phi detection
                if "phi" in cfg_model_type or any("phi" in a for a in architectures):
                    return "phi"
                
                # Gemma detection
                if "gemma" in cfg_model_type or any("gemma" in a for a in architectures):
                    return "gemma"
                
                # DeepSeek detection
                if "deepseek" in cfg_model_type or any("deepseek" in a for a in architectures):
                    return "deepseek"
                
            except Exception:
                pass  # Fall through to name-based detection
    
    # Fallback: Name-based detection
    name_lower = model_name.lower()
    
    if "ministral-3" in name_lower or "ministral3" in name_lower or "pixtral" in name_lower:
        return "mistral3"
    elif "mistral" in name_lower:
        return "mistral"
    elif "florence" in name_lower:
        return "florence2"
    elif "qwen" in name_lower and ("vl" in name_lower or "vision" in name_lower):
        return "qwenvl"
    elif "qwen" in name_lower:
        return "qwen"
    # Check for Llama 3.2 Vision (Mllama) BEFORE generic llava check
    elif ("llama-3.2" in name_lower or "llama3.2" in name_lower or "llama-3-2" in name_lower) and "vision" in name_lower:
        return "mllama"
    elif "mllama" in name_lower:
        return "mllama"
    elif "llava" in name_lower:
        return "llava"
    elif "phi" in name_lower:
        return "phi"
    elif "gemma" in name_lower:
        return "gemma"
    elif "deepseek" in name_lower:
        return "deepseek"
    else:
        return "llm"


# ============================================================================
# Few-Shot Example Loading
# ============================================================================

LLM_FEW_SHOT_EXAMPLES: Dict[str, Any] = {}
_few_shot_mtime: float = 0.0
_few_shot_path: Optional[Path] = None


def get_llm_few_shot_config_path() -> Path:
    # Get path to LLM few-shot config file.
    # Reads the filename from config.json "few_shot_training_file" key.
    # Falls back to "llm_few_shot_training.json" if not set or invalid.
    default_name = "llm_few_shot_training.json"
    configured_name = get_config_value("few_shot_training_file", default_name)
    
    # Sanitize: strip path separators to prevent path traversal
    if not configured_name or not isinstance(configured_name, str):
        configured_name = default_name
    configured_name = configured_name.strip()
    safe_name = Path(configured_name).name  # strip any directory components
    
    # Must end with .json
    if not safe_name.endswith(".json"):
        log.warning(_LOG_PREFIX, f"Invalid few-shot file name '{safe_name}', must end with .json. Using default.")
        safe_name = default_name
    
    target = REPO_CONFIG_DIR / safe_name
    if not target.exists():
        log.warning(_LOG_PREFIX, f"Few-shot file '{safe_name}' not found in {REPO_CONFIG_DIR}, falling back to default")
        target = REPO_CONFIG_DIR / default_name
    
    return target


def _load_few_shot_configs():
    # Load LLM few-shot training examples and show transformers version.
    global LLM_FEW_SHOT_EXAMPLES, _few_shot_mtime, _few_shot_path

    llm_config_path = get_llm_few_shot_config_path()
    _few_shot_path = llm_config_path
    try:
        with open(llm_config_path, 'r', encoding='utf-8') as f:
            loaded_data = json.load(f)
        # Normalize all entries: the JSON stores everything as list-of-pairs
        # [["key", val], ...] at every level.  Convert recursively so callers
        # can safely call .get() and Ollama/vLLM receive plain dicts.
        def _normalize(obj: Any) -> Any:
            if isinstance(obj, list) and obj and isinstance(obj[0], list) and len(obj[0]) == 2:
                # Looks like list-of-pairs → dict, then recurse into values
                try:
                    return {pair[0]: _normalize(pair[1]) for pair in obj}
                except (TypeError, IndexError):
                    pass
            if isinstance(obj, list):
                return [_normalize(item) for item in obj]
            return obj

        normalized: Dict[str, Any] = {}
        for k, v in loaded_data.items():
            if k.startswith("_"):
                # Metadata keys (e.g. _description) — keep as-is
                normalized[k] = v
            else:
                normalized[k] = _normalize(v)
        LLM_FEW_SHOT_EXAMPLES.clear()
        LLM_FEW_SHOT_EXAMPLES.update(normalized)
        try:
            _few_shot_mtime = llm_config_path.stat().st_mtime
        except OSError:
            _few_shot_mtime = 0.0
        log.msg(_LOG_PREFIX, f"Loaded LLM few-shot training examples ({len(loaded_data)} modes) from {llm_config_path.name}")
    except Exception as exc:
        log.warning(_LOG_PREFIX, f"LLM few-shot config load failed: {exc}")
        LLM_FEW_SHOT_EXAMPLES.clear()
        LLM_FEW_SHOT_EXAMPLES.update({
            "prompt_generation": {
                "system_prompt": "You are a helpful assistant.",
                "examples": []
            },
            "direct_chat": {
                "system_prompt": "You are a helpful assistant. Try your best to give the best response possible to the user.",
                "examples": []
            }
        })

    # Show transformers version
    try:
        import transformers #type: ignore
        log.msg(_LOG_PREFIX, f"Transformers version: {transformers.__version__}")
    except Exception:
        log.warning(_LOG_PREFIX, "Transformers not found")


def get_llm_few_shot_examples() -> Dict[str, Any]:
    # Get loaded LLM few-shot examples.
    # Auto-reloads if the source JSON file has changed on disk OR the
    # configured `few_shot_training_file` (NSFW vs. SFW switch) was changed.
    global _few_shot_mtime, _few_shot_path
    try:
        current_path = get_llm_few_shot_config_path()
        if _few_shot_path != current_path:
            # Configured file changed (e.g. SFW <-> NSFW toggle) → full reload
            _load_few_shot_configs()
        else:
            try:
                mtime = current_path.stat().st_mtime
            except OSError:
                mtime = 0.0
            if mtime and mtime != _few_shot_mtime:
                _load_few_shot_configs()
    except Exception:
        # Never fail callers because of an mtime check
        pass
    return LLM_FEW_SHOT_EXAMPLES


def reload_few_shot_configs() -> Dict[str, Any]:
    # Force-reload few-shot training examples from disk.
    # Returns a small status dict for endpoint reporting.
    _load_few_shot_configs()
    return {
        "modes": len(LLM_FEW_SHOT_EXAMPLES),
        "path": str(_few_shot_path) if _few_shot_path else "",
    }


def get_vision_few_shot_messages(task_name: str) -> list:
    # Get text-only few-shot example messages for a vision task.
    #
    # These are user/assistant text pairs that teach the model the desired
    # output style (no "illustration" prefixes, uncensored descriptions, etc.)
    # without requiring example images. Injected before the actual image message.
    #
    # Args:
    #     task_name: Display name like "Detailed Description" or key like "detailed_description"
    #
    # Returns:
    #     List of message dicts (user/assistant pairs), empty if no examples found
    if not task_name:
        return []

    # Normalize to key format: "Detailed Description" -> "detailed_description"
    task_key = task_name.lower().replace(" ", "_").replace("&", "&")

    config = LLM_FEW_SHOT_EXAMPLES.get(task_key)
    if not config:
        return []

    # All task entries use the list-of-pairs format: [["key", value], ...].
    # Convert to dict for .get() access.
    if isinstance(config, list):
        try:
            config = dict(config)
        except (TypeError, ValueError):
            return []

    examples = config.get("examples", [])
    if not examples:
        return []

    # Normalize content to list-of-dicts for VLM chat templates that iterate
    # content expecting {"type": "text", "text": ...} entries (Qwen3-VL,
    # Qwen3.5-VL, etc.). Mixing string content with list content in the same
    # message thread raises "string indices must be integers" inside the
    # jinja template. Real user messages always use list-of-dicts because
    # they need a {"type": "image"} entry alongside text.
    normalized = []
    for ex in examples:
        # Each example may itself be a list-of-pairs [["role", "user"], ["content", "..."]].
        # Convert to dict before accessing keys.
        if isinstance(ex, list):
            try:
                ex = dict(ex)
            except (TypeError, ValueError):
                continue
        if not isinstance(ex, dict):
            continue
        content = ex.get("content")
        if isinstance(content, str):
            normalized.append({**ex, "content": [{"type": "text", "text": content}]})
        else:
            normalized.append(ex)

    log.debug(_LOG_PREFIX, f"Found {len(normalized)} vision few-shot examples for '{task_key}'")
    return normalized


# ============================================================================
# Auto-load configs on import
# ============================================================================
_load_few_shot_configs()
