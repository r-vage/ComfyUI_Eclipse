import os
import re
import random
import shutil
import time
import torch  # type: ignore
import json
import numpy as np  # type: ignore
import folder_paths  # type: ignore

from pathlib import Path
from typing import Optional, Final, Dict, List, Union, Any
from datetime import datetime
from PIL import Image  # type: ignore
from PIL.PngImagePlugin import PngInfo  # type: ignore

from ..core import CATEGORY
from ..core.logger import log
from ..core.model_integrity import sha256_for, read_expected
from comfy_api.latest import io  # type: ignore

_LOG_PREFIX = "Save Images v2"

RE_LORA_TAG = re.compile(r'<lora:([^>:]+)', re.IGNORECASE)

ALLOWED_EXT = ('.jpeg', '.jpg', '.png', '.tiff', '.gif', '.bmp', '.webp')

# Global variables to store pipe values for filename placeholders
global_values = {
    'model': '',
    'basemodel': '',
    'seed': '',
    'sampler_name': '',
    'scheduler': '',
    'steps': '',
    'cfg': '',
    'denoise': '',
    'clip_skip': ''
}

# ─── Filename placeholder processing ───────────────────────────────

class FilenameProcessor:
    def __init__(self):
        self.placeholders = {
            '%today': self._get_date,
            '%date': self._get_date,
            '%time': self._get_time,
            '%Y': lambda: datetime.now().strftime('%Y'),
            '%y': lambda: datetime.now().strftime('%y'),
            '%m': lambda: datetime.now().strftime('%m'),
            '%M': lambda: datetime.now().strftime('%m'),
            '%d': lambda: datetime.now().strftime('%d'),
            '%D': lambda: datetime.now().strftime('%d'),
            '%H': lambda: datetime.now().strftime('%H'),
            '%S': lambda: datetime.now().strftime('%S'),
            '%basemodel': lambda: global_values.get('basemodel', ''),
            '%model': lambda: global_values.get('model', ''),
            '%seed': lambda: global_values.get('seed', ''),
            '%sampler_name': lambda: global_values.get('sampler_name', ''),
            '%scheduler': lambda: global_values.get('scheduler', ''),
            '%steps': lambda: global_values.get('steps', ''),
            '%cfg': lambda: global_values.get('cfg', ''),
            '%denoise': lambda: global_values.get('denoise', ''),
            '%clip_skip': lambda: global_values.get('clip_skip', '')
        }

    @staticmethod
    def _get_date() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _get_time() -> str:
        return datetime.now().strftime("%H%M%S")

    def get_used_placeholders(self, filename: str) -> List[str]:
        if not isinstance(filename, str):
            log.warning(_LOG_PREFIX, f"Invalid filename type: {type(filename)}")
            return []
        return [p for p in self.placeholders.keys() if p in filename]

    def get_placeholder_value(self, placeholder: str) -> str:
        try:
            if placeholder not in self.placeholders:
                log.debug(_LOG_PREFIX, f"Unknown placeholder: {placeholder}; falling back to name without %")
                return placeholder.lstrip('%')
            value = self.placeholders[placeholder]()
            if value in (None, ''):
                log.debug(_LOG_PREFIX, f"Placeholder {placeholder} resolved to empty; falling back to name without %")
                return placeholder.lstrip('%')
            return value
        except Exception as e:
            log.error(_LOG_PREFIX, f"Error getting value for {placeholder}: {e}")
            return ''

    def process_string(self, filename_prefix: str, isPath: bool) -> str:
        try:
            if not filename_prefix or not isinstance(filename_prefix, str):
                log.warning(_LOG_PREFIX, "Invalid filename_prefix")
                return "default"
            used_placeholders = self.get_used_placeholders(filename_prefix)
            if not used_placeholders:
                return filename_prefix
            # Sort longest-first to prevent short placeholders matching inside longer ones
            # (e.g. %D matching inside %denoise before %denoise is processed)
            used_placeholders.sort(key=len, reverse=True)
            result = filename_prefix
            for placeholder in used_placeholders:
                value = self.get_placeholder_value(placeholder)
                result = result.replace(placeholder, value)
            if isPath:
                return self.sanitize_path(result)
            else:
                return self.sanitize_filename(result)
        except Exception as e:
            log.error(_LOG_PREFIX, f"Error processing filename: {e}")
            return "error_" + datetime.now().strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        windows_invalid = '<>:"/\\|?*'
        linux_invalid = '/'
        control_chars = ''.join(chr(i) for i in range(32))
        for char in windows_invalid + linux_invalid + control_chars:
            filename = filename.replace(char, '_')
        filename = filename.strip(' .')
        if not filename:
            return "untitled"
        windows_reserved = {
            'CON', 'PRN', 'AUX', 'NUL',
            'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
            'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
        }
        name_without_ext = filename.split('.')[0].upper()
        if name_without_ext in windows_reserved:
            filename = '_' + filename
        if len(filename) > 255:
            base, ext = os.path.splitext(filename)
            filename = base[:255-len(ext)] + ext
        return filename

    @staticmethod
    def sanitize_path(path: str) -> str:
        # Normalize backslashes to forward slashes (user may type Windows-style paths on Linux)
        path = path.replace('\\', '/')
        parts = Path(path).parts
        sanitized_parts = []
        for i, part in enumerate(parts):
            if i == 0 and len(parts) > 1 and part.endswith(':'):
                sanitized_parts.append(part)
            else:
                windows_invalid = '<>:"|?*'
                linux_invalid = ''
                control_chars = ''.join(chr(c) for c in range(32))
                for char in windows_invalid + linux_invalid + control_chars:
                    part = part.replace(char, '_')
                part = part.strip(' .')
                if not part:
                    part = "unnamed"
                sanitized_parts.append(part)
        sanitized_path = str(Path(*sanitized_parts))
        if len(sanitized_path) > 255:
            log.warning(_LOG_PREFIX, f"Path too long, may cause issues on some systems: {sanitized_path}")
        return sanitized_path

