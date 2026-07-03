import hashlib
import json
import os
import re
import time
import comfy #type: ignore
import ipaddress
import socket
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional
from urllib.parse import urlparse

# Import log from logger (centralized location)
from .logger import log

# ============================================================================
# Eclipse config utilities (read/write config.json)
# ============================================================================

# Path to the extension root (one level up from core/)
_NODE_DIR = Path(__file__).resolve().parent.parent

# Config cache for get_config_value (avoids repeated file I/O)
_config_cache: Dict[str, Any] = {}
_config_cache_time: float = 0.0
_CONFIG_CACHE_TTL: float = 5.0  # Cache for 5 seconds


def get_config_value(key: str, default: Any = None) -> Any:
    # Get a configuration value from config.json (cached)
    # If config.json doesn't exist, copies from config.json.example first.
    global _config_cache, _config_cache_time

    current_time = time.time()

    # Check if cache is valid
    if current_time - _config_cache_time < _CONFIG_CACHE_TTL and _config_cache:
        return _config_cache.get(key, default)

    # Ensure config exists (auto-copy from .example on first run)
    config_path = _NODE_DIR / "config.json"
    if not config_path.exists():
        _ensure_config_exists()

    # Reload config from file
    try:
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                _config_cache = json.load(f)
                _config_cache_time = current_time
                return _config_cache.get(key, default)
    except Exception:
        pass
    return default


def _ensure_config_exists() -> bool:
    # Create config.json from config.json.example if missing.
    # This allows users to edit their config without git conflicts on pull/update.
    import shutil

    config_path = _NODE_DIR / "config.json"
    if config_path.exists():
        return False

    example_path = _NODE_DIR / "config.json.example"
    try:
        if example_path.exists():
            shutil.copy2(example_path, config_path)
            log.msg("Config", "Created config.json from .example template")
        else:
            # Fallback: create minimal defaults if .example is missing
            default_config = {
                "_comments": {
                    "description": "Eclipse ComfyUI Node Configuration",
                    "log_level_options": "error | warning | info | debug"
                },
                "log_level": "warning",
                "vue_size_fix": True
            }
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=2)
            log.msg("Config", "Created default config.json (no .example found)")
        return True
    except Exception as e:
        log.error("Config", f"Failed to create config.json: {e}")
        return False


def invalidate_config_cache():
    # Invalidate config cache (call after updating config)
    global _config_cache_time
    _config_cache_time = 0.0


def update_config_value(key: str, value, nested_key: Optional[str] = None) -> bool:
    # Update a configuration value in config.json.
    invalidate_config_cache()
    config_path = _NODE_DIR / "config.json"
    try:
        config = {}
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)

        if nested_key:
            if key not in config:
                config[key] = {}
            if not isinstance(config[key], dict):
                config[key] = {}
            config[key][nested_key] = value
        else:
            config[key] = value

        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)

        return True
    except Exception as e:
        log.error("Config", f"Failed to update {key}: {e}")
        return False


def calculate_file_hash(file_path: Path, show_progress: bool = True, progress_cb=None) -> str:
    # Calculate SHA256 hash of a file with optional progress display.
    import sys

    sha256_hash = hashlib.sha256()
    file_size = file_path.stat().st_size
    bytes_processed = 0
    last_progress = -1

    size_mb = file_size / (1024 * 1024)
    if show_progress and file_size > 100 * 1024 * 1024:
        log.msg("FileHash", f"Calculating hash for {file_path.name} ({size_mb:.1f} MB)...")
    elif show_progress:
        log.msg("FileHash", f"Calculating hash for {file_path.name}...")

    with open(file_path, "rb") as f:
        while chunk := f.read(8192 * 1024):  # 8MB chunks
            sha256_hash.update(chunk)
            bytes_processed += len(chunk)
            if progress_cb:
                try:
                    progress_cb(bytes_processed, file_size)
                except Exception:
                    pass
            if show_progress and file_size > 100 * 1024 * 1024:
                progress = int((bytes_processed / file_size) * 100)
                if progress != last_progress:
                    sys.stdout.write(f"\rEclipse: [FileHash]   Hashing: {progress}% ({bytes_processed / (1024*1024):.0f}/{size_mb:.0f} MB)")
                    sys.stdout.flush()
                    last_progress = progress

    if show_progress and file_size > 100 * 1024 * 1024:
        print()

    hex_digest = sha256_hash.hexdigest()
    if show_progress:
        log.msg("FileHash", f"SHA256: {hex_digest}  {file_path.name}")
    return hex_digest


