# Eclipse Server Endpoints
#
# Centralized REST API endpoints for all Eclipse functionality:
# - Wildcard management (list, refresh, process)
# - Config management (log_level, dev_mode)
# - Smart Prompt folder/file access

import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# Prevent shadowing of ComfyUI's top-level utils package by comfy/utils.py when nodes.py has been imported first.
if "utils" not in sys.modules:
    try:
        import utils  # type: ignore  # noqa: F401
    except ImportError:
        pass

import folder_paths  # type: ignore
from aiohttp import web  # type: ignore
from server import PromptServer  # type: ignore

from .common import (
    DEFAULT_CHIP_COLOR,
    get_config_value,
    normalize_chip_color,
    update_config_value,
    update_config_values,
)
from .logger import log
from .network_security import (
    PublicAddressResolver,
    read_stream_limited,
    validate_public_http_url,
)
from .request_security import global_mutation_denial, read_json_object_request
from .wildcard_engine import get_wildcard_list, process, wildcard_load

# Inline pattern to avoid regex_patterns dependency
RE_LEADING_NUMBERS = re.compile(r"^\d+[._-]*", re.IGNORECASE)

# Module-level storage for wildcard path (set by WildcardEndpoints)
_wildcard_path: str | None = None

# Debounce repeat /eclipse/reload_all calls — multiple Eclipse extensions
# (wildcard-processor, prompt-styler, ...) hit this endpoint on R-key refresh.
_RELOAD_ALL_DEBOUNCE_S = 2.0
_last_reload_all_ts: float = 0.0
_last_reload_all_result: dict[str, Any] | None = None
_MAX_IMAGE_BYTES = 100 * 1024 * 1024
_AUDIO_SLICE_SEMAPHORE = asyncio.Semaphore(2)

# Detect ComfyUI native dynamic VRAM:
# 0.18.x: ModelPatcher gained 'model_mmap_residency'
# 0.23.0+: ModelPatcherDynamic subclass (is_dynamic() returns True) replaces that attribute
try:
    import comfy.model_patcher as _mp  # type: ignore

    _HAS_NATIVE_DYNAMIC_VRAM = hasattr(
        _mp, "ModelPatcherDynamic"
    ) or hasattr(  # 0.23.0+
        _mp.ModelPatcher, "model_mmap_residency"
    )  # 0.18.x
except Exception:  # noqa: BLE001 - optional ComfyUI compatibility probe
    _HAS_NATIVE_DYNAMIC_VRAM = False


def _read_nonempty_text_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8") as file_handle:
        return [line.strip() for line in file_handle if line.strip()]