# Singleton
filename_processor = FilenameProcessor()

def string_placeholder(filename_prefix: str, isPath: bool) -> str:
    return filename_processor.process_string(filename_prefix, isPath)

# ─── Global value management ───────────────────────────────────────

def set_global_values(
    model: Optional[str] = None,
    basemodel: Optional[str] = None,
    seed_value: Optional[Union[int, float]] = None,
    sampler_name: Optional[str] = None,
    scheduler: Optional[str] = None,
    steps: Optional[Union[int, float]] = None,
    cfg: Optional[Union[int, float]] = None,
    denoise: Optional[Union[int, float]] = None,
    clip_skip: Optional[Union[int, float]] = None
) -> None:
    try:
        value_types = {
            'model': str, 'basemodel': str, 'seed': (int, float),
            'sampler_name': str, 'scheduler': str, 'steps': (int, float),
            'cfg': (int, float), 'denoise': (int, float), 'clip_skip': (int, float)
        }
        values = {
            'model': model, 'basemodel': basemodel, 'seed': seed_value,
            'sampler_name': sampler_name, 'scheduler': scheduler, 'steps': steps,
            'cfg': cfg, 'denoise': denoise, 'clip_skip': clip_skip
        }
        for key, value in values.items():
            if value is None:
                global_values[key] = ''
                continue
            if value in ('', 'None'):
                global_values[key] = ''
                continue
            expected_type = value_types[key]
            if not isinstance(value, expected_type):  # type: ignore[arg-type]
                try:
                    if expected_type in [(int, float), float]:
                        value = float(value)
                    elif expected_type == int:
                        value = int(value)
                    elif expected_type == str:
                        value = str(value)
                except (ValueError, TypeError) as e:
                    log.debug(_LOG_PREFIX, f"Ignoring non-numeric/invalid value for {key}: {e}")
                    value = ''
            if isinstance(value, (int, float)):
                if key in ['steps', 'cfg', 'denoise'] and value < 0:
                    log.warning(_LOG_PREFIX, f"Negative value for {key} adjusted to 0")
                    value = 0
            global_values[key] = str(value)
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error in set_global_values: {e}")
        for key in global_values:
            global_values[key] = ''

# ─── Hash calculation ──────────────────────────────────────────────

def get_sha256(file_path: str) -> Optional[str]:
    if not file_path or file_path in ('undefined', 'none'):
        log.warning(_LOG_PREFIX, f"Invalid file path: {file_path}")
        return None

    hash_value = sha256_for(
        file_path,
        use_sidecar=True,
        write_sidecar=True,
        show_progress=True,
    )
    if not hash_value:
        log.error(_LOG_PREFIX, f"Hash calculation failed for {file_path}")
    return hash_value

# ─── CivitAI key helpers ──────────────────────────────────────────

def civitai_embedding_key_name(embedding: str):
    return f'embed:{embedding}'

def civitai_lora_key_name(lora: str):
    return f'LORA:{lora}'

def civitai_model_key_name(model: str):
    return f'Model:{model}'

# ─── Model/embedding/lora path resolution ─────────────────────────

def __list_loras():
    return folder_paths.get_filename_list("loras")

def __list_embeddings():
    return folder_paths.get_filename_list("embeddings")

def full_embedding_path_for(embedding: str):
    name = embedding
    matching = None
    for x in __list_embeddings():
        if Path(x).name.lower().startswith(name.lower()):
            matching = x
            break
    if not matching:
        if os.sep in name or '/' in name:
            candidate = os.path.join(folder_paths.get_folder_paths("embeddings")[0], name)
            if os.path.exists(candidate):
                matching = name
    if not matching:
        for ext in ['.pt', '.safetensors', '.bin']:
            candidate = name if name.lower().endswith(ext) else name + ext
            candidate_path = os.path.join(folder_paths.get_folder_paths("embeddings")[0], candidate)
            if os.path.exists(candidate_path):
                matching = candidate
                break
    if not matching:
        return None
    return folder_paths.get_full_path("embeddings", matching)

def full_lora_path_for(lora: str):
    original = lora
    m = RE_LORA_TAG.search(original)
    if m:
        name = m.group(1)
    else:
        name = original.split(':')[0]
    matching = None
    for x in __list_loras():
        if Path(x).name.lower().startswith(name.lower()):
            matching = x
            break
    if not matching:
        if os.sep in name or '/' in name:
            candidate = os.path.join(folder_paths.get_folder_paths("loras")[0], name)
            if os.path.exists(candidate):
                matching = name
    if not matching:
        for ext in ['.safetensors', '.pt', '.bin']:
            candidate = name if name.lower().endswith(ext) else name + ext
            candidate_path = os.path.join(folder_paths.get_folder_paths("loras")[0], candidate)
            if os.path.exists(candidate_path):
                matching = candidate
                break
    if not matching:
        log.error(_LOG_PREFIX, f'Could not find full path to lora "{original}"')
        return None
    return folder_paths.get_full_path("loras", matching)

# ─── Lora string parsing ──────────────────────────────────────────