class AnyType(str):
    # A special class that is always equal in not-equal comparisons. Credit to pythongosssss

    def __eq__(self, _) -> bool:
        return True

    def __ne__(self, __value: object) -> bool:
        return False


def get_workflow_node(extra_pnginfo: Optional[dict], node_id: str, default=None):
    # Find a node in the workflow JSON by its colon-path unique_id (e.g. "42:7").
    # Handles nodes inside ComfyUI subgraphs by traversing
    # workflow.definitions.subgraphs when the node type matches a subgraph UUID.
    # Mirrors rgthree's get_worflow_node() server-side helper.
    if not extra_pnginfo or 'workflow' not in extra_pnginfo:
        return default
    workflow = extra_pnginfo['workflow']
    parts = node_id.split(':')
    nodes_list = workflow.get('nodes', [])
    subgraph_defs = (workflow.get('definitions') or {}).get('subgraphs', [])
    found = None
    for part in parts:
        found = next((n for n in nodes_list if str(n.get('id', '')) == part), None)
        if found is None:
            return default
        # If there are more parts, dive into the subgraph definition
        node_type = found.get('type', '')
        sg_def = next((sg for sg in subgraph_defs if str(sg.get('id', '')) == str(node_type)), None)
        if sg_def is not None and 'nodes' in sg_def:
            nodes_list = sg_def['nodes']
    return found if found is not None else default


def is_safe_url(url: str) -> bool:
    # Validate URL to prevent SSRF attacks.
    # Blocks private IP ranges and localhost to prevent internal network access.
    #
    # Returns:
    #     True if URL is safe to fetch, False otherwise.
    if not url:
        log.warning("Security", "Blocked empty URL")
        return False
    
    try:
        parsed = urlparse(url)
        
        # Only allow http/https
        if parsed.scheme not in ('http', 'https'):
            log.warning("Security", f"Blocked non-http(s) URL scheme: {parsed.scheme}")
            return False
        
        hostname = parsed.hostname
        if not hostname:
            log.warning("Security", f"Blocked URL with no hostname: {url}")
            return False
        
        # Block localhost variants
        if hostname.lower() in ('localhost', '127.0.0.1', '::1', '0.0.0.0'):
            log.warning("Security", f"Blocked localhost URL: {url}")
            return False
        
        # Try to resolve hostname and check if it's a private IP
        try:
            ip = socket.gethostbyname(hostname)
            ip_obj = ipaddress.ip_address(ip)
            
            # Block private, loopback, link-local, and reserved ranges
            if (ip_obj.is_private or ip_obj.is_loopback or 
                ip_obj.is_link_local or ip_obj.is_reserved):
                log.warning("Security", f"Blocked private/reserved IP URL: {url} (resolved to {ip})")
                return False
        except (socket.gaierror, ValueError):
            # Could not resolve - allow (might be valid external domain)
            pass
        
        return True
    except Exception as e:
        log.warning("Security", f"Blocked URL due to parse error: {url} ({e})")
        return False


