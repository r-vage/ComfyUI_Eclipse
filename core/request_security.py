"""Shared trust-boundary checks for Eclipse HTTP mutations."""

from __future__ import annotations

import ipaddress
import json
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web  # type: ignore

MAX_JSON_REQUEST_BYTES = 64 * 1024


def same_origin_browser_request(request: web.Request) -> bool:
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
        origin_port = origin_parts.port or (
            443 if origin_parts.scheme == "https" else 80
        )
        host_port = host_parts.port or origin_port
        return origin_port == host_port
    except ValueError:
        return False


def request_is_loopback(request: web.Request) -> bool:
    remote = request.remote
    if not remote:
        return False
    try:
        address = ipaddress.ip_address(remote.split("%", 1)[0])
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def global_mutation_denial(request: web.Request) -> web.Response | None:
    if not same_origin_browser_request(request):
        return web.json_response(
            {"success": False, "error": "Cross-origin mutation request rejected"},
            status=403,
        )
    try:
        from comfy.cli_args import args  # type: ignore

        multi_user = args.multi_user
    except (AttributeError, ImportError):
        multi_user = False
    if multi_user and not request_is_loopback(request):
        return web.json_response(
            {
                "success": False,
                "error": "Global mutations are limited to loopback clients in multi-user mode",
            },
            status=403,
        )
    return None


async def read_json_object_request(
    request: web.Request,
    max_bytes: int = MAX_JSON_REQUEST_BYTES,
) -> dict[str, Any]:
    def bad_request(message: str) -> web.HTTPBadRequest:
        return web.HTTPBadRequest(
            text=json.dumps({"success": False, "error": message}),
            content_type="application/json",
        )

    if request.content_type != "application/json":
        raise bad_request("Content-Type must be application/json")
    if request.content_length is not None and request.content_length > max_bytes:
        raise bad_request("Request body exceeds the size limit")
    raw = await request.read()
    if len(raw) > max_bytes:
        raise bad_request("Request body exceeds the size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise bad_request("Request body must be valid JSON") from error
    if not isinstance(payload, dict):
        raise bad_request("Request body must be a JSON object")
    return payload
