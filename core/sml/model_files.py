# Smart Language Model File Handling
# Handles file scanning, model list generation, download utilities, and hash verification

from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass
from collections import defaultdict
import hashlib
import shutil

from .logger import log
from .config_templates import (
    get_llm_models_path,
    get_config_value,
)

# Model list cache for get_llm_model_list and get_mmproj_list (avoids repeated directory scans)
_model_list_cache: List[str] = []
_model_list_cache_time: float = 0.0
_mmproj_list_cache: List[str] = []
_mmproj_list_cache_time: float = 0.0
_MODEL_LIST_CACHE_TTL: float = 30.0  # Cache for 30 seconds


@dataclass
class VerificationResult:
    # Result of model integrity verification.
    success: bool
    corrupted_files: List[Path]  # List of files that failed hash verification
    verified_count: int  # Number of files that passed verification
    skipped_count: int  # Number of files skipped (no reference hash)


_LOG_PREFIX = ""


def download_with_progress(url: str, path: str, name: str) -> None:
    # Download file with progress bar
    import urllib.request
    from tqdm import tqdm  # type: ignore
    from .common import is_safe_url

    # Security: validate URL to prevent SSRF
    if not is_safe_url(url):
        raise ValueError(
            f"URL blocked: cannot access private or local network addresses: {url}"
        )

    request = urllib.request.urlopen(url)
    total = int(request.headers.get("Content-Length", 0))
    with tqdm(
        total=total,
        desc=f"[SmartLM] Downloading {name}",
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
    ) as progress:
        urllib.request.urlretrieve(
            url,
            path,
            reporthook=lambda count, block_size, total_size: progress.update(
                block_size
            ),
        )


def is_same_drive(path1: Path, path2: Path) -> bool:
    # Check if two paths are on the same drive/mount point.
    #
    # On Windows: compares drive letters (e.g., C: vs D:)
    # On Unix: compares mount points using os.stat().st_dev
    #
    # Args:
    #     path1: First path
    #     path2: Second path
    #
    # Returns:
    #     True if both paths are on the same drive/filesystem
    import os
    import platform

    try:
        if platform.system() == "Windows":
            # On Windows, compare drive letters
            drive1 = os.path.splitdrive(str(path1.resolve()))[0].upper()
            drive2 = os.path.splitdrive(str(path2.resolve()))[0].upper()
            return drive1 == drive2
        else:
            # On Unix, compare device IDs (st_dev)
            # Need to use existing parent dirs for paths that don't exist yet
            check_path1 = path1
            while not check_path1.exists() and check_path1.parent != check_path1:
                check_path1 = check_path1.parent

            check_path2 = path2
            while not check_path2.exists() and check_path2.parent != check_path2:
                check_path2 = check_path2.parent

            return os.stat(check_path1).st_dev == os.stat(check_path2).st_dev
    except Exception:
        # If we can't determine, assume different drives (safer)
        return False


def download_file_via_temp(
    url: str,
    final_path: Path,
    filename: str,
    expected_hash: Optional[str] = None,
    max_verify_attempts: int = 3,
) -> bool:
    # Download a file to temp folder first, verify hash, then move to final location.
    #
    # This approach is more reliable for drives with issues because:
    # - Temp folder (usually on SSD) may be faster and more reliable
    # - If verification fails, we don't leave corrupted files in the model folder
    # - The move operation is atomic on the same filesystem
    #
    # If temp folder is on the same drive as target, downloads directly to target
    # (no benefit from temp folder in that case).
    #
    # Args:
    #     url: URL to download from
    #     final_path: Final destination path for the file
    #     filename: Display name for progress bar
    #     expected_hash: Optional SHA256 hash to verify against
    #     max_verify_attempts: Max attempts to download and verify (default 3)
    #
    # Returns:
    #     True if download and verification succeeded, False otherwise
    import tempfile
    import shutil

    # Check if temp folder is on the same drive as target
    temp_check_dir = Path(tempfile.gettempdir())
    use_temp_folder = not is_same_drive(temp_check_dir, final_path)

    if not use_temp_folder:
        log.debug(
            _LOG_PREFIX, f"Temp folder is on same drive as target, downloading directly"
        )

    for attempt in range(max_verify_attempts):
        temp_dir = None
        try:
            if use_temp_folder:
                # Create temp directory for download
                temp_dir = tempfile.mkdtemp(prefix="sml_download_")
                download_path = Path(temp_dir) / filename
            else:
                # Download directly to final location
                final_path.parent.mkdir(parents=True, exist_ok=True)
                download_path = final_path

            if attempt > 0:
                log.msg(
                    _LOG_PREFIX,
                    f"Retry attempt {attempt + 1}/{max_verify_attempts} for {filename}...",
                )

            # Download to target location (temp or final)
            download_with_progress(url, str(download_path), filename)

            if not download_path.exists():
                log.error(_LOG_PREFIX, f"Download failed: file not created")
                continue

            # Verify hash if provided
            verified_hash = None
            if expected_hash:
                location_desc = (
                    "temp location" if use_temp_folder else "download location"
                )
                log.msg(_LOG_PREFIX, f"Verifying {filename} in {location_desc}...")
                actual_hash = calculate_file_hash(download_path, show_progress=True)

                if actual_hash != expected_hash:
                    log.error(
                        _LOG_PREFIX,
                        f"✗ Hash verification failed for {filename} (attempt {attempt + 1}/{max_verify_attempts})",
                    )
                    log.error(_LOG_PREFIX, f"  Expected: {expected_hash}")
                    log.error(_LOG_PREFIX, f"  Got:      {actual_hash}")
                    # Clean up and retry download
                    if download_path.exists():
                        download_path.unlink()
                    continue

                log.msg(_LOG_PREFIX, f"✓ Hash verified in {location_desc}")
                verified_hash = actual_hash

            # If using temp folder, copy to final location (keep temp for retry if copy fails)
            if use_temp_folder:
                # Ensure target directory exists
                final_path.parent.mkdir(parents=True, exist_ok=True)

                # Retry loop for copy operation (don't need to re-download if copy fails)
                max_copy_attempts = 3
                copy_success = False
                corrupted_file_exists = (
                    False  # Track if we have a corrupted file occupying sectors
                )

                for copy_attempt in range(max_copy_attempts):
                    try:
                        # On first attempt or if no corrupted file, copy directly to final path
                        # On retry after corruption: copy to temp name to force different sectors
                        if corrupted_file_exists:
                            # Copy to temporary name (corrupted file still occupies original sectors)
                            temp_final_path = (
                                final_path.parent / f"{final_path.name}.new"
                            )
                            target_path = temp_final_path
                            log.msg(
                                _LOG_PREFIX,
                                f"Copying to alternate location to avoid bad sectors...",
                            )
                        else:
                            # Delete existing file if present (first attempt)
                            if final_path.exists():
                                final_path.unlink()
                            target_path = final_path

                        # Copy from temp to target location
                        shutil.copy2(str(download_path), str(target_path))

                        if copy_attempt > 0:
                            log.msg(
                                _LOG_PREFIX,
                                f"✓ Copied {filename} (attempt {copy_attempt + 1})",
                            )
                        else:
                            log.msg(
                                _LOG_PREFIX, f"✓ Copied {filename} to final location"
                            )

                        # Verify hash after copy to detect target drive issues
                        if expected_hash:
                            log.msg(_LOG_PREFIX, f"Verifying {filename} after copy...")
                            post_copy_hash = calculate_file_hash(
                                target_path, show_progress=False
                            )

                            if post_copy_hash != expected_hash:
                                log.error(
                                    _LOG_PREFIX,
                                    f"⚠ DRIVE ISSUE DETECTED: File corrupted after copy to target drive!",
                                )
                                log.error(
                                    _LOG_PREFIX,
                                    f"  File was verified correct in temp folder but corrupted after copying.",
                                )
                                log.error(
                                    _LOG_PREFIX,
                                    f"  This indicates your target drive may have bad sectors or write errors.",
                                )
                                log.error(_LOG_PREFIX, f"  Expected: {expected_hash}")
                                log.error(_LOG_PREFIX, f"  Got:      {post_copy_hash}")

                                if not corrupted_file_exists:
                                    # First corruption - keep the file to occupy bad sectors
                                    corrupted_file_exists = True
                                    log.msg(
                                        _LOG_PREFIX,
                                        f"Keeping corrupted file to force write to different sectors on retry...",
                                    )
                                else:
                                    # Retry with temp name also failed - delete it
                                    if target_path.exists():
                                        target_path.unlink()

                                if copy_attempt < max_copy_attempts - 1:
                                    log.msg(
                                        _LOG_PREFIX,
                                        f"Retrying copy (attempt {copy_attempt + 2}/{max_copy_attempts})...",
                                    )
                                continue  # Retry copy

                            # Use the post-copy hash (verified to match expected)
                            verified_hash = post_copy_hash

                        # If we used a temp name, rename to final name
                        if corrupted_file_exists:
                            # Delete the corrupted file and rename the good one
                            if final_path.exists():
                                final_path.unlink()
                            target_path.rename(final_path)
                            log.msg(
                                _LOG_PREFIX,
                                f"✓ Renamed to final filename after successful verification",
                            )

                        copy_success = True
                        break

                    except Exception as e:
                        log.error(
                            _LOG_PREFIX,
                            f"Copy error (attempt {copy_attempt + 1}/{max_copy_attempts}): {e}",
                        )
                        # Clean up temp file if it exists
                        if corrupted_file_exists:
                            temp_final_path = (
                                final_path.parent / f"{final_path.name}.new"
                            )
                            if temp_final_path.exists():
                                try:
                                    temp_final_path.unlink()
                                except Exception:
                                    pass

                # Clean up after copy attempts
                if copy_success:
                    # Clean up temp download file
                    try:
                        if download_path.exists():
                            download_path.unlink()
                    except Exception:
                        pass  # Not critical if temp cleanup fails
                else:
                    # All copy attempts failed - clean up and retry download
                    log.error(
                        _LOG_PREFIX,
                        f"All {max_copy_attempts} copy attempts failed for {filename}",
                    )
                    # Clean up any leftover files
                    if final_path.exists():
                        try:
                            final_path.unlink()
                        except Exception:
                            pass
                    temp_final_path = final_path.parent / f"{final_path.name}.new"
                    if temp_final_path.exists():
                        try:
                            temp_final_path.unlink()
                        except Exception:
                            pass
                    if download_path.exists():
                        download_path.unlink()
                    continue  # Retry download

            # Save hash file only if we have a verified hash
            if verified_hash:
                try:
                    sha_file = final_path.parent / f"{final_path.name}.sha256"
                    sha_file.write_text(verified_hash)
                    log.debug(_LOG_PREFIX, f"Saved hash file: {sha_file.name}")
                except Exception as e:
                    log.warning(_LOG_PREFIX, f"Could not cache hash: {e}")

            return True

        except Exception as e:
            log.error(
                _LOG_PREFIX,
                f"Download error (attempt {attempt + 1}/{max_verify_attempts}): {e}",
            )
            # Clean up failed download if downloading directly
            if not use_temp_folder and final_path.exists():
                try:
                    final_path.unlink()
                except Exception:
                    pass
        finally:
            # Clean up temp directory
            if temp_dir and Path(temp_dir).exists():
                try:
                    shutil.rmtree(temp_dir)
                except Exception:
                    pass

    log.error(
        _LOG_PREFIX,
        f"✗ Failed to download {filename} after {max_verify_attempts} attempts",
    )
    return False


def get_hf_file_hash(
    repo_id: str, filename: str, token: Optional[str] = None
) -> str | None:
    # Get SHA256 hash for a file from HuggingFace metadata.
    #
    # Args:
    #     repo_id: HuggingFace repo_id (user/repo format)
    #     filename: Filename to get hash for
    #     token: Optional HuggingFace token
    #
    # Returns:
    #     SHA256 hash string or None if not available
    try:
        from huggingface_hub import hf_hub_url, get_hf_file_metadata  # type: ignore
        import os

        # Priority: passed token -> HF_TOKEN env -> HUGGING_FACE_HUB_TOKEN env -> config
        hf_token = (
            token
            or os.environ.get("HF_TOKEN")
            or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        )
        if not hf_token:
            config_token = get_config_value("hf_token", "")
            if config_token and config_token.strip():
                hf_token = config_token.strip()
        if not hf_token:
            hf_token = None

        url = hf_hub_url(repo_id=repo_id, filename=filename, repo_type="model")
        metadata = get_hf_file_metadata(url=url, token=hf_token)

        if hasattr(metadata, "etag") and metadata.etag:
            return metadata.etag
    except Exception as e:
        log.debug(_LOG_PREFIX, f"Could not get HF hash for {filename}: {e}")

    return None