def purge_vram() -> None:
    # Central helper to purge VRAM and unload models safely.
    #
    # Use this from nodes instead of duplicating the try/except import and
    # GC/CUDA/model unload sequence. Any exception is reported via the
    # project's cstr warning helper so callers don't need to duplicate
    # error handling.
    #
    # This function unloads all models and clears allocator caches to free
    # maximum VRAM. This will require models to be reloaded on next use.
    # Based on comfyui-multigpu's soft_empty_cache_multigpu approach.
    try:
        import gc
        torch: Optional[ModuleType]
        comfy_mod: Optional[ModuleType]
        try:
            import torch  # type: ignore
        except Exception:
            torch = None

        try:
            import comfy.model_management  # type: ignore
            comfy_mod = comfy
        except Exception:
            comfy_mod = None

        # Step 1: Python garbage collection
        gc.collect()
        
        # Step 2: Clear device caches (multi-device support)
        if torch is not None:
            try:
                # CUDA devices
                if torch.cuda.is_available():
                    device_count = torch.cuda.device_count()
                    for i in range(device_count):
                        with torch.cuda.device(i):
                            torch.cuda.empty_cache()
                            if hasattr(torch.cuda, 'ipc_collect'):
                                torch.cuda.ipc_collect()
                
                # MPS (Apple Silicon)
                if hasattr(torch, 'mps') and hasattr(torch.mps, 'empty_cache'):
                    torch.mps.empty_cache()
                
                # XPU (Intel)
                if hasattr(torch, 'xpu') and hasattr(torch.xpu, 'empty_cache'):
                    torch.xpu.empty_cache()
                
                # NPU (Huawei/Ascend)
                npu = getattr(torch, 'npu', None)
                if npu is not None and hasattr(npu, 'empty_cache'):
                    npu.empty_cache()
                
                # MLU (Cambricon)
                mlu = getattr(torch, 'mlu', None)
                if mlu is not None and hasattr(mlu, 'empty_cache'):
                    mlu.empty_cache()
                
            except Exception:
                # Ignore device-specific failures
                pass

        # Step 3: ComfyUI model unloading and cache clearing
        if comfy_mod is not None:
            try:
                # Unload all models first, then clear caches
                if hasattr(comfy_mod.model_management, 'unload_all_models'):
                    comfy_mod.model_management.unload_all_models()
                if hasattr(comfy_mod.model_management, 'soft_empty_cache'):
                    comfy_mod.model_management.soft_empty_cache()
            except Exception:
                # Ignore model-management failures
                pass
    except Exception as e:
        try:
            log.warning("VRAM", f"Purge failed: {e}")
        except Exception:
            try:
                print(f"VRAM purge failed: {e}")
            except Exception:
                pass


# Pre-instantiated AnyType for use across nodes
# Import as: from ..core.common import any_type
any_type = AnyType("*")


def cleanup_memory_before_load(aggressive: bool = True) -> None:
    # Clean up memory before loading a new model.
    #
    # Parameters:
    #     aggressive: If True (default), performs full multi-device CUDA cleanup with
    #                 ipc_collect and verbose logging. Used by Smart Loaders.
    #                 If False, performs gentle cleanup that only clears unused cache
    #                 without disrupting loaded models.
    #
    # Note: Neither mode unloads models - use purge_vram() for that.
    import gc
    torch_mod: Optional[ModuleType]
    try:
        import torch as torch_mod #type: ignore
    except ImportError:
        torch_mod = None
    
    if aggressive:
        log.msg("Memory Cleanup", "Starting pre-load memory cleanup...")
    
    gc.collect()
    
    if torch_mod is not None:
        # CUDA / ROCm (NVIDIA + AMD)
        if torch_mod.cuda.is_available():
            if aggressive:
                device_count = torch_mod.cuda.device_count()
                log.msg("Memory Cleanup", f"Clearing CUDA cache on {device_count} device(s)")
                for i in range(device_count):
                    with torch_mod.cuda.device(i):
                        torch_mod.cuda.empty_cache()
                        if hasattr(torch_mod.cuda, 'ipc_collect'):
                            torch_mod.cuda.ipc_collect()
            else:
                torch_mod.cuda.empty_cache()
        
        # MPS (Apple Silicon)
        if hasattr(torch_mod, 'mps') and hasattr(torch_mod.mps, 'empty_cache'):
            try:
                torch_mod.mps.empty_cache()
                if aggressive:
                    log.msg("Memory Cleanup", "Cleared MPS cache")
            except Exception:
                pass
        
        # XPU (Intel Arc)
        if hasattr(torch_mod, 'xpu') and hasattr(torch_mod.xpu, 'empty_cache'):
            try:
                torch_mod.xpu.empty_cache()
                if aggressive:
                    log.msg("Memory Cleanup", "Cleared XPU cache")
            except Exception:
                pass
        
        # NPU (Huawei/Ascend)
        npu = getattr(torch_mod, 'npu', None)
        if npu is not None and hasattr(npu, 'empty_cache'):
            try:
                npu.empty_cache()
                if aggressive:
                    log.msg("Memory Cleanup", "Cleared NPU cache")
            except Exception:
                pass
        
        # MLU (Cambricon)
        mlu = getattr(torch_mod, 'mlu', None)
        if mlu is not None and hasattr(mlu, 'empty_cache'):
            try:
                mlu.empty_cache()
                if aggressive:
                    log.msg("Memory Cleanup", "Cleared MLU cache")
            except Exception:
                pass
    
    try:
        import comfy.model_management as mm #type: ignore
        if hasattr(mm, 'soft_empty_cache'):
            mm.soft_empty_cache()
    except Exception:
        pass
    
    if aggressive:
        log.msg("Memory Cleanup", "✓ Memory cleanup complete")


