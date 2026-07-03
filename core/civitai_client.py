import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, TypedDict

import requests #type: ignore

from .logger import log

_LOG_PREFIX = "CivitAI"
_BASE_URL = "https://civitai.com"
_SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_AIR_RE = re.compile(r"^urn:air:[^:]+:[^:]+:civitai:(\d+)@(\d+)(?:\+(\d+))?$")


class CivitaiResolvedFile(TypedDict):
    air: Optional[str]
    sha256: Optional[str]
    filename: str
    download_url: str
    model_version_id: int
    file_id: Optional[int]


def _auth_headers(api_key: Optional[str]) -> Dict[str, str]:
    headers: Dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": "ComfyUI-Eclipse/ModelIntegrity",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    return headers


def _is_valid_sha(value: Optional[str]) -> bool:
    return bool(value and _SHA_RE.match(value.strip()))


def parse_air(air: str) -> Optional[Dict[str, int]]:
    if not isinstance(air, str):
        return None
    m = _AIR_RE.match(air.strip())
    if not m:
        return None
    model_id = int(m.group(1))
    version_id = int(m.group(2))
    file_id = int(m.group(3)) if m.group(3) else None
    out: Dict[str, int] = {
        "model_id": model_id,
        "version_id": version_id,
    }
    if file_id is not None:
        out["file_id"] = file_id
    return out