def get_llm_model_list() -> List[str]:
    # Scan models/LLM folder and models/florence2 folder and return list of available models (cached).
    # First collects all model files, then filters to show:
    # - For shard files: show folder/ instead of individual files
    # - For single files: show full relative path to the file
    import time

    global _model_list_cache, _model_list_cache_time

    current_time = time.time()

    # Check if cache is valid
    if (
        current_time - _model_list_cache_time < _MODEL_LIST_CACHE_TTL
        and _model_list_cache
    ):
        return _model_list_cache.copy()

    try:
        import folder_paths  # type: ignore

        llm_dir = get_llm_models_path()
        florence2_dir = Path(folder_paths.models_dir) / "florence2"

        # Build list of folders to scan with their prefixes
        folders_to_scan = []
        if llm_dir.exists():
            folders_to_scan.append((llm_dir, ""))
        if florence2_dir.exists():
            folders_to_scan.append((florence2_dir, "florence2/"))

        if not folders_to_scan:
            return ["(No models/LLM folder found)"]

        model_extensions = {".safetensors", ".gguf", ".bin", ".pt"}
        all_model_files = []  # List of display paths
        folder_to_base = (
            {}
        )  # Map folder display path to actual base Path for config.json check

        # Step 1: Recursively scan and collect all model files
        def scan_files(
            base_path: Path,
            relative_path: str = "",
            display_prefix: str = "",
            base_for_config: Optional[Path] = None,
        ):
            # Recursively collect all model files
            try:
                for item in base_path.iterdir():
                    if item.is_file() and item.suffix in model_extensions:
                        # Build full relative path
                        if relative_path:
                            file_path = f"{relative_path}/{item.name}"
                        else:
                            file_path = item.name
                        display_path = f"{display_prefix}{file_path}"
                        all_model_files.append(display_path)
                        # Track folder -> base mapping for config check
                        if relative_path:
                            folder_display = f"{display_prefix}{relative_path}"
                            folder_to_base[folder_display] = (
                                base_for_config / relative_path
                                if base_for_config
                                else base_path
                            )
                    elif item.is_dir():
                        # Recurse into subdirectories (limit depth to avoid infinite loops)
                        item_rel_path = (
                            f"{relative_path}/{item.name}"
                            if relative_path
                            else item.name
                        )
                        if relative_path.count("/") < 4:  # Max 4 levels deep
                            scan_files(
                                item, item_rel_path, display_prefix, base_for_config
                            )
            except PermissionError:
                pass  # Skip directories we can't access

        for scan_path, prefix in folders_to_scan:
            scan_files(scan_path, "", prefix, scan_path)

        if not all_model_files:
            return ["(No models found in models/LLM)"]

        # Step 2: Group files by their parent folder (using display path)
        # Step 2: Group files by their parent folder (using display path)
        folder_files = defaultdict(list)

        for file_path in all_model_files:
            if "/" in file_path:
                folder = file_path.rsplit("/", 1)[0]
                filename = file_path.rsplit("/", 1)[1]
            else:
                folder = ""  # Root level
                filename = file_path

            folder_files[folder].append(filename)

        # Step 3: Check for config.json to identify model repositories
        # Use folder_to_base mapping to find correct path for config.json
        folders_with_config = set()
        for folder in folder_files.keys():
            if folder:  # Skip root level
                # Use the tracked base path if available, otherwise try llm_dir
                if folder in folder_to_base:
                    actual_folder = folder_to_base[folder]
                    config_path = actual_folder / "config.json"
                else:
                    # Fallback: try llm_dir (for models without prefix)
                    config_path = llm_dir / folder / "config.json"
                if config_path.exists():
                    folders_with_config.add(folder)

        # Step 4: Filter to create final model list
        models = []

        for folder, files in folder_files.items():
            # Separate mmproj files from model files
            model_files = [f for f in files if "mmproj" not in f.lower()]

            # Separate GGUF files from other model files
            gguf_files = [f for f in model_files if f.lower().endswith(".gguf")]
            non_gguf_files = [f for f in model_files if not f.lower().endswith(".gguf")]

            # Check if any non-GGUF file is a shard file
            has_shards = any(
                "-of-" in f or ".shard" in f.lower() for f in non_gguf_files
            )

            # Check if folder has config.json (indicates it's a model repository)
            has_config = folder in folders_with_config

            # For non-GGUF files: show folder/ if has shards or config.json
            if non_gguf_files and (has_shards or has_config):
                # Show folder/ for sharded models or model repositories with config.json
                if folder:
                    models.append(folder + "/")
                else:
                    # Shards in root - shouldn't happen but handle it
                    for f in non_gguf_files:
                        models.append(f)
            elif non_gguf_files:
                # List individual non-GGUF files
                for f in non_gguf_files:
                    if folder:
                        models.append(f"{folder}/{f}")
                    else:
                        models.append(f)

            # GGUF files: ALWAYS show individual files (even if folder has config.json)
            # This allows users to select specific quantization variants
            for f in gguf_files:
                if folder:
                    models.append(f"{folder}/{f}")
                else:
                    models.append(f)

        # Cache the result before returning
        result = sorted(models)
        _model_list_cache.clear()
        _model_list_cache.extend(result)
        _model_list_cache_time = current_time
        return result

    except Exception as e:
        log.error(_LOG_PREFIX, f"Error scanning models/LLM: {e}")
        return ["(Error scanning models folder)"]


def get_mmproj_list() -> List[str]:
    # Scan models/LLM folder for mmproj files for GGUF QwenVL models.
    # Returns only individual .mmproj files and .gguf files containing 'mmproj' in the name.
    # Never shows folders, only file paths.
    # Results are cached for 30 seconds to avoid repeated filesystem scans.
    import time

    global _mmproj_list_cache, _mmproj_list_cache_time

    current_time = time.time()
    if (
        current_time - _mmproj_list_cache_time < _MODEL_LIST_CACHE_TTL
        and _mmproj_list_cache
    ):
        return _mmproj_list_cache.copy()

    try:
        llm_dir = get_llm_models_path()

        if not llm_dir.exists():
            return ["None", "(No models/LLM folder found)"]

        mmproj_files = ["None"]  # Add None option for when mmproj is not needed

        def scan_for_mmproj(base_path: Path, relative_path: str = ""):
            # Recursively scan for mmproj files
            try:
                for item in base_path.iterdir():
                    if item.is_file():
                        # Match .mmproj files or .gguf files with 'mmproj' in name
                        if item.suffix == ".mmproj" or (
                            item.suffix == ".gguf" and "mmproj" in item.name.lower()
                        ):
                            if relative_path:
                                mmproj_files.append(f"{relative_path}/{item.name}")
                            else:
                                mmproj_files.append(item.name)
                    elif item.is_dir():
                        # Recurse into subdirectories (limit depth to avoid infinite loops)
                        item_rel_path = (
                            f"{relative_path}/{item.name}"
                            if relative_path
                            else item.name
                        )
                        if relative_path.count("/") < 4:  # Max 4 levels deep
                            scan_for_mmproj(item, item_rel_path)
            except PermissionError:
                pass  # Skip directories we can't access

        # Start recursive scan from LLM root
        scan_for_mmproj(llm_dir)

        if len(mmproj_files) == 1:  # Only "None" option
            mmproj_files.append("(No mmproj files found)")

        # Cache the result before returning
        result = sorted(mmproj_files)
        _mmproj_list_cache.clear()
        _mmproj_list_cache.extend(result)
        _mmproj_list_cache_time = current_time
        return result

    except Exception as e:
        log.error(_LOG_PREFIX, f"Error scanning for mmproj files: {e}")
        return ["None", "(Error scanning mmproj files)"]


def search_model_file(filename: str, llm_base: Path) -> Path | None:
    # Search recursively for a model file in the LLM folder.
    # Used to find legacy model files when template paths are outdated.
    # Returns Path object if found, None otherwise.
    try:
        if not llm_base.exists():
            return None

        # Search recursively (limit depth implicitly by rglob)
        for path in llm_base.rglob(filename):
            if path.is_file():
                return path

        return None
    except Exception as e:
        log.warning(_LOG_PREFIX, f"Error searching for {filename}: {e}")
        return None


def calculate_model_size(target_path: Path) -> float:
    # Calculate total model size in GB from a file or directory.
    # Handles sharded models, single files, and directories with multiple model files.
    # Returns size in GB, or 0.0 if calculation fails.
    try:
        total_size_gb = 0.0

        if target_path.is_file():
            # Single file (GGUF, safetensors, etc.)
            total_size_gb = target_path.stat().st_size / (1024**3)
        elif target_path.is_dir():
            # Model folder - check for sharded models first, then single files
            # Priority: .safetensors (preferred) > .bin > .pt > .gguf
            all_files = list(target_path.rglob("*"))
            model_files = [f for f in all_files if f.is_file()]

            # Check for shard files (e.g., model-00001-of-00005.safetensors)
            safetensors_files = [f for f in model_files if f.suffix == ".safetensors"]
            bin_files = [f for f in model_files if f.suffix == ".bin"]
            pt_files = [f for f in model_files if f.suffix == ".pt"]
            gguf_files = [f for f in model_files if f.suffix == ".gguf"]

            # Check if we have shards (files with -of- pattern)
            has_shards = lambda files: any("-of-" in f.name for f in files)

            # Priority: safetensors shards > single safetensors > bin shards > single bin > pt > gguf
            if has_shards(safetensors_files):
                # Use safetensors shards
                for file in safetensors_files:
                    if "-of-" in file.name:
                        total_size_gb += file.stat().st_size / (1024**3)
            elif safetensors_files:
                # Single safetensors file (no shards)
                for file in safetensors_files:
                    total_size_gb += file.stat().st_size / (1024**3)
            elif has_shards(bin_files):
                # Use bin shards
                for file in bin_files:
                    if "-of-" in file.name:
                        total_size_gb += file.stat().st_size / (1024**3)
            elif bin_files:
                # Single bin file
                for file in bin_files:
                    total_size_gb += file.stat().st_size / (1024**3)
            elif pt_files:
                # PT files
                for file in pt_files:
                    total_size_gb += file.stat().st_size / (1024**3)
            elif gguf_files:
                # GGUF files
                for file in gguf_files:
                    total_size_gb += file.stat().st_size / (1024**3)

        return total_size_gb

    except Exception as e:
        log.warning(_LOG_PREFIX, f"Error calculating model size: {e}")
        return 0.0


def calculate_file_hash(file_path: Path, show_progress: bool = True) -> str:
    # Calculate SHA256 hash of a file with optional progress display.
    #
    # Args:
    #     file_path: Path to the file to hash
    #     show_progress: Whether to display progress for large files (>100MB)
    #
    # Returns:
    #     Hexadecimal SHA256 hash string
    import sys

    sha256_hash = hashlib.sha256()
    file_size = file_path.stat().st_size
    bytes_processed = 0
    last_progress = -1

    # Show initial message with file size for large files
    size_mb = file_size / (1024 * 1024)
    if show_progress and file_size > 100 * 1024 * 1024:
        log.msg(
            _LOG_PREFIX, f"Calculating hash for {file_path.name} ({size_mb:.1f} MB)..."
        )
    elif show_progress:
        log.msg(_LOG_PREFIX, f"Calculating hash for {file_path.name}...")

    with open(file_path, "rb") as f:
        while chunk := f.read(8192 * 1024):  # 8MB chunks for speed
            sha256_hash.update(chunk)
            bytes_processed += len(chunk)
            # Show progress for large files (> 100MB)
            if show_progress and file_size > 100 * 1024 * 1024:
                progress = int((bytes_processed / file_size) * 100)
                # Update every 1% to keep progress smooth
                if progress != last_progress:
                    # Use carriage return to overwrite the same line
                    sys.stdout.write(
                        f"\rSML: [SmartLM]   Hashing: {progress}% ({bytes_processed / (1024*1024):.0f}/{size_mb:.0f} MB)"
                    )
                    sys.stdout.flush()
                    last_progress = progress

    # Print newline after progress is complete to preserve the final line
    if show_progress and file_size > 100 * 1024 * 1024:
        print()  # Move to next line

    return sha256_hash.hexdigest()


