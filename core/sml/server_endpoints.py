# SML Server Endpoints
#
# Centralized REST API endpoints for SML functionality:
# - Config management (log level, dev mode, LLM paths)
# - Model registry (list, entry, reload)
# - Task list (filtered by vision/family)

import sys
# Prevent shadowing of ComfyUI's top-level utils package by comfy/utils.py when nodes.py has been imported first.
if 'utils' not in sys.modules:
    try:
        import utils  # type: ignore
    except ImportError:
        pass

from server import PromptServer #type: ignore
from aiohttp import web #type: ignore
import time
from typing import Any

from .logger import log
from .config_templates import get_config_value

_LOG_PREFIX = "Endpoints"

# Debounce window for /smartlml/registry/reload — multiple node-type extensions
# (Smart LM Loader + Smart Detection) hit this endpoint on a single R-key press.
# Without dedup the registry would reload twice (or more) per refresh.
_REGISTRY_RELOAD_DEBOUNCE_S = 2.0
_last_registry_reload_ts = 0.0


class SMLConfigEndpoints:
    # Config management endpoints for SML.

    def __init__(self):
        self._register_endpoints()

    def _register_endpoints(self):

        # ==================== CONFIG ====================

        @PromptServer.instance.routes.get("/smartlml/config/log_level")
        async def get_log_level(request):
            from .config_templates import get_config_value
            log_level = get_config_value("log_level", "warning")
            return web.json_response({"log_level": log_level})

        @PromptServer.instance.routes.post("/smartlml/config/log_level")
        async def set_log_level(request):
            try:
                data = await request.json()
                log_level = data.get("log_level", "").lower()

                valid_levels = ["error", "warning", "info", "debug"]
                if log_level not in valid_levels:
                    return web.json_response(
                        {"success": False, "error": f"Invalid log level. Must be one of: {', '.join(valid_levels)}"},
                        status=400
                    )

                from .config_templates import update_config_value
                success = update_config_value("log_level", log_level)

                if success:
                    from .logger import log
                    log._reload_config()
                    return web.json_response({"success": True, "log_level": log_level})
                else:
                    return web.json_response({"success": False, "error": "Failed to update config"}, status=500)
            except Exception as e:
                return web.json_response({"success": False, "error": str(e)}, status=500)

        @PromptServer.instance.routes.get("/smartlml/config/all")
        async def get_all_config(request):
            from .config_templates import get_config_value
            return web.json_response({
                "log_level": get_config_value("log_level", "warning"),
                "llm_models_path": get_config_value("llm_models_path", "LLM"),
                "retry_download_attempts": get_config_value("retry_download_attempts", 2),
                "hf_token": get_config_value("hf_token", "")
            })

        @PromptServer.instance.routes.post("/smartlml/config/update")
        async def update_config(request):
            try:
                data = await request.json()
                from .config_templates import update_config_value

                valid_keys = ["llm_models_path", "retry_download_attempts", "hf_token"]
                updated = {}

                for key, value in data.items():
                    if key not in valid_keys:
                        continue

                    if key == "retry_download_attempts":
                        if not isinstance(value, int) or value < 0:
                            return web.json_response(
                                {"success": False, "error": "retry_download_attempts must be a non-negative integer"},
                                status=400
                            )
                    elif key in ["llm_models_path", "hf_token"]:
                        if not isinstance(value, str):
                            return web.json_response(
                                {"success": False, "error": f"{key} must be a string"},
                                status=400
                            )
                        # Hardening: reject path traversal, null bytes, and absurdly
                        # long values for the model directory path. Absolute paths
                        # are allowed (USB / external drive use case).
                        if key == "llm_models_path":
                            if len(value) > 4096:
                                return web.json_response(
                                    {"success": False, "error": "llm_models_path is too long (max 4096 chars)"},
                                    status=400
                                )
                            if "\x00" in value:
                                return web.json_response(
                                    {"success": False, "error": "llm_models_path contains null bytes"},
                                    status=400
                                )
                            # Reject parent-traversal segments anywhere in the path.
                            normalized = value.replace("\\", "/")
                            if any(seg == ".." for seg in normalized.split("/")):
                                return web.json_response(
                                    {"success": False, "error": "llm_models_path may not contain '..' segments"},
                                    status=400
                                )

                    if update_config_value(key, value):
                        updated[key] = value
                    else:
                        return web.json_response(
                            {"success": False, "error": f"Failed to update {key}"},
                            status=500
                        )

                return web.json_response({"success": True, "updated": updated})
            except Exception as e:
                return web.json_response({"success": False, "error": str(e)}, status=500)

        # ==================== RELOAD ALL ====================

        @PromptServer.instance.routes.get("/smartlml/reload_all")
        async def reload_all_configs(request):
            log.debug(_LOG_PREFIX, "reload_all called")
            results: dict[str, Any] = {"success": True, "reloaded": []}

            # Invalidate config cache and reload logger
            try:
                from .config_templates import invalidate_config_cache
                invalidate_config_cache()
                log._reload_config()
                results["reloaded"].append("Config (cache invalidated, log level reloaded)")
                log.debug(_LOG_PREFIX, "reload_all: config cache invalidated + log reloaded")
            except Exception as e:
                log.error(_LOG_PREFIX, f"reload_all: config reload failed: {e}")
                results["config_error"] = str(e)

            # Reload LLM few-shot training examples
            try:
                from .config_templates import reload_few_shot_configs
                fs = reload_few_shot_configs()
                results["reloaded"].append(f"Few-shot examples ({fs['modes']} modes)")
                results["few_shot"] = fs
            except Exception as e:
                log.error(_LOG_PREFIX, f"reload_all: few-shot reload failed: {e}")
                results["few_shot_error"] = str(e)

            log.debug(_LOG_PREFIX, f"reload_all: done, reloaded: {results['reloaded']}")
            return web.json_response(results)

        log.debug(_LOG_PREFIX, "Registered config endpoints")


