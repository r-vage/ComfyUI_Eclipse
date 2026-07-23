# Shared outbound-network safety helpers.

import ipaddress
import socket
from urllib.parse import ParseResult, urlparse

from aiohttp.resolver import DefaultResolver  # type: ignore


def validate_public_http_url(url: str) -> ParseResult:
    # Validate URL syntax before a request. Hostname resolution is validated by
    # PublicAddressResolver at connection time, including redirect targets.
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("Only HTTP/HTTPS URLs are supported")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing credentials are not supported")

    try:
        parsed.port
    except ValueError as e:
        raise ValueError("URL contains an invalid port") from e

    # IP literals may bypass DNS resolution, so validate them here as well.
    try:
        address = ipaddress.ip_address(parsed.hostname.split("%", 1)[0])
    except ValueError:
        return parsed
    if not address.is_global:
        raise ValueError("Local and private network addresses are not allowed")
    return parsed


class PublicAddressResolver(DefaultResolver):
    # Resolve hostnames through aiohttp's normal resolver, but reject the entire
    # result if any address is not globally routable. The connector uses these
    # exact results, avoiding a separate check-then-resolve DNS race.

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ):
        results = await super().resolve(host, port, family)
        if not results:
            raise OSError(f"Hostname did not resolve: {host}")

        for result in results:
            raw_address = result["host"].split("%", 1)[0]
            try:
                address = ipaddress.ip_address(raw_address)
            except ValueError as e:
                raise OSError(f"Resolver returned an invalid address for {host}") from e
            if not address.is_global:
                raise OSError(f"Hostname resolves to a non-public address: {host}")
        return results


async def read_stream_limited(
    stream,
    max_bytes: int,
    chunk_size: int = 64 * 1024,
    *,
    collect: bool = True,
) -> bytes:
    # Read either an aiohttp response stream or multipart body part without ever
    # retaining more than max_bytes in memory.
    chunks = bytearray()
    total = 0
    read_chunk = getattr(stream, "read_chunk", None)

    while True:
        chunk = (
            await read_chunk(chunk_size)
            if callable(read_chunk)
            else await stream.read(chunk_size)
        )
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"Payload exceeds the {max_bytes}-byte limit")
        if collect:
            chunks.extend(chunk)

    return bytes(chunks)