class WildcardEndpoints:
    # Manages wildcard server endpoints.

    def __init__(self, wildcard_path: str | None = None):
        # nitialize endpoints.
        #
        # Args:
        #     wildcard_path: Path to wildcard directory. If None, uses default.
        global _wildcard_path

        if wildcard_path is None:
            wildcard_path = self._get_default_wildcard_path()

        self.wildcard_path = wildcard_path
        _wildcard_path = wildcard_path  # Store at module level for reload_all

        # Load wildcards on initialization
        log.debug("Wildcard", f"Loading wildcards from: {wildcard_path}")
        wildcard_load(wildcard_path)

        self._register_endpoints()

    def _get_default_wildcard_path(self) -> str:
        # Determine the default wildcard path.
        #
        # Priority:
        # 1. ComfyUI/models/wildcards (create if doesn't exist and copy examples)
        # 2. Extension's wildcards/ folder (fallback)
        #
        # Returns:
        #     Path to wildcard directory
        # Extension's wildcard folder (fallback)
        extension_root = os.path.dirname(os.path.dirname(__file__))
        extension_wildcard_path = os.path.join(extension_root, "wildcards")

        # Try to find ComfyUI root (go up from custom_nodes/ComfyUI_Eclipse_X)
        comfyui_root = os.path.abspath(os.path.join(extension_root, "..", ".."))
        models_wildcard_path = os.path.join(comfyui_root, "models", "wildcards")

        # Check if we're actually in a ComfyUI installation
        if os.path.exists(os.path.join(comfyui_root, "models")):
            # Create models/wildcards directory if it doesn't exist
            if not os.path.exists(models_wildcard_path):
                try:
                    os.makedirs(models_wildcard_path, exist_ok=True)
                    log.msg("Wildcard", f"Created directory: {models_wildcard_path}")

                    # Copy example files from extension's wildcards folder
                    if os.path.exists(extension_wildcard_path):
                        self._copy_example_wildcards(
                            extension_wildcard_path, models_wildcard_path
                        )
                except Exception as e:  # noqa: BLE001 - filesystem fallback boundary
                    log.error(
                        "Wildcard", f"Failed to create {models_wildcard_path}: {e}"
                    )
                    return extension_wildcard_path

            return models_wildcard_path
        else:
            # Not in a standard ComfyUI structure, use extension folder
            log.msg(
                "Wildcard",
                "Using extension's wildcard folder (ComfyUI models dir not found)",
            )
            return extension_wildcard_path

    def _copy_example_wildcards(self, source_dir: str, dest_dir: str) -> None:
        # Copy example wildcard files from source to destination.
        #
        # Args:
        #     source_dir: Source directory with example wildcards
        #     dest_dir: Destination directory
        import shutil

        try:
            copied_count = 0
            for filename in os.listdir(source_dir):
                if filename.endswith((".txt", ".yaml", ".yml")):
                    source_file = os.path.join(source_dir, filename)
                    dest_file = os.path.join(dest_dir, filename)

                    # Only copy if destination doesn't exist
                    if not os.path.exists(dest_file):
                        shutil.copy2(source_file, dest_file)
                        copied_count += 1

            if copied_count > 0:
                log.msg(
                    "Wildcard",
                    f"Copied {copied_count} example wildcard files to {dest_dir}",
                )
        except Exception as e:  # noqa: BLE001 - filesystem migration boundary
            log.error("Wildcard", f"Error copying example wildcards: {e}")

    def _register_endpoints(self):
        # Register all endpoints with PromptServer.

        # ==================== CONFIG ====================

        @PromptServer.instance.routes.get("/eclipse/config/log_level")
        async def get_log_level(request):
            # GET /eclipse/config/log_level
            #
            # Returns current log level from config.json
            log_level = get_config_value("log_level", "warning")
            return web.json_response({"log_level": log_level})

        @PromptServer.instance.routes.post("/eclipse/config/log_level")
        async def set_log_level(request):
            # POST /eclipse/config/log_level
            #
            # Updates log level in config.json
            # Body: {"log_level": "error|warning|info|debug"}
            denial = global_mutation_denial(request)
            if denial is not None:
                return denial
            try:
                data = await read_json_object_request(request)
                log_level = data.get("log_level", "").lower()

                # Validate log level
                valid_levels = ["error", "warning", "info", "debug"]
                if log_level not in valid_levels:
                    return web.json_response(
                        {
                            "success": False,
                            "error": f"Invalid log level. Must be one of: {', '.join(valid_levels)}",
                        },
                        status=400,
                    )

                # Update config
                success = update_config_value("log_level", log_level)

                if success:
                    # Reload logger config
                    from .logger import log

                    log._reload_config()
                    return web.json_response({"success": True, "log_level": log_level})
                else:
                    return web.json_response(
                        {"success": False, "error": "Failed to update config"},
                        status=500,
                    )
            except web.HTTPException:
                raise
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("Config", f"Log-level update failed: {type(e).__name__}")
                return web.json_response(
                    {"success": False, "error": "Config update failed"}, status=500
                )

        @PromptServer.instance.routes.get("/eclipse/config/all")
        async def get_all_config(request):
            # GET /eclipse/config/all
            #
            # Returns current Eclipse settings.
            try:
                chip_color = normalize_chip_color(
                    get_config_value("chip_color", DEFAULT_CHIP_COLOR)
                )
            except (TypeError, ValueError):
                chip_color = DEFAULT_CHIP_COLOR
            return web.json_response(
                {
                    "log_level": get_config_value("log_level", "warning"),
                    "vue_zoom_fix": get_config_value("vue_zoom_fix", True),
                    "vue_size_fix": get_config_value("vue_size_fix", False),
                    "hide_node_state_badges": get_config_value(
                        "hide_node_state_badges", False
                    ),
                    "vue_low_zoom_lod": get_config_value("vue_low_zoom_lod", True),
                    "vue_full_detail_zoom": get_config_value(
                        "vue_full_detail_zoom", 95
                    ),
                    "use_sliders": get_config_value("use_sliders", True),
                    "preview_culling": get_config_value("preview_culling", True),
                    "chip_color": chip_color,
                    "has_native_dynamic_vram": _HAS_NATIVE_DYNAMIC_VRAM,
                }
            )

        @PromptServer.instance.routes.post("/eclipse/config/update")
        async def update_config(request):
            # POST /eclipse/config/update
            #
            # Updates multiple config values at once
            # Body: {"key": value, ...}
            denial = global_mutation_denial(request)
            if denial is not None:
                return denial
            try:
                data = await read_json_object_request(request)

                # Validate and update each key
                valid_keys = [
                    "log_level",
                    "vue_zoom_fix",
                    "use_sliders",
                    "preview_culling",
                    "chip_color",
                ]
                updated = {}

                for key, value in data.items():
                    if key not in valid_keys:
                        continue

                    # Type validation
                    if key == "log_level" and (
                        not isinstance(value, str)
                        or value
                        not in [
                            "error",
                            "warning",
                            "info",
                            "debug",
                        ]
                    ):
                        return web.json_response(
                            {
                                "success": False,
                                "error": "log_level must be one of: error, warning, info, debug",
                            },
                            status=400,
                        )
                    elif key in (
                        "vue_zoom_fix",
                        "use_sliders",
                        "preview_culling",
                    ) and not isinstance(value, bool):
                        return web.json_response(
                            {
                                "success": False,
                                "error": f"{key} must be true or false",
                            },
                            status=400,
                        )
                    elif key == "chip_color":
                        try:
                            value = normalize_chip_color(value)
                        except (TypeError, ValueError) as error:
                            return web.json_response(
                                {"success": False, "error": str(error)}, status=400
                            )

                    updated[key] = value

                if updated and not update_config_values(updated):
                    return web.json_response(
                        {"success": False, "error": "Failed to update config"},
                        status=500,
                    )

                return web.json_response({"success": True, "updated": updated})
            except web.HTTPException:
                raise
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("Config", f"Config update failed: {type(e).__name__}")
                return web.json_response(
                    {"success": False, "error": "Config update failed"}, status=500
                )

        # ==================== WILDCARDS ====================

        @PromptServer.instance.routes.get("/eclipse/wildcards/list")
        async def handle_get_wildcard_list(request):
            # GET /eclipse/wildcards/list
            #
            # Returns:
            #     JSON list of available wildcards in format: ['__keyword1__', '__keyword2__', ...]
            try:
                wildcard_list = get_wildcard_list()
                return web.json_response(wildcard_list)
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("Wildcard", f"Error getting wildcard list: {e}")
                return web.json_response([])

        @PromptServer.instance.routes.get("/eclipse/wildcards/refresh")
        async def handle_refresh_wildcards(request):
            # GET /eclipse/wildcards/refresh
            #
            # Reloads wildcards from disk. Useful for discovering newly added wildcard files.
            #
            # Returns:
            #     JSON with success status and count of loaded wildcards
            try:
                wildcard_load(self.wildcard_path)
                wildcard_list = get_wildcard_list()

                return web.json_response(
                    {
                        "success": True,
                        "message": f"Loaded {len(wildcard_list)} wildcard groups",
                        "count": len(wildcard_list),
                    }
                )
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("Wildcard", f"Error refreshing wildcards: {e}")
                return web.json_response(
                    {"success": False, "message": str(e), "count": 0}
                )

        @PromptServer.instance.routes.post("/eclipse/wildcards/process")
        async def handle_process_wildcards(request):
            # POST /eclipse/wildcards/process
            #
            # Process text with wildcard expansion.
            #
            # Request JSON:
            # {
            #     "text": "Text with __wildcards__ and {options|go|here}",
            #     "seed": 12345 (optional)
            # }
            #
            # Returns:
            #     JSON with processed text
            try:
                # Parse request body
                if request.content_length:
                    body = await request.json()
                else:
                    body = {}

                text = body.get("text", "")
                seed = body.get("seed", None)

                if not isinstance(text, str):
                    return web.json_response(
                        {"success": False, "error": "Invalid text parameter"}
                    )

                # Process the text
                result = process(text, seed=seed)

                return web.json_response(
                    {"success": True, "input": text, "output": result, "seed": seed}
                )

            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("Wildcard", f"Error processing wildcards: {e}")
                return web.json_response({"success": False, "error": str(e)})

        log.debug("Wildcard", "Registered server endpoints")


def onprompt_populate_wildcards(json_data):
    # Preprocess wildcard nodes before execution.
    #
    # This runs BEFORE ComfyUI's execution engine, allowing us to:
    # 1. Detect seed connections in the prompt
    # 2. Extract actual seed values from connected nodes
    # 3. Process wildcards with the correct seed
    # 4. Update the prompt with processed text
    # 5. Does NOT switch mode or send UI feedback (for realtime preview support)
    prompt = json_data.get("prompt", {})

    for node_id, node_data in prompt.items():
        # Check if this is a Wildcard Processor node (old version)
        if "class_type" not in node_data:
            continue

        if node_data["class_type"] != "Wildcard Processor [Eclipse]":
            continue

        inputs = node_data.get("inputs", {})
        mode = inputs.get("mode", "populate")

        # In fixed mode, normalize the seed to 0 to ensure caching works
        # The seed is not used for wildcard processing in fixed mode
        if mode == "fixed":
            # Force seed to 0 in fixed mode so cache works regardless of seed changes
            inputs["seed"] = 0
            continue

        # Only process wildcards in populate mode
        if mode != "populate":
            continue

        wildcard_text = inputs.get("wildcard_text", "")
        if not wildcard_text or not isinstance(wildcard_text, str):
            continue

        # Get seed - check if it's connected (list format) or widget value (int)
        seed_value = inputs.get("seed", 0)

        if isinstance(seed_value, list):
            # Seed is connected - extract actual value from connected node
            try:
                connected_node_id = str(seed_value[0])
                connected_node = prompt.get(connected_node_id)

                if not connected_node:
                    log.warning(
                        "Wildcard", f"Connected seed node {connected_node_id} not found"
                    )
                    continue

                class_type = connected_node.get("class_type", "")
                connected_inputs = connected_node.get("inputs", {})

                # Handle different seed node types (like Impact Pack does)
                if class_type == "Seed (rgthree)":
                    input_seed = int(connected_inputs.get("seed", 0))
                elif class_type in ["ImpactInt", "Primitive", "PrimitiveNode"]:
                    input_seed = int(connected_inputs.get("value", 0))
                else:
                    # Try common parameter names
                    input_seed = None
                    for key in ["seed", "value", "number", "int"]:
                        if key in connected_inputs:
                            value = connected_inputs[key]
                            if not isinstance(value, list):  # Not another connection
                                input_seed = int(value)
                                break

                    if input_seed is None:
                        log.warning(
                            "Wildcard",
                            f"Could not extract seed from node type: {class_type}",
                        )
                        continue

            except Exception as e:  # noqa: BLE001 - malformed prompt-node isolation
                log.error("Wildcard", f"Error extracting seed from connection: {e}")
                continue
        else:
            # Seed is a direct value
            input_seed = int(seed_value)

        # Process wildcards with the determined seed
        try:
            processed_text = process(wildcard_text, seed=input_seed)

            # Update the populated_text in the prompt (this is what gets sent to execute)
            inputs["populated_text"] = processed_text

            # Also update the seed input so execute() receives the actual seed used
            # This ensures the seed is saved correctly in metadata
            inputs["seed"] = input_seed

        except Exception as e:  # noqa: BLE001 - prompt hook isolates node failures
            log.error("Wildcard", f"Error processing wildcards for node {node_id}: {e}")

    # CRITICAL: Must return json_data for the handler chain to continue
    return json_data