def verify_model_integrity(
    model_path: Path,
    repo_id: Optional[str] = None,
    hf_filename: Optional[str] = None,
    return_details: bool = False,
):
    # Verify model file integrity using SHA256 checksums.
    # Calculates and saves hashes on first load, then verifies on subsequent loads.
    #
    # Args:
    #     model_path: Path to model file or directory
    #     repo_id: HuggingFace repo_id (user/repo format or full URL)
    #     hf_filename: Optional filename to use for HuggingFace lookup (for renamed files)
    #     return_details: If True, return VerificationResult with details; otherwise return bool
    #
    # Returns:
    #     If return_details=False: True if verification passes, False if corruption detected
    #     If return_details=True: VerificationResult with success status and list of corrupted files
    corrupted_files = []

    try:
        # Look for model.safetensors, pytorch_model.bin, or model.onnx
        critical_files = []
        if model_path.is_dir():
            safetensors = list(model_path.glob("*.safetensors"))
            bin_files = list(model_path.glob("pytorch_model*.bin"))
            onnx_files = list(model_path.glob("*.onnx"))
            critical_files = (
                safetensors if safetensors else bin_files if bin_files else onnx_files
            )
        else:
            critical_files = (
                [model_path]
                if model_path.suffix in [".gguf", ".safetensors", ".bin", ".onnx"]
                else []
            )

        if not critical_files:
            log.warning(_LOG_PREFIX, f"No model files found to verify at {model_path}")
            if return_details:
                return VerificationResult(
                    success=True, corrupted_files=[], verified_count=0, skipped_count=0
                )
            return True  # Skip verification

        verified_count = 0
        failed_count = 0
        calculated_count = 0

        for file_path in critical_files:
            sha_file = file_path.parent / f"{file_path.name}.sha256"
            expected_hash = None

            # Check if we have a cached hash file first
            if sha_file.exists():
                try:
                    expected_hash = sha_file.read_text().strip().split()[0]
                    verified_count += 1
                    continue  # Skip hash calculation, already verified
                except Exception:
                    pass

            # If no cached hash, try to get it from HuggingFace
            if not expected_hash and repo_id:
                lookup_filename = hf_filename if hf_filename else file_path.name
                try:
                    import os
                    from huggingface_hub import hf_hub_url, get_hf_file_metadata  # type: ignore

                    log.msg(
                        _LOG_PREFIX,
                        f"Fetching hash from HuggingFace for {lookup_filename}...",
                    )

                    # Use HF token (config or environment) for authenticated metadata requests
                    # to avoid rate-limit warnings and gated-repo failures
                    hf_token = (
                        get_config_value("hf_token", "")
                        or os.environ.get("HF_TOKEN", "")
                        or os.environ.get("HUGGING_FACE_HUB_TOKEN", "")
                        or None
                    )

                    # Construct URL and get metadata
                    url = hf_hub_url(
                        repo_id=repo_id, filename=lookup_filename, repo_type="model"
                    )
                    metadata = get_hf_file_metadata(url=url, token=hf_token)

                    # ETag is the SHA256 hash for git-lfs files (per HuggingFace docs)
                    if hasattr(metadata, "etag") and metadata.etag:
                        expected_hash = metadata.etag
                        log.msg(_LOG_PREFIX, f"Retrieved hash from HuggingFace")
                    else:
                        log.warning(
                            _LOG_PREFIX,
                            f"No hash available in HuggingFace metadata for {lookup_filename}",
                        )
                except Exception as e:
                    log.warning(
                        _LOG_PREFIX,
                        f"Could not retrieve hash from HuggingFace ({repo_id}/{lookup_filename}): {e}",
                    )

            # If we still don't have a reference hash, skip verification
            if not expected_hash:
                log.warning(
                    _LOG_PREFIX,
                    f"No reference hash available for {file_path.name}, skipping verification",
                )
                calculated_count += 1
                continue

            # Calculate actual hash using centralized function
            actual_hash = calculate_file_hash(file_path, show_progress=True)

            # Verify against HuggingFace hash
            if actual_hash == expected_hash:
                log.msg(_LOG_PREFIX, f"✓ {file_path.name} integrity verified")
                verified_count += 1

                # Save hash file for future fast verification
                try:
                    sha_file.write_text(expected_hash)
                    log.msg(_LOG_PREFIX, f"Cached hash to {sha_file.name}")
                except Exception as e:
                    log.warning(_LOG_PREFIX, f"Could not cache hash: {e}")
            else:
                log.error(_LOG_PREFIX, f"✗ {file_path.name} CORRUPTED! Hash mismatch.")
                log.error(_LOG_PREFIX, f"  Expected: {expected_hash}")
                log.error(_LOG_PREFIX, f"  Got:      {actual_hash}")
                failed_count += 1
                corrupted_files.append(file_path)
                # Don't save hash file on failure - user needs to redownload

        if failed_count > 0:
            log.error(
                _LOG_PREFIX,
                f"⚠ Model verification FAILED! {failed_count} corrupted file(s) detected.",
            )
            if return_details:
                return VerificationResult(
                    success=False,
                    corrupted_files=corrupted_files,
                    verified_count=verified_count,
                    skipped_count=calculated_count,
                )
            return False
        elif verified_count > 0:
            log.msg(
                _LOG_PREFIX, f"✓ Model integrity verified ({verified_count} file(s))"
            )
        elif calculated_count > 0:
            log.warning(
                _LOG_PREFIX,
                f"⚠ No reference hash available, skipping verification for {calculated_count} file(s)",
            )

        if return_details:
            return VerificationResult(
                success=True,
                corrupted_files=[],
                verified_count=verified_count,
                skipped_count=calculated_count,
            )
        return True

    except Exception as e:
        log.warning(_LOG_PREFIX, f"Model verification error (non-critical): {e}")
        if return_details:
            return VerificationResult(
                success=True, corrupted_files=[], verified_count=0, skipped_count=0
            )
        return True  # Don't block loading on verification errors


def check_model_completeness(
    model_path: Path, repo_id: Optional[str] = None, hf_token: Optional[str] = None
) -> Tuple[bool, List[str]]:
    # Check if all required model files are present by reading the model index file.
    #
    # For sharded models, reads model.safetensors.index.json or pytorch_model.bin.index.json
    # to get the list of required weight files and checks if they all exist.
    #
    # Note: Consolidated files are intentionally ignored - users may have deleted them
    # to save space since they're only needed for specific loading methods.
    #
    # Args:
    #     model_path: Path to model directory
    #     repo_id: HuggingFace repo_id for re-downloading missing files
    #     hf_token: Optional HuggingFace token for authenticated downloads
    #
    # Returns:
    #     Tuple of (is_complete, missing_files_list)
    import json

    if not model_path.is_dir():
        # Single file model (e.g., GGUF) - just check if file exists
        if model_path.exists():
            return (True, [])
        return (False, [model_path.name])

    missing_files = []

    # Check for safetensors index file first (preferred), then pytorch
    index_files = [
        model_path / "model.safetensors.index.json",
        model_path / "pytorch_model.bin.index.json",
    ]

    index_file = None
    for idx_file in index_files:
        if idx_file.exists():
            index_file = idx_file
            break

    if not index_file:
        # Retrieve HF token for authenticated requests if needed
        import os

        token = (
            hf_token
            or os.environ.get("HF_TOKEN")
            or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        )
        if not token:
            config_token = get_config_value("hf_token", "")
            if config_token and config_token.strip():
                token = config_token.strip()

        # No local index file - check if we can query HuggingFace repo to list files
        if repo_id:
            try:
                from huggingface_hub import list_repo_files  # type: ignore

                repo_files = list_repo_files(repo_id, token=token)

                # Check for remote index files first
                remote_index_files = [
                    "model.safetensors.index.json",
                    "pytorch_model.bin.index.json",
                ]
                found_remote_index = None
                for rif in remote_index_files:
                    if rif in repo_files:
                        found_remote_index = rif
                        break

                if found_remote_index:
                    # Index file is present in repo but missing locally!
                    missing_files.append(found_remote_index)
                    weight_suffix = (
                        ".safetensors"
                        if "safetensors" in found_remote_index
                        else ".bin"
                    )
                    for f in repo_files:
                        if (
                            f.endswith(weight_suffix)
                            and "consolidated" not in f.lower()
                        ):
                            if not (model_path / f).exists():
                                missing_files.append(f)
                else:
                    # No index file exists in the repo either. Just check all files in repo_files
                    for f in repo_files:
                        if f.startswith(".") or f.lower() in (
                            "readme.md",
                            "licence",
                            "license",
                        ):
                            continue
                        if "consolidated" in f.lower():
                            continue
                        is_essential = (
                            f.endswith(".json")
                            or f.endswith(".txt")
                            or f.endswith(".model")
                        )
                        is_weight = (
                            f.endswith(".safetensors")
                            or f.endswith(".bin")
                            or f.endswith(".gguf")
                            or f.endswith(".pt")
                            or f.endswith(".pth")
                            or f.endswith(".ckpt")
                            or f.endswith(".onnx")
                        )
                        if is_essential or is_weight:
                            if not (model_path / f).exists():
                                missing_files.append(f)
            except Exception as e:
                log.warning(
                    _LOG_PREFIX,
                    f"Could not list repo files for completeness check: {e}",
                )

        # If we couldn't list files or found nothing, check basic config and weight heuristics locally
        if not missing_files:
            config_file = model_path / "config.json"
            if not config_file.exists():
                return (False, ["config.json"])

            # Local fallback: ensure there is at least one weight file in the directory
            weight_extensions = (
                ".safetensors",
                ".bin",
                ".gguf",
                ".pt",
                ".pth",
                ".ckpt",
            )
            has_weights = any(
                f.name.endswith(weight_extensions)
                for f in model_path.iterdir()
                if f.is_file()
            )
            if not has_weights:
                log.warning(
                    _LOG_PREFIX,
                    f"Model folder {model_path} has config.json but no weights found.",
                )
                return (
                    False,
                    ["model.safetensors"],
                )  # request at least one weights file

            return (True, [])
        else:
            log.warning(
                _LOG_PREFIX,
                f"Model incomplete (repo scan): {len(missing_files)} file(s) missing",
            )
            return (False, missing_files)

    try:
        with open(index_file, "r", encoding="utf-8") as f:
            index_data = json.load(f)

        # Get unique weight files from the weight_map
        weight_map = index_data.get("weight_map", {})
        required_files = set(weight_map.values())

        # Check each required file (but ignore consolidated files - user may have deleted them intentionally)
        for filename in required_files:
            # Skip consolidated files - these are optional alternative formats
            # Users often delete them to save disk space
            if "consolidated" in filename.lower():
                continue

            file_path = model_path / filename
            if not file_path.exists():
                missing_files.append(filename)
                log.debug(_LOG_PREFIX, f"Missing model file: {filename}")

        # Also check essential config files
        essential_files = ["config.json"]
        for filename in essential_files:
            file_path = model_path / filename
            if not file_path.exists():
                missing_files.append(filename)

        if missing_files:
            log.warning(
                _LOG_PREFIX, f"Model incomplete: {len(missing_files)} file(s) missing"
            )
            return (False, missing_files)

        return (True, [])

    except Exception as e:
        log.warning(_LOG_PREFIX, f"Could not read model index file: {e}")
        return (True, [])  # Assume complete if we can't check