# ============================================================================
# Video resolution presets and mappings
# ============================================================================

VIDEO_RESOLUTION_PRESETS = [
    "Custom",
    "480x832",
    "576x1024",
    "--- 9:16 ---",
    "240x426 (240p)",
    "360x640 (360p)",
    "480x853 (SD)",
    "720x1280 (HD)",
    "1080x1920 (FullHD)",
    "1440x2560 (2K)",
    "2160x3840 (4K)",
    "4320x7680 (8K)",
    "--- 16:9 ---",
    "832x480",
    "1024x576",
    "426x240 (240p)",
    "640x360 (360p)",
    "853x480 (SD)",
    "1280x720 (HD)",
    "1920x1080 (FullHD)",
    "2560x1440 (2K)",
    "3840x2160 (4K)",
    "7680x4320 (8K)",
]

VIDEO_RESOLUTION_MAP = {
    "480x832": (480, 832),
    "576x1024": (576, 1024),
    "240x426 (240p)": (240, 426),
    "360x640 (360p)": (360, 640),
    "480x853 (SD)": (480, 853),
    "720x1280 (HD)": (720, 1280),
    "1080x1920 (FullHD)": (1080, 1920),
    "1440x2560 (2K)": (1440, 2560),
    "2160x3840 (4K)": (2160, 3840),
    "4320x7680 (8K)": (4320, 7680),
    "832x480": (832, 480),
    "1024x576": (1024, 576),
    "426x240 (240p)": (426, 240),
    "640x360 (360p)": (640, 360),
    "853x480 (SD)": (853, 480),
    "1280x720 (HD)": (1280, 720),
    "1920x1080 (FullHD)": (1920, 1080),
    "2560x1440 (2K)": (2560, 1440),
    "3840x2160 (4K)": (3840, 2160),
    "7680x4320 (8K)": (7680, 4320),
}


# Latent type presets — (channels, spatial_downscale) per model architecture
# Sourced from comfy/latent_formats.py
LATENT_TYPE_PRESETS = [
    "SD 1.5 / SDXL",
    "SD3 / Flux / Wan / HunyuanVideo",
    "Flux 2",
    "Wan 2.2 TI2V",
    "HunyuanVideo 1.5",
    "HunyuanImage 2.1",
    "HunyuanImage 2.1 Refiner",
    "LTXV",
    "Mochi",
    "Stable Cascade Prior",
    "Stable Cascade B",
    "StableAudio 1",
    "ACE Audio",
    "ACE Audio 1.5",
    "Hunyuan3D v2",
    "Cosmos1",
    "SD X4 Upscaler",
]

LATENT_TYPE_MAP = {
    "SD 1.5 / SDXL":                        (4,   8),
    "SD3 / Flux / Wan / HunyuanVideo":      (16,  8),
    "Flux 2":                                (128, 16),
    "Wan 2.2 TI2V":                          (48,  16),
    "HunyuanVideo 1.5":                      (32,  16),
    "HunyuanImage 2.1":                      (64,  32),
    "HunyuanImage 2.1 Refiner":              (64,  8),
    "LTXV":                                  (128, 32),
    "Mochi":                                 (12,  8),
    "Stable Cascade Prior":                  (16,  42),
    "Stable Cascade B":                      (4,   4),
    "StableAudio 1":                         (64,  8),
    "ACE Audio":                             (8,   8),
    "ACE Audio 1.5":                         (64,  8),
    "Hunyuan3D v2":                          (64,  8),
    "Cosmos1":                               (16,  8),
    "SD X4 Upscaler":                        (4,   8),
}


