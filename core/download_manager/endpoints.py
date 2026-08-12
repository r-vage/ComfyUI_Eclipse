# Dedicated HTTP surface for the standalone Eclipse Download Manager.

from __future__ import annotations

import asyncio
import copy
import threading
import time
import uuid
from typing import Any

from aiohttp import web  # type: ignore
from server import PromptServer  # type: ignore

from ..logger import log
from ..model_loader.endpoints import global_mutation_denial, read_json_object_request
from .manager import get_manager
from .providers import get_destination_categories, inspect_provider

_LOG_PREFIX = "DownloadManagerEndpoints"
_MAX_PAGE_SIZE = 100
_MAX_QUEUE_BATCH = 128
_INSPECTION_TTL_SECONDS = 30 * 60
_MAX_INSPECTIONS = 8
_SORT_FIELDS = {
    "remote_path",
    "provider_type",
    "format",
    "precision",
    "size",
    "primary",
    "supported",
}
_REGISTERED = False


class InspectionStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, dict[str, Any]] = {}

    def add(self, metadata: dict[str, Any], rows: list[dict[str, Any]]) -> str:
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            while len(self._items) >= _MAX_INSPECTIONS:
                oldest = min(self._items, key=lambda key: self._items[key]["created"])
                self._items.pop(oldest, None)
            inspection_id = str(uuid.uuid4())
            self._items[inspection_id] = {
                "created": now,
                "metadata": copy.deepcopy(metadata),
                "rows": copy.deepcopy(rows),
            }
            return inspection_id

    def get(self, inspection_id: str) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            item = self._items.get(inspection_id)
            if item is None:
                raise KeyError("Inspection expired or was not found")
            return copy.deepcopy(item)

    def _prune_locked(self, now: float) -> None:
        expired = [
            key
            for key, item in self._items.items()
            if now - item["created"] > _INSPECTION_TTL_SECONDS
        ]
        for key in expired:
            self._items.pop(key, None)


_INSPECTIONS = InspectionStore()


def _error_response(error: BaseException, *, default_status: int = 500) -> web.Response:
    if isinstance(error, KeyError):
        status = 404
    elif isinstance(error, BlockingIOError):
        status = 409
    elif isinstance(error, (TypeError, ValueError)):
        status = 422
    elif isinstance(error, RuntimeError):
        status = 503
    else:
        status = default_status
    message = str(error).strip("'")
    if status == 500 or len(message) > 240:
        message = f"{type(error).__name__}: operation failed"
    return web.json_response({"success": False, "error": message}, status=status)