def parse_lora_string(lora_input):
    tokens = []
    weights = {}
    seen = set()
    if lora_input is None:
        return ('', {})
    token_matches = re.findall(r'<lora:([^>:]+):?([0-9\.]+)?[^>]*>', str(lora_input))
    if token_matches:
        for name, w in token_matches:
            name = Path(name).stem
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            weight = float(w) if w not in (None, '') else 1.0
            tokens.append(f"<lora:{name}:{weight}>")
            weights[name] = weight
        return (''.join(tokens), weights)
    parts = [p.strip() for p in re.split(r'[,;\s]+', str(lora_input)) if p.strip()]
    for part in parts:
        if ':' in part:
            name, w = part.split(':', 1)
            name = Path(name).stem
            try:
                weight = float(w)
            except Exception:
                weight = 1.0
        else:
            name = Path(part).stem
            weight = 1.0
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        tokens.append(f"<lora:{name}:{weight}>")
        weights[name] = weight
    return (''.join(tokens), weights)

# ─── Prompt metadata extraction (CivitAI compatible) ──────────────

class PromptMetadataExtractor:
    EMBEDDING = r'embedding:([^,\s\(\)\:]+)'
    LORA = r'<lora:([^>:]+)(?::[^>]+)?>'

    def __init__(self, prompts: List[str]):
        self.__embeddings: dict[str, Optional[str]] = {}
        self.__loras: dict[str, Optional[str]] = {}
        self.__collected_airs: List[str] = []
        self.__perform(prompts)

    def get_embeddings(self):
        return self.__embeddings

    def get_loras(self):
        return self.__loras

    def get_collected_airs(self) -> List[str]:
        return self.__collected_airs

    def __perform(self, prompts):
        for prompt in prompts:
            for embedding in re.findall(self.EMBEDDING, prompt, re.IGNORECASE | re.MULTILINE):
                self.__extract_embedding(embedding)
            for lora in re.findall(self.LORA, prompt, re.IGNORECASE | re.MULTILINE):
                base = lora.split(':')[0] if ':' in lora else lora
                self.__extract_lora(base)

    def __extract_embedding(self, embedding: str):
        key = civitai_embedding_key_name(embedding)
        path = full_embedding_path_for(embedding)
        if path is None or not os.path.exists(path):
            log.warning(_LOG_PREFIX, f"Embedding file not found for hash: {embedding}")
            return
        
        expected = read_expected(path)
        sha = None
        if expected:
            sha = expected.get("sha256")
            air = expected.get("air")
            if air:
                self.__collected_airs.append(air)
        
        if not sha:
            sha = get_sha256(path)
        
        self.__embeddings[key] = sha[:10] if sha else None

    def __extract_lora(self, lora: str):
        path = full_lora_path_for(lora)
        if path is None or not os.path.exists(path):
            log.warning(_LOG_PREFIX, f"Lora file not found for hash: {lora}")
            return
        lora_filename = os.path.basename(path)
        if lora_filename.lower().endswith('.safetensors'):
            lora_base = lora_filename[:-12]
        elif lora_filename.lower().endswith('.pt'):
            lora_base = lora_filename[:-3]
        elif lora_filename.lower().endswith('.bin'):
            lora_base = lora_filename[:-4]
        else:
            lora_base = os.path.splitext(lora_filename)[0]
        key = civitai_lora_key_name(lora_base)
        
        expected = read_expected(path)
        sha = None
        if expected:
            sha = expected.get("sha256")
            air = expected.get("air")
            if air:
                self.__collected_airs.append(air)
        
        if not sha:
            sha = get_sha256(path)
        
        self.__loras[key] = sha[:10] if sha else None

# ─── Utility helpers ───────────────────────────────────────────────

def return_filename(ckpt_name):
    return os.path.basename(ckpt_name)

def return_filename_without_extension(ckpt_name):
    return os.path.splitext(return_filename(ckpt_name))[0]

def handle_whitespace(string: str):
    return string.strip().replace("\n", " ").replace("\r", " ").replace("\t", " ")

def save_json(image_info, filename):
    try:
        workflow = (image_info or {}).get('workflow')
        if workflow is None:
            log.warning(_LOG_PREFIX, f"No image info found, skipping saving of JSON")
        with open(f'{filename}.json', 'w') as workflow_file:
            json.dump(workflow, workflow_file)
            log.msg(_LOG_PREFIX, f"Workflow saved to: '{filename}.json'")
    except Exception as e:
        log.error(_LOG_PREFIX, f'Failed to save workflow as json due to: {e}')

# ─── Module-level state ───────────────────────────────────────────

_output_dir = folder_paths.output_directory
_temp_dir = folder_paths.get_temp_directory()
_search_dirs_cache = None
_upscale_dirs_cache = None
_save_type = 'output'
_prefix_append = "_temp_" + ''.join(random.choice("abcdefghijklmnopqrstupvxyz") for x in range(5))

_civitai_sampler_map = {
    'euler_ancestral': 'Euler a', 'euler': 'Euler', 'lms': 'LMS',
    'heun': 'Heun', 'dpm_2': 'DPM2', 'dpm_2_ancestral': 'DPM2 a',
    'dpmpp_2s_ancestral': 'DPM++ 2S a', 'dpmpp_2m': 'DPM++ 2M',
    'dpmpp_sde': 'DPM++ SDE', 'dpmpp_2m_sde': 'DPM++ 2M SDE',
    'dpmpp_3m_sde': 'DPM++ 3M SDE', 'dpm_fast': 'DPM fast',
    'dpm_adaptive': 'DPM adaptive', 'ddim': 'DDIM', 'plms': 'PLMS',
    'uni_pc_bh2': 'UniPC', 'uni_pc': 'UniPC', 'lcm': 'LCM',
}

