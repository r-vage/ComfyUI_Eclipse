import os
import json
import time
import torch #type: ignore
import numpy as np #type: ignore
import nodes #type: ignore
import folder_paths #type: ignore

from PIL import Image, ImageOps #type: ignore
from PIL.PngImagePlugin import PngInfo #type: ignore
from typing import List, Optional, Tuple
from server import PromptServer #type: ignore
from ..core import CATEGORY
from ..core.logger import log
from ..core.file_cache import FileListCache
from comfy_api.latest import io #type: ignore


_temp_dir = folder_paths.get_temp_directory()
_prefix_append = "_temp_" + ''.join(__import__('random').choice("abcdefghijklmnopqrstupvxyz") for _ in range(5))


_LOG_PREFIX = "Load Image From Folder"


# Supported image extensions
SUPPORTED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff', '.tif')


# ============================================================================
# Helper functions
# ============================================================================

def _get_image_files(folder_path: str, include_subfolders: bool) -> List[str]:
    # Get all image files from the folder.
    image_files = []

    if not os.path.exists(folder_path):
        return image_files

    if include_subfolders:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                if file.lower().endswith(SUPPORTED_EXTENSIONS):
                    image_files.append(os.path.join(root, file))
    else:
        for file in os.listdir(folder_path):
            filepath = os.path.join(folder_path, file)
            if os.path.isfile(filepath) and file.lower().endswith(SUPPORTED_EXTENSIONS):
                image_files.append(filepath)

    return image_files


def _sort_files(files: List[str], sort_by: str, sort_order: str) -> List[str]:
    # Sort the file list based on criteria.
    # IMPORTANT: Always uses full path or filename as secondary sort key to ensure deterministic ordering.
    # This prevents the issue where files with the same primary sort key (e.g., same timestamp)
    # could appear in different orders across executions.
    reverse = sort_order == "descending"

    if sort_by == "name":
        # Sort by full path (case-insensitive) for consistent ordering across subfolders
        # This makes it easier to track progress when stopping/continuing later
        files.sort(key=lambda x: x.lower(), reverse=reverse)
    elif sort_by == "date_modified":
        # Primary: modification time, Secondary: full path for determinism
        files.sort(key=lambda x: (os.path.getmtime(x), x.lower()), reverse=reverse)
    elif sort_by == "date_created":
        # Primary: creation time, Secondary: full path for determinism
        # On Windows, getctime is creation time; on Unix, it's the last metadata change
        files.sort(key=lambda x: (os.path.getctime(x), x.lower()), reverse=reverse)
    elif sort_by == "size":
        # Primary: file size, Secondary: full path for determinism
        files.sort(key=lambda x: (os.path.getsize(x), x.lower()), reverse=reverse)

    return files


def _get_or_create_file_list(
    folder_path: str,
    include_subfolders: bool,
    sort_by: str,
    sort_order: str,
    refresh: bool = False
) -> List[str]:
    # Get file list from cache or create and cache it.
    # This ensures consistent ordering across executions.
    cache_key = FileListCache.get_cache_key(folder_path, include_subfolders, sort_by, sort_order)

    # Check if we need to refresh
    if refresh:
        FileListCache.invalidate(folder_path)
        log.debug(_LOG_PREFIX, f"Refreshing file list for: {folder_path}")

    # Try to get from cache
    cached_list = FileListCache.get_cached_list(cache_key)
    if cached_list is not None:
        log.debug(_LOG_PREFIX, f"Using cached file list ({len(cached_list)} images)")
        return cached_list

    # Build new list
    log.debug(_LOG_PREFIX, f"Building file list for: {folder_path}")
    image_files = _get_image_files(folder_path, include_subfolders)

    # Sort the list (with deterministic secondary key)
    image_files = _sort_files(image_files, sort_by, sort_order)

    # Cache the result
    params = {
        "folder_path": folder_path,
        "include_subfolders": include_subfolders,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "count": len(image_files)
    }
    FileListCache.set_cached_list(cache_key, image_files, params)
    log.debug(_LOG_PREFIX, f"Cached file list ({len(image_files)} images)")

    return image_files