def download_missing_files(
    model_path: Path,
    missing_files: List[str],
    repo_id: str,
    hf_token: Optional[str] = None,
) -> bool:
    # Download specific missing files from HuggingFace via temp folder.
    #
    # Downloads to temp folder first, verifies hash, then moves to final location.
    # This is more reliable for drives with issues. If temp folder is on the same
    # drive as target, downloads directly (no benefit from temp folder).
    #
    # Args:
    #     model_path: Path to model directory
    #     missing_files: List of filenames that are missing
    #     repo_id: HuggingFace repo_id (user/repo format)
    #     hf_token: Optional HuggingFace token for authenticated downloads
    #
    # Returns:
    #     True if all files were successfully downloaded, False otherwise
    import tempfile
    import shutil

    if not missing_files:
        return True

    if not repo_id:
        log.error(_LOG_PREFIX, "Cannot download missing files: no repo_id provided")
        return False

    # Extract clean repo_id if it's a URL
    clean_repo_id = extract_repo_id_from_url(repo_id)
    if not clean_repo_id:
        log.error(_LOG_PREFIX, f"Cannot extract repo_id from: {repo_id}")
        return False

    try:
        from huggingface_hub import hf_hub_download  # type: ignore
        import inspect
    except ImportError:
        log.error(
            _LOG_PREFIX, "huggingface_hub not installed, cannot download missing files"
        )
        return False

    # Check if temp folder is on the same drive as target
    temp_check_dir = Path(tempfile.gettempdir())
    use_temp_folder = not is_same_drive(temp_check_dir, model_path)

    if use_temp_folder:
        log.debug(
            _LOG_PREFIX,
            f"Using temp folder for download (temp={temp_check_dir.drive}, target={model_path.drive})",
        )
    else:
        log.debug(
            _LOG_PREFIX,
            f"Temp folder is on same drive as target ({temp_check_dir.drive}), downloading directly",
        )

    log.msg(
        _LOG_PREFIX,
        f"Downloading {len(missing_files)} missing file(s) from {clean_repo_id}...",
    )

    # Pre-fetch all hashes upfront (reduces interleaved HEAD requests during downloads)
    log.debug(_LOG_PREFIX, f"Pre-fetching hashes for {len(missing_files)} file(s)...")
    file_hashes = {}
    for filename in missing_files:
        hash_value = get_hf_file_hash(clean_repo_id, filename, hf_token)
        file_hashes[filename] = hash_value
        if hash_value:
            log.debug(_LOG_PREFIX, f"  Got hash for {filename}: {hash_value[:16]}...")
        else:
            log.debug(_LOG_PREFIX, f"  No hash available for {filename}")

    success_count = 0
    failed_files = []
    max_attempts = get_config_value("retry_download_attempts", 2) + 1

    for filename in missing_files:
        file_success = False
        final_path = model_path / filename

        # Use pre-fetched hash
        expected_hash = file_hashes.get(filename)

        for attempt in range(max_attempts):
            temp_dir = None
            try:
                if attempt > 0:
                    log.msg(
                        _LOG_PREFIX,
                        f"  Retry attempt {attempt + 1}/{max_attempts} for {filename}...",
                    )
                else:
                    log.msg(_LOG_PREFIX, f"  Downloading {filename}...")

                if use_temp_folder:
                    # Create temp directory for download
                    temp_dir = tempfile.mkdtemp(prefix="sml_download_")
                    download_dir = Path(temp_dir)
                    log.debug(_LOG_PREFIX, f"  Created temp dir: {temp_dir}")
                else:
                    # Download directly to final location (same drive optimization)
                    final_path.parent.mkdir(parents=True, exist_ok=True)
                    download_dir = model_path
                    log.debug(_LOG_PREFIX, f"  Direct download to: {download_dir}")

                download_kwargs: Dict[str, Any] = {
                    "repo_id": clean_repo_id,
                    "filename": filename,
                    "local_dir": str(download_dir),
                }
                if (
                    "local_dir_use_symlinks"
                    in inspect.signature(hf_hub_download).parameters
                ):
                    download_kwargs["local_dir_use_symlinks"] = False

                if hf_token:
                    download_kwargs["token"] = hf_token

                hf_hub_download(**download_kwargs)

                # File is downloaded to download_dir/filename
                downloaded_file = download_dir / filename
                if not downloaded_file.exists():
                    log.error(_LOG_PREFIX, f"  Download failed: file not created")
                    continue

                # Verify hash if available
                verified_hash = None
                if expected_hash:
                    actual_hash = calculate_file_hash(
                        downloaded_file, show_progress=False
                    )
                    if actual_hash != expected_hash:
                        log.error(
                            _LOG_PREFIX,
                            f"  ✗ Hash mismatch for {filename} (attempt {attempt + 1}/{max_attempts})",
                        )
                        if downloaded_file.exists():
                            downloaded_file.unlink()
                        continue
                    log.debug(_LOG_PREFIX, f"  Hash verified for {filename}")
                    verified_hash = actual_hash
                else:
                    log.debug(
                        _LOG_PREFIX,
                        f"  No hash available for {filename}, skipping verification",
                    )
                    # For direct downloads without hash, calculate hash for saving
                    if not use_temp_folder:
                        verified_hash = calculate_file_hash(
                            downloaded_file, show_progress=False
                        )

                # Copy to final location if using temp folder (keep temp for retry if copy fails)
                if use_temp_folder:
                    log.debug(_LOG_PREFIX, f"  Copying from temp to final location...")
                    final_path.parent.mkdir(parents=True, exist_ok=True)

                    # Retry loop for copy operation
                    max_copy_attempts = 3
                    copy_success = False
                    corrupted_file_exists = (
                        False  # Track if we have a corrupted file occupying sectors
                    )

                    for copy_attempt in range(max_copy_attempts):
                        try:
                            # On retry after corruption: copy to temp name to force different sectors
                            if corrupted_file_exists:
                                temp_final_path = (
                                    final_path.parent / f"{final_path.name}.new"
                                )
                                target_path = temp_final_path
                                log.debug(
                                    _LOG_PREFIX,
                                    f"  Copying to alternate location to avoid bad sectors...",
                                )
                            else:
                                if final_path.exists():
                                    final_path.unlink()
                                target_path = final_path

                            shutil.copy2(str(downloaded_file), str(target_path))

                            # Verify hash after copy to detect target drive issues
                            if expected_hash:
                                post_copy_hash = calculate_file_hash(
                                    target_path, show_progress=False
                                )

                                if post_copy_hash != expected_hash:
                                    log.error(
                                        _LOG_PREFIX,
                                        f"  ⚠ DRIVE ISSUE: File corrupted after copy to target drive!",
                                    )
                                    log.error(
                                        _LOG_PREFIX,
                                        f"    This indicates your target drive may have write errors.",
                                    )

                                    if not corrupted_file_exists:
                                        corrupted_file_exists = True
                                        log.debug(
                                            _LOG_PREFIX,
                                            f"  Keeping corrupted file to force write to different sectors...",
                                        )
                                    else:
                                        if target_path.exists():
                                            target_path.unlink()

                                    if copy_attempt < max_copy_attempts - 1:
                                        log.debug(
                                            _LOG_PREFIX,
                                            f"  Retrying copy (attempt {copy_attempt + 2}/{max_copy_attempts})...",
                                        )
                                    continue  # Retry copy

                                verified_hash = post_copy_hash

                            # If we used a temp name, rename to final name
                            if corrupted_file_exists:
                                if final_path.exists():
                                    final_path.unlink()
                                target_path.rename(final_path)
                                log.debug(
                                    _LOG_PREFIX,
                                    f"  Renamed to final filename after successful verification",
                                )

                            copy_success = True
                            log.debug(_LOG_PREFIX, f"  Copy successful to {final_path}")
                            break

                        except Exception as e:
                            log.error(
                                _LOG_PREFIX,
                                f"  Copy error (attempt {copy_attempt + 1}/{max_copy_attempts}): {e}",
                            )
                            if corrupted_file_exists:
                                temp_final_path = (
                                    final_path.parent / f"{final_path.name}.new"
                                )
                                if temp_final_path.exists():
                                    try:
                                        temp_final_path.unlink()
                                    except Exception:
                                        pass

                    # Clean up after copy attempts
                    if copy_success:
                        try:
                            if downloaded_file.exists():
                                downloaded_file.unlink()
                        except Exception:
                            pass
                    else:
                        log.error(
                            _LOG_PREFIX,
                            f"  All {max_copy_attempts} copy attempts failed for {filename}",
                        )
                        # Clean up any leftover files
                        if final_path.exists():
                            try:
                                final_path.unlink()
                            except Exception:
                                pass
                        temp_final_path = final_path.parent / f"{final_path.name}.new"
                        if temp_final_path.exists():
                            try:
                                temp_final_path.unlink()
                            except Exception:
                                pass
                        if downloaded_file.exists():
                            downloaded_file.unlink()
                        continue  # Retry download

                # Save hash file only if we have a verified hash
                if verified_hash:
                    try:
                        sha_file = final_path.parent / f"{final_path.name}.sha256"
                        sha_file.write_text(verified_hash)
                        log.debug(_LOG_PREFIX, f"  Saved hash file: {sha_file.name}")
                    except Exception as e:
                        log.warning(_LOG_PREFIX, f"  Could not save hash file: {e}")

                log.msg(_LOG_PREFIX, f"  ✓ Downloaded {filename}")
                success_count += 1
                file_success = True
                break

            except Exception as e:
                log.error(
                    _LOG_PREFIX,
                    f"  \u2717 Error downloading {filename} (attempt {attempt + 1}/{max_attempts}): {e}",
                )
                # Clean up failed download if downloading directly
                if not use_temp_folder and final_path.exists():
                    try:
                        final_path.unlink()
                    except Exception:
                        pass
            finally:
                # Clean up temp directory
                if temp_dir and Path(temp_dir).exists():
                    try:
                        shutil.rmtree(temp_dir)
                    except Exception:
                        pass

        if not file_success:
            failed_files.append(filename)

    if failed_files:
        log.error(
            _LOG_PREFIX,
            f"Failed to download {len(failed_files)} file(s): {', '.join(failed_files)}",
        )
        return False

    log.msg(
        _LOG_PREFIX, f"\u2713 Successfully downloaded {success_count} missing file(s)"
    )
    return True