class EclipseRuntimeEndpoints:
    # Eclipse reload and Smart Prompt endpoints.

    def __init__(self):
        self.extension_root = os.path.dirname(os.path.dirname(__file__))
        self.eclipse_prompt_dir = os.path.join(self.extension_root, "prompt")
        self.repo_prompt_dir = self.eclipse_prompt_dir
        self._register_endpoints()

    def _register_endpoints(self):
        # ==================== RELOAD ALL ====================

        @PromptServer.instance.routes.get("/eclipse/reload_all")
        async def reload_all_configs(request):
            # GET /eclipse/reload_all
            #
            # Reloads ALL Eclipse configs and caches from disk:
            # - Config (invalidate cache + reload logger)
            # - Wildcards
            # - Styles
            # - Pattern processor
            global _last_reload_all_ts, _last_reload_all_result
            now = time.monotonic()
            if (
                _last_reload_all_result is not None
                and (now - _last_reload_all_ts) < _RELOAD_ALL_DEBOUNCE_S
            ):
                return web.json_response({**_last_reload_all_result, "debounced": True})

            results: dict = {"success": True, "reloaded": []}

            # 1. Invalidate config cache and reload logger
            try:
                from .common import invalidate_config_cache

                invalidate_config_cache()
                from .logger import log as _log

                _log._reload_config()
                results["reloaded"].append(
                    "Config (cache invalidated, log level reloaded)"
                )
            except Exception as e:  # noqa: BLE001 - optional reload boundary
                results["config_error"] = str(e)

            # 2. Reload wildcards
            try:
                from .wildcard_engine import get_wildcard_list, wildcard_load

                if _wildcard_path:
                    wildcard_load(_wildcard_path)
                    wc_count = len(get_wildcard_list())
                    results["reloaded"].append(f"Wildcards ({wc_count} groups)")
                    results["wildcards"] = {"count": wc_count}
                else:
                    results["wildcards_error"] = "Wildcard path not initialized"
            except Exception as e:  # noqa: BLE001 - optional reload boundary
                results["wildcards_error"] = str(e)

            # 3. Reload styles
            try:
                from .styles import reload_styles as core_reload_styles

                style_result = core_reload_styles()
                if style_result.get("success"):
                    results["reloaded"].append(
                        f"Styles ({style_result.get('total_styles', 0)} styles)"
                    )
                    results["styles"] = style_result
                else:
                    results["styles_error"] = style_result.get("error")
            except Exception as e:  # noqa: BLE001 - optional reload boundary
                results["styles_error"] = str(e)

            # 4. Invalidate pattern processor cache (reloads JSON patterns on next use)
            try:
                from .smart_text_processor import invalidate_processor

                invalidate_processor()
                results["reloaded"].append("Pattern processor")
            except Exception as e:  # noqa: BLE001 - optional reload boundary
                results["patterns_error"] = str(e)

            _last_reload_all_ts = time.monotonic()
            _last_reload_all_result = results
            return web.json_response(results)

        # ==================== SMART PROMPT / FOLDER FILES ====================

        @PromptServer.instance.routes.get("/eclipse/folder_files/{folder}")
        async def get_folder_files(request):
            # Get files from a smart prompt folder.
            folder = request.match_info.get("folder", "")
            if not folder:
                return web.json_response({})

            folder_path = os.path.join(self.eclipse_prompt_dir, folder)
            if not os.path.isdir(folder_path):
                folder_path = os.path.join(self.repo_prompt_dir, folder)

            files = {}
            if os.path.isdir(folder_path):
                folder_name = os.path.basename(folder_path)
                clean_folder_name = RE_LEADING_NUMBERS.sub("", folder_name)

                folder_files = []
                for fname in os.listdir(folder_path):
                    if fname.lower().endswith(".txt") and fname.startswith(
                        ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9")
                    ):
                        try:
                            number = int(fname.split("_")[0])
                            folder_files.append((number, fname))
                        except ValueError:
                            continue
                folder_files.sort(key=lambda x: x[0])

                for number, fname in folder_files:
                    base = os.path.splitext(fname)[0]
                    clean_base = RE_LEADING_NUMBERS.sub("", base).replace("_", " ")
                    display = f"{clean_folder_name} {clean_base}"
                    fpath = os.path.join(folder_path, fname)
                    try:
                        files[display] = await asyncio.to_thread(
                            _read_nonempty_text_lines,
                            fpath,
                        )
                    except Exception:  # noqa: BLE001 - unreadable prompt files remain isolated
                        files[display] = []

            return web.json_response(files)

        @PromptServer.instance.routes.get("/eclipse/widget_folder_mapping")
        async def get_widget_folder_mapping(request):
            # Get widget-to-folder mapping for smart prompt.
            prompt_dir = self.eclipse_prompt_dir
            if not os.path.isdir(prompt_dir):
                prompt_dir = self.repo_prompt_dir

            mapping = {}
            if os.path.isdir(prompt_dir):
                for item in os.listdir(prompt_dir):
                    item_path = os.path.join(prompt_dir, item)
                    if os.path.isdir(item_path):
                        folder_name = os.path.basename(item_path)
                        clean_folder_name = RE_LEADING_NUMBERS.sub("", folder_name)

                        folder_files = []
                        for fname in os.listdir(item_path):
                            if fname.lower().endswith(".txt") and fname.startswith(
                                ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9")
                            ):
                                try:
                                    number = int(fname.split("_")[0])
                                    folder_files.append((number, fname))
                                except ValueError:
                                    continue
                        folder_files.sort(key=lambda x: x[0])

                        for number, fname in folder_files:
                            base = os.path.splitext(fname)[0]
                            clean_base = RE_LEADING_NUMBERS.sub("", base).replace(
                                "_", " "
                            )
                            display = f"{clean_folder_name} {clean_base}"
                            mapping[display] = clean_folder_name

            return web.json_response(mapping)

        log.debug("", "Registered template and config endpoints")