# Resolution presets and mappings for image generation
RESOLUTION_PRESETS = [
    "Custom",
    "512x512 (1:1 SD1.5)",
    "512x682 (3:4 SD1.5)",
    "512x768 (2:3 SD1.5)",
    "512x910 (9:16 SD1.5)",
    "512x952 (1:1.85 SD1.5)",
    "512x1024 (1:2 SD1.5)",
    "512x1224 (1:2.39 SD1.5)",
    "576x1024 (9:16 Krea2)",
    "640x1536 (9:21 XL/SD3/Flux/HiDream)",
    "682x512 (4:3 SD1.5)",
    "688x1024 (2:3 Krea2)",
    "720x1280 (9:16 Flux2/ZImg)",
    "768x512 (3:2 SD1.5)",
    "768x1280 (3:5 Flux)",
    "768x1344 (9:16 XL/SD3/Flux/HiDream)",
    "816x1024 (4:5 Krea2)",
    "832x1216 (2:3 XL/SD3/Flux/HiDream)",
    "832x1248 (2:3 Flux2/ZImg)",
    "864x1152 (3:4 Flux2/ZImg)",
    "896x1120 (4:5 Flux2/ZImg)",
    "896x1152 (3:4 XL/SD3/Flux/HiDream)",
    "910x512 (16:9 SD1.5)",
    "928x1664 (9:16 Qwen)",
    "952x512 (1.85:1 SD1.5)",
    "1008x1792 (9:16 Flux2/ZImg 2MP)",
    "1024x432 (2.35:1 Krea2)",
    "1024x512 (2:1 SD1.5)",
    "1024x576 (16:9 Krea2)",
    "1024x688 (3:2 Krea2)",
    "1024x768 (4:3 Krea2)",
    "1024x1024 (1:1 Krea2)",
    "1024x1024 (1:1 XL/SD3/Flux/HiDream)",
    "1024x1536 (2:3 Flux)",
    "1056x1584 (2:3 Qwen)",
    "1104x1472 (3:4 Qwen)",
    "1120x896 (5:4 Flux2/ZImg)",
    "1152x864 (4:3 Flux2/ZImg)",
    "1152x896 (4:3 XL/SD3/Flux/HiDream)",
    "1152x1728 (2:3 Flux2/ZImg 2MP)",
    "1216x832 (3:2 XL/SD3/Flux/HiDream)",
    "1224x512 (2.39:1 SD1.5)",
    "1248x832 (3:2 Flux2/ZImg)",
    "1248x1664 (3:4 Flux2/ZImg 2MP)",
    "1280x720 (16:9 Flux2/ZImg)",
    "1280x768 (5:3 Flux)",
    "1280x1600 (4:5 Flux2/ZImg 2MP)",
    "1328x1328 (1:1 Qwen)",
    "1344x768 (16:9 XL/SD3/Flux/HiDream)",
    "1440x1440 (1:1 Flux2/ZImg 2MP)",
    "1472x1104 (4:3 Qwen)",
    "1536x640 (21:9 XL/SD3/Flux/HiDream)",
    "1536x1024 (3:2 Flux)",
    "1584x1056 (3:2 Qwen)",
    "1600x1280 (5:4 Flux2/ZImg 2MP)",
    "1664x928 (16:9 Qwen)",
    "1664x1248 (4:3 Flux2/ZImg 2MP)",
    "1728x1152 (3:2 Flux2/ZImg 2MP)",
    "1792x1008 (16:9 Flux2/ZImg 2MP)",
]