def redownload_corrupted_files(
    corrupted_files: List[Path],
    repo_id: str,
    local_dir: Path,
    hf_token: Optional[str] = None,
) -> bool:
    # Re-download only the corrupted files instead of the entire model.
    #
    # Downloads to temp folder first, verifies hash, then moves to final location.
    # This is more reliable for drives with issues - if verification fails in temp,
    # we retry without leaving corrupted files in the model folder.
    #
    # If temp folder is on the same drive as target, downloads directly
    # (no benefit from temp folder in that case).
    #
    # Args:
    #     corrupted_files: List of Path objects for files that failed hash verification
    #     repo_id: HuggingFace repo_id (user/repo format, not URL)
    #     local_dir: Local directory where the model is stored
    #     hf_token: Optional HuggingFace token for authenticated downloads
    #
    # Returns:
    #     True if all files were successfully re-downloaded and verified, False otherwise
    import tempfile
    import shutil

    if not corrupted_files:
        return True

    if not repo_id:
        log.warning(_LOG_PREFIX, "Cannot re-download files: no repo_id provided")
        return False

    # Extract clean repo_id if it's a URL
    clean_repo_id = extract_repo_id_from_url(repo_id)
    if not clean_repo_id:
        log.warning(_LOG_PREFIX, f"Cannot extract repo_id from: {repo_id}")
        return False

    try:
        from huggingface_hub import hf_hub_download  # type: ignore
        import inspect
    except ImportError:
        log.error(
            _LOG_PREFIX,
            "huggingface_hub not installed, cannot re-download individual files",
        )
        return False

    # Check if temp folder is on the same drive as target
    temp_check_dir = Path(tempfile.gettempdir())
    use_temp_folder = not is_same_drive(temp_check_dir, local_dir)

    if not use_temp_folder:
        log.debug(
            _LOG_PREFIX, "Temp folder is on same drive as target, downloading directly"
        )

    # Pre-fetch all hashes upfront (reduces interleaved HEAD requests during downloads)
    file_hashes = {}
    for file_path in corrupted_files:
        filename = file_path.name
        expected_hash = get_hf_file_hash(clean_repo_id, filename, hf_token)
        if expected_hash:
            file_hashes[filename] = expected_hash
        else:
            log.warning(
                _LOG_PREFIX,
                f"Could not get expected hash for {filename}, will download without verification",
            )
            file_hashes[filename] = None

    success_count = 0
    failed_files = []
    max_attempts = get_config_value("retry_download_attempts", 2) + 1

    for file_path in corrupted_files:
        filename = file_path.name
        file_success = False
        final_path = local_dir / filename

        # Delete the corrupted file and its hash file first
        try:
            if file_path.exists():
                file_path.unlink()
                log.msg(_LOG_PREFIX, f"Deleted corrupted file: {filename}")

            sha_file = file_path.parent / f"{filename}.sha256"
            if sha_file.exists():
                sha_file.unlink()
        except Exception as e:
            log.warning(_LOG_PREFIX, f"Failed to delete corrupted file {filename}: {e}")

        # Use pre-fetched hash
        expected_hash = file_hashes.get(filename)

        for attempt in range(max_attempts):
            temp_dir = None
            try:
                if attempt > 0:
                    log.msg(
                        _LOG_PREFIX,
                        f"Retry attempt {attempt + 1}/{max_attempts} for {filename}...",
                    )
                else:
                    location_desc = "via temp" if use_temp_folder else "directly"
                    log.msg(
                        _LOG_PREFIX,
                        f"Re-downloading {filename} from {clean_repo_id} ({location_desc})...",
                    )

                if use_temp_folder:
                    # Create temp directory for download
                    temp_dir = tempfile.mkdtemp(prefix="sml_redownload_")
                    download_dir = Path(temp_dir)
                else:
                    # Download directly to final location
                    final_path.parent.mkdir(parents=True, exist_ok=True)
                    download_dir = local_dir

                download_kwargs = {
                    "repo_id": clean_repo_id,
                    "filename": filename,
                    "local_dir": str(download_dir),
                    "force_download": True,  # Force re-download even if cached
                }
                if (
                    "local_dir_use_symlinks"
                    in inspect.signature(hf_hub_download).parameters
                ):
                    download_kwargs["local_dir_use_symlinks"] = False

                if hf_token:
                    download_kwargs["token"] = hf_token

                hf_hub_download(**download_kwargs)

                # File is downloaded to download_dir/filename
                downloaded_file = download_dir / filename
                if not downloaded_file.exists():
                    log.error(_LOG_PREFIX, f"Download failed: file not created")
                    continue

                # Verify hash in download location
                verified_hash = None
                if expected_hash:
                    location_desc = (
                        "temp location" if use_temp_folder else "download location"
                    )
                    log.msg(_LOG_PREFIX, f"Verifying {filename} in {location_desc}...")
                    actual_hash = calculate_file_hash(
                        downloaded_file, show_progress=True
                    )

                    if actual_hash != expected_hash:
                        log.error(
                            _LOG_PREFIX,
                            f"✗ Hash verification failed (attempt {attempt + 1}/{max_attempts})",
                        )
                        log.error(_LOG_PREFIX, f"  Expected: {expected_hash}")
                        log.error(_LOG_PREFIX, f"  Got:      {actual_hash}")
                        # Clean up and retry download
                        if downloaded_file.exists():
                            downloaded_file.unlink()
                        continue

                    log.msg(_LOG_PREFIX, f"✓ Hash verified in {location_desc}")
                    verified_hash = actual_hash

                # Copy verified file to final location if using temp folder (keep temp for retry if copy fails)
                if use_temp_folder:
                    final_path.parent.mkdir(parents=True, exist_ok=True)

                    # Retry loop for copy operation
                    max_copy_attempts = 3
                    copy_success = False
                    corrupted_file_exists = (
                        False  # Track if we have a corrupted file occupying sectors
                    )

                    for copy_attempt in range(max_copy_attempts):
                        try:
                            # On retry after corruption: copy to temp name to force different sectors
                            if corrupted_file_exists:
                                temp_final_path = (
                                    final_path.parent / f"{final_path.name}.new"
                                )
                                target_path = temp_final_path
                                log.msg(
                                    _LOG_PREFIX,
                                    f"Copying to alternate location to avoid bad sectors...",
                                )
                            else:
                                if final_path.exists():
                                    final_path.unlink()
                                target_path = final_path

                            shutil.copy2(str(downloaded_file), str(target_path))

                            if copy_attempt > 0:
                                log.msg(
                                    _LOG_PREFIX,
                                    f"✓ Copied {filename} (attempt {copy_attempt + 1})",
                                )

                            # Verify hash after copy to detect target drive issues
                            if expected_hash:
                                log.msg(
                                    _LOG_PREFIX, f"Verifying {filename} after copy..."
                                )
                                post_copy_hash = calculate_file_hash(
                                    target_path, show_progress=False
                                )

                                if post_copy_hash != expected_hash:
                                    log.error(
                                        _LOG_PREFIX,
                                        f"⚠ DRIVE ISSUE DETECTED: File corrupted after copy to target drive!",
                                    )
                                    log.error(
                                        _LOG_PREFIX,
                                        f"  File was verified correct in temp folder but corrupted after copying.",
                                    )
                                    log.error(
                                        _LOG_PREFIX,
                                        f"  This indicates your target drive may have bad sectors or write errors.",
                                    )
                                    log.error(
                                        _LOG_PREFIX,
                                        f"  Consider running disk check (chkdsk) or moving models to a different drive.",
                                    )

                                    if not corrupted_file_exists:
                                        corrupted_file_exists = True
                                        log.msg(
                                            _LOG_PREFIX,
                                            f"Keeping corrupted file to force write to different sectors on retry...",
                                        )
                                    else:
                                        if target_path.exists():
                                            target_path.unlink()

                                    if copy_attempt < max_copy_attempts - 1:
                                        log.msg(
                                            _LOG_PREFIX,
                                            f"Retrying copy (attempt {copy_attempt + 2}/{max_copy_attempts})...",
                                        )
                                    continue  # Retry copy

                                verified_hash = post_copy_hash

                            # If we used a temp name, rename to final name
                            if corrupted_file_exists:
                                if final_path.exists():
                                    final_path.unlink()
                                target_path.rename(final_path)
                                log.msg(
                                    _LOG_PREFIX,
                                    f"✓ Renamed to final filename after successful verification",
                                )

                            copy_success = True
                            break

                        except Exception as e:
                            log.error(
                                _LOG_PREFIX,
                                f"Copy error (attempt {copy_attempt + 1}/{max_copy_attempts}): {e}",
                            )
                            if corrupted_file_exists:
                                temp_final_path = (
                                    final_path.parent / f"{final_path.name}.new"
                                )
                                if temp_final_path.exists():
                                    try:
                                        temp_final_path.unlink()
                                    except Exception:
                                        pass

                    # Clean up after copy attempts
                    if copy_success:
                        try:
                            if downloaded_file.exists():
                                downloaded_file.unlink()
                        except Exception:
                            pass
                    else:
                        log.error(
                            _LOG_PREFIX,
                            f"All {max_copy_attempts} copy attempts failed for {filename}",
                        )
                        # Clean up any leftover files
                        if final_path.exists():
                            try:
                                final_path.unlink()
                            except Exception:
                                pass
                        temp_final_path = final_path.parent / f"{final_path.name}.new"
                        if temp_final_path.exists():
                            try:
                                temp_final_path.unlink()
                            except Exception:
                                pass
                        if downloaded_file.exists():
                            downloaded_file.unlink()
                        continue  # Retry download

                # Save hash file only if we have a verified hash
                if verified_hash:
                    try:
                        sha_file = final_path.parent / f"{final_path.name}.sha256"
                        sha_file.write_text(verified_hash)
                        log.debug(_LOG_PREFIX, f"Saved hash file: {sha_file.name}")
                    except Exception as e:
                        log.warning(_LOG_PREFIX, f"Could not cache hash: {e}")

                log.msg(
                    _LOG_PREFIX, f"✓ Successfully re-downloaded and verified {filename}"
                )
                success_count += 1
                file_success = True
                break

            except Exception as e:
                log.error(
                    _LOG_PREFIX,
                    f"\u2717 Error re-downloading {filename} (attempt {attempt + 1}/{max_attempts}): {e}",
                )
                # Clean up failed download if downloading directly
                if not use_temp_folder and final_path.exists():
                    try:
                        final_path.unlink()
                    except Exception:
                        pass
            finally:
                # Clean up temp directory
                if temp_dir and Path(temp_dir).exists():
                    try:
                        shutil.rmtree(temp_dir)
                    except Exception:
                        pass

        if not file_success:
            failed_files.append(filename)

    if failed_files:
        log.error(
            _LOG_PREFIX,
            f"Failed to re-download {len(failed_files)} file(s): {', '.join(failed_files)}",
        )
        return False

    log.msg(
        _LOG_PREFIX, f"✓ Successfully re-downloaded {success_count} corrupted file(s)"
    )
    return True


def extract_repo_id_from_url(repo_id: str) -> str:
    # Extract actual repo_id (namespace/repo_name) from a HuggingFace URL.
    #
    # Args:
    #     repo_id: Either a direct repo_id like "user/repo" or a full HuggingFace URL
    #
    # Returns:
    #     Extracted repo_id in format "user/repo", or original string if not a URL
    #
    # Examples:
    #     "bartowski/model" -> "bartowski/model"
    #     "https://huggingface.co/user/repo/resolve/main/file.gguf" -> "user/repo"
    if not repo_id:
        return ""

    # If it's a URL, extract the repo_id part
    if repo_id.startswith("http") and "huggingface.co" in repo_id:
        parts = repo_id.split("/")
        if len(parts) >= 5:
            # URL format: https://huggingface.co/USER/REPO/resolve/main/file
            return f"{parts[3]}/{parts[4]}"

    # Already in correct format or not a URL
    return repo_id


# ============================================================================
# Model Discovery Functions (v2 workflow)
# ============================================================================


def _verify_fp8_tensors(model_path: Path) -> bool:
    # Verify a model folder actually contains FP8 quantized tensors.
    #
    # Checks safetensors files for float8_e4m3fn dtype or FP8 scale patterns.
    # This is used to distinguish true FP8 models from models that were
    # converted/dequantized to BF16 but still have FP8 in their config.
    #
    # Args:
    #     model_path: Path to model folder
    #
    # Returns:
    #     True if actual FP8 tensors are found
    try:
        from safetensors import safe_open  # type: ignore

        sf_files = list(model_path.glob("*.safetensors"))
        if not sf_files:
            return False

        # Check first safetensors file
        with safe_open(str(sf_files[0]), framework="pt") as f:
            # Check a few weight tensors for FP8 dtype
            for key in list(f.keys())[:20]:
                if "weight" in key.lower() and "scale" not in key.lower():
                    tensor = f.get_tensor(key)
                    dtype_str = str(tensor.dtype).lower()
                    if (
                        "float8" in dtype_str
                        or "e4m3" in dtype_str
                        or "e5m2" in dtype_str
                    ):
                        return True

            # Also check for FP8 scale tensors (weight_scale_inv, activation_scale)
            tensor_keys = list(f.keys())
            has_fp8_scales = any(
                "weight_scale" in k.lower() or "scale_inv" in k.lower()
                for k in tensor_keys
            )
            if has_fp8_scales:
                return True

    except Exception:
        pass

    return False


def detect_prequantized_model(model_path: Path) -> tuple[bool, str]:
    # Check if a model is pre-quantized by inspecting config.json and safetensors.
    #
    # Detects: AWQ, GPTQ, BitsAndBytes (BNB), FP8, GGML/GGUF markers.
    # Also checks actual tensor dtypes in safetensors to verify FP8 vs BF16.
    #
    # Args:
    #     model_path: Path to model folder or GGUF file
    #
    # Returns:
    #     Tuple of (is_quantized: bool, quant_type: str)
    #     quant_type is one of: "awq", "gptq", "bnb", "fp8", "gguf", "unknown", ""
    import json

    p = Path(model_path)

    # GGUF files are always pre-quantized
    if p.is_file() and p.suffix.lower() == ".gguf":
        return True, "gguf"

    if not p.is_dir():
        return False, ""

    # Check config.json for quantization_config
    config_file = p / "config.json"
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text())
            if "quantization_config" in config:
                quant_config = config["quantization_config"]
                quant_method = quant_config.get("quant_method", "").lower()

                # AWQ
                if quant_method == "awq":
                    return True, "awq"

                # GPTQ
                if quant_method == "gptq":
                    return True, "gptq"

                # BitsAndBytes
                if (
                    quant_method in ["bitsandbytes", "bnb"]
                    or quant_config.get("load_in_4bit")
                    or quant_config.get("load_in_8bit")
                ):
                    return True, "bnb"

                # FP8 (various indicators in config)
                if quant_method == "fp8" or "activation_scheme" in quant_config:
                    # Double-check: verify safetensors actually contain FP8 tensors
                    # Some models may have config from FP8 but weights converted to BF16
                    if _verify_fp8_tensors(p):
                        return True, "fp8"
                    # Config says FP8 but no FP8 tensors found - not actually quantized
                    # (model was likely converted/dequantized to BF16)
                # Unknown quantization config present
                if quant_method:
                    return True, quant_method

        except Exception:
            pass

    # Check params.json (Mistral native format)
    params_file = p / "params.json"
    if params_file.exists():
        try:
            params = json.loads(params_file.read_text())
            if "quantization" in params:
                qformat = params["quantization"].get("qformat_weight", "")
                if "fp8" in qformat.lower():
                    return True, "fp8"
                if qformat:
                    return True, qformat.lower()
        except Exception:
            pass

    # Fallback: check filename markers (less reliable but catches edge cases)
    model_name_lower = p.name.lower()

    # Standard quantization format markers
    quant_markers = {
        "awq": ["-awq", "_awq", ".awq"],
        "gptq": ["-gptq", "_gptq", ".gptq"],
        "bnb": ["-bnb", "_bnb"],
        "fp8": ["-fp8", "_fp8"],
        "int8": ["-int8", "_int8"],
        "int4": ["-int4", "_int4"],
    }
    for quant_type, markers in quant_markers.items():
        if any(m in model_name_lower for m in markers):
            return True, quant_type

    # GGUF quantization markers (Q4_K_M, Q5_K_S, Q8_0, etc.)
    # These appear in GGUF filenames and folder names
    gguf_quant_markers = [
        "_q4_",
        "_q5_",
        "_q6_",
        "_q8_",  # underscore style
        "-q4-",
        "-q5-",
        "-q6-",
        "-q8-",  # dash style
        "_k_m",
        "_k_s",
        "_k_l",  # quality suffixes (K_M, K_S, K_L)
        "q4_k",
        "q5_k",
        "q6_k",
        "q8_0",  # full quant names
        ".q4_",
        ".q5_",
        ".q6_",
        ".q8_",  # dot prefix style
        "_iq4",
        "_iq3",
        "_iq2",  # imatrix quants
    ]
    if any(m in model_name_lower for m in gguf_quant_markers):
        return True, "gguf"

    return False, ""