def get_model_version(version_id: int, api_key: Optional[str]) -> Dict[str, Any]:
    url = f"{_BASE_URL}/api/v1/model-versions/{version_id}"
    resp = requests.get(url, headers=_auth_headers(api_key), timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_model_version_by_hash(sha256: str, api_key: Optional[str]) -> Optional[Dict[str, Any]]:
    if not _is_valid_sha(sha256):
        return None
    url = f"{_BASE_URL}/api/v1/model-versions/by-hash/{sha256.strip().upper()}"
    resp = requests.get(url, headers=_auth_headers(api_key), timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _pick_file_from_version(
    version_data: Dict[str, Any],
    *,
    wanted_sha: Optional[str] = None,
    wanted_file_id: Optional[int] = None,
    download_preference: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    files = version_data.get("files")
    if not isinstance(files, list) or not files:
        return None

    if wanted_file_id is not None:
        for f in files:
            if isinstance(f, dict) and int(f.get("id", -1)) == wanted_file_id:
                return f

    primary_type = None
    for f in files:
        if isinstance(f, dict) and f.get("primary") is True:
            primary_type = f.get("type")
            break

    if download_preference and download_preference.lower() != "default":
        pref = download_preference.lower()
        matches = []
        for f in files:
            if not isinstance(f, dict):
                continue
            if primary_type and f.get("type") != primary_type:
                continue
            metadata = f.get("metadata")
            if isinstance(metadata, dict):
                fp = str(metadata.get("fp") or "").lower()
                fmt = str(metadata.get("format") or "").lower()
                if fp == pref or fmt == pref:
                    matches.append(f)
        
        if matches:
            primary_match = next((m for m in matches if m.get("primary") is True), None)
            return primary_match if primary_match else matches[0]

    if wanted_sha and _is_valid_sha(wanted_sha):
        target = wanted_sha.strip().lower()
        for f in files:
            if not isinstance(f, dict):
                continue
            hashes = f.get("hashes")
            sha = hashes.get("SHA256") if isinstance(hashes, dict) else None
            if isinstance(sha, str) and sha.lower() == target:
                return f

    for f in files:
        if isinstance(f, dict) and f.get("primary") is True:
            return f

    for f in files:
        if isinstance(f, dict):
            return f

    return None


def resolve_file_for_download(
    *,
    air: Optional[str],
    sha256: Optional[str],
    api_key: Optional[str],
    download_preference: Optional[str] = None,
) -> Optional[CivitaiResolvedFile]:
    by_air = parse_air(air) if air else None

    version: Optional[Dict[str, Any]] = None

    if by_air:
        version = get_model_version(by_air["version_id"], api_key)

    if version is None and sha256 and _is_valid_sha(sha256):
        version = get_model_version_by_hash(sha256, api_key)

    if version is None:
        return None

    wanted_file_id = by_air.get("file_id") if by_air else None
    selected = _pick_file_from_version(version, wanted_sha=sha256, wanted_file_id=wanted_file_id, download_preference=download_preference)
    if not selected:
        return None

    filename = str(selected.get("name") or "").strip()
    download_url = str(selected.get("downloadUrl") or "").strip()
    if not filename or not download_url:
        return None

    metadata = selected.get("metadata")
    if isinstance(metadata, dict):
        fp_metadata = str(metadata.get("fp") or "").lower()
        if fp_metadata and download_preference and download_preference.lower() != "default":
            filename = re.sub(r'bf16fp8|fp8bf16', fp_metadata, filename, flags=re.IGNORECASE)

    hashes = selected.get("hashes") if isinstance(selected.get("hashes"), dict) else {}
    resolved_sha = hashes.get("SHA256") if isinstance(hashes, dict) else None
    resolved_sha = resolved_sha.lower() if isinstance(resolved_sha, str) and _is_valid_sha(resolved_sha) else None

    version_id = int(version.get("id", 0)) if version.get("id") is not None else 0
    file_id = int(selected.get("id", 0)) if selected.get("id") is not None else None

    return {
        "air": version.get("air") if isinstance(version.get("air"), str) else air,
        "sha256": resolved_sha,
        "filename": filename,
        "download_url": download_url,
        "model_version_id": version_id,
        "file_id": file_id,
    }


def download_file(
    *,
    url: str,
    destination: Path,
    api_key: Optional[str],
    progress_cb=None,
) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_suffix(destination.suffix + ".part")

    # Match the Smart LM loader's console UX (tqdm bar). Fall back to a plain
    # percent line if tqdm isn't available.
    try:
        from tqdm import tqdm  # type: ignore
    except Exception:
        tqdm = None

    # Transient network errors: preserve .part file so the next call can resume.
    # Permanent errors (HTTP 4xx/5xx, bad data): delete .part and fail cleanly.
    _TRANSIENT = (
        requests.exceptions.ChunkedEncodingError,  # wraps http.client.IncompleteRead
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
    )

    # Resume if a partial file already exists.
    partial_size = tmp_path.stat().st_size if tmp_path.exists() else 0
    req_headers = _auth_headers(api_key)
    if partial_size > 0:
        req_headers = {**req_headers, "Range": f"bytes={partial_size}-"}
        log.msg(_LOG_PREFIX, f"Resuming {destination.name} from {partial_size / 1024 / 1024:.0f} MB")

    try:
        with requests.get(url, headers=req_headers, timeout=120, stream=True, allow_redirects=True) as resp:
            # 416 = Range Not Satisfiable → server says file is already complete
            if partial_size > 0 and resp.status_code == 416:
                log.msg(_LOG_PREFIX, f"{destination.name} already complete per server (416); finalising.")
                tmp_path.replace(destination)
                return True

            resp.raise_for_status()

            resuming = resp.status_code == 206
            if not resuming and partial_size > 0:
                # Server ignored the Range header → must restart
                log.warning(_LOG_PREFIX, f"Server does not support range requests; restarting {destination.name}")
                partial_size = 0

            total_remaining = 0
            try:
                total_remaining = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total_remaining = 0

            # Total is partial already written + bytes remaining from server.
            total = (partial_size + total_remaining) if total_remaining else 0
            downloaded = partial_size
            last_logged_pct = -1

            bar = tqdm(
                total=total or None,
                initial=partial_size,
                desc=f"[CivitAI] Downloading {destination.name}",
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
            ) if tqdm is not None else None

            file_mode = "ab" if resuming else "wb"
            with open(tmp_path, file_mode) as f:
                for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)

                    if bar is not None:
                        bar.update(len(chunk))
                    elif total > 0:
                        pct = int(downloaded / total * 100)
                        if pct != last_logged_pct:
                            last_logged_pct = pct
                            mb = downloaded / (1024 * 1024)
                            tot_mb = total / (1024 * 1024)
                            sys.stdout.write(
                                f"\rEclipse: [CivitAI]   Downloading {destination.name}: "
                                f"{pct}% ({mb:.0f}/{tot_mb:.0f} MB)"
                            )
                            sys.stdout.flush()

                    if progress_cb is not None:
                        try:
                            progress_cb(downloaded, total)
                        except Exception:
                            pass

            if bar is not None:
                bar.close()
            elif total > 0:
                print()  # newline after the manual progress line

        tmp_path.replace(destination)
        return True

    except _TRANSIENT as e:
        # Network interruption — .part file is preserved so the next attempt resumes.
        log.error(_LOG_PREFIX, f"Download interrupted for {destination.name}: {e}")
        log.msg(_LOG_PREFIX, f"Partial file kept at {tmp_path.name} — click Download again to resume.")
        return False
    except Exception as e:
        log.error(_LOG_PREFIX, f"Download failed for {destination.name}: {e}")
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        return False