class LoadImageFolderEndpoints:
    # Manages Load Image From Folder server endpoints.

    # Supported image extensions (must match RvImage_LoadImageFromFolder.py)
    SUPPORTED_EXTENSIONS = (
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".bmp",
        ".gif",
        ".tiff",
        ".tif",
    )

    def __init__(self):
        self._register_endpoints()

    def _resolve_folder_path(self, folder_path: str) -> str:
        # Resolve folder path - can be absolute or relative to input directory.
        if not folder_path:
            return folder_paths.get_input_directory()

        # Strip quotes from path
        folder_path = folder_path.strip().strip('"').strip("'")

        # If absolute path exists, use it
        if os.path.isabs(folder_path) and os.path.exists(folder_path):
            return folder_path

        # Try relative to input directory
        input_dir = folder_paths.get_input_directory()
        relative_path = os.path.join(input_dir, folder_path)
        if os.path.exists(relative_path):
            return relative_path

        # Try relative to ComfyUI root
        comfyui_root = os.path.dirname(
            os.path.dirname(folder_paths.get_input_directory())
        )
        root_relative = os.path.join(comfyui_root, folder_path)
        if os.path.exists(root_relative):
            return root_relative

        return folder_path

    def _count_images(self, folder_path: str, include_subfolders: bool) -> int:
        # Count image files in folder.
        count = 0

        if not os.path.exists(folder_path):
            return 0

        if include_subfolders:
            for root, dirs, files in os.walk(folder_path):
                for file in files:
                    if file.lower().endswith(self.SUPPORTED_EXTENSIONS):
                        count += 1
        else:
            for file in os.listdir(folder_path):
                filepath = os.path.join(folder_path, file)
                if os.path.isfile(filepath) and file.lower().endswith(
                    self.SUPPORTED_EXTENSIONS
                ):
                    count += 1

        return count

    def _register_endpoints(self):
        @PromptServer.instance.routes.post("/eclipse/load_image_folder/count")
        async def get_image_count(request):
            # POST /eclipse/load_image_folder/count
            #
            # Returns the total image count for given folder path(s).
            # Request body: {"folder_path": "...", "include_subfolders": false}
            # folder_path can contain multiple paths separated by newlines.
            try:
                data = await request.json()
                folder_path = data.get("folder_path", "")
                include_subfolders = data.get("include_subfolders", False)

                # Parse multiple folders (one per line)
                folder_lines = [
                    f.strip() for f in folder_path.strip().split("\n") if f.strip()
                ]

                total_count = 0
                folder_counts = []

                for folder_line in folder_lines:
                    resolved_path = self._resolve_folder_path(folder_line)
                    if os.path.exists(resolved_path):
                        count = self._count_images(resolved_path, include_subfolders)
                        total_count += count
                        folder_counts.append({"path": folder_line, "count": count})
                    else:
                        folder_counts.append(
                            {"path": folder_line, "count": 0, "error": "not_found"}
                        )

                return web.json_response(
                    {"total_count": total_count, "folders": folder_counts}
                )
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("LoadImageFolder", f"Error getting image count: {e}")
                return web.json_response(
                    {"error": str(e), "total_count": 0}, status=500
                )

        @PromptServer.instance.routes.post(
            "/eclipse/load_image_folder/invalidate_cache"
        )
        async def invalidate_cache(request):
            # POST /eclipse/load_image_folder/invalidate_cache
            #
            # Invalidates the file list cache for a folder.
            # Request body: {"folder_path": "..."}
            try:
                data = await request.json()
                folder_path = data.get("folder_path", "")

                # Import FileListCache from the core module
                try:
                    from .file_cache import FileListCache

                    resolved_path = self._resolve_folder_path(folder_path)
                    FileListCache.invalidate(resolved_path)
                    return web.json_response({"success": True, "path": resolved_path})
                except ImportError:
                    return web.json_response(
                        {"success": False, "error": "FileListCache not available"}
                    )
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("LoadImageFolder", f"Error invalidating cache: {e}")
                return web.json_response(
                    {"error": str(e), "success": False}, status=500
                )

        log.debug("LoadImageFolder", "Registered folder endpoints")