def detect_fp8_model(model_path: Path) -> bool:
    # Check if a model folder contains FP8 quantized weights.
    #
    # Checks:
    # 1. params.json for quantization.qformat_weight containing "fp8"
    # 2. config.json for quantization_config with FP8 indicators
    # 3. Safetensors files for float8_e4m3fn dtype tensors
    #
    # Args:
    #     model_path: Path to model folder
    #
    # Returns:
    #     True if model uses FP8 quantization
    import json

    # Check params.json (Mistral native format)
    params_file = model_path / "params.json"
    if params_file.exists():
        try:
            params = json.loads(params_file.read_text())
            if "quantization" in params:
                qformat = params["quantization"].get("qformat_weight", "")
                if "fp8" in qformat.lower():
                    return True
        except Exception:
            pass

    # Check config.json for quantization_config
    config_file = model_path / "config.json"
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text())
            if "quantization_config" in config:
                quant_config = config["quantization_config"]
                quant_method = quant_config.get("quant_method", "")
                # FP8 indicators
                if quant_method == "fp8" or "activation_scheme" in quant_config:
                    return True
        except Exception:
            pass

    # Check safetensors metadata for tensor dtype info
    # Use metadata check instead of loading tensors (much faster)
    try:
        import safetensors  # type: ignore

        sf_files = list(model_path.glob("*.safetensors"))
        if sf_files:
            # Read file metadata without loading tensors
            with safetensors.safe_open(str(sf_files[0]), framework="pt") as f:
                metadata = f.metadata()
                # Some models include dtype info in metadata
                if metadata:
                    format_str = str(metadata).lower()
                    if (
                        "float8" in format_str
                        or "fp8" in format_str
                        or "e4m3" in format_str
                    ):
                        return True

                # Fallback: check tensor names for FP8 scale patterns
                # FP8 models typically have weight_scale or scale_inv tensors
                tensor_keys = list(f.keys())
                has_fp8_scales = any("scale" in k.lower() for k in tensor_keys[:50])
                if has_fp8_scales and any(
                    "weight" in k.lower() and "scale" in k.lower()
                    for k in tensor_keys[:50]
                ):
                    return True
    except Exception:
        pass

    return False


def discover_models_in_folder(folder_path: Optional[Path] = None) -> List[dict]:
    # Scan LLM folder and other model folders (florence2) and discover all models with their detected families.
    #
    # Args:
    #     folder_path: Optional path to scan (defaults to models/LLM + models/florence2)
    #
    # Returns:
    #     List of dicts with keys: name, path, family, is_gguf, is_folder, is_fp8
    try:
        import folder_paths  # type: ignore
        from .model_types import get_model_family_from_name

        models = []
        model_extensions = {".safetensors", ".gguf", ".bin", ".pt", ".onnx"}

        # Determine which folders to scan
        folders_to_scan = []
        if folder_path is None:
            # Scan default folders: models/LLM and models/florence2
            llm_path = get_llm_models_path()
            if llm_path.exists():
                folders_to_scan.append(
                    (llm_path, "")
                )  # (path, prefix for display name)

            florence2_path = Path(folder_paths.models_dir) / "florence2"
            if florence2_path.exists():
                folders_to_scan.append(
                    (florence2_path, "florence2/")
                )  # prefix with "florence2/"
        else:
            if folder_path.exists():
                folders_to_scan.append((folder_path, ""))

        if not folders_to_scan:
            return []

        def scan_dir(
            base_path: Path, relative_path: str = "", display_prefix: str = ""
        ):
            # Recursively scan for models.
            try:
                for item in base_path.iterdir():
                    if item.is_file() and item.suffix in model_extensions:
                        # Skip mmproj files
                        if "mmproj" in item.name.lower():
                            continue

                        file_path = (
                            f"{relative_path}/{item.name}"
                            if relative_path
                            else item.name
                        )
                        display_name = f"{display_prefix}{file_path}"
                        family = get_model_family_from_name(item.name)

                        models.append(
                            {
                                "name": display_name,
                                "path": str(item),
                                "family": family.value,
                                "is_gguf": item.suffix == ".gguf",
                                "is_folder": False,
                                "is_fp8": False,  # Single files are not FP8 (GGUF has own quant)
                            }
                        )
                    elif item.is_dir():
                        # Check if this is a model folder (has config.json or model files)
                        has_config = (item / "config.json").exists()
                        has_safetensors = any(item.glob("*.safetensors"))
                        has_gguf = list(item.glob("*.gguf"))
                        has_model_files = (
                            any(
                                (item / f).exists()
                                for f in [
                                    "model.safetensors",
                                    "pytorch_model.bin",
                                    "model.onnx",
                                ]
                            )
                            or has_safetensors
                            or has_gguf
                        )

                        if has_config or has_model_files:
                            folder_name = (
                                f"{relative_path}/{item.name}/"
                                if relative_path
                                else f"{item.name}/"
                            )
                            display_name = f"{display_prefix}{folder_name}"
                            # Pass full path so config.json can be read for family detection
                            family = get_model_family_from_name(str(item))

                            # Check for FP8 quantization
                            is_fp8 = detect_fp8_model(item)

                            # Add folder entry for non-GGUF models (safetensors, bin, etc.)
                            # Only add folder if there are non-GGUF model files
                            non_gguf_models = has_safetensors or any(
                                (item / f).exists()
                                for f in [
                                    "model.safetensors",
                                    "pytorch_model.bin",
                                    "model.onnx",
                                ]
                            )
                            if non_gguf_models:
                                models.append(
                                    {
                                        "name": display_name,
                                        "path": str(item),
                                        "family": family.value,
                                        "is_gguf": False,
                                        "is_folder": True,
                                        "is_fp8": is_fp8,
                                    }
                                )

                            # ALWAYS list individual GGUF files (even if folder has config.json)
                            # This allows users to select specific quantization variants
                            for gguf_file in has_gguf:
                                # Skip mmproj files
                                if "mmproj" in gguf_file.name.lower():
                                    continue
                                gguf_path = (
                                    f"{relative_path}/{item.name}/{gguf_file.name}"
                                    if relative_path
                                    else f"{item.name}/{gguf_file.name}"
                                )
                                gguf_display = f"{display_prefix}{gguf_path}"
                                gguf_family = get_model_family_from_name(gguf_file.name)
                                models.append(
                                    {
                                        "name": gguf_display,
                                        "path": str(gguf_file),
                                        "family": gguf_family.value,
                                        "is_gguf": True,
                                        "is_folder": False,
                                        "is_fp8": False,
                                    }
                                )
                        else:
                            # Recurse into subdirectory
                            item_rel = (
                                f"{relative_path}/{item.name}"
                                if relative_path
                                else item.name
                            )
                            if relative_path.count("/") < 4:
                                scan_dir(item, item_rel, display_prefix)
            except PermissionError:
                pass

        # Scan all folders
        for scan_path, prefix in folders_to_scan:
            scan_dir(scan_path, "", prefix)

        return sorted(models, key=lambda x: x["name"])

    except Exception as e:
        log.error(_LOG_PREFIX, f"Error discovering models: {e}")
        return []


# ============================================================================
# Model Download Functions
# ============================================================================


def ensure_mmproj_path(
    template_info: dict,
    model_folder: str,
) -> str | None:
    # Ensure mmproj file exists locally, downloading if needed.
    #
    # This function handles the separation between:
    # - mmproj_path: Local file path (relative to LLM folder) - for loading and dropdown selection
    # - mmproj_url: URL for downloading - used when local file doesn't exist
    #
    # Search order when mmproj_path is empty:
    # 1. Check if expected target file exists (derived from URL)
    # 2. Search for any .mmproj.gguf file in model folder
    # 3. Download from URL if not found
    #
    # Args:
    #     template_info: Template dict with mmproj_path (local) and mmproj_url (for download)
    #     model_folder: Folder to download mmproj into (usually model folder)
    #
    # Returns:
    #     Absolute path to mmproj file, or None if not available
    import re
    import folder_paths  # type: ignore

    mmproj_path = template_info.get("mmproj_path", "")
    mmproj_url = template_info.get("mmproj_url", "")

    # Skip if neither path nor URL is provided
    if not mmproj_path and not mmproj_url:
        return None

    llm_dir = get_llm_models_path()
    model_folder_path = Path(model_folder)

    # Case 1: mmproj_path is a local path (not URL) - check if it exists
    if mmproj_path and not mmproj_path.startswith("http"):
        # Resolve to absolute path
        import os

        if os.path.sep in mmproj_path or (
            os.path.altsep and os.path.altsep in mmproj_path
        ):
            local_file = llm_dir / mmproj_path
        else:
            local_file = model_folder_path / mmproj_path

        if local_file.exists():
            return str(local_file)
        # Local path specified but file doesn't exist - fall through to search/download

    # Case 2: mmproj_path is empty or file not found - search for existing mmproj files
    # Search in model folder for any file with 'mmproj' in the name (broader pattern)
    if model_folder_path.exists():
        # Broader search: any .gguf file with 'mmproj' in the name
        # This catches patterns like: model.mmproj-Q8_0.gguf, model.mmproj.gguf, mmproj-fp16.gguf
        all_gguf = list(model_folder_path.glob("*.gguf"))
        mmproj_files = [f for f in all_gguf if "mmproj" in f.name.lower()]
        if mmproj_files:
            found_file = mmproj_files[0]
            log.msg(_LOG_PREFIX, f"✓ Found existing mmproj: {found_file.name}")
            return str(found_file)

    # Case 3: Need to download from URL
    if not mmproj_url:
        if mmproj_path:
            log.warning(
                _LOG_PREFIX,
                f"mmproj_path specified but file not found and no mmproj_url: {mmproj_path}",
            )
        return None

    # Determine target filename and path from URL
    original_filename = mmproj_url.split("/")[-1]

    # Preserve precision info (fp16, bf16, f16, etc.) when renaming
    precision_match = re.search(r"(fp16|bf16|f16|f32)", original_filename.lower())
    precision_suffix = f"-{precision_match.group(1)}" if precision_match else ""

    model_base = model_folder_path.name
    target_filename = f"{model_base}{precision_suffix}.mmproj.gguf"
    target = model_folder_path / target_filename

    # Check if target already exists (may have been downloaded previously with expected name)
    if target.exists():
        return str(target)

    # Also check for original filename (user might have downloaded manually)
    original_target = model_folder_path / original_filename
    if original_target.exists():
        log.msg(
            _LOG_PREFIX, f"✓ Found mmproj with original filename: {original_filename}"
        )
        return str(original_target)

    # Download from URL
    log.msg(_LOG_PREFIX, f"Downloading MMProj from {mmproj_url}")
    target.parent.mkdir(parents=True, exist_ok=True)

    parts = mmproj_url.split("/")
    if "huggingface.co" in mmproj_url and len(parts) >= 6:
        download_with_progress(mmproj_url, str(target), target_filename)
        log.msg(_LOG_PREFIX, f"✓ MMProj downloaded as {target_filename}")

        # Verify integrity
        if target.exists():
            if not verify_model_integrity(
                target, extract_repo_id_from_url(mmproj_url), original_filename
            ):
                log.warning(
                    _LOG_PREFIX, f"MMProj verification failed for {target_filename}"
                )

            return str(target)
    else:
        log.warning(_LOG_PREFIX, f"Invalid mmproj_url format: {mmproj_url}")

    return None