def _deduplicate_models(model_string):
    if not model_string or model_string in (None, '', 'undefined', 'none'):
        return []
    models = model_string.split(', ')
    seen = set()
    unique = []
    for model in models:
        s = model.strip()
        n = return_filename_without_extension(s).lower()
        if n and n not in seen:
            seen.add(n)
            unique.append(s)
    return unique

def _get_search_directories():
    global _search_dirs_cache
    if _search_dirs_cache is None:
        _search_dirs_cache = []
        for key in ["checkpoints", "diffusion_models", "unet", "upscale_models"]:
            _search_dirs_cache.extend(folder_paths.get_folder_paths(key))
    return _search_dirs_cache

def _get_upscale_directories():
    global _upscale_dirs_cache
    if _upscale_dirs_cache is None:
        _upscale_dirs_cache = set(folder_paths.get_folder_paths("upscale_models"))
    return _upscale_dirs_cache

def _get_civitai_sampler_name(sampler_name, scheduler):
    if sampler_name in _civitai_sampler_map:
        civitai_name = _civitai_sampler_map[sampler_name]
        sched_suffixes = {
            "karras": " Karras", "exponential": " Exponential",
            "sgm_uniform": " SGM Uniform", "simple": " Simple",
            "ddim_uniform": " DDIM Uniform", "beta": " Beta",
            "linear_quadratic": " Linear Quadratic", "kl_optimal": " kl optimal",
            "AYS SDXL": " AYS SDXL", "AYS SD1": " AYS SD1",
            "AYS SVD": " AYS SVD", "simple_test": " Simple Test",
        }
        suffix = sched_suffixes.get(scheduler, "")
        return civitai_name + suffix
    else:
        if scheduler != 'normal':
            return f"{sampler_name}_{scheduler}"
        return sampler_name

def _get_subfolder_path(image_path, output_path):
    output_parts = output_path.strip(os.sep).split(os.sep)
    image_parts = image_path.strip(os.sep).split(os.sep)
    common_parts = os.path.commonprefix([output_parts, image_parts])
    subfolder_parts = image_parts[len(common_parts):]
    return os.sep.join(subfolder_parts[:-1])


