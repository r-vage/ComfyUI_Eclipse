# SML Server Endpoints
#
# Centralized REST API endpoints for SML functionality:
# - Config management (log level, dev mode, LLM paths)
# - Model registry (list, entry, reload)
# - Task list (filtered by vision/family)

import asyncio
import ipaddress
import json
import sys
import time
from typing import Any
from urllib.parse import urlsplit

# Prevent shadowing of ComfyUI's top-level utils package by comfy/utils.py when nodes.py has been imported first.
if "utils" not in sys.modules:
    try:
        import utils  # type: ignore  # noqa: F401
    except ImportError:
        pass

from aiohttp import web  # type: ignore
from comfy.cli_args import args  # type: ignore
from server import PromptServer  # type: ignore

from .config_templates import (
    get_config_snapshot,
    get_config_value,
    update_config_values,
)
from .credentials import get_auth_token_status
from .lifecycle import model_maintenance_if_idle
from .logger import log

_LOG_PREFIX = "Endpoints"

# Debounce window for /smartlml/registry/reload — multiple node-type extensions
# (Smart LM Loader + Smart Detection) hit this endpoint on a single R-key press.
# Without dedup the registry would reload twice (or more) per refresh.
_REGISTRY_RELOAD_DEBOUNCE_S = 2.0
_last_registry_reload_ts = 0.0
_MAX_JSON_REQUEST_BYTES = 16 * 1024


def _same_origin_browser_request(request: web.Request) -> bool:
    # Non-browser/API clients commonly omit both browser provenance headers.
    # The server still follows ComfyUI's existing API trust model for them.
    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return False

    origin = request.headers.get("Origin")
    host = request.headers.get("Host")
    if not origin or not host:
        return True

    try:
        origin_parts = urlsplit(origin)
        host_parts = urlsplit(f"//{host}")
        if origin_parts.scheme not in {"http", "https"}:
            return False
        if origin_parts.username is not None or origin_parts.password is not None:
            return False
        if not origin_parts.hostname or not host_parts.hostname:
            return False
        if origin_parts.hostname.lower() != host_parts.hostname.lower():
            return False

        origin_port = origin_parts.port
        host_port = host_parts.port
        if origin_port is not None and host_port is not None:
            return origin_port == host_port
        if origin_port is not None:
            return origin_port == (443 if origin_parts.scheme == "https" else 80)
        if host_port is not None:
            return host_port == (443 if origin_parts.scheme == "https" else 80)
        return True
    except ValueError:
        return False


def _request_is_loopback(request: web.Request) -> bool:
    remote = request.remote
    if not remote:
        return False
    try:
        address = ipaddress.ip_address(remote.split("%", 1)[0])
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped_address = getattr(address, "ipv4_mapped", None)
    return bool(mapped_address and mapped_address.is_loopback)


def _global_mutation_denial(request: web.Request) -> web.Response | None:
    if not _same_origin_browser_request(request):
        return web.json_response(
            {"success": False, "error": "Cross-origin mutation request rejected"},
            status=403,
        )
    if args.multi_user and not _request_is_loopback(request):
        return web.json_response(
            {
                "success": False,
                "error": (
                    "ComfyUI multi-user mode does not provide an authenticated "
                    "administrator role; global Smart LM mutations are limited "
                    "to loopback clients"
                ),
            },
            status=403,
        )
    return None


async def _read_json_object(
    request: web.Request,
    max_bytes: int = _MAX_JSON_REQUEST_BYTES,
) -> dict[str, Any]:
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(
            text="Content-Type must be application/json"
        )
    if request.content_length is not None and request.content_length > max_bytes:
        raise web.HTTPRequestEntityTooLarge(
            max_size=max_bytes,
            actual_size=request.content_length,
        )

    payload = await request.read()
    if len(payload) > max_bytes:
        raise web.HTTPRequestEntityTooLarge(
            max_size=max_bytes,
            actual_size=len(payload),
        )
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise web.HTTPBadRequest(text="Request body must be valid JSON") from error
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="Request body must be a JSON object")
    return data


async def _delete_model_if_idle(display_name: str) -> tuple[dict[str, Any], int]:
    # Reserve destructive maintenance without waiting behind a long inference.
    with model_maintenance_if_idle() as maintenance_acquired:
        if not maintenance_acquired:
            return (
                {
                    "success": False,
                    "error": (
                        "A Smart LM model is currently active; "
                        "retry deletion after the prompt finishes"
                    ),
                },
                409,
            )

        from .model_files import delete_model

        # Model deletion can remove large directories or call Docker. Keep that
        # blocking work off the aiohttp event-loop thread.
        result = await asyncio.to_thread(delete_model, display_name)
    return result, 200 if result["success"] else 404