def _bounded_string(value: Any, name: str, maximum: int, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    result = value.strip()
    if required and not result:
        raise ValueError(f"{name} is required")
    if len(result) > maximum:
        raise ValueError(f"{name} exceeds the size limit")
    return result


def _positive_int(value: Any, name: str, default: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


def _job_ids(payload: dict[str, Any]) -> list[str]:
    values = payload.get("job_ids")
    if (
        not isinstance(values, list)
        or not 1 <= len(values) <= _MAX_QUEUE_BATCH
        or not all(isinstance(value, str) and 1 <= len(value) <= 64 for value in values)
    ):
        raise ValueError(
            f"Select between 1 and {_MAX_QUEUE_BATCH} valid download jobs"
        )
    return list(dict.fromkeys(values))


def _filter_rows(rows: list[dict[str, Any]], options: dict[str, Any]) -> list[dict[str, Any]]:
    query = _bounded_string(options.get("query"), "Search query", 256).casefold()
    compatible_only = options.get("compatible_only", True)
    show_unsupported = options.get("show_unsupported", False)
    if not isinstance(compatible_only, bool) or not isinstance(show_unsupported, bool):
        raise TypeError("Filter toggles must be true or false")
    result = []
    for row in rows:
        if not show_unsupported and row.get("supported") is not True:
            continue
        if compatible_only and not show_unsupported and not row.get("compatible_categories"):
            continue
        if query:
            haystack = " ".join(
                str(row.get(key) or "")
                for key in (
                    "remote_path",
                    "suggested_filename",
                    "provider_type",
                    "format",
                    "precision",
                    "air",
                )
            ).casefold()
            if query not in haystack:
                continue
        result.append(row)
    sort_by = options.get("sort_by", "supported")
    sort_dir = options.get("sort_dir", "desc")
    if sort_by not in _SORT_FIELDS or sort_dir not in {"asc", "desc"}:
        raise ValueError("Invalid inspection sort")

    def sort_key(row: dict[str, Any]) -> tuple[bool, Any, str]:
        value = row.get(sort_by)
        normalized = value.casefold() if isinstance(value, str) else value
        return value is None, normalized if normalized is not None else "", row["remote_path"].casefold()

    result.sort(key=sort_key, reverse=sort_dir == "desc")
    if sort_by != "supported":
        result.sort(key=lambda row: row.get("supported") is not True)
    return result


def _page(inspection_id: str, options: dict[str, Any]) -> dict[str, Any]:
    item = _INSPECTIONS.get(inspection_id)
    page = _positive_int(options.get("page"), "Page", 1, 1_000_000)
    page_size = _positive_int(options.get("page_size"), "Page size", 50, _MAX_PAGE_SIZE)
    filtered = _filter_rows(item["rows"], options)
    start = (page - 1) * page_size
    return {
        "success": True,
        "inspection_id": inspection_id,
        "inspection": item["metadata"],
        "rows": filtered[start : start + page_size],
        "page": page,
        "page_size": page_size,
        "filtered_total": len(filtered),
        "total": len(item["rows"]),
        "pages": max(1, (len(filtered) + page_size - 1) // page_size),
    }


def _query_options(request: web.Request) -> dict[str, Any]:
    query = request.query
    return {
        "page": int(query.get("page", "1")),
        "page_size": int(query.get("page_size", "50")),
        "query": query.get("query", ""),
        "compatible_only": query.get("compatible_only", "true").casefold() == "true",
        "show_unsupported": query.get("show_unsupported", "false").casefold() == "true",
        "sort_by": query.get("sort_by", "supported"),
        "sort_dir": query.get("sort_dir", "desc"),
    }


def _mutation_denial(request: web.Request) -> web.Response | None:
    return global_mutation_denial(request)


def initialize_endpoints() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    routes = PromptServer.instance.routes

    @routes.get("/eclipse/download_manager/categories")
    async def categories_endpoint(_request: web.Request) -> web.Response:
        return web.json_response({"success": True, "categories": get_destination_categories()})

    @routes.post("/eclipse/download_manager/inspect")
    async def inspect_endpoint(request: web.Request) -> web.Response:
        denial = _mutation_denial(request)
        if denial is not None:
            return denial
        try:
            payload = await read_json_object_request(request)
            provider = _bounded_string(payload.get("provider"), "Provider", 32, required=True)
            locator = _bounded_string(payload.get("locator"), "Locator", 2048, required=True)
            revision = _bounded_string(payload.get("revision"), "Revision", 256) or None
            metadata, rows = await asyncio.to_thread(
                inspect_provider, provider, locator, revision
            )
            inspection_id = _INSPECTIONS.add(metadata, rows)
            options = {
                "page": payload.get("page", 1),
                "page_size": payload.get("page_size", 50),
                "query": payload.get("query", ""),
                "compatible_only": payload.get("compatible_only", True),
                "show_unsupported": payload.get("show_unsupported", False),
                "sort_by": payload.get("sort_by", "supported"),
                "sort_dir": payload.get("sort_dir", "desc"),
            }
            return web.json_response(_page(inspection_id, options))
        except Exception as error:  # noqa: BLE001 - endpoint sanitizes provider failures
            log.warning(_LOG_PREFIX, f"Inspection failed: {type(error).__name__}")
            return _error_response(error)

    @routes.get("/eclipse/download_manager/inspection/{inspection_id}")
    async def inspection_page_endpoint(request: web.Request) -> web.Response:
        try:
            return web.json_response(_page(request.match_info["inspection_id"], _query_options(request)))
        except (KeyError, TypeError, ValueError) as error:
            return _error_response(error)

    @routes.get("/eclipse/download_manager/queue")
    async def queue_endpoint(_request: web.Request) -> web.Response:
        return web.json_response(get_manager().snapshot())

    @routes.post("/eclipse/download_manager/queue")
    async def enqueue_endpoint(request: web.Request) -> web.Response:
        denial = _mutation_denial(request)
        if denial is not None:
            return denial
        try:
            payload = await read_json_object_request(request)
            inspection_id = _bounded_string(
                payload.get("inspection_id"), "Inspection ID", 64, required=True
            )
            inspection = _INSPECTIONS.get(inspection_id)
            selection = payload.get("selection")
            if not isinstance(selection, dict):
                raise TypeError("Selection must be a JSON object")
            rows_by_key = {row["key"]: row for row in inspection["rows"]}
            if selection.get("all_filtered") is True:
                rows = _filter_rows(inspection["rows"], selection.get("filter", {}))
                excluded = selection.get("excluded_keys", [])
                if not isinstance(excluded, list) or not all(isinstance(key, str) for key in excluded):
                    raise TypeError("Excluded keys must be a string array")
                excluded_set = set(excluded)
                rows = [row for row in rows if row["key"] not in excluded_set]
            else:
                keys = selection.get("file_keys", [])
                if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
                    raise TypeError("Selected file keys must be a string array")
                rows = [rows_by_key[key] for key in dict.fromkeys(keys) if key in rows_by_key]
            if not rows or len(rows) > _MAX_QUEUE_BATCH:
                raise ValueError(f"Select between 1 and {_MAX_QUEUE_BATCH} files per queue request")
            bulk = payload.get("bulk", {})
            overrides = payload.get("overrides", {})
            if not isinstance(bulk, dict) or not isinstance(overrides, dict):
                raise TypeError("Queue assignments must be JSON objects")
            manager = get_manager()
            pairs = []
            for row in rows:
                override = overrides.get(row["key"], {})
                if not isinstance(override, dict):
                    raise TypeError("Per-file assignment must be a JSON object")
                assignment = {**bulk, **override}
                pairs.append((row, assignment))
            jobs = manager.enqueue_many(pairs)
            return web.json_response({"success": True, "jobs": jobs})
        except Exception as error:  # noqa: BLE001 - endpoint reports bounded validation errors
            return _error_response(error)

    @routes.post("/eclipse/download_manager/queue/cancel")
    async def cancel_endpoint(request: web.Request) -> web.Response:
        denial = _mutation_denial(request)
        if denial is not None:
            return denial
        try:
            payload = await read_json_object_request(request)
            job_uuid = _bounded_string(payload.get("job_uuid"), "Job UUID", 64, required=True)
            return web.json_response({"success": True, "status": get_manager().cancel(job_uuid)})
        except Exception as error:  # noqa: BLE001 - endpoint reports bounded validation errors
            return _error_response(error)

    @routes.post("/eclipse/download_manager/queue/start")
    async def start_endpoint(request: web.Request) -> web.Response:
        denial = _mutation_denial(request)
        if denial is not None:
            return denial
        try:
            payload = await read_json_object_request(request)
            jobs = get_manager().start_jobs(_job_ids(payload))
            return web.json_response({"success": True, "jobs": jobs})
        except Exception as error:  # noqa: BLE001 - endpoint reports bounded validation errors
            return _error_response(error)

    @routes.post("/eclipse/download_manager/queue/remove")
    async def remove_endpoint(request: web.Request) -> web.Response:
        denial = _mutation_denial(request)
        if denial is not None:
            return denial
        try:
            payload = await read_json_object_request(request)
            manager = get_manager()
            removed = manager.remove_jobs(_job_ids(payload))
            return web.json_response(
                {
                    "success": True,
                    "removed_job_ids": removed,
                    "jobs": manager.snapshot()["jobs"],
                }
            )
        except Exception as error:  # noqa: BLE001 - endpoint reports bounded validation errors
            return _error_response(error)

    @routes.post("/eclipse/download_manager/queue/retry")
    async def retry_endpoint(request: web.Request) -> web.Response:
        denial = _mutation_denial(request)
        if denial is not None:
            return denial
        try:
            payload = await read_json_object_request(request)
            job_uuid = _bounded_string(payload.get("job_uuid"), "Job UUID", 64, required=True)
            return web.json_response({"success": True, "job": get_manager().retry(job_uuid)})
        except Exception as error:  # noqa: BLE001 - endpoint reports bounded validation errors
            return _error_response(error)

    @routes.post("/eclipse/download_manager/queue/discard-partial")
    async def discard_partial_endpoint(request: web.Request) -> web.Response:
        denial = _mutation_denial(request)
        if denial is not None:
            return denial
        try:
            payload = await read_json_object_request(request)
            job_uuid = _bounded_string(payload.get("job_uuid"), "Job UUID", 64, required=True)
            removed_bytes = get_manager().discard_partial(job_uuid)
            return web.json_response(
                {"success": True, "removed_bytes": removed_bytes}
            )
        except Exception as error:  # noqa: BLE001 - endpoint reports bounded validation errors
            return _error_response(error)

    @routes.post("/eclipse/download_manager/bundles/export")
    async def bundle_export_endpoint(request: web.Request) -> web.Response:
        denial = _mutation_denial(request)
        if denial is not None:
            return denial
        try:
            payload = await read_json_object_request(request)
            job_ids = payload.get("job_ids", [])
            if not isinstance(job_ids, list) or not all(isinstance(value, str) for value in job_ids):
                raise TypeError("Job IDs must be a string array")
            return web.json_response({"success": True, "bundle": get_manager().export_bundle(job_ids)})
        except Exception as error:  # noqa: BLE001 - endpoint reports bounded validation errors
            return _error_response(error)

    @routes.post("/eclipse/download_manager/bundles/import")
    async def bundle_import_endpoint(request: web.Request) -> web.Response:
        denial = _mutation_denial(request)
        if denial is not None:
            return denial
        try:
            payload = await read_json_object_request(request)
            bundle = payload.get("bundle")
            if not isinstance(bundle, dict) or bundle.get("schema_version") != 1:
                raise ValueError("Unsupported download-bundle schema")
            items = bundle.get("items")
            if not isinstance(items, list) or not 1 <= len(items) <= _MAX_QUEUE_BATCH:
                raise ValueError("Download bundle must contain a bounded item list")
            manager = get_manager()
            if not all(isinstance(item, dict) for item in items):
                raise TypeError("Download bundle contains a non-object item")
            pairs = [manager.bundle_item_pair(item) for item in items]
            jobs = manager.enqueue_many(pairs)
            return web.json_response({"success": True, "jobs": jobs})
        except Exception as error:  # noqa: BLE001 - endpoint reports bounded validation errors
            return _error_response(error)

    get_manager().start()
    _REGISTERED = True
    log.msg(_LOG_PREFIX, "Standalone Download Manager endpoints initialized")