class RvImage_SaveImages_v2(io.ComfyNode):

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Save Images v2 [Eclipse]",
            display_name="Save Images",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            description="Save images with combo-chip feature toggles. Enable chips to customize quality, DPI, output path, filename, and save options.",
            is_output_node=True,
            inputs=[
                # Multi-select feature toggle (replaced by combo-chip in JS, no socket needed)
                io.String.Input("features", default="save,embed_workflow,save_gen_data,output,filename", socketless=True,
                    tooltip="Comma-separated feature list. JS combo-chip replaces this widget.",
                ),
                # Combo-chip backing booleans (hidden by JS, synced from chip state, no socket needed)
                io.Boolean.Input("optimize_image", default=False, label_on="yes", label_off="no", socketless=True, tooltip="Optimize image output"),
                io.Boolean.Input("lossless_webp", default=False, label_on="yes", label_off="no", socketless=True, tooltip="Use lossless compression for WebP"),
                io.Boolean.Input("embed_workflow", default=True, label_on="yes", label_off="no", socketless=True, tooltip="Embed workflow in image metadata"),
                io.Boolean.Input("save_generation_data", default=True, label_on="yes", label_off="no", socketless=True, tooltip="Save A1111-compatible generation data"),
                io.Boolean.Input("remove_prompts", default=False, label_on="yes", label_off="no", socketless=True, tooltip="Remove prompts from metadata"),
                io.Boolean.Input("save_workflow_as_json", default=False, label_on="yes", label_off="no", socketless=True, tooltip="Save workflow as separate JSON file"),
                io.Boolean.Input("add_loras_to_prompt", default=False, label_on="yes", label_off="no", socketless=True, tooltip="Add LoRA tags to prompt metadata"),
                io.Boolean.Input("show_previews", default=True, label_on="yes", label_off="no", socketless=True, tooltip="Show image previews in UI"),
                io.Boolean.Input("save_to_disk", default=True, label_on="yes", label_off="no", socketless=True, tooltip="Save images to disk (disable for preview-only mode)"),
                # Visibility-toggle backing booleans
                io.Boolean.Input("use_quality", default=False, label_on="yes", label_off="no", socketless=True, tooltip="Enable custom quality (default: 100)"),
                io.Boolean.Input("use_dpi", default=False, label_on="yes", label_off="no", socketless=True, tooltip="Enable custom DPI (default: 300)"),
                io.Boolean.Input("use_output", default=True, label_on="yes", label_off="no", socketless=True, tooltip="Enable custom output path (default: ComfyUI output folder)"),
                io.Boolean.Input("use_filename", default=True, label_on="yes", label_off="no", socketless=True, tooltip="Enable custom filename (default: ComfyUI prefix)"),
                # Output path (shown when output chip active)
                io.String.Input("output_path", default=r'%Y-%M-%D\%basemodel', tooltip="Output path. Placeholders: %today, %date, %time, %Y, %m/%M, %d/%D, %H, %S, %basemodel, %model, %seed, %sampler_name, %scheduler, %steps, %cfg, %denoise, %clip_skip"),
                # Filename widgets (shown when filename chip active)
                io.String.Input("filename_prefix", default="%basemodel, %seed, %sampler_name, %scheduler, %steps_steps, %cfg", tooltip="Filename prefix. Placeholders: %today, %date, %time, %Y, %m/%M, %d/%D, %H, %S, %basemodel, %model, %seed, %sampler_name, %scheduler, %steps, %cfg, %denoise, %clip_skip"),
                io.String.Input("filename_delimiter", default="_", tooltip="Filename delimiter"),
                io.Int.Input("filename_number_padding", default=4, min=1, max=9, step=1, tooltip="Number of digits for counter padding"),
                io.Boolean.Input("filename_number_start", default=False, label_on="yes", label_off="no", tooltip="Number at start of filename"),
                # Always-visible
                io.Combo.Input("extension", options=['png', 'jpg', 'jpeg', 'gif', 'tiff', 'webp', 'bmp'], tooltip="Image file format"),
                # Quality/DPI (shown when respective chips active, below extension)
                io.Int.Input("quality", default=100, min=1, max=100, step=1, tooltip="Image quality (1-100)"),
                io.Int.Input("dpi", default=300, min=1, max=2400, step=1, tooltip="Image DPI"),
                # Optional inputs
                io.Image.Input("images", optional=True),
                io.Custom("PIPE").Input("pipe_opt", optional=True),
            ],
            outputs=[
                io.Image.Output("images"),
                io.String.Output("files"),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo],
        )

    @classmethod
    def execute(cls,
                    images=None,
                    features=None,  # multi_select chip (not used directly, backing booleans are source of truth)
                    # Backing booleans
                    optimize_image=False,
                    lossless_webp=True,
                    embed_workflow=True,
                    save_generation_data=True,
                    remove_prompts=False,
                    save_workflow_as_json=False,
                    add_loras_to_prompt=False,
                    show_previews=False,
                    save_to_disk=True,
                    # Visibility-toggle booleans
                    use_quality=False,
                    use_dpi=False,
                    use_output=False,
                    use_filename=False,
                    # Value widgets
                    output_path='',
                    filename_prefix='ComfyUI',
                    filename_delimiter='_',
                    filename_number_padding=4,
                    filename_number_start=False,
                    extension='png',
                    quality=100,
                    dpi=300,
                    # Optional
                    pipe_opt=None,
                    ):
        # Apply defaults when use_* toggles are off
        if not use_quality:
            quality = 100
        if not use_dpi:
            dpi = 300
        if not use_output:
            output_path = ''
        if not use_filename:
            filename_prefix = 'ComfyUI'
            filename_delimiter = '_'
            filename_number_padding = 4
            filename_number_start = False

        # Access hidden inputs
        prompt = cls.hidden.prompt
        extra_pnginfo = cls.hidden.extra_pnginfo

        # Require either images or a pipe to proceed
        if images is None and pipe_opt is None:
            raise RuntimeError("Save Images v2 requires either an image input or a pipe input (pipe_opt).")

        a111_params = ""
        lora_weights = {}
        ctx = None

        # Extract pipe context (cheap - just dict/tuple unwrap)
        if pipe_opt is not None:
            if isinstance(pipe_opt, tuple) and len(pipe_opt) > 0:
                ctx = pipe_opt[0] if isinstance(pipe_opt[0], dict) else {}
            elif isinstance(pipe_opt, dict):
                ctx = pipe_opt
            else:
                raise ValueError("Save Images v2 expects dict-style or tuple-style pipes for pipe_opt.")

        # Handle image source (direct or from pipe) - extracted early for preview-only mode
        if images is None and pipe_opt is not None:
            try:
                pipe_images = (ctx.get("images") or ctx.get("image")) if isinstance(ctx, dict) else None
            except Exception:
                pipe_images = None

            if pipe_images is None:
                raise RuntimeError("Save Images v2: pipe_opt provided but contains no 'images' data.")

            if isinstance(pipe_images, torch.Tensor):
                if pipe_images.numel() == 0:
                    raise RuntimeError("Save Images v2: pipe_opt contains an empty tensor for 'images'.")
                images = [pipe_images[i] for i in range(pipe_images.size(0))] if pipe_images.dim() == 4 else [pipe_images]
                original_images_tensor = pipe_images
            elif isinstance(pipe_images, np.ndarray):
                if pipe_images.size == 0:
                    raise RuntimeError("Save Images v2: pipe_opt contains an empty numpy array for 'images'.")
                images = [pipe_images[i] for i in range(pipe_images.shape[0])] if pipe_images.ndim == 4 else [pipe_images]
                original_images_tensor = pipe_images
            elif isinstance(pipe_images, (list, tuple)):
                if len(pipe_images) == 0:
                    raise RuntimeError("Save Images v2: pipe_opt provided but contains no 'images' data.")
                images = list(pipe_images)
                try:
                    original_images_tensor = torch.stack(images) if len(images) > 1 else images[0]
                except Exception:
                    original_images_tensor = images
            else:
                images = [pipe_images]
                original_images_tensor = pipe_images
        else:
            original_images_tensor = images

        # Normalize: flatten any 4D tensors in list (from ConcatMulti shape-mismatch)
        _flat = []
        if images is None:
            images = []
        elif not isinstance(images, (list, tuple, torch.Tensor, np.ndarray)):
            try:
                iter(images)
            except TypeError:
                images = [images]
                
        for _img in images:
            if isinstance(_img, torch.Tensor) and _img.dim() == 4:
                _flat.extend(_img[j] for j in range(_img.size(0)))
            else:
                _flat.append(_img)
        images = _flat

        # Preview-only mode: save to temp folder, skip all metadata processing
        # (skips ~150 lines of SHA256 model hashing, metadata building, filename resolution)
        if not save_to_disk:
            filename_prefix_temp = "ComfyUI" + _prefix_append
            results = []

            # Build workflow metadata if embed_workflow is active (allows drag-back into ComfyUI)
            preview_metadata = None
            if embed_workflow:
                preview_metadata = PngInfo()
                if prompt is not None:
                    preview_metadata.add_text("prompt", json.dumps(prompt))
                if extra_pnginfo is not None:
                    for x in extra_pnginfo:
                        preview_metadata.add_text(x, json.dumps(extra_pnginfo[x]))

            for batch_number, image in enumerate(images):
                if hasattr(image, 'shape') and image.ndim == 4 and image.shape[0] == 1:
                    image = image.squeeze(0)
                i = 255. * image.cpu().numpy()
                img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))
                height, width = img.size[1], img.size[0]
                full_output_folder, filename_base, counter_temp, subfolder, _ = folder_paths.get_save_image_path(
                    filename_prefix_temp, _temp_dir, width, height)
                timestamp = int(time.time() * 1000) % 100000000
                file = f"{filename_base}_{counter_temp:05}_{timestamp}_.png"
                filepath = os.path.join(full_output_folder, file)
                img.save(filepath, pnginfo=preview_metadata, compress_level=1)
                results.append({"filename": file, "subfolder": subfolder, "type": "temp"})
                counter_temp += 1
            return io.NodeOutput(original_images_tensor, [], ui={"images": results})

        # ─── Full save mode: process pipe metadata for CivitAI-compatible embedding ───
        if pipe_opt is not None and isinstance(ctx, dict):
            sampler_name = ctx.get("sampler_name")
            scheduler = ctx.get("scheduler")
            steps = ctx.get("steps")
            cfg = ctx.get("cfg")
            seed_value = ctx.get("seed")
            width = ctx.get("width")
            height = ctx.get("height")
            positive = ctx.get("text_pos")
            negative = ctx.get("text_neg")
            model_name = ctx.get("model_name")
            vae_name = ctx.get("vae_name")
            lora_names = ctx.get("lora_names") or ctx.get("loras")
            denoise = ctx.get("denoise") if ctx.get("denoise") is not None else ctx.get("guidance")
            clip_skip = ctx.get("clip_skip")

            try:
                set_global_values('', '', seed_value, sampler_name, scheduler, steps, cfg, denoise, clip_skip)
            except Exception as e:
                log.error(_LOG_PREFIX, f"Failed to set global values: {e}")

            def _prompt_to_str(x):
                if x is None:
                    return ""
                if isinstance(x, str):
                    return x
                if isinstance(x, bytes):
                    return x.decode('utf-8', errors='ignore')
                if isinstance(x, (list, tuple)):
                    parts = []
                    for item in x:
                        if isinstance(item, (list, tuple)):
                            part = _prompt_to_str(item)
                        else:
                            part = str(item)
                        if part:
                            parts.append(part)
                    return " ".join(parts)
                try:
                    return str(x)
                except Exception:
                    return ""

            positive = _prompt_to_str(positive) if positive not in (None, '', 'undefined', 'none') else ""
            negative = _prompt_to_str(negative) if negative not in (None, '', 'undefined', 'none') else ""

            collected_airs = []
            model_string = {}
            modelhash: Optional[str] = ""
            vae_hash: Optional[str] = ""

            # Process model names
            models = _deduplicate_models(model_name)
            if models:
                first_model = models[0]
                global_values['basemodel'] = return_filename_without_extension(first_model)
                global_values['model'] = first_model

                import glob

                def find_model_file(model, search_dirs, extensions):
                    for search_dir in search_dirs:
                        for ext in extensions:
                            pattern = os.path.join(search_dir, '**', model if model.lower().endswith(ext) else model + ext)
                            matches = glob.glob(pattern, recursive=True)
                            if matches:
                                  return matches[0], search_dir
                    if os.path.exists(model):
                        return model, None
                    for ext in extensions:
                        candidate = model if model.lower().endswith(ext) else model + ext
                        if os.path.exists(candidate):
                            return candidate, None
                    return None, None

                search_dirs = _get_search_directories()
                extensions = ['.safetensors', '.pt', '.pth', '.ckpt', '.bin', '.gguf']
                upscale_model_dirs = _get_upscale_directories()

                for model in models:
                    model_path, model_dir = find_model_file(model, search_dirs, extensions)
                    if model_path and os.path.exists(model_path):
                        expected = read_expected(model_path)
                        modelhash = None
                        if expected:
                            modelhash = expected.get("sha256")
                            air = expected.get("air")
                            if air:
                                collected_airs.append(air)
                        if not modelhash:
                            modelhash = get_sha256(model_path)
                        if modelhash:
                            modelhash = modelhash[:10]
                            if model_dir and model_dir in upscale_model_dirs:
                                model_key = return_filename_without_extension(model)
                            else:
                                model_key = civitai_model_key_name(return_filename_without_extension(model))
                            model_string[model_key] = modelhash
                    else:
                        log.warning(_LOG_PREFIX, f"Model file not found for hash: {model} (path: {model_path}, dir: {model_dir})")

            # Process VAE names
            vae_models = _deduplicate_models(vae_name)
            for model in vae_models:
                vae_full_path = folder_paths.get_full_path("vae", model)
                if vae_full_path:
                    expected = read_expected(vae_full_path)
                    sha_result = None
                    if expected:
                        sha_result = expected.get("sha256")
                        air = expected.get("air")
                        if air:
                            collected_airs.append(air)
                    if not sha_result:
                        sha_result = get_sha256(vae_full_path)
                    if sha_result:
                        vae_hash = sha_result[:10]
                        vae_file = return_filename_without_extension(model)
                        model_string[vae_file] = vae_hash

            if not lora_names in (None, '', 'undefined', 'none'):
                lora_tokens, lora_weights = parse_lora_string(lora_names)
                positive_with_loras = positive + lora_tokens
                metadata_extractor = PromptMetadataExtractor([positive_with_loras, negative])
                if add_loras_to_prompt:
                    positive_for_meta = positive_with_loras
                else:
                    positive_for_meta = positive
            else:
                positive_for_meta = positive
                lora_weights = {}
                metadata_extractor = PromptMetadataExtractor([positive, negative])

            collected_airs.extend(metadata_extractor.get_collected_airs())

            # Inject collected AIRs into extra_pnginfo workflow extra.airs
            if extra_pnginfo is not None and collected_airs:
                try:
                    workflow_data = extra_pnginfo.get("workflow")
                    if isinstance(workflow_data, str):
                        try:
                            workflow_data = json.loads(workflow_data)
                        except Exception:
                            pass
                    
                    if isinstance(workflow_data, dict):
                        if "extra" not in workflow_data or not isinstance(workflow_data["extra"], dict):
                            workflow_data["extra"] = {}
                        if "airs" not in workflow_data["extra"] or not isinstance(workflow_data["extra"]["airs"], list):
                            workflow_data["extra"]["airs"] = []
                        
                        existing_airs = set(workflow_data["extra"]["airs"])
                        added_any = False
                        for air in collected_airs:
                            if air not in existing_airs:
                                workflow_data["extra"]["airs"].append(air)
                                existing_airs.add(air)
                                added_any = True
                        
                        if added_any:
                            if isinstance(extra_pnginfo.get("workflow"), str):
                                extra_pnginfo["workflow"] = json.dumps(workflow_data)
                            else:
                                extra_pnginfo["workflow"] = workflow_data
                            log.msg(_LOG_PREFIX, f"Injected {len(collected_airs)} AIRs into workflow metadata: {collected_airs}")
                except Exception as e:
                    log.error(_LOG_PREFIX, f"Failed to inject AIRs into workflow metadata: {e}")

            embeddings = metadata_extractor.get_embeddings()
            loras = metadata_extractor.get_loras()

            if sampler_name and sampler_name not in (None, '', 'undefined', 'none'):
                civitai_sampler_name = _get_civitai_sampler_name(sampler_name.replace('_gpu', ''), scheduler)
            else:
                civitai_sampler_name = "Euler Simple"

            extension_hashes = json.dumps(model_string | embeddings | loras)

            clip_skip_meta = global_values.get('clip_skip', '')
            try:
                if clip_skip_meta in (None, '', 'None'):
                    clip_skip_value_for_meta = None
                else:
                    clip_skip_value_for_meta = int(float(clip_skip_meta))
                    if clip_skip_value_for_meta < 0:
                        clip_skip_value_for_meta = abs(clip_skip_value_for_meta)
            except Exception:
                clip_skip_value_for_meta = None

            clip_skip_segment = f", Clip skip: {clip_skip_value_for_meta}" if clip_skip_value_for_meta is not None else ''

            def _val_to_str(v):
                try:
                    if v in (None, '', 'undefined', 'none'):
                        return ''
                except Exception:
                    pass
                try:
                    if isinstance(v, torch.Tensor):
                        if v.numel() == 1:
                            return str(v.item())
                        else:
                            return str(v.tolist())
                    if isinstance(v, np.generic):
                        return str(np.asscalar(v)) if hasattr(np, 'asscalar') else str(v.item())
                except Exception:
                    pass
                return str(v)

            steps_str = _val_to_str(steps)
            cfg_str = _val_to_str(cfg)
            seed_str = _val_to_str(seed_value)

            if not remove_prompts:
                a111_params = f"{handle_whitespace(positive_for_meta)}\nNegative prompt: {handle_whitespace(negative)}\nSteps: {steps_str}, Sampler: {civitai_sampler_name}, CFG scale: {cfg_str}, Seed: {seed_str}, Size: {width}x{height}{clip_skip_segment}, Hashes: {extension_hashes}, Version: ComfyUI"
            else:
                a111_params = f"\nNegative prompt: \nSteps: {steps_str}, Sampler: {civitai_sampler_name}, CFG scale: {cfg_str}, Seed: {seed_str}, Size: {width}x{height}{clip_skip_segment}, Hashes: {extension_hashes}, Version: ComfyUI"

        delimiter = filename_delimiter
        number_padding = filename_number_padding
        lossless_webp = (lossless_webp == True)
        optimize_image = (optimize_image == True)

        original_output = _output_dir

        # Setup output path
        if output_path in [None, '', 'none', '.', './']:
            output_path = _output_dir

        output_path = string_placeholder(output_path, True)

        # Always resolve to absolute path inside ComfyUI output folder
        comfy_output_dir = os.path.abspath(_output_dir)
        if not os.path.isabs(output_path):
            output_path = os.path.normpath(output_path)
            if output_path.startswith('.' + os.sep):
                output_path = output_path[2:]
            output_path = os.path.join(comfy_output_dir, output_path)
        output_path = os.path.abspath(output_path)

        # Flag external paths — saved via temp-then-copy so preview still works
        _is_external = not output_path.startswith(comfy_output_dir)

        if output_path.strip() != '':
            if not os.path.exists(output_path.strip()):
                log.warning(_LOG_PREFIX, f'The path `{output_path.strip()}` specified doesn\'t exist! Creating directory.')
                os.makedirs(output_path, exist_ok=True)

        filename_prefix = string_placeholder(filename_prefix, False)

        # Find existing counter values
        if filename_number_start:
            pattern = f"(\\d+){re.escape(delimiter)}{re.escape(filename_prefix)}"
        else:
            pattern = f"{re.escape(filename_prefix)}{re.escape(delimiter)}(\\d+)"
        existing_counters = []
        for filename in os.listdir(output_path):
            match = re.search(pattern, filename)
            if match and re.match(pattern, os.path.basename(filename)):
                existing_counters.append(int(match.group(1)))
        existing_counters.sort(reverse=True)

        if existing_counters:
            counter = existing_counters[0] + 1
        else:
            counter = 1

        file_extension = '.' + extension
        if file_extension not in ALLOWED_EXT:
            log.error(_LOG_PREFIX, f"The extension `{extension}` is not valid. The valid formats are: {', '.join(sorted(ALLOWED_EXT))}")
            file_extension = ".png"

        results = list()
        output_files = list()

        for image in images:
            i = 255. * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            # Delegate metadata/pnginfo
            if extension == 'webp':
                img_exif = img.getexif()
                if embed_workflow:
                    workflow_metadata = ''
                    prompt_str = ''
                    if prompt is not None:
                        prompt_str = json.dumps(prompt)
                        img_exif[0x010f] = "Prompt:" + prompt_str
                    if extra_pnginfo is not None:
                        for x in extra_pnginfo:
                            workflow_metadata += json.dumps(extra_pnginfo[x])
                    img_exif[0x010e] = "Workflow:" + workflow_metadata
                try:
                    log.debug(_LOG_PREFIX, f"WEBP parameters (diagnostic): {a111_params}")
                except Exception:
                    pass
                exif_data: Union[bytes, PngInfo] = img_exif.tobytes()
            else:
                metadata = PngInfo()

                if embed_workflow:
                    if prompt is not None:
                        metadata.add_text("prompt", json.dumps(prompt))
                    if extra_pnginfo is not None:
                        for x in extra_pnginfo:
                            metadata.add_text(x, json.dumps(extra_pnginfo[x]))

                if pipe_opt != None and save_generation_data:
                    metadata.add_text("parameters", a111_params)
                    if lora_weights:
                        try:
                            metadata.add_text('lora_weights', json.dumps(lora_weights))
                        except Exception as e:
                            log.error(_LOG_PREFIX, f"Failed to add lora_weights metadata: {e}")

                exif_data = metadata

            # Delegate the filename
            if filename_number_start == True:
                file = f"{counter:0{number_padding}}{delimiter}{filename_prefix}{file_extension}"
                jsonfile = f"{counter:0{number_padding}}{delimiter}{filename_prefix}"
            else:
                file = f"{filename_prefix}{delimiter}{counter:0{number_padding}}{file_extension}"
                jsonfile = f"{filename_prefix}{delimiter}{counter:0{number_padding}}"
            if os.path.exists(os.path.join(output_path, file)):
                counter += 1

            # Save the images
            output_file = ""
            try:
                output_file = os.path.abspath(os.path.join(output_path, file))
                if _is_external:
                    temp_filename = f"eclipse_si_{counter:05}{file_extension}"
                    save_path = os.path.join(_temp_dir, temp_filename)
                else:
                    save_path = output_file
                    temp_filename = None
                if extension in ["jpg", "jpeg"]:
                    img.save(save_path,
                             quality=quality, optimize=optimize_image, dpi=(dpi, dpi))
                elif extension == 'webp':
                    img.save(save_path,
                             quality=quality, lossless=lossless_webp, exif=exif_data)
                elif extension == 'png':
                    img.save(save_path,
                             pnginfo=exif_data, optimize=optimize_image)
                elif extension == 'bmp':
                    img.save(save_path)
                elif extension == 'tiff':
                    img.save(save_path,
                             quality=quality, optimize=optimize_image)
                else:
                    img.save(save_path,
                             pnginfo=exif_data, optimize=optimize_image)

                if _is_external:
                    shutil.copy2(save_path, output_file)

                log.msg(_LOG_PREFIX, f"Image file saved to: {output_file}")
                output_files.append(output_file)

                if show_previews:
                    if _is_external:
                        results.append({
                            "filename": temp_filename,
                            "subfolder": "",
                            "type": "temp",
                        })
                    else:
                        subfolder = _get_subfolder_path(output_file, original_output)
                        results.append({
                            "filename": file,
                            "subfolder": subfolder,
                            "type": _save_type,
                        })

            except OSError as e:
                log.error(_LOG_PREFIX, f'Unable to save file to: {output_file}')
                log.error(_LOG_PREFIX, str(e))
            except Exception as e:
                log.error(_LOG_PREFIX, 'Unable to save file due to the following error:')
                log.error(_LOG_PREFIX, str(e))

            if save_workflow_as_json:
                output_json = os.path.abspath(os.path.join(output_path, jsonfile))
                save_json(extra_pnginfo, output_json)

            counter += 1

        if show_previews == True:
            return io.NodeOutput(original_images_tensor, output_files, ui={"images": results, "files": output_files})
        else:
            return io.NodeOutput(original_images_tensor, output_files, ui={"images": []})