def _load_image(filepath: str) -> Tuple[Optional[torch.Tensor], Optional[torch.Tensor]]:
    # Load a single image and convert to tensor. Returns (image, mask) or (None, None) on failure.
    try:
        img = Image.open(filepath)
        img = ImageOps.exif_transpose(img)

        if img.mode == 'I':
            img = img.point(lambda i: i * (1 / 255))

        # Convert to RGB for image tensor
        image_rgb = img.convert("RGB")
        image_np = np.array(image_rgb).astype(np.float32) / 255.0
        image_tensor = torch.from_numpy(image_np)[None,]

        # Extract mask from alpha channel if present
        if 'A' in img.getbands():
            mask_np = np.array(img.getchannel('A')).astype(np.float32) / 255.0
            mask_tensor = 1. - torch.from_numpy(mask_np)
        else:
            mask_tensor = torch.zeros((64, 64), dtype=torch.float32, device="cpu")

        return image_tensor, mask_tensor.unsqueeze(0)

    except Exception as e:
        log.error(_LOG_PREFIX, f"Failed to load image {filepath}: {e}")
        return None, None


def _resolve_folder_path(folder_path: str) -> str:
    # Resolve folder path - can be absolute or relative to input directory.
    if not folder_path:
        return folder_paths.get_input_directory()

    # Strip quotes from path (in case user pastes path with quotes)
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
    comfy_root = os.path.dirname(os.path.dirname(folder_paths.get_input_directory()))
    root_relative = os.path.join(comfy_root, folder_path)
    if os.path.exists(root_relative):
        return root_relative

    # Return as-is, will fail gracefully later
    return folder_path


# ============================================================================
# Main node class (V3)
# ============================================================================