class LoadImageEndpoints:
    # Manages Load Image (Metadata Pipe) server endpoints.

    def __init__(self):
        self._register_endpoints()

    def _register_endpoints(self):

        @PromptServer.instance.routes.post("/eclipse/load_image/delete")
        async def delete_image_endpoint(request):
            # POST /eclipse/load_image/delete
            #
            # Deletes an image from the ComfyUI input or output folder.
            # Request body: {"filename": "image.png", "folder": "input"|"output"}
            try:
                data = await request.json()
                filename = data.get("filename", "").strip()
                folder = data.get("folder", "input").strip()

                if not filename:
                    return web.json_response(
                        {"success": False, "error": "Filename is required"}, status=400
                    )
                if folder not in ("input", "output"):
                    return web.json_response(
                        {"success": False, "error": "Invalid folder"}, status=400
                    )

                # Block path traversal and null bytes
                if ".." in filename or "\\" in filename or "\x00" in filename:
                    log.warning(
                        "LoadImage", f"Blocked path traversal attempt: {filename}"
                    )
                    return web.json_response(
                        {"success": False, "error": "Invalid filename"}, status=400
                    )

                base_dir = (
                    folder_paths.get_input_directory()
                    if folder == "input"
                    else folder_paths.get_output_directory()
                )
                filepath = os.path.join(base_dir, filename)

                # Verify the resolved path is still inside the base directory
                real_base = os.path.realpath(base_dir)
                real_file = os.path.realpath(filepath)
                if (
                    not real_file.startswith(real_base + os.sep)
                    and real_file != real_base
                ):
                    log.warning("LoadImage", f"Blocked path escape attempt: {filename}")
                    return web.json_response(
                        {"success": False, "error": "Invalid filename"}, status=400
                    )

                if not os.path.isfile(filepath):
                    return web.json_response(
                        {"success": False, "error": "File not found"}, status=404
                    )

                os.remove(filepath)
                log.msg(
                    "LoadImage",
                    f"\u2713 Deleted image '{filename}' from {folder} folder",
                )
                return web.json_response({"success": True})
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("LoadImage", f"Error deleting image: {e}")
                return web.json_response(
                    {"success": False, "error": str(e)}, status=500
                )

        @PromptServer.instance.routes.get("/eclipse/load_image/list")
        async def list_input_images_endpoint(request):
            # GET /eclipse/load_image/list
            #
            # Returns an up-to-date list of images in the ComfyUI input folder
            # (including subfolders), sorted alphabetically.
            _img_exts = {
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
                ".bmp",
                ".gif",
                ".tiff",
                ".tif",
            }
            try:
                input_dir = folder_paths.get_input_directory()
                results = []
                for root, _dirs, filenames in os.walk(input_dir):
                    for f in filenames:
                        if os.path.splitext(f)[1].lower() not in _img_exts:
                            continue
                        full = os.path.join(root, f)
                        if not os.path.isfile(full):
                            continue
                        rel = os.path.relpath(full, input_dir).replace("\\", "/")
                        results.append(rel)
                files = sorted(results)
                return web.json_response({"success": True, "files": files})
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("LoadImage", f"Error listing images: {e}")
                return web.json_response(
                    {"success": False, "error": str(e)}, status=500
                )

        @PromptServer.instance.routes.get("/eclipse/load_image/list_output")
        async def list_output_images_endpoint(request):
            # GET /eclipse/load_image/list_output
            #
            # Returns image files in the ComfyUI output folder (including subfolders),
            # sorted by modification time descending (newest first).
            _img_exts = {
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
                ".bmp",
                ".gif",
                ".tiff",
                ".tif",
            }
            try:
                output_dir = folder_paths.get_output_directory()
                results = []
                for root, _dirs, filenames in os.walk(output_dir):
                    for f in filenames:
                        if os.path.splitext(f)[1].lower() not in _img_exts:
                            continue
                        full = os.path.join(root, f)
                        if not os.path.isfile(full):
                            continue
                        rel = os.path.relpath(full, output_dir).replace("\\", "/")
                        try:
                            mtime = os.path.getmtime(full)
                        except Exception:  # noqa: BLE001 - cache cleanup is best effort
                            mtime = 0
                        results.append((rel, mtime))
                results.sort(key=lambda x: x[1], reverse=True)
                files = [r[0] for r in results]
                return web.json_response({"success": True, "files": files})
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("LoadImage", f"Error listing output images: {e}")
                return web.json_response(
                    {"success": False, "error": str(e)}, status=500
                )

        log.debug("LoadImage", "Registered Load Image endpoints")

        @PromptServer.instance.routes.post("/eclipse/load_image/upload")
        async def upload_images_endpoint(request):
            # POST /eclipse/load_image/upload (multipart/form-data)
            #
            # Accepts one or more image files and copies them to the input folder.
            # If a file with the same name exists, appends " (N)" to avoid overwriting.
            # Returns the list of saved filenames.
            _img_exts = {
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
                ".bmp",
                ".gif",
                ".tiff",
                ".tif",
            }
            saved = []
            errors = []
            try:
                reader = await request.multipart()
                input_dir = folder_paths.get_input_directory()

                async for part in reader:
                    if part.name != "images":
                        await read_stream_limited(
                            part, _MAX_IMAGE_BYTES, collect=False
                        )
                        continue

                    original_name = part.filename or ""
                    # Sanitize filename
                    safe_name = os.path.basename(original_name).strip()
                    if not safe_name or ".." in safe_name or "\x00" in safe_name:
                        errors.append(f"Invalid filename: {original_name}")
                        await read_stream_limited(
                            part, _MAX_IMAGE_BYTES, collect=False
                        )
                        continue

                    ext = os.path.splitext(safe_name)[1].lower()
                    if ext not in _img_exts:
                        errors.append(f"Unsupported format: {safe_name}")
                        await read_stream_limited(
                            part, _MAX_IMAGE_BYTES, collect=False
                        )
                        continue

                    # Read file data in bounded chunks. Do not trust multipart
                    # headers to enforce the per-file limit.
                    data = await read_stream_limited(part, _MAX_IMAGE_BYTES)
                    if not data:
                        errors.append(f"Empty file: {safe_name}")
                        continue

                    # Determine unique filename (avoid overwriting)
                    stem, suffix = os.path.splitext(safe_name)
                    dest = os.path.join(input_dir, safe_name)
                    counter = 1
                    while os.path.exists(dest):
                        dest = os.path.join(input_dir, f"{stem} ({counter}){suffix}")
                        counter += 1

                    final_name = os.path.basename(dest)
                    await asyncio.to_thread(Path(dest).write_bytes, data)

                    saved.append(final_name)
                    log.msg(
                        "LoadImage", f"\u2713 Uploaded '{final_name}' to input folder"
                    )

                return web.json_response(
                    {"success": True, "files": saved, "errors": errors}
                )
            except ValueError as e:
                log.warning("LoadImage", f"Rejected oversized upload: {e}")
                return web.json_response(
                    {
                        "success": False,
                        "error": "File too large (max 100MB per image)",
                        "files": saved,
                    },
                    status=413,
                )
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("LoadImage", f"Error uploading images: {e}")
                return web.json_response(
                    {"success": False, "error": str(e), "files": saved}, status=500
                )

        @PromptServer.instance.routes.post("/eclipse/load_image/download_url")
        async def download_url_endpoint(request):
            # POST /eclipse/load_image/download_url
            #
            # Downloads an image from a URL and saves it to the ComfyUI input folder.
            # Request body: {"url": "https://example.com/image.png"}
            # Returns: {"success": true, "filename": "saved_name.png"}
            import io as _io
            import time
            import urllib.parse

            import aiohttp as _aiohttp  # type: ignore
            from PIL import Image as PILImage  # type: ignore

            _img_exts = {
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
                ".bmp",
                ".gif",
                ".tiff",
                ".tif",
            }
            _ct_map = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/webp": ".webp",
                "image/gif": ".gif",
                "image/bmp": ".bmp",
                "image/tiff": ".tiff",
            }
            try:
                body = await request.json()
                url = (body.get("url") or "").strip()
                if not url:
                    return web.json_response(
                        {"success": False, "error": "No URL provided"}, status=400
                    )

                # Validate every destination and pin connection-time DNS results
                # to public addresses. Redirects are followed manually so each
                # target is subject to the same SSRF policy.
                timeout = _aiohttp.ClientTimeout(total=60)
                connector = _aiohttp.TCPConnector(
                    resolver=PublicAddressResolver(),
                    use_dns_cache=False,
                )
                current_url = url
                parsed = validate_public_http_url(current_url)
                async with _aiohttp.ClientSession(
                    timeout=timeout,
                    connector=connector,
                ) as session:
                    for redirect_count in range(6):
                        parsed = validate_public_http_url(current_url)
                        async with session.get(
                            current_url,
                            allow_redirects=False,
                        ) as resp:
                            if resp.status in (301, 302, 303, 307, 308):
                                if redirect_count >= 5:
                                    raise ValueError("Too many redirects")
                                location = resp.headers.get("Location")
                                if not location:
                                    raise ValueError("Redirect response has no location")
                                current_url = urllib.parse.urljoin(
                                    current_url,
                                    location,
                                )
                                continue

                            if resp.status != 200:
                                return web.json_response(
                                    {"success": False, "error": f"HTTP {resp.status}"},
                                    status=400,
                                )

                            content_length = resp.headers.get("Content-Length")
                            if (
                                content_length
                                and int(content_length) > _MAX_IMAGE_BYTES
                            ):
                                return web.json_response(
                                    {
                                        "success": False,
                                        "error": "File too large (max 100MB)",
                                    },
                                    status=400,
                                )

                            data = await read_stream_limited(
                                resp.content,
                                _MAX_IMAGE_BYTES,
                            )
                            content_type = resp.headers.get("Content-Type", "")
                            break
                    else:
                        raise ValueError("Too many redirects")

                # Determine filename and extension from URL
                url_path = urllib.parse.unquote(parsed.path)
                url_filename = os.path.basename(url_path) if url_path else ""
                ext = os.path.splitext(url_filename)[1].lower() if url_filename else ""

                if ext not in _img_exts:
                    # Try content-type mapping
                    ext = ""
                    for ct, ct_ext in _ct_map.items():
                        if ct in content_type:
                            ext = ct_ext
                            break

                if not ext:
                    # Try PIL format detection
                    try:
                        img = PILImage.open(_io.BytesIO(data))
                        fmt = (img.format or "PNG").lower()
                        ext_map = {
                            "png": ".png",
                            "jpeg": ".jpg",
                            "webp": ".webp",
                            "gif": ".gif",
                            "bmp": ".bmp",
                            "tiff": ".tiff",
                        }
                        ext = ext_map.get(fmt, ".png")
                    except Exception:  # noqa: BLE001 - image decoder rejects unknown formats
                        return web.json_response(
                            {
                                "success": False,
                                "error": "Could not identify image format",
                            },
                            status=400,
                        )

                # Validate it's a real image
                try:
                    PILImage.open(_io.BytesIO(data)).verify()
                except Exception:  # noqa: BLE001 - image decoder rejects invalid payloads
                    return web.json_response(
                        {
                            "success": False,
                            "error": "Downloaded file is not a valid image",
                        },
                        status=400,
                    )

                # Build sanitized filename
                if url_filename and os.path.splitext(url_filename)[0].strip():
                    stem = os.path.splitext(url_filename)[0]
                    safe_stem = re.sub(r"[^a-zA-Z0-9_\-. ]", "_", stem)[:200].strip()
                else:
                    safe_stem = ""

                if not safe_stem:
                    safe_stem = f"url_download_{int(time.time())}"

                safe_name = safe_stem + ext

                # Save to input folder with unique name
                input_dir = folder_paths.get_input_directory()
                dest = os.path.join(input_dir, safe_name)
                counter = 1
                stem_base = os.path.splitext(safe_name)[0]
                while os.path.exists(dest):
                    dest = os.path.join(input_dir, f"{stem_base} ({counter}){ext}")
                    counter += 1

                final_name = os.path.basename(dest)
                await asyncio.to_thread(Path(dest).write_bytes, data)

                log.msg(
                    "LoadImage",
                    f"\u2713 Downloaded '{final_name}' from URL to input folder",
                )
                return web.json_response({"success": True, "filename": final_name})

            except ValueError as e:
                log.warning("LoadImage", f"Rejected URL download: {e}")
                return web.json_response(
                    {"success": False, "error": str(e)}, status=400
                )
            except _aiohttp.ClientError as e:
                log.error("LoadImage", f"URL download failed: {e}")
                return web.json_response(
                    {"success": False, "error": f"Download failed: {e}"}, status=500
                )
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("LoadImage", f"Error downloading from URL: {e}")
                return web.json_response(
                    {"success": False, "error": str(e)}, status=500
                )