async def _verify_model_if_idle(
    display_name: str,
    quantization: str | None = None,
) -> tuple[dict[str, Any], int]:
    # A forced hash can saturate the model drive for minutes. Keep it outside
    # active inference and off the aiohttp event-loop thread.
    with model_maintenance_if_idle() as maintenance_acquired:
        if not maintenance_acquired:
            return (
                {
                    "success": False,
                    "error": (
                        "A Smart LM model is currently active; "
                        "retry verification after the prompt finishes"
                    ),
                },
                409,
            )

        from .model_files import force_verify_registered_model

        result = await asyncio.to_thread(
            force_verify_registered_model,
            display_name,
            quantization,
        )
    return result, 200 if result["success"] else 422


async def _download_model_if_idle(
    display_name: str,
    quantization: str | None = None,
) -> tuple[dict[str, Any], int]:
    with model_maintenance_if_idle() as maintenance_acquired:
        if not maintenance_acquired:
            return (
                {
                    "success": False,
                    "error": (
                        "A Smart LM model is currently active; "
                        "retry the download after the prompt finishes"
                    ),
                },
                409,
            )

        from .model_acquisition import download_registered_model
        from .model_registry import get_model_entry

        entry = get_model_entry(display_name)
        if entry is None:
            return {"success": False, "error": "Registry entry was not found"}, 404
        result = await asyncio.to_thread(
            download_registered_model,
            entry,
            quantization,
            log_prefix="Registry Manager",
        )
    return result, 200


async def _registry_write_if_idle(
    operation,
    *args,
    **kwargs,
) -> tuple[dict[str, Any], int]:
    with model_maintenance_if_idle() as maintenance_acquired:
        if not maintenance_acquired:
            return (
                {
                    "success": False,
                    "error": (
                        "A Smart LM model is currently active; "
                        "retry the registry change after the prompt finishes"
                    ),
                },
                409,
            )
        result = await asyncio.to_thread(operation, *args, **kwargs)
    return result, 200


def _registry_display_name(data: dict[str, Any]) -> str:
    value = data.get("display_name", "")
    if not isinstance(value, str):
        raise TypeError("display_name must be a string")
    display_name = value.strip()
    if not display_name or len(display_name) > 512:
        raise ValueError("Invalid display_name")
    if ".." in display_name or "\x00" in display_name:
        raise ValueError("Invalid model name")
    return display_name


def _registry_quantization(data: dict[str, Any]) -> str | None:
    value = data.get("quantization")
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value.strip()) > 128:
        raise ValueError("quantization must be a string of at most 128 characters")
    return value.strip()