class RvImage_LoadImageFromFolder(io.ComfyNode):
    # Load images from one or more folders with index control.
    # Useful for batch processing workflows like captioning or tagging.
    #
    # MULTI-FOLDER SUPPORT:
    # - Enter multiple folder paths, one per line
    # - Index spans across all folders (cumulative)
    # - Each folder is cached separately for efficiency
    # - Folders are processed in order listed
    #
    # Outputs image and mask. For metadata extraction, use the Pipe variant.
    #
    # File list is cached for consistent ordering across executions.
    # Use refresh_list to force a rescan of the folder(s).

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Load Image From Folder [Eclipse]",
            display_name="Load Image From Folder",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            is_output_node=True,
            inputs=[
                io.String.Input("folder_path", default="", multiline=True, tooltip="Path(s) to folder(s) containing images. One folder per line. Can be absolute or relative to ComfyUI input folder. Index spans across all folders."),
                io.Boolean.Input("include_subfolders", default=True, socketless=True, tooltip="Include images from subfolders recursively."),
                io.Int.Input("index", default=0, min=-4, max=999999, step=1, tooltip="Image index. Special modes: -1=Random, -2=Increment, -3=Decrement, -4=Shuffle (no repeat)."),
                io.Combo.Input("sort_by", options=["name", "date_modified", "date_created", "size"], default="name", tooltip="How to sort the image list."),
                io.Combo.Input("sort_order", options=["ascending", "descending"], default="ascending", tooltip="Sort order for the image list."),
                io.Boolean.Input("stop_at_end", default=True, socketless=True, tooltip="Stop workflow when index reaches end of list. Disable to wrap around."),
                io.Boolean.Input("refresh_list", default=False, socketless=True, tooltip="Force refresh of the cached file list. Enable once to rescan the folder, then disable. Useful after adding/removing files."),
                io.Int.Input("seed_input", force_input=True, optional=True, tooltip="When connected, special index modes (-1/-2/-3/-4) only advance when this value changes. Keep the same seed to freeze image selection while tweaking other workflow settings."),
            ],
            outputs=[
                io.Image.Output("image"),
                io.Mask.Output("mask"),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        folder_path = kwargs.get("folder_path", "")
        index = kwargs.get("index", 0)
        refresh_list = kwargs.get("refresh_list", False)
        import hashlib
        folder_hash = hashlib.md5(folder_path.encode()).hexdigest()[:8]
        return f"{folder_hash}_{index}_{refresh_list}"

    @classmethod
    def execute(cls, folder_path, include_subfolders, index, sort_by, sort_order, stop_at_end=True, refresh_list=False, seed_input=None):
        # Execute the node with multi-folder support.

        # Parse multiple folders (one per line)
        folder_lines = [f.strip() for f in folder_path.strip().split('\n') if f.strip()]

        if not folder_lines:
            log.error(_LOG_PREFIX, "No folder paths provided")
            raise ValueError("No folder paths provided")

        # Build combined file list from all folders
        # Each folder is cached separately (Option B)
        all_files: List[Tuple[str, int, str]] = []  # [(filepath, folder_index, folder_path), ...]
        folder_info: List[Tuple[str, int, int]] = []  # [(resolved_path, start_idx, count), ...]
        skipped_folders: List[str] = []

        cumulative_idx = 0
        for folder_idx, folder_line in enumerate(folder_lines):
            # Resolve folder path
            resolved_path = _resolve_folder_path(folder_line)

            # Check if folder exists
            if not os.path.exists(resolved_path):
                log.warning(_LOG_PREFIX, f"Folder not found, skipping: {folder_line}")
                skipped_folders.append(folder_line)
                continue

            # Invalidate cache if refresh requested
            if refresh_list:
                FileListCache.invalidate(resolved_path)

            # Get file list from cache or build it
            image_files = _get_or_create_file_list(
                resolved_path,
                include_subfolders,
                sort_by,
                sort_order,
                refresh=False  # Already invalidated above if needed
            )

            if not image_files:
                log.warning(_LOG_PREFIX, f"No images in folder, skipping: {folder_line}")
                skipped_folders.append(folder_line)
                continue

            # Track folder info
            folder_info.append((resolved_path, cumulative_idx, len(image_files)))

            # Add files to combined list with folder tracking
            for filepath in image_files:
                all_files.append((filepath, len(folder_info) - 1, resolved_path))

            cumulative_idx += len(image_files)
            log.debug(_LOG_PREFIX, f"Folder {len(folder_info)}: {os.path.basename(resolved_path)} ({len(image_files)} images)")

        # Check if we have any valid folders/files
        total_count = len(all_files)
        total_folders = len(folder_info)

        if total_count == 0:
            if skipped_folders:
                log.error(_LOG_PREFIX, f"No images found. Skipped folders: {skipped_folders}")
                raise ValueError(f"No images found in any provided folders. Skipped: {skipped_folders}")
            else:
                log.error(_LOG_PREFIX, "No images found in any provided folders")
                raise ValueError("No images found in any provided folders")

        log.msg(_LOG_PREFIX, f"Total: {total_count} images across {total_folders} folder(s)")

        # Clamp index to valid range first
        # This handles the case where user changes to a smaller folder but index is still high
        start_index = index % total_count

        # Warn if index exceeds available images (e.g., after switching to a smaller folder)
        if index > total_count:
            log.warning(_LOG_PREFIX, f"Index {index} exceeds image count ({total_count}). Wrapping to index {start_index}.")

        # Only stop if the original index equals total_count exactly (meaning we just finished)
        # If index > total_count (e.g., old folder had more images), we wrap to start
        if stop_at_end and index == total_count:
            log.msg(_LOG_PREFIX, f"Reached end of all folders ({total_count} images in {total_folders} folders). Stopping workflow and disabling auto-queue.")
            PromptServer.instance.send_sync("stop-iteration", {})
            nodes.interrupt_processing()
            # Return empty tensors - won't be used since workflow is interrupted
            # But we must return to prevent further execution
            empty_image = torch.zeros((1, 64, 64, 3), dtype=torch.float32)
            empty_mask = torch.zeros((1, 64, 64), dtype=torch.float32)
            return io.NodeOutput(empty_image, empty_mask, ui={"images": []})

        # Try to load image, skip to next on failure
        current_index = start_index
        attempts = 0
        max_attempts = total_count

        while attempts < max_attempts:
            current_filepath, current_folder_idx, current_folder_path = all_files[current_index]
            current_image, current_mask = _load_image(current_filepath)

            if current_image is not None:
                # Get folder info for this file
                folder_path_resolved, folder_start, folder_count = folder_info[current_folder_idx]
                local_index = current_index - folder_start

                # Log with multi-folder context
                if total_folders > 1:
                    log.msg(_LOG_PREFIX, f"Folder {current_folder_idx + 1}/{total_folders}: {os.path.basename(folder_path_resolved)}")
                    log.msg(_LOG_PREFIX, f"Image {local_index + 1}/{folder_count} (global: {current_index + 1}/{total_count}): {os.path.basename(current_filepath)}")
                else:
                    log.msg(_LOG_PREFIX, f"Loading image {current_index + 1}/{total_count}: {os.path.basename(current_filepath)}")

                # Save preview for node display
                ui_images = _save_preview(current_image, cls.hidden.prompt, cls.hidden.extra_pnginfo)

                return io.NodeOutput(current_image, current_mask, ui={"images": ui_images})

            # Failed to load, try next image
            log.warning(_LOG_PREFIX, f"Skipping unreadable image {current_index + 1}/{total_count}: {os.path.basename(current_filepath)}")
            current_index = (current_index + 1) % total_count
            attempts += 1

            # Check if we've wrapped around and should stop
            if stop_at_end and current_index < start_index:
                log.msg(_LOG_PREFIX, f"Reached end of all folders after skipping failed images. Stopping workflow and disabling auto-queue.")
                PromptServer.instance.send_sync("stop-iteration", {})
                nodes.interrupt_processing()
                # Return empty tensors - won't be used since workflow is interrupted
                empty_image = torch.zeros((1, 64, 64, 3), dtype=torch.float32)
                empty_mask = torch.zeros((1, 64, 64), dtype=torch.float32)
                return io.NodeOutput(empty_image, empty_mask, ui={"images": []})

        # All images failed to load
        log.error(_LOG_PREFIX, f"Could not load any images from {total_folders} folder(s)")
        raise ValueError(f"Could not load any images from {total_folders} folder(s)")


def _save_preview(image_tensor, prompt, extra_pnginfo):
    # Save image tensor to temp folder for node preview display
    results = []

    metadata = PngInfo()
    if prompt is not None:
        metadata.add_text("prompt", json.dumps(prompt))
    if extra_pnginfo is not None:
        for x in extra_pnginfo:
            metadata.add_text(x, json.dumps(extra_pnginfo[x]))

    # Handle batched images — preview first frame only
    img_data = image_tensor[0] if image_tensor.ndim == 4 else image_tensor
    if img_data.ndim == 4 and img_data.shape[0] == 1:
        img_data = img_data.squeeze(0)

    i = 255.0 * img_data.cpu().numpy()
    img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))
    width, height = img.size

    full_output_folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
        "ComfyUI" + _prefix_append, _temp_dir, width, height)
    timestamp = int(time.time() * 1000) % 100000000
    file = f"{filename}_{counter:05}_{timestamp}_.png"
    filepath = os.path.join(full_output_folder, file)
    img.save(filepath, pnginfo=metadata, compress_level=1)
    results.append({"filename": file, "subfolder": subfolder, "type": "temp"})

    return results