class PromptStylerEndpoints:
    # Manages Prompt Styler server endpoints.

    def __init__(self):
        self._register_endpoints()

    def _register_endpoints(self):
        @PromptServer.instance.routes.get("/eclipse/prompt_styler/styles/{mode}")
        async def get_styles_for_mode(request):
            # GET /eclipse/prompt_styler/styles/{mode}
            #
            # Returns styles for the specified mode (tag_based or natural_language).
            mode = "unknown"
            try:
                mode = request.match_info.get("mode", "tag_based")

                # Import from core styles module
                from .styles import get_styles_for_mode as core_get_styles_for_mode

                # Get styles for the requested mode
                styles = core_get_styles_for_mode(mode)

                return web.json_response(
                    {"mode": mode, "styles": styles, "count": len(styles)}
                )
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("PromptStyler", f"Error getting styles for mode {mode}: {e}")
                return web.json_response({"error": str(e), "styles": []}, status=500)

        @PromptServer.instance.routes.get("/eclipse/prompt_styler/reload")
        async def reload_styles(request):
            # GET /eclipse/prompt_styler/reload
            #
            # Reloads styles from disk. Useful for discovering newly added style files.
            try:
                from .styles import reload_styles as core_reload_styles

                result = core_reload_styles()
                log.msg(
                    "PromptStyler", f"Reloaded styles: {result['total_styles']} total"
                )
                return web.json_response(result)
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("PromptStyler", f"Error reloading styles: {e}")
                return web.json_response(
                    {"success": False, "error": str(e)}, status=500
                )

        log.debug("PromptStyler", "Registered style endpoints")


