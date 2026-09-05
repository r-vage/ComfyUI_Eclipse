"""Download a Hugging Face dataset snapshot with API rate-limit retries."""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

DEFAULT_MAX_RETRIES = 6
DEFAULT_MAX_WORKERS = 4
MAX_RETRIES_LIMIT = 20
MAX_WORKERS_LIMIT = 32


def _bounded_environment_integer(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}.") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}.")
    return parsed


def _response_status(exception: BaseException) -> int | None:
    response = getattr(exception, "response", None)
    return getattr(response, "status_code", None)


def _response_headers(exception: BaseException) -> Mapping[str, str]:
    response = getattr(exception, "response", None)
    headers = getattr(response, "headers", None)
    return headers if isinstance(headers, Mapping) else {}


def _retry_after_header_seconds(value: str) -> int | None:
    try:
        return max(0, int(value.strip()))
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    remaining = (retry_at - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(remaining))


def retry_delay_seconds(exception: BaseException) -> int:
    """Return the server-requested delay plus a small rate-window buffer."""
    headers = _response_headers(exception)
    retry_after = headers.get("retry-after") or headers.get("Retry-After")
    if retry_after:
        seconds = _retry_after_header_seconds(retry_after)
        if seconds is not None:
            return seconds + 1

    rate_limit = headers.get("ratelimit") or headers.get("RateLimit") or ""
    match = re.search(r"(?:^|[;,])\s*t=(\d+)", rate_limit, flags=re.IGNORECASE)
    if match:
        return int(match.group(1)) + 1

    match = re.search(
        r"retry\s+after\s+(\d+)\s+seconds?",
        str(exception),
        flags=re.IGNORECASE,
    )
    if match:
        return int(match.group(1)) + 1

    return 61


def download_snapshot(
    repo_id: str,
    local_dir: str,
    revision: str | None,
    *,
    max_retries: int,
    max_workers: int,
    snapshot_download: Callable[..., str],
    sleep: Callable[[float], Any] = time.sleep,
) -> str:
    retries_used = 0
    while True:
        try:
            return snapshot_download(
                repo_id=repo_id,
                repo_type="dataset",
                local_dir=local_dir,
                revision=revision,
                max_workers=max_workers,
            )
        except Exception as exc:
            if _response_status(exc) != 429 or retries_used >= max_retries:
                raise

            retries_used += 1
            delay = retry_delay_seconds(exc)
            print(
                "Hugging Face API rate limit reached. "
                f"Retrying in {delay} seconds ({retries_used}/{max_retries}).",
                file=sys.stderr,
                flush=True,
            )
            sleep(delay)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download a complete Hugging Face dataset repository.",
    )
    parser.add_argument("repo_id")
    parser.add_argument("local_dir")
    parser.add_argument("revision", nargs="?", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        max_retries = _bounded_environment_integer(
            "ECLIPSE_HF_MAX_RETRIES",
            DEFAULT_MAX_RETRIES,
            minimum=0,
            maximum=MAX_RETRIES_LIMIT,
        )
        max_workers = _bounded_environment_integer(
            "ECLIPSE_HF_MAX_WORKERS",
            DEFAULT_MAX_WORKERS,
            minimum=1,
            maximum=MAX_WORKERS_LIMIT,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("ERROR: huggingface_hub is not installed in this Python environment.", file=sys.stderr)
        return 2

    try:
        result = download_snapshot(
            args.repo_id,
            str(Path(args.local_dir)),
            args.revision or None,
            max_retries=max_retries,
            max_workers=max_workers,
            snapshot_download=snapshot_download,
        )
    except KeyboardInterrupt:
        print("\nDownload cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - convert CLI failures to concise output
        if _response_status(exc) == 429:
            print(
                "ERROR: Hugging Face API rate limiting continued after all retries. "
                "Wait and rerun, or authenticate with `hf auth login` or HF_TOKEN.",
                file=sys.stderr,
            )
        else:
            print(f"ERROR: Download failed: {exc}", file=sys.stderr)
        return 1

    print(f"\nSnapshot ready: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