def ensure_model_path(
    template_info: dict,
) -> tuple:
    # Download model if needed and return (model_path, model_folder_path, repo_id).
    #
    # Unified model download function with hash verification and automatic retry.
    # Supports automatic retry on hash verification failure (configurable via retry_download_attempts).
    #
    # Args:
    #     template_info: Template dict with local_path, repo_id, model_type
    #
    # Returns:
    #     Tuple of (model_path, model_folder_path, repo_id)
    #
    # Raises:
    #     ValueError: If template is invalid or path not found
    #     RuntimeError: If model verification fails after all retries
    import shutil
    from .model_types import detect_model_type, ModelType

    local_path = template_info.get("local_path")
    repo_id = template_info.get("repo_id")

    is_direct_url = repo_id and (
        repo_id.startswith("http://") or repo_id.startswith("https://")
    )

    if not repo_id and not local_path:
        raise ValueError("Template missing repo_id or local_path")

    model_type = detect_model_type(template_info)
    models_base = get_llm_models_path()

    # Get retry attempts from config (default 2)
    max_retries = get_config_value("retry_download_attempts", 2)

    # For Florence2 models, also check the models/florence2/ folder (used by comfyui-florence2 node)
    import folder_paths  # type: ignore

    florence2_base = Path(folder_paths.models_dir) / "florence2"
    # For QwenVL models, also check the models/llm/Qwen-VL/ folder (used by other ComfyUI nodes)
    qwenvl_base = models_base / "Qwen-VL"

    target = None

    # Construct target path
    if local_path:
        if local_path.lower().endswith(".gguf"):
            import os

            if os.path.sep in local_path or (
                os.path.altsep and os.path.altsep in local_path
            ):
                target = models_base / local_path
            else:
                model_name = Path(local_path).stem
                # Download GGUF files directly to models/llm/model_name/ (not Qwen-VL subfolder)
                target = models_base / model_name / Path(local_path).name

                # But also check Qwen-VL folder for existing models (backward compatibility)
                if not target.exists() and (
                    model_type == ModelType.QWENVL or "qwen" in local_path.lower()
                ):
                    qwenvl_target = qwenvl_base / model_name / Path(local_path).name
                    if qwenvl_target.exists():
                        target = qwenvl_target
                        log.msg(
                            _LOG_PREFIX,
                            f"✓ Found QwenVL model in Qwen-VL folder: {model_name}",
                        )
        else:
            # Check if local_path starts with a known subfolder of models_dir (e.g., "florence2/")
            # This handles paths like "florence2/base-PromptGen-v1.5/"
            from .common import to_posix_path

            local_path_parts = to_posix_path(local_path).split("/")
            models_dir = Path(folder_paths.models_dir)

            # Get the configured LLM folder name to exclude it from models_dir subfolder detection
            configured_llm_path = get_config_value("llm_models_path", "LLM")
            llm_folder_name = Path(configured_llm_path).name

            first_part = local_path_parts[0] if local_path_parts else ""
            first_part_is_models_subfolder = (
                first_part and (models_dir / first_part).exists()
            )
            first_part_is_llm_folder = first_part == llm_folder_name

            if first_part_is_models_subfolder and not first_part_is_llm_folder:
                # Path is relative to models_dir (e.g., "florence2/model_name/")
                target = models_dir / local_path
            else:
                # Path is relative to LLM folder (models_base)
                target = models_base / local_path

            # For Florence2 models, check alternative locations if not found at local_path
            if not target.exists() and model_type == ModelType.FLORENCE2:
                # Extract just the model name from local_path (remove any folder prefix)
                from .common import to_posix_path

                local_path_clean = to_posix_path(local_path).rstrip("/")
                model_folder_name = local_path_clean.split("/")[
                    -1
                ]  # Get last component

                # Also derive model_name from repo_id (the folder name snapshot_download creates)
                repo_model_name = repo_id.split("/")[-1] if repo_id else None

                # Build list of candidate names to search for
                candidate_names = [model_folder_name]
                if repo_model_name and repo_model_name != model_folder_name:
                    candidate_names.append(repo_model_name)
                # Also try alt names with common Florence prefixes removed/added
                for name in list(candidate_names):
                    if name.startswith("Florence-2-"):
                        candidate_names.append(name[len("Florence-2-") :])
                    if name.startswith("Florence-2.1-"):
                        candidate_names.append(name[len("Florence-2.1-") :])

                # Check LLM folder (models_base) first — this is where both SML and
                # ComfyUI-Florence2 download to
                for name in candidate_names:
                    llm_target = models_base / name
                    if llm_target.exists():
                        target = llm_target
                        log.msg(
                            _LOG_PREFIX,
                            f"✓ Found Florence2 model in LLM folder: {name}",
                        )
                        break

                # Then check models/florence2/ folder (backward compat with older setups)
                if not target.exists() and florence2_base.exists():
                    for name in candidate_names:
                        f2_target = florence2_base / name
                        if f2_target.exists():
                            target = f2_target
                            log.msg(
                                _LOG_PREFIX,
                                f"✓ Found Florence2 model in models/florence2/: {name}",
                            )
                            break

        # GGUF: try recursive filename search before giving up
        if (
            target is not None
            and not target.exists()
            and local_path.lower().endswith(".gguf")
        ):
            filename = Path(local_path).name
            log.msg(_LOG_PREFIX, f"Searching for GGUF file: {filename}...")
            found_path = search_model_file(filename, models_base)
            if found_path:
                target = found_path
                log.msg(_LOG_PREFIX, f"✓ Found at {target}")

        # If target still doesn't exist after all searches, local_path is stale — reset
        # and fall through to repo_id branch so the download goes to the correct location.
        if target is not None and not target.exists() and repo_id:
            log.warning(
                _LOG_PREFIX,
                f"local_path '{local_path}' not found — resetting to repo_id-based download",
            )
            target = None  # Triggers repo_id branch below

    if target is None:
        if not repo_id:
            raise ValueError(
                "Model path not found and no repo_id specified for download"
            )
        model_name = repo_id.split("/")[-1]
        if is_direct_url and model_name.lower().endswith(".gguf"):
            filename = model_name
            folder_name = Path(filename).stem
            # Download GGUF files directly to models/llm/folder_name/ (not Qwen-VL subfolder)
            target = models_base / folder_name / filename

            # Search for existing file (including in Qwen-VL subfolder for backward compatibility)
            if not target.exists():
                log.msg(_LOG_PREFIX, f"Searching for GGUF file: {filename}...")
                found_path = search_model_file(filename, models_base)
                if found_path:
                    target = found_path
                    log.msg(_LOG_PREFIX, f"✓ Found at {target}")
        elif model_type == ModelType.QWENVL:
            # Download new QwenVL models directly to models/llm/ (not Qwen-VL subfolder)
            target = models_base / model_name
            # But check Qwen-VL folder for existing models (backward compatibility with other ComfyUI nodes)
            if not target.exists() and qwenvl_base.exists():
                qwenvl_target = qwenvl_base / model_name
                if qwenvl_target.exists():
                    target = qwenvl_target
                    log.msg(
                        _LOG_PREFIX,
                        f"✓ Found QwenVL model in Qwen-VL folder: {model_name}",
                    )
        elif model_type == ModelType.FLORENCE2:
            # Florence2: check LLM folder first, then models/florence2/ with alt-name matching
            target = models_base / model_name
            if not target.exists():
                # Build candidate names to search for
                candidate_names = [model_name]
                if model_name.startswith("Florence-2-"):
                    candidate_names.append(model_name[len("Florence-2-") :])
                if model_name.startswith("Florence-2.1-"):
                    candidate_names.append(model_name[len("Florence-2.1-") :])

                # Check LLM folder with alt names
                for name in candidate_names[1:]:  # Skip first (already checked above)
                    llm_target = models_base / name
                    if llm_target.exists():
                        target = llm_target
                        log.msg(
                            _LOG_PREFIX,
                            f"✓ Found Florence2 model in LLM folder: {name}",
                        )
                        break

                # Check models/florence2/ folder (backward compat)
                if not target.exists() and florence2_base.exists():
                    for name in candidate_names:
                        f2_target = florence2_base / name
                        if f2_target.exists():
                            target = f2_target
                            log.msg(
                                _LOG_PREFIX,
                                f"✓ Found Florence2 model in models/florence2/: {name}",
                            )
                            break
        else:
            target = models_base / model_name

    def _delete_corrupted_files(
        path: Path, specific_files: Optional[List[Path]] = None
    ):
        # Delete corrupted model files for re-download.
        #
        # Args:
        #     path: Model directory or file path (used as fallback)
        #     specific_files: If provided, only delete these specific files instead of entire folder
        try:
            if specific_files:
                # Delete only the specific corrupted files
                for file_path in specific_files:
                    if file_path.exists():
                        file_path.unlink()
                        log.msg(
                            _LOG_PREFIX, f"Deleted corrupted file: {file_path.name}"
                        )
                    # Also delete any .sha256 hash file
                    sha_file = file_path.parent / f"{file_path.name}.sha256"
                    if sha_file.exists():
                        sha_file.unlink()
            elif path.is_dir():
                # Delete the entire model folder (fallback for full re-download)
                shutil.rmtree(path)
                log.msg(_LOG_PREFIX, f"Deleted corrupted folder: {path}")
            elif path.is_file():
                # Delete the single file
                path.unlink()
                log.msg(_LOG_PREFIX, f"Deleted corrupted file: {path}")
                # Also delete any .sha256 hash file
                sha_file = path.parent / f"{path.name}.sha256"
                if sha_file.exists():
                    sha_file.unlink()
        except Exception as e:
            log.error(_LOG_PREFIX, f"Failed to delete corrupted files: {e}")

    def _download_model(target_path: Path) -> bool:
        # Perform the actual download. Returns True if downloaded.
        # Get HF token from environment or config for faster downloads
        # Priority: HF_TOKEN env > HUGGING_FACE_HUB_TOKEN env > config
        import os

        hf_token = os.environ.get("HF_TOKEN") or os.environ.get(
            "HUGGING_FACE_HUB_TOKEN"
        )
        if not hf_token:
            config_token = get_config_value("hf_token", "")
            if config_token and config_token.strip():
                hf_token = config_token.strip()

        if is_direct_url:
            assert repo_id is not None
            log.msg(_LOG_PREFIX, f"Downloading from {repo_id}")
            target_path.parent.mkdir(parents=True, exist_ok=True)

            parts = repo_id.split("/")
            if "huggingface.co" in repo_id and len(parts) >= 6:
                filename = parts[-1]

                # Get expected hash from HuggingFace for verification
                hf_repo_id = extract_repo_id_from_url(repo_id)
                expected_hash = (
                    get_hf_file_hash(hf_repo_id, filename, hf_token)
                    if hf_repo_id
                    else None
                )

                # Use temp folder approach for more reliable downloads
                if download_file_via_temp(
                    repo_id, target_path, filename, expected_hash
                ):
                    log.msg(_LOG_PREFIX, f"✓ Downloaded to {target_path}")
                    return True
                else:
                    raise RuntimeError(
                        f"Failed to download {filename} after multiple attempts"
                    )
            else:
                raise ValueError(f"Invalid URL format: {repo_id}")
        elif repo_id:
            log.msg(_LOG_PREFIX, f"Downloading {repo_id} to {target_path}")
            from huggingface_hub import snapshot_download  # type: ignore
            import inspect

            # Log if Xet high-performance transfer is active
            if os.environ.get("HF_XET_HIGH_PERFORMANCE") == "1":
                log.msg(
                    _LOG_PREFIX,
                    "Using Xet high-performance transfer for accelerated download",
                )

            download_kwargs = {
                "repo_id": repo_id,
                "local_dir": str(target_path),
                "ignore_patterns": ["*.md", ".git*"],
            }
            if (
                "local_dir_use_symlinks"
                in inspect.signature(snapshot_download).parameters
            ):
                download_kwargs["local_dir_use_symlinks"] = False

            # Add token if available for faster authenticated downloads
            if hf_token:
                download_kwargs["token"] = hf_token
                log.debug(_LOG_PREFIX, "Using HF token for authenticated download")

            snapshot_download(**download_kwargs)
            return True
        else:
            raise ValueError(f"Model path not found: {target_path}")

    def _get_hf_token() -> Optional[str]:
        # Get HuggingFace token from environment or config.
        import os

        hf_token = os.environ.get("HF_TOKEN") or os.environ.get(
            "HUGGING_FACE_HUB_TOKEN"
        )
        if not hf_token:
            config_token = get_config_value("hf_token", "")
            if config_token and config_token.strip():
                hf_token = config_token.strip()
        return hf_token

    # Download with retry logic
    downloaded = False
    verification_failed = False
    last_corrupted_files = []  # Track corrupted files for selective re-download

    for attempt in range(max_retries + 1):  # +1 because first attempt is not a "retry"
        # Download if target doesn't exist
        if not target.exists():
            try:
                downloaded = _download_model(target)
            except Exception as e:
                # Download threw an exception - check if partial files were created
                if target.exists():
                    log.warning(
                        _LOG_PREFIX,
                        f"Download failed (attempt {attempt + 1}/{max_retries + 1}): {e}",
                    )
                    # Check if download is incomplete and delete partial files
                    hf_token = _get_hf_token()
                    is_complete, missing = check_model_completeness(
                        target,
                        extract_repo_id_from_url(repo_id) if repo_id else None,
                        hf_token,
                    )
                    if not is_complete:
                        log.warning(
                            _LOG_PREFIX,
                            f"Incomplete download detected ({len(missing)} files missing), cleaning up...",
                        )
                        _delete_corrupted_files(
                            target
                        )  # Delete entire folder for clean retry
                    if attempt < max_retries:
                        continue
                    raise
                else:
                    if attempt < max_retries:
                        log.warning(
                            _LOG_PREFIX,
                            f"Download failed (attempt {attempt + 1}/{max_retries + 1}): {e}",
                        )
                        continue
                    raise
        elif target.exists() and not downloaded:
            # Target exists but wasn't downloaded in this session - verify completeness
            hf_token = _get_hf_token()
            is_complete, missing = check_model_completeness(
                target, extract_repo_id_from_url(repo_id) if repo_id else None, hf_token
            )
            if not is_complete and missing:
                log.msg(
                    _LOG_PREFIX,
                    f"Existing model folder is incomplete: {len(missing)} file(s) missing",
                )
                clean_repo_id = extract_repo_id_from_url(repo_id) if repo_id else None
                if clean_repo_id and download_missing_files(
                    target, missing, clean_repo_id, hf_token
                ):
                    downloaded = True  # Mark as downloaded for verification
                else:
                    log.warning(_LOG_PREFIX, "Could not download missing files")
                    if attempt < max_retries:
                        _delete_corrupted_files(target)
                        continue
                    raise RuntimeError(
                        f"Model incomplete and could not download missing files: {', '.join(missing)}"
                    )
            else:
                # Model is complete, no need to download
                downloaded = True  # Trigger verification

        # Verify integrity after download
        if downloaded and target.exists():
            # Use return_details=True to get list of corrupted files
            verification_result = verify_model_integrity(
                target, extract_repo_id_from_url(repo_id or ""), return_details=True
            )

            # Unpack verification_result (either VerificationResult or bool)
            if isinstance(verification_result, VerificationResult):
                success = verification_result.success
                corrupted_files = verification_result.corrupted_files
            else:
                success = verification_result
                corrupted_files = []

            if success:
                # Verification passed
                verification_failed = False
                break
            else:
                # Verification failed
                verification_failed = True
                last_corrupted_files = corrupted_files

                if attempt < max_retries:
                    # Try selective re-download of only corrupted files (if we have a valid repo_id)
                    clean_repo_id = extract_repo_id_from_url(repo_id or "")

                    if last_corrupted_files and clean_repo_id and not is_direct_url:
                        # Selective re-download: only re-download corrupted files
                        log.msg(
                            _LOG_PREFIX,
                            f"⚠ {len(last_corrupted_files)} file(s) failed verification (attempt {attempt + 1}/{max_retries + 1})",
                        )
                        log.msg(
                            _LOG_PREFIX,
                            f"Attempting selective re-download of corrupted files only...",
                        )

                        hf_token = _get_hf_token()
                        if redownload_corrupted_files(
                            last_corrupted_files, clean_repo_id, target, hf_token
                        ):
                            # Files were re-downloaded, continue to next verification attempt
                            # Don't set downloaded=False since the folder still exists
                            continue
                        else:
                            # Selective re-download failed, fall back to full re-download
                            log.warning(
                                _LOG_PREFIX,
                                "Selective re-download failed, falling back to full re-download...",
                            )
                            _delete_corrupted_files(target)
                            downloaded = False
                    else:
                        # No repo_id or direct URL - delete and re-download everything
                        log.warning(
                            _LOG_PREFIX,
                            f"⚠ Hash verification failed (attempt {attempt + 1}/{max_retries + 1}), will retry download...",
                        )
                        _delete_corrupted_files(target)
                        downloaded = False  # Reset to trigger re-download
                else:
                    log.error(
                        _LOG_PREFIX,
                        f"✗ Hash verification failed after {max_retries + 1} attempts",
                    )
                    # Delete corrupted files so next restart will trigger fresh download
                    if last_corrupted_files:
                        log.msg(
                            _LOG_PREFIX,
                            "Deleting corrupted files to allow fresh download on restart...",
                        )
                        _delete_corrupted_files(target, last_corrupted_files)
        else:
            # downloaded is False and target doesn't exist - shouldn't happen, but break to avoid infinite loop
            if not target.exists():
                raise RuntimeError(
                    f"Model download failed: target does not exist after download attempt: {target}"
                )
            # Target exists but downloaded is False - this means completeness check didn't trigger download
            # This is a valid state for pre-existing complete models that don't need verification
            break

    # Final check - raise error if verification still failed
    if verification_failed:
        corrupted_names = (
            [f.name for f in last_corrupted_files] if last_corrupted_files else []
        )
        raise RuntimeError(
            f"Model verification failed for {target} after {max_retries + 1} attempts.\n"
            f"Corrupted files: {', '.join(corrupted_names) if corrupted_names else 'unknown'}\n"
            f"The download may be corrupted. Please check your network connection and try again."
        )

    # Update template with local path
    # Prefer relative path to LLM folder, but also support models in other locations (e.g., models/florence2/)
    current_local_path = None

    # First try: relative to LLM folder (models_base)
    try:
        relative_path = target.relative_to(models_base)
        current_local_path = relative_path.as_posix()
        if target.is_dir() and not current_local_path.endswith("/"):
            current_local_path += "/"
    except ValueError:
        pass

    # Second try: relative to ComfyUI models folder (e.g., "florence2/model_name/")
    if current_local_path is None:
        try:
            relative_path = target.relative_to(Path(folder_paths.models_dir))
            current_local_path = relative_path.as_posix()
            if target.is_dir() and not current_local_path.endswith("/"):
                current_local_path += "/"
        except ValueError:
            # Model is not under models_dir - don't update local_path
            pass

    return (str(target), str(target.parent), repo_id or "")