# Initialize endpoints when module is imported
class ReadPromptFilesEndpoints:
    # Manages Read Prompt Files server endpoints.

    def __init__(self):
        self._register_endpoints()

    def _resolve_file_path(self, file_path: str):
        # Resolve file path with security validation.
        # Returns (resolved_path, error_response) - error_response is None if successful
        from pathlib import Path

        import folder_paths  # type: ignore

        if not file_path or not file_path.strip():
            return None, web.json_response(
                {"error": "No file path provided"}, status=400
            )

        try:
            # Expand and resolve file path
            resolved_path = Path(file_path.strip()).expanduser()

            # If not absolute, try relative to ComfyUI root
            if not resolved_path.is_absolute():
                comfyui_root = Path(folder_paths.base_path)
                resolved_path = comfyui_root / resolved_path

            # Resolve path
            comfyui_root = Path(folder_paths.base_path).resolve()
            resolved_path = resolved_path.resolve()

            # Check if file exists and is readable
            if not resolved_path.exists():
                return None, web.json_response(
                    {"error": f"File not found: {file_path}"}, status=404
                )

            if not resolved_path.is_file():
                return None, web.json_response(
                    {"error": f"Path is not a file: {file_path}"}, status=400
                )

            return resolved_path, None

        except Exception as e:  # noqa: BLE001 - path-validation boundary
            log.error(
                "ReadPromptFiles", f"Error resolving file path '{file_path}': {e}"
            )
            return None, web.json_response(
                {"error": f"Invalid file path: {e}"}, status=400
            )

    def _count_prompts(self, file_path, encoding="utf-8"):
        # Count non-empty lines in the file - simple direct read
        # Returns (count, total_lines) or raises exception
        try:
            with open(file_path, "r", encoding=encoding, errors="replace") as f:
                lines = f.readlines()

            # Count non-empty lines (after stripping whitespace)
            prompt_count = sum(1 for line in lines if line.strip())

            return prompt_count, len(lines)

        except UnicodeDecodeError as e:
            log.error(
                "ReadPromptFiles",
                f"Encoding error reading file '{file_path}' with {encoding}: {e}",
            )
            raise ValueError(f"Encoding error with {encoding}")
        except Exception as e:
            log.error("ReadPromptFiles", f"Error reading file '{file_path}': {e}")
            raise

    def _register_endpoints(self):
        @PromptServer.instance.routes.get("/eclipse/read_prompt_files/count")
        async def get_prompt_count(request):
            # GET /eclipse/read_prompt_files/count?file_paths=...&indexing_mode=...&encoding=utf-8
            #
            # Returns the prompt count based on indexing mode.
            # Query parameters:
            #   - file_paths (required): File paths separated by newlines
            #   - indexing_mode (optional): "all_prompts" or "per_file", default "all_prompts"
            #   - encoding (optional): Text encoding, default "utf-8"
            try:
                file_paths_param = request.query.get("file_paths", "").strip()
                indexing_mode = request.query.get("indexing_mode", "all_prompts")
                encoding = request.query.get("encoding", "utf-8")

                if not file_paths_param:
                    return web.json_response(
                        {"error": "No file paths provided"}, status=400
                    )

                # Parse file paths
                file_lines = [
                    line.strip()
                    for line in file_paths_param.split("\n")
                    if line.strip()
                ]
                if not file_lines:
                    return web.json_response(
                        {"error": "No valid file paths found"}, status=400
                    )

                # Resolve and validate files
                resolved_paths = []
                for file_path in file_lines:
                    resolved_path, error_response = self._resolve_file_path(file_path)
                    if error_response:
                        # Skip invalid files but continue processing others
                        log.warning(
                            "ReadPromptFiles", f"Skipping invalid file: {file_path}"
                        )
                        continue
                    resolved_paths.append(str(resolved_path))

                if not resolved_paths:
                    return web.json_response(
                        {"error": "No valid files found"}, status=404
                    )

                # Calculate count based on indexing mode
                try:
                    if indexing_mode == "per_file":
                        # Per-file mode: return number of files
                        count = len(resolved_paths)
                        total_prompts = 0

                        # Also calculate total prompts for info
                        for file_path in resolved_paths:
                            try:
                                prompt_count, _ = self._count_prompts(
                                    file_path, encoding
                                )
                                total_prompts += prompt_count
                            except Exception as error:  # noqa: BLE001 - per-file count isolation
                                log.debug(
                                    "ReadPromptFiles",
                                    f"Could not count prompts in {file_path}: {type(error).__name__}",
                                )
                                continue

                        log.debug(
                            "ReadPromptFiles",
                            f"Per-file mode: {count} files, {total_prompts} total prompts",
                        )

                        return web.json_response(
                            {
                                "count": count,
                                "indexing_mode": "per_file",
                                "total_files": count,
                                "total_prompts": total_prompts,
                                "encoding_used": encoding,
                            }
                        )

                    else:  # all_prompts mode
                        # All-prompts mode: return total prompts across all files
                        total_prompts = 0
                        file_details = []

                        for file_path in resolved_paths:
                            try:
                                prompt_count, total_lines = self._count_prompts(
                                    file_path, encoding
                                )
                                total_prompts += prompt_count
                                file_details.append(
                                    {
                                        "file": Path(file_path).name,
                                        "prompts": prompt_count,
                                        "total_lines": total_lines,
                                    }
                                )
                            except Exception as e:  # noqa: BLE001 - per-file count isolation
                                log.warning(
                                    "ReadPromptFiles", f"Error reading {file_path}: {e}"
                                )
                                continue

                        log.debug(
                            "ReadPromptFiles",
                            f"All-prompts mode: {total_prompts} prompts from {len(resolved_paths)} files",
                        )

                        return web.json_response(
                            {
                                "count": total_prompts,
                                "indexing_mode": "all_prompts",
                                "total_files": len(resolved_paths),
                                "total_prompts": total_prompts,
                                "file_details": file_details,
                                "encoding_used": encoding,
                            }
                        )

                except ValueError as e:
                    # Encoding or validation error
                    return web.json_response({"error": str(e)}, status=400)
                except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                    return web.json_response(
                        {"error": f"Could not process files: {e}"}, status=500
                    )

            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error(
                    "ReadPromptFiles", f"Unexpected error in get_prompt_count: {e}"
                )
                return web.json_response({"error": "Internal server error"}, status=500)

        @PromptServer.instance.routes.post("/eclipse/read_prompt_files_count")
        async def get_prompt_count_post(request):
            # POST /eclipse/read_prompt_files_count
            # Body: {"file_paths": "path1\npath2\n...", "encoding": "utf-8"}
            # Returns total prompt count from all files
            try:
                data = await request.json()
                file_paths_text = data.get("file_paths", "").strip()
                encoding = data.get("encoding", "utf-8")

                if not file_paths_text:
                    return web.json_response({"count": 0})

                # Parse file paths (same logic as Python node)
                paths = []
                for line in file_paths_text.split("\n"):
                    line = line.strip()
                    if not line:
                        continue

                    # Remove quotes if present
                    if (line.startswith('"') and line.endswith('"')) or (
                        line.startswith("'") and line.endswith("'")
                    ):
                        line = line[1:-1]

                    # Convert to absolute path
                    resolved_path, error_response = self._resolve_file_path(line)
                    if error_response:
                        continue  # Skip invalid files
                    paths.append(str(resolved_path))

                if not paths:
                    return web.json_response({"count": 0})

                # Count total prompts across all files
                total_count = 0
                for file_path in paths:
                    try:
                        prompt_count, _ = self._count_prompts(file_path, encoding)
                        total_count += prompt_count
                    except Exception as e:  # noqa: BLE001 - per-file read isolation
                        log.warning(
                            "ReadPromptFiles", f"Error reading {file_path}: {e}"
                        )
                        continue

                return web.json_response({"count": total_count})

            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("ReadPromptFiles", f"Error in POST prompt count: {e}")
                return web.json_response({"count": 0})

        @PromptServer.instance.routes.post(
            "/eclipse/read_prompt_files/invalidate_cache"
        )
        async def invalidate_prompt_files_cache(request):
            # POST /eclipse/read_prompt_files/invalidate_cache
            # Body: {"file_paths": "path1\npath2\n..."}
            # Invalidates file cache for specified file paths
            try:
                from ..core.file_cache import FileListCache

                data = await request.json()
                file_paths_text = data.get("file_paths", "").strip()

                if not file_paths_text:
                    return web.json_response({"invalidated": 0})

                # Parse file paths (same logic as Python node)
                paths = []
                for line in file_paths_text.split("\n"):
                    line = line.strip()
                    if not line:
                        continue

                    # Remove quotes if present
                    if (line.startswith('"') and line.endswith('"')) or (
                        line.startswith("'") and line.endswith("'")
                    ):
                        line = line[1:-1]

                    # Convert to absolute path
                    resolved_path, error_response = self._resolve_file_path(line)
                    if error_response:
                        continue  # Skip invalid files
                    paths.append(str(resolved_path))

                # Invalidate cache for each file path
                invalidated_count = 0
                for file_path in paths:
                    try:
                        # ReadPromptFiles uses cache keys like "prompts:/path/to/file:mtime|..."
                        # We need to clear all cache entries that contain this file path
                        cache_keys_to_remove = []
                        for cache_key in FileListCache._cache:
                            if (
                                cache_key.startswith("prompts:")
                                and file_path in cache_key
                            ):
                                cache_keys_to_remove.append(cache_key)

                        # Remove matching cache entries
                        for cache_key in cache_keys_to_remove:
                            del FileListCache._cache[cache_key]
                            if cache_key in FileListCache._cache_params:
                                del FileListCache._cache_params[cache_key]
                            invalidated_count += 1

                        if cache_keys_to_remove:
                            log.msg(
                                "ReadPromptFiles",
                                f"Invalidated {len(cache_keys_to_remove)} cache entries for: {file_path}",
                            )
                        else:
                            log.debug(
                                "ReadPromptFiles",
                                f"No cache entries found for: {file_path}",
                            )

                    except Exception as e:  # noqa: BLE001 - per-file cache isolation
                        log.warning(
                            "ReadPromptFiles",
                            f"Error invalidating cache for {file_path}: {e}",
                        )

                return web.json_response({"invalidated": invalidated_count})

            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error(
                    "ReadPromptFiles", f"Error invalidating prompt files cache: {e}"
                )
                return web.json_response({"error": "Internal server error"}, status=500)

        log.debug("ReadPromptFiles", "Registered prompt file endpoints")