RESOLUTION_MAP = {
    "512x512 (1:1 SD1.5)": (512, 512),
    "512x682 (3:4 SD1.5)": (512, 682),
    "512x768 (2:3 SD1.5)": (512, 768),
    "512x910 (9:16 SD1.5)": (512, 910),
    "512x952 (1:1.85 SD1.5)": (512, 952),
    "512x1024 (1:2 SD1.5)": (512, 1024),
    "512x1224 (1:2.39 SD1.5)": (512, 1224),
    "576x1024 (9:16 Krea2)": (576, 1024),
    "640x1536 (9:21 XL/SD3/Flux/HiDream)": (640, 1536),
    "682x512 (4:3 SD1.5)": (682, 512),
    "688x1024 (2:3 Krea2)": (688, 1024),
    "720x1280 (9:16 Flux2/ZImg)": (720, 1280),
    "768x512 (3:2 SD1.5)": (768, 512),
    "768x1280 (3:5 Flux)": (768, 1280),
    "768x1344 (9:16 XL/SD3/Flux/HiDream)": (768, 1344),
    "816x1024 (4:5 Krea2)": (816, 1024),
    "832x1216 (2:3 XL/SD3/Flux/HiDream)": (832, 1216),
    "832x1248 (2:3 Flux2/ZImg)": (832, 1248),
    "864x1152 (3:4 Flux2/ZImg)": (864, 1152),
    "896x1120 (4:5 Flux2/ZImg)": (896, 1120),
    "896x1152 (3:4 XL/SD3/Flux/HiDream)": (896, 1152),
    "910x512 (16:9 SD1.5)": (910, 512),
    "928x1664 (9:16 Qwen)": (928, 1664),
    "952x512 (1.85:1 SD1.5)": (952, 512),
    "1008x1792 (9:16 Flux2/ZImg 2MP)": (1008, 1792),
    "1024x432 (2.35:1 Krea2)": (1024, 432),
    "1024x512 (2:1 SD1.5)": (1024, 512),
    "1024x576 (16:9 Krea2)": (1024, 576),
    "1024x688 (3:2 Krea2)": (1024, 688),
    "1024x768 (4:3 Krea2)": (1024, 768),
    "1024x1024 (1:1 Krea2)": (1024, 1024),
    "1024x1024 (1:1 XL/SD3/Flux/HiDream)": (1024, 1024),
    "1024x1536 (2:3 Flux)": (1024, 1536),
    "1056x1584 (2:3 Qwen)": (1056, 1584),
    "1104x1472 (3:4 Qwen)": (1104, 1472),
    "1120x896 (5:4 Flux2/ZImg)": (1120, 896),
    "1152x864 (4:3 Flux2/ZImg)": (1152, 864),
    "1152x896 (4:3 XL/SD3/Flux/HiDream)": (1152, 896),
    "1152x1728 (2:3 Flux2/ZImg 2MP)": (1152, 1728),
    "1216x832 (3:2 XL/SD3/Flux/HiDream)": (1216, 832),
    "1224x512 (2.39:1 SD1.5)": (1224, 512),
    "1248x832 (3:2 Flux2/ZImg)": (1248, 832),
    "1248x1664 (3:4 Flux2/ZImg 2MP)": (1248, 1664),
    "1280x720 (16:9 Flux2/ZImg)": (1280, 720),
    "1280x768 (5:3 Flux)": (1280, 768),
    "1280x1600 (4:5 Flux2/ZImg 2MP)": (1280, 1600),
    "1328x1328 (1:1 Qwen)": (1328, 1328),
    "1344x768 (16:9 XL/SD3/Flux/HiDream)": (1344, 768),
    "1440x1440 (1:1 Flux2/ZImg 2MP)": (1440, 1440),
    "1472x1104 (4:3 Qwen)": (1472, 1104),
    "1536x640 (21:9 XL/SD3/Flux/HiDream)": (1536, 640),
    "1536x1024 (3:2 Flux)": (1536, 1024),
    "1584x1056 (3:2 Qwen)": (1584, 1056),
    "1600x1280 (5:4 Flux2/ZImg 2MP)": (1600, 1280),
    "1664x928 (16:9 Qwen)": (1664, 928),
    "1664x1248 (4:3 Flux2/ZImg 2MP)": (1664, 1248),
    "1728x1152 (3:2 Flux2/ZImg 2MP)": (1728, 1152),
    "1792x1008 (16:9 Flux2/ZImg 2MP)": (1792, 1008),
}

# Sampler and scheduler lists for ComfyUI (lazy-loaded to avoid import errors in standalone tests)
_SAMPLERS_COMFY = None
_SCHEDULERS_ANY = None

def get_samplers_comfy():
    """Get ComfyUI sampler list (lazy-loaded)."""
    global _SAMPLERS_COMFY
    if _SAMPLERS_COMFY is None:
        _SAMPLERS_COMFY = comfy.samplers.KSampler.SAMPLERS
    return _SAMPLERS_COMFY

def get_schedulers_any():
    """Get ComfyUI scheduler list (lazy-loaded)."""
    global _SCHEDULERS_ANY
    if _SCHEDULERS_ANY is None:
        _SCHEDULERS_ANY = comfy.samplers.KSampler.SCHEDULERS
    return _SCHEDULERS_ANY

# ============================================================================
# Filename date token resolution
# ============================================================================