class SMLConfigEndpoints:
    # Config management endpoints for SML.

    def __init__(self):
        self._register_endpoints()

    def _register_endpoints(self):

        # ==================== CONFIG ====================

        @PromptServer.instance.routes.get("/smartlml/config/log_level")
        async def get_log_level(request):
            log_level = get_config_value("log_level", "warning")
            return web.json_response({"log_level": log_level})

        @PromptServer.instance.routes.post("/smartlml/config/log_level")
        async def set_log_level(request):
            denial = _global_mutation_denial(request)
            if denial is not None:
                return denial
            try:
                data = await _read_json_object(request)
                log_level_value = data.get("log_level", "")
                if not isinstance(log_level_value, str):
                    return web.json_response(
                        {"success": False, "error": "log_level must be a string"},
                        status=400,
                    )
                log_level = log_level_value.lower()

                valid_levels = ["error", "warning", "info", "debug"]
                if log_level not in valid_levels:
                    return web.json_response(
                        {
                            "success": False,
                            "error": f"Invalid log level. Must be one of: {', '.join(valid_levels)}",
                        },
                        status=400,
                    )

                from .config_templates import update_config_value

                success = update_config_value("log_level", log_level)

                if success:
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
            except Exception as e:  # noqa: BLE001 -- endpoint boundary
                log.error(_LOG_PREFIX, f"Failed to update log level: {e}")
                return web.json_response(
                    {"success": False, "error": "Failed to update log level"},
                    status=500,
                )

        @PromptServer.instance.routes.get("/smartlml/config/all")
        async def get_all_config(request):
            config = get_config_snapshot()
            hf_token_configured, hf_token_source = get_auth_token_status()
            return web.json_response(
                {
                    "log_level": config.get("log_level", "warning"),
                    "llm_models_path": config.get("llm_models_path", "LLM"),
                    "retry_download_attempts": config.get(
                        "retry_download_attempts", 2
                    ),
                    "hf_token_configured": hf_token_configured,
                    "hf_token_source": hf_token_source,
                }
            )

        @PromptServer.instance.routes.post("/smartlml/config/update")
        async def update_config(request):
            denial = _global_mutation_denial(request)
            if denial is not None:
                return denial
            try:
                data = await _read_json_object(request)

                valid_keys = ["llm_models_path", "retry_download_attempts", "hf_token"]
                pending_updates = {}
                response_updates = {}

                for key, value in data.items():
                    if key not in valid_keys:
                        return web.json_response(
                            {"success": False, "error": f"Unknown config key: {key}"},
                            status=400,
                        )

                    if key == "retry_download_attempts":
                        if (
                            isinstance(value, bool)
                            or not isinstance(value, int)
                            or not 0 <= value <= 20
                        ):
                            return web.json_response(
                                {
                                    "success": False,
                                    "error": (
                                        "retry_download_attempts must be an integer "
                                        "between 0 and 20"
                                    ),
                                },
                                status=400,
                            )
                    elif key in ["llm_models_path", "hf_token"]:
                        if not isinstance(value, str):
                            return web.json_response(
                                {"success": False, "error": f"{key} must be a string"},
                                status=400,
                            )
                        # Hardening: reject path traversal, null bytes, and absurdly
                        # long values for the model directory path. Absolute paths
                        # are allowed (USB / external drive use case).
                        if key == "llm_models_path":
                            if len(value) > 4096:
                                return web.json_response(
                                    {
                                        "success": False,
                                        "error": "llm_models_path is too long (max 4096 chars)",
                                    },
                                    status=400,
                                )
                            if "\x00" in value:
                                return web.json_response(
                                    {
                                        "success": False,
                                        "error": "llm_models_path contains null bytes",
                                    },
                                    status=400,
                                )
                            # Reject parent-traversal segments anywhere in the path.
                            normalized = value.replace("\\", "/")
                            if any(seg == ".." for seg in normalized.split("/")):
                                return web.json_response(
                                    {
                                        "success": False,
                                        "error": "llm_models_path may not contain '..' segments",
                                    },
                                    status=400,
                                )

                    pending_updates[key] = value
                    # Tokens remain write-only in every response.
                    response_updates[key] = (
                        bool(value) if key == "hf_token" else value
                    )

                if pending_updates and not update_config_values(pending_updates):
                    return web.json_response(
                        {"success": False, "error": "Failed to update config"},
                        status=500,
                    )

                return web.json_response(
                    {"success": True, "updated": response_updates}
                )
            except web.HTTPException:
                raise
            except Exception as e:  # noqa: BLE001 -- endpoint boundary
                log.error(_LOG_PREFIX, f"Failed to update Smart LM config: {e}")
                return web.json_response(
                    {"success": False, "error": "Failed to update config"},
                    status=500,
                )

        # ==================== RELOAD ALL ====================

        async def reload_all_configs(request):
            denial = _global_mutation_denial(request)
            if denial is not None:
                return denial
            log.debug(_LOG_PREFIX, "reload_all called")
            results: dict[str, Any] = {"success": True, "reloaded": []}

            # Invalidate config cache and reload logger
            try:
                from .config_templates import invalidate_config_cache

                invalidate_config_cache()
                log._reload_config()
                results["reloaded"].append(
                    "Config (cache invalidated, log level reloaded)"
                )
                log.debug(
                    _LOG_PREFIX, "reload_all: config cache invalidated + log reloaded"
                )
            except Exception as e:  # noqa: BLE001 -- independent reload boundary
                log.error(_LOG_PREFIX, f"reload_all: config reload failed: {e}")
                results["config_error"] = "Config reload failed; see server log"

            # Reload LLM few-shot training examples
            try:
                from .config_templates import reload_few_shot_configs

                fs = reload_few_shot_configs()
                results["reloaded"].append(f"Few-shot examples ({fs['modes']} modes)")
                results["few_shot"] = fs
            except Exception as e:  # noqa: BLE001 -- independent reload boundary
                log.error(_LOG_PREFIX, f"reload_all: few-shot reload failed: {e}")
                results["few_shot_error"] = "Few-shot reload failed; see server log"

            log.debug(_LOG_PREFIX, f"reload_all: done, reloaded: {results['reloaded']}")
            return web.json_response(results)

        PromptServer.instance.routes.post("/smartlml/reload_all")(
            reload_all_configs
        )

        @PromptServer.instance.routes.get("/smartlml/reload_all")
        async def reload_all_requires_post(_request):
            return web.json_response(
                {
                    "success": False,
                    "error": "This mutation requires POST",
                },
                status=405,
                headers={"Allow": "POST"},
            )

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
                return web.json_response(
                    {"error": "Missing 'name' parameter"}, status=400
                )
            from .model_registry import get_model_entry_for_api

            entry = get_model_entry_for_api(display_name)
            if entry is None:
                return web.json_response(
                    {"error": f"Model not found: {display_name}"}, status=404
                )
            return web.json_response(entry)

        @PromptServer.instance.routes.post("/smartlml/model/delete")
        async def delete_model_endpoint(request):
            # Delete a model from disk by its registry display name.
            denial = _global_mutation_denial(request)
            if denial is not None:
                return denial
            try:
                data = await _read_json_object(request)
                display_name_value = data.get("display_name", "")
                if not isinstance(display_name_value, str):
                    return web.json_response(
                        {"success": False, "error": "display_name must be a string"},
                        status=400,
                    )
                display_name = display_name_value.strip()
                if not display_name:
                    return web.json_response(
                        {"success": False, "error": "display_name is required"},
                        status=400,
                    )
                if len(display_name) > 512:
                    return web.json_response(
                        {"success": False, "error": "display_name is too long"},
                        status=400,
                    )

                # Block path traversal in the display name itself
                if ".." in display_name or "\x00" in display_name:
                    log.warning(
                        _LOG_PREFIX,
                        f"Blocked suspicious model delete request: {display_name!r}",
                    )
                    return web.json_response(
                        {"success": False, "error": "Invalid model name"}, status=400
                    )

                result, status = await _delete_model_if_idle(display_name)

                if status == 409:
                    return web.json_response(result, status=status)

                if result["success"]:
                    log.msg(_LOG_PREFIX, f"Model deleted: {display_name}")
                else:
                    log.warning(
                        _LOG_PREFIX,
                        f"Model delete failed: {display_name} — {result.get('error', '')}",
                    )

                return web.json_response(result, status=status)
            except web.HTTPException:
                raise
            except Exception as e:  # noqa: BLE001 -- endpoint boundary
                log.error(_LOG_PREFIX, f"Error in model delete endpoint: {e}")
                return web.json_response(
                    {"success": False, "error": "Model deletion failed"},
                    status=500,
                )

        @PromptServer.instance.routes.post("/smartlml/model/verify")
        async def verify_model_endpoint(request):
            # Force a full SHA-256 read of the selected local model artifacts.
            denial = _global_mutation_denial(request)
            if denial is not None:
                return denial
            try:
                data = await _read_json_object(request)
                display_name_value = data.get("display_name", "")
                quantization_value = data.get("quantization")
                if not isinstance(display_name_value, str):
                    return web.json_response(
                        {"success": False, "error": "display_name must be a string"},
                        status=400,
                    )
                display_name = display_name_value.strip()
                if not display_name or len(display_name) > 512:
                    return web.json_response(
                        {"success": False, "error": "Invalid display_name"},
                        status=400,
                    )
                if ".." in display_name or "\x00" in display_name:
                    return web.json_response(
                        {"success": False, "error": "Invalid model name"},
                        status=400,
                    )
                if quantization_value is not None and not isinstance(
                    quantization_value, str
                ):
                    return web.json_response(
                        {"success": False, "error": "quantization must be a string"},
                        status=400,
                    )
                quantization = (
                    quantization_value.strip() if quantization_value else None
                )
                if quantization is not None and len(quantization) > 128:
                    return web.json_response(
                        {"success": False, "error": "quantization is too long"},
                        status=400,
                    )

                result, status = await _verify_model_if_idle(
                    display_name,
                    quantization,
                )
                if result["success"]:
                    log.msg(
                        _LOG_PREFIX,
                        f"Forced model verification completed: {display_name}",
                    )
                else:
                    log.warning(
                        _LOG_PREFIX,
                        f"Forced model verification failed: {display_name}",
                    )
                return web.json_response(result, status=status)
            except web.HTTPException:
                raise
            except Exception as error:  # noqa: BLE001 -- endpoint boundary
                log.error(
                    _LOG_PREFIX,
                    f"Error in model verification endpoint: {error}",
                )
                return web.json_response(
                    {"success": False, "error": "Model verification failed"},
                    status=500,
                )

        @PromptServer.instance.routes.post("/smartlml/model/download")
        async def download_model_endpoint(request):
            denial = _global_mutation_denial(request)
            if denial is not None:
                return denial
            try:
                data = await _read_json_object(request)
                display_name = _registry_display_name(data)
                quantization = _registry_quantization(data)
                result, status = await _download_model_if_idle(
                    display_name,
                    quantization,
                )
                if result.get("success"):
                    log.msg(_LOG_PREFIX, f"Model download completed: {display_name}")
                return web.json_response(result, status=status)
            except web.HTTPException:
                raise
            except (TypeError, ValueError) as error:
                return web.json_response(
                    {"success": False, "error": str(error)}, status=400
                )
            except Exception as error:  # noqa: BLE001 -- endpoint boundary
                log.error(_LOG_PREFIX, f"Model download failed: {error}")
                return web.json_response(
                    {"success": False, "error": "Model download failed"},
                    status=500,
                )

        @PromptServer.instance.routes.post("/smartlml/registry/inspect")
        async def inspect_registry_entry(request):
            denial = _global_mutation_denial(request)
            if denial is not None:
                return denial
            try:
                data = await _read_json_object(request)
                entry = data.get("entry")
                original_display_name = data.get("original_display_name")
                if original_display_name is not None and not isinstance(
                    original_display_name, str
                ):
                    raise ValueError("original_display_name must be a string")
                from .model_registry import inspect_registry_candidate

                inspected = await asyncio.to_thread(
                    inspect_registry_candidate,
                    entry,
                    original_display_name=original_display_name or None,
                )
                return web.json_response({"success": True, "entry": inspected})
            except web.HTTPException:
                raise
            except (TypeError, ValueError) as error:
                return web.json_response(
                    {"success": False, "error": str(error)}, status=400
                )
            except Exception as error:  # noqa: BLE001 -- endpoint boundary
                log.error(_LOG_PREFIX, f"Registry inspection failed: {error}")
                return web.json_response(
                    {"success": False, "error": "Registry inspection failed"},
                    status=500,
                )

        @PromptServer.instance.routes.post("/smartlml/registry/upsert")
        async def upsert_registry_entry_endpoint(request):
            denial = _global_mutation_denial(request)
            if denial is not None:
                return denial
            try:
                data = await _read_json_object(request)
                entry = data.get("entry")
                original_display_name = data.get("original_display_name")
                if original_display_name is not None and not isinstance(
                    original_display_name, str
                ):
                    raise ValueError("original_display_name must be a string")
                from .model_registry import upsert_registry_entry

                result, status = await _registry_write_if_idle(
                    upsert_registry_entry,
                    entry,
                    original_display_name=original_display_name or None,
                )
                if status != 200:
                    return web.json_response(result, status=status)
                return web.json_response(
                    {"success": True, "entry": result},
                    status=status,
                )
            except web.HTTPException:
                raise
            except (TypeError, ValueError) as error:
                return web.json_response(
                    {"success": False, "error": str(error)}, status=400
                )
            except Exception as error:  # noqa: BLE001 -- endpoint boundary
                log.error(_LOG_PREFIX, f"Registry save failed: {error}")
                return web.json_response(
                    {"success": False, "error": "Registry save failed"},
                    status=500,
                )

        @PromptServer.instance.routes.post("/smartlml/registry/remove")
        async def remove_registry_entry_endpoint(request):
            denial = _global_mutation_denial(request)
            if denial is not None:
                return denial
            try:
                data = await _read_json_object(request)
                display_name = _registry_display_name(data)
                from .model_registry import remove_registry_entry

                result, status = await _registry_write_if_idle(
                    remove_registry_entry,
                    display_name,
                )
                return web.json_response(result, status=status)
            except web.HTTPException:
                raise
            except (TypeError, ValueError) as error:
                return web.json_response(
                    {"success": False, "error": str(error)}, status=400
                )
            except Exception as error:  # noqa: BLE001 -- endpoint boundary
                log.error(_LOG_PREFIX, f"Registry removal failed: {error}")
                return web.json_response(
                    {"success": False, "error": "Registry removal failed"},
                    status=500,
                )

        @PromptServer.instance.routes.post("/smartlml/registry/reload")
        async def reload_registry(request):
            denial = _global_mutation_denial(request)
            if denial is not None:
                return denial
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

            return web.json_response(
                get_task_names(has_vision, family, with_separators=True)
            )

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
    except Exception as e:  # noqa: BLE001 -- extension initialization boundary
        log.error(_LOG_PREFIX, f"Failed to initialize endpoints: {e}")