class PatternProcessorEndpoints:
    # Endpoints for managing SmartTextProcessor pattern cache
    def __init__(self):
        self.register_routes()

    def register_routes(self):
        @PromptServer.instance.routes.post("/eclipse/patterns/invalidate")
        async def invalidate_pattern_cache(request):
            # Invalidate the pattern processor cache to force reload on next use
            try:
                from .smart_text_processor import invalidate_processor

                invalidate_processor()
                return web.json_response(
                    {
                        "success": True,
                        "message": "Pattern cache invalidated successfully",
                    }
                )
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                return web.json_response(
                    {"success": False, "error": str(e)}, status=500
                )

        log.debug("PatternProcessor", "Registered pattern processor endpoints")


def initialize_endpoints(wildcard_path: str | None = None):
    # Initialize all Eclipse server endpoints.
    #
    # Args:
    #     wildcard_path: Path to wildcard directory. If None, uses default.
    try:
        WildcardEndpoints(wildcard_path)
        EclipseRuntimeEndpoints()
        LoadImageFolderEndpoints()
        LoadImageEndpoints()
        PromptStylerEndpoints()
        ReadPromptFilesEndpoints()
        PatternProcessorEndpoints()
        ImageSelectorEndpoints()
        AudioSliceEndpoints()

        # Register prompt handler for wildcard preprocessing
        PromptServer.instance.add_on_prompt_handler(onprompt_populate_wildcards)

        log.msg("", "All server endpoints initialized successfully")
    except Exception as e:  # noqa: BLE001 - optional processor initialization boundary
        log.error("", f"Failed to initialize endpoints: {e}")


class ImageSelectorEndpoints:
    # REST endpoints for the Image Selector node.
    #
    # POST /eclipse/image_selector/confirm
    #   Body: {"node_id": str, "indices": [int, ...]}
    #   → Stores the selection; node will consume it on next re-queue.
    #
    # POST /eclipse/image_selector/discard
    #   Body: {"node_id": str}
    #   → Clears all state for this node (next queue = fresh first run).

    def __init__(self):
        self._register_endpoints()

    def _register_endpoints(self):
        @PromptServer.instance.routes.post("/eclipse/image_selector/confirm")
        async def confirm(request):
            try:
                data = await request.json()
                node_id = str(data.get("node_id", ""))
                indices = data.get("indices", [])
                if not node_id:
                    return web.json_response({"error": "node_id required"}, status=400)
                if not isinstance(indices, list) or not all(
                    isinstance(i, int) for i in indices
                ):
                    return web.json_response(
                        {"error": "indices must be a list of ints"}, status=400
                    )
                unique_indices = []
                seen = set()
                for i in indices:
                    if i not in seen:
                        seen.add(i)
                        unique_indices.append(i)
                from ..py.RvImage_Selector import store_selection

                store_selection(node_id, unique_indices)
                log.msg("ImageSelector", f"Node {node_id}: confirmed indices {indices}")
                return web.json_response(
                    {"ok": True, "node_id": node_id, "indices": indices}
                )
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("ImageSelector", f"confirm error: {e}")
                return web.json_response({"error": str(e)}, status=500)

        @PromptServer.instance.routes.post("/eclipse/image_selector/reset_selection")
        async def reset_selection(request):
            try:
                data = await request.json()
                node_id = str(data.get("node_id", ""))
                if not node_id:
                    return web.json_response({"error": "node_id required"}, status=400)
                from ..py.RvImage_Selector import reset_selection as py_reset_selection

                py_reset_selection(node_id)
                log.msg("ImageSelector", f"Node {node_id}: selection reset")
                return web.json_response({"ok": True, "node_id": node_id})
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("ImageSelector", f"reset_selection error: {e}")
                return web.json_response({"error": str(e)}, status=500)

        @PromptServer.instance.routes.post("/eclipse/image_selector/discard")
        async def discard(request):
            try:
                data = await request.json()
                node_id = str(data.get("node_id", ""))
                if not node_id:
                    return web.json_response({"error": "node_id required"}, status=400)
                from ..py.RvImage_Selector import clear_state

                clear_state(node_id)
                log.msg("ImageSelector", f"Node {node_id}: state discarded")
                return web.json_response({"ok": True, "node_id": node_id})
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("ImageSelector", f"discard error: {e}")
                return web.json_response({"error": str(e)}, status=500)


class AudioSliceEndpoints:
    # REST endpoints for serving sliced audio segments.
    #
    # GET /eclipse/audio_slice
    #   Query: filename=str, start_time=float, duration=float
    #   → Returns the dynamically sliced audio file in WAV format.

    def __init__(self):
        self._register_endpoints()

    def _register_endpoints(self):
        @PromptServer.instance.routes.get("/eclipse/audio_slice")
        async def get_audio_slice(request):
            try:
                params = request.query
                filename = params.get("filename", "")
                if not filename:
                    return web.Response(text="filename parameter required", status=400)

                try:
                    audio_path = folder_paths.get_annotated_filepath(filename)
                except Exception as e:  # noqa: BLE001 - per-slice isolation
                    return web.Response(text=f"Invalid file path: {e}", status=400)

                if not os.path.isfile(audio_path):
                    return web.Response(text=f"File not found: {filename}", status=404)

                try:
                    start_time = float(params.get("start_time", 0.0))
                    duration = float(params.get("duration", 0.0))
                except ValueError:
                    return web.Response(
                        text="Invalid start_time or duration parameter", status=400
                    )

                # Load trimmed audio using the load function from RvAudio_LoadAudio
                from ..py.RvAudio_LoadAudio import _load_trimmed

                def _decode_and_encode_audio() -> bytes:
                    import io as python_io
                    import wave

                    import torch  # type: ignore

                    waveform, sample_rate = _load_trimmed(
                        audio_path,
                        start_time=start_time,
                        duration=duration,
                    )

                    # waveform shape: [channels, samples], [samples], or
                    # [1, channels, samples]
                    if waveform.ndim == 3:
                        waveform = waveform[0]

                    waveform = torch.clamp(waveform, -1.0, 1.0)
                    int_waveform = (waveform * 32767.0).to(torch.int16)

                    if int_waveform.ndim == 2:
                        int_waveform = int_waveform.t()
                        num_channels = int_waveform.shape[1]
                    else:
                        num_channels = 1

                    pcm_data = int_waveform.cpu().numpy().tobytes()
                    buf = python_io.BytesIO()
                    with wave.open(buf, "wb") as wav_file:
                        wav_file.setnchannels(num_channels)
                        wav_file.setsampwidth(2)
                        wav_file.setframerate(sample_rate)
                        wav_file.writeframes(pcm_data)
                    return buf.getvalue()

                async with _AUDIO_SLICE_SEMAPHORE:
                    wav_bytes = await asyncio.to_thread(_decode_and_encode_audio)

                return web.Response(
                    body=wav_bytes,
                    content_type="audio/wav",
                    headers={
                        "Accept-Ranges": "bytes",
                        "Cache-Control": "no-cache",
                    },
                )
            except Exception as e:  # noqa: BLE001 - endpoint boundary sanitizes failures
                log.error("AudioSlice", f"Error slicing audio: {e}")
                return web.Response(text=str(e), status=500)