# Matches %date:FORMAT% tokens, e.g. %date:dd_hh-mm-ss%
_RE_DATE_TOKEN = re.compile(r'%date:([^%]+)%')

# Maps user-friendly format codes to Python strftime codes.
# Ordered longest-first so 'yyyy' is replaced before 'yy'.
_DATE_FORMAT_MAP = [
    ('yyyy', '%Y'),
    ('yy',   '%y'),
    ('MM',   '%m'),
    ('dd',   '%d'),
    ('hh',   '%H'),
    ('mm',   '%M'),
    ('ss',   '%S'),
]

def resolve_date_tokens(s: str) -> str:
    # Replace all %date:FORMAT% tokens in s with the current date/time.
    # Supported format codes: yyyy, yy, MM, dd, hh, mm, ss
    # Example: "%date:dd_hh-mm-ss%" → "22_14-30-45"
    def _replace(m: re.Match) -> str:
        fmt = m.group(1)
        for src, dst in _DATE_FORMAT_MAP:
            fmt = fmt.replace(src, dst)
        return datetime.now().strftime(fmt)
    return _RE_DATE_TOKEN.sub(_replace, s)


# Backward compatibility - these will fail if accessed before ComfyUI is loaded
# Use get_samplers_comfy() and get_schedulers_any() instead for safe access
try:
    SAMPLERS_COMFY = comfy.samplers.KSampler.SAMPLERS
    SCHEDULERS_ANY = comfy.samplers.KSampler.SCHEDULERS
except AttributeError:
    # ComfyUI not fully loaded yet (standalone test mode)
    SAMPLERS_COMFY = []
    SCHEDULERS_ANY = []

# ============================================================================
# Slider display mode (configurable via config.json "use_sliders")
# ============================================================================

try:
    from comfy_api.latest import io as _io  # type: ignore
    SLIDER_DISPLAY = _io.NumberDisplay.slider if get_config_value("use_sliders", True) else None
except Exception:
    SLIDER_DISPLAY = None


# ============================================================================
# ComfyUI progress bar utilities
# ============================================================================

def make_comfy_progress(total: int):
    # Create a ComfyUI ProgressBar for a batch loop.
    #
    # Usage:
    #   pbar = make_comfy_progress(n_frames)
    #   for i in range(n_frames):
    #       ...
    #       pbar.update(1)
    import comfy.utils  # type: ignore  # lazy — safe at module load time
    return comfy.utils.ProgressBar(max(total, 1))


def make_comfy_tqdm_class(desc: Optional[str] = None, log_prefix: Optional[str] = None):
    # Return a tqdm-compatible class for use with hf_hub_download(tqdm_class=...).
    #
    # Replaces the duplicated ComfyTqdm inner classes in download helpers.
    # Includes set_postfix_str / reset / refresh stubs required by some HF Hub versions.
    #
    # Args:
    #     desc:       Fallback filename/description logged when the download starts.
    #     log_prefix: If set, logs the desc via log.msg(log_prefix, "  <desc>").
    #
    # Usage:
    #   hf_hub_download(..., tqdm_class=make_comfy_tqdm_class(filename, log_prefix=_LOG_PREFIX))
    import comfy.utils  # type: ignore

    _desc = desc
    _log_prefix = log_prefix

    class _ComfyTqdm:
        def __init__(self, *args, **kwargs):
            self.total = kwargs.get("total", 0) or 0
            self.n = kwargs.get("initial", 0)
            self.pbar = comfy.utils.ProgressBar(max(self.total, 1))
            if self.n > 0:
                self.pbar.update_absolute(self.n, self.total)
            if _log_prefix:
                d = kwargs.get("desc", _desc)
                if d:
                    log.msg(_log_prefix, f"  {d}")

        def update(self, n=1):
            self.n += n
            self.pbar.update_absolute(self.n, self.total)

        def close(self):
            if self.total > 0:
                self.pbar.update_absolute(self.total, self.total)

        def set_postfix_str(self, s, **kwargs):
            pass

        def reset(self, total=None):
            if total:
                self.total = total
                self.pbar = comfy.utils.ProgressBar(max(self.total, 1))
            self.n = 0

        def refresh(self):
            if self.n > 0:
                self.pbar.update_absolute(self.n, self.total)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    return _ComfyTqdm