# ============================================================================
# Model Deletion
# ============================================================================


def delete_model(display_name: str) -> Dict[str, Any]:
    # Delete a model from disk given its registry display name.
    # Returns {"success": bool, "error": str (if failed), "deleted": str (path/id)}.
    #
    # Backend-specific deletion:
    #   transformers/wd14: shutil.rmtree on model folder
    #   gguf: unlink .gguf file + .sha256 sidecar
    #   yolo: unlink .pt file
    #   ollama: docker exec sml-ollama ollama rm <repo_id>
    #   vllm/sglang: no local files (API-only, returns error)
    from .model_registry import (
        get_model_entry,
        invalidate_cache,
        invalidate_yolo_cache,
        sync_yolo_registry,
    )
    from .backend_yolo import resolve_yolo_model_path

    entry = get_model_entry(display_name)
    if entry is None:
        return {
            "success": False,
            "error": f"Model not found in registry: {display_name}",
        }

    backend = entry.get("backend", "")
    repo_id = entry.get("repo_id", "")
    name = entry.get("name", "")

    # ── Docker / API backends ─────────────────────────────────────────
    if backend in ("vllm", "sglang"):
        return {
            "success": False,
            "error": f"Cannot delete {backend} models — they are API-only (no local files)",
        }

    if backend == "ollama":
        return _delete_ollama_model(repo_id or name)

    # ── YOLO models ───────────────────────────────────────────────────
    if backend == "yolo":
        filename = entry.get("filename", f"{name}.pt")
        full_path = resolve_yolo_model_path(filename)
        if not full_path:
            return {
                "success": False,
                "error": f"YOLO model file not found on disk: {filename}",
            }
        try:
            Path(full_path).unlink()
            log.msg(_LOG_PREFIX, f"Deleted YOLO model: {full_path}")
            sync_yolo_registry()
            invalidate_yolo_cache()
            return {"success": True, "deleted": full_path}
        except OSError as e:
            return {"success": False, "error": f"Failed to delete {full_path}: {e}"}

    # ── Local backends (transformers, gguf, wd14) ─────────────────────
    llm_base = get_llm_models_path()
    import folder_paths  # type: ignore

    if backend == "gguf":
        deleted = _delete_gguf_model(entry, llm_base)
        if deleted:
            _invalidate_model_list_cache()
            invalidate_cache()
            return {"success": True, "deleted": deleted}
        return {
            "success": False,
            "error": f"GGUF model file not found on disk for: {display_name}",
        }

    # Transformers or WD14 — delete the model folder
    candidates = []
    if backend == "wd14":
        wd14_name = repo_id.split("/")[-1] if "/" in repo_id else name
        candidates.append(llm_base / wd14_name)
    else:
        # Transformers: check name, repo_id last component, and florence2/
        candidates.append(llm_base / name)
        if "/" in repo_id:
            candidates.append(llm_base / repo_id.split("/")[-1])
        if entry.get("family") == "Florence":
            candidates.append(Path(folder_paths.models_dir) / "florence2" / name)

    for folder in candidates:
        if folder.exists() and folder.is_dir():
            # Security: ensure folder is within expected base directories
            try:
                folder.resolve().relative_to(llm_base.resolve())
            except ValueError:
                try:
                    folder.resolve().relative_to(
                        Path(folder_paths.models_dir).resolve()
                    )
                except ValueError:
                    return {
                        "success": False,
                        "error": f"Path traversal blocked: {folder}",
                    }
            try:
                shutil.rmtree(folder)
                log.msg(_LOG_PREFIX, f"Deleted model folder: {folder}")
                _invalidate_model_list_cache()
                invalidate_cache()
                return {"success": True, "deleted": str(folder)}
            except OSError as e:
                return {"success": False, "error": f"Failed to delete {folder}: {e}"}

    return {
        "success": False,
        "error": f"Model folder not found on disk for: {display_name}",
    }


def _delete_gguf_model(entry: Dict[str, Any], llm_base: Path) -> Optional[str]:
    # Find and delete a GGUF model file. Returns deleted path or None.
    name = entry.get("name", "")
    repo_id = entry.get("repo_id", "")
    file_pattern = entry.get("file_pattern", "")
    repo_folder = repo_id.split("/")[-1] if "/" in repo_id else name

    # Collect possible filenames (all quantizations)
    quantizations = entry.get("quantizations", [])

    # Build candidate folder names
    seen = set()
    folders = []
    for c in [repo_folder, name]:
        if c and c not in seen:
            seen.add(c)
            folders.append(c)

    # Check if the entire folder is a single-model GGUF repo
    for folder_name in folders:
        candidate_dir = llm_base / folder_name
        if not candidate_dir.exists():
            continue
        gguf_files = list(candidate_dir.glob("*.gguf"))
        sha_files = list(candidate_dir.glob("*.gguf.sha256"))
        other_files = [
            f
            for f in candidate_dir.iterdir()
            if f.is_file()
            and not f.name.endswith(".gguf")
            and not f.name.endswith(".sha256")
        ]
        # If folder contains only GGUF/SHA files, delete the whole folder
        if gguf_files and not other_files:
            try:
                shutil.rmtree(candidate_dir)
                log.msg(_LOG_PREFIX, f"Deleted GGUF model folder: {candidate_dir}")
                return str(candidate_dir)
            except OSError:
                pass

    # Fallback: delete individual GGUF files matching this model
    for folder_name in folders:
        candidate_dir = llm_base / folder_name
        if not candidate_dir.exists():
            continue
        for f in candidate_dir.glob("*.gguf"):
            if _gguf_file_matches_model(f.name, name, file_pattern, quantizations):
                sidecar = f.with_suffix(f.suffix + ".sha256")
                try:
                    f.unlink()
                    if sidecar.exists():
                        sidecar.unlink()
                    log.msg(_LOG_PREFIX, f"Deleted GGUF file: {f}")
                    return str(f)
                except OSError:
                    pass

    # Flat file in LLM base
    for f in llm_base.glob("*.gguf"):
        if _gguf_file_matches_model(f.name, name, file_pattern, quantizations):
            sidecar = f.with_suffix(f.suffix + ".sha256")
            try:
                f.unlink()
                if sidecar.exists():
                    sidecar.unlink()
                log.msg(_LOG_PREFIX, f"Deleted GGUF file: {f}")
                return str(f)
            except OSError:
                pass

    return None


def _gguf_file_matches_model(
    filename: str, model_name: str, file_pattern: str, quantizations: list
) -> bool:
    # Check if a GGUF filename belongs to this model entry.
    lower = filename.lower()
    name_lower = model_name.lower()
    if lower.startswith(name_lower):
        return True
    if file_pattern:
        # Check pattern with any quantization placeholder
        base_pattern = file_pattern.replace("{quant}", "").lower()
        if base_pattern and lower.startswith(base_pattern.rstrip("-.").lower()):
            return True
    return False


def _delete_ollama_model(model_id: str) -> Dict[str, Any]:
    # Delete an Ollama model via docker exec (primary) or local filesystem (fallback).
    #
    # Priority:
    #   1. docker exec ollama rm — auto-starts container if needed (handles root-owned files)
    #   2. Local filesystem — parse manifest, delete exclusive blobs (when Docker unavailable)
    import subprocess

    try:
        from .backend_ollama_docker import (
            OLLAMA_CONTAINER_NAME,
            ensure_ollama_running,
            delete_ollama_model_local,
            is_ollama_container_running,
            stop_ollama_container,
        )
    except ImportError:
        return {"success": False, "error": "Ollama backend not available"}

    # Primary — docker exec (auto-start container, no model loading needed)
    was_running = is_ollama_container_running()
    if ensure_ollama_running():
        try:
            proc = subprocess.run(
                ["docker", "exec", OLLAMA_CONTAINER_NAME, "ollama", "rm", model_id],
                capture_output=True,
                timeout=30,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode == 0:
                log.msg(_LOG_PREFIX, f"Deleted Ollama model via container: {model_id}")
                result = {"success": True, "deleted": model_id}
            else:
                error_msg = (
                    proc.stderr.strip() or proc.stdout.strip() or "Unknown error"
                )
                result = {"success": False, "error": f"ollama rm failed: {error_msg}"}
        except subprocess.TimeoutExpired:
            result = {"success": False, "error": "ollama rm timed out (30s)"}
        except Exception as e:
            result = {"success": False, "error": f"Failed to run ollama rm: {e}"}

        # Stop container if we started it just for deletion
        if not was_running:
            stop_ollama_container()

        return result

    # Fallback — local filesystem deletion (Docker unavailable)
    return delete_ollama_model_local(model_id)


def _invalidate_model_list_cache():
    # Clear the model list cache so next call rescans disk.
    global _model_list_cache, _model_list_cache_time
    global _mmproj_list_cache, _mmproj_list_cache_time
    _model_list_cache.clear()
    _model_list_cache_time = 0.0
    _mmproj_list_cache.clear()
    _mmproj_list_cache_time = 0.0