class SMLRegistryEndpoints:
    # Model registry endpoints for the new Smart Model Loader.

    def __init__(self):
        self._register_endpoints()

    def _register_endpoints(self):

        @PromptServer.instance.routes.get("/smartlml/model_list")
        async def get_model_list(request):
            from .model_registry import get_model_list_for_api
            return web.json_response(get_model_list_for_api())

        @PromptServer.instance.routes.get("/smartlml/model_entry")
        async def get_model_entry(request):
            display_name = request.query.get("name", "")
            if not display_name:
                return web.json_response({"error": "Missing 'name' parameter"}, status=400)
            from .model_registry import get_model_entry_for_api
            entry = get_model_entry_for_api(display_name)
            if entry is None:
                return web.json_response({"error": f"Model not found: {display_name}"}, status=404)
            return web.json_response(entry)

        @PromptServer.instance.routes.post("/smartlml/model/delete")
        async def delete_model_endpoint(request):
            # Delete a model from disk by its registry display name.
            try:
                data = await request.json()
                display_name = data.get("display_name", "").strip()
                if not display_name:
                    return web.json_response(
                        {"success": False, "error": "display_name is required"}, status=400)

                # Block path traversal in the display name itself
                if ".." in display_name or "\x00" in display_name:
                    log.warning(_LOG_PREFIX, f"Blocked suspicious model delete request: {repr(display_name)}")
                    return web.json_response(
                        {"success": False, "error": "Invalid model name"}, status=400)

                from .model_files import delete_model
                result = delete_model(display_name)

                if result["success"]:
                    log.msg(_LOG_PREFIX, f"Model deleted: {display_name}")
                else:
                    log.warning(_LOG_PREFIX, f"Model delete failed: {display_name} — {result.get('error', '')}")

                status = 200 if result["success"] else 404
                return web.json_response(result, status=status)
            except Exception as e:
                log.error(_LOG_PREFIX, f"Error in model delete endpoint: {e}")
                return web.json_response(
                    {"success": False, "error": str(e)}, status=500)

        @PromptServer.instance.routes.post("/smartlml/registry/reload")
        async def reload_registry(request):
            global _last_registry_reload_ts
            now = time.monotonic()
            if (now - _last_registry_reload_ts) < _REGISTRY_RELOAD_DEBOUNCE_S:
                # Recent reload — return cached state. Avoids duplicate work
                # when multiple SML node extensions trigger refresh together.
                return web.json_response({"success": True, "debounced": True})
            _last_registry_reload_ts = now
            from .model_registry import invalidate_cache, load_all_registries
            invalidate_cache()
            load_all_registries(force=True)
            return web.json_response({"success": True})

        log.debug(_LOG_PREFIX, "Registered model registry endpoints")


class SMLTaskEndpoints:
    # Task list endpoint for the new Smart Model Loader.

    def __init__(self):
        self._register_endpoints()

    def _register_endpoints(self):

        @PromptServer.instance.routes.get("/smartlml/task_list")
        async def get_task_list(request):
            # Return filtered task names for a model.
            # Query params: has_vision (bool), family (str, optional)
            has_vision = request.query.get("has_vision", "true").lower() == "true"
            family = request.query.get("family", "")
            from .tasks import get_task_names
            return web.json_response(get_task_names(has_vision, family, with_separators=True))

        log.debug(_LOG_PREFIX, "Registered task endpoints")


class SMLDetectionEndpoints:
    # Detection model list endpoint for the Detection node.

    def __init__(self):
        self._register_endpoints()

    def _register_endpoints(self):

        @PromptServer.instance.routes.get("/smartlml/detection/model_list")
        async def get_detection_model_list(request):
            # Return detection-capable models (VLM + YOLO) with separator tokens.
            from .model_registry import get_detection_model_list
            return web.json_response(get_detection_model_list())

        log.debug(_LOG_PREFIX, "Registered detection endpoints")


def initialize_endpoints():
    # Initialize all SML server endpoints.
    try:
        SMLConfigEndpoints()
        SMLRegistryEndpoints()
        SMLTaskEndpoints()
        SMLDetectionEndpoints()

        log.msg(_LOG_PREFIX, "All server endpoints initialized successfully")
    except Exception as e:
        log.error(_LOG_PREFIX, f"Failed to initialize endpoints: {e}")