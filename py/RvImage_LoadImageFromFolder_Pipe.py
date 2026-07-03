# Load Image From Folder (Pipe) — pipe-only output with execution preview
# Combo-chip toggles for boolean options and preview enable/disable
# STANDALONE FILE — does NOT import from other node files (py/Rv*.py)

import os
import json
import time
import torch #type: ignore
import numpy as np #type: ignore
import nodes #type: ignore
import folder_paths #type: ignore

from PIL import Image, ImageOps #type: ignore
from PIL.PngImagePlugin import PngInfo #type: ignore
from typing import Any, Dict, List, Optional, Tuple
from server import PromptServer #type: ignore
from ..core import CATEGORY
from ..core.logger import log
from ..core.file_cache import FileListCache
from ..core.image_metadata import extract_image_metadata
from comfy_api.latest import io #type: ignore


_LOG_PREFIX = "LoadImageFromFolder Pipe"

_temp_dir = folder_paths.get_temp_directory()
_prefix_append = "_temp_" + ''.join(__import__('random').choice("abcdefghijklmnopqrstupvxyz") for _ in range(5))


# Supported image extensions
SUPPORTED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff', '.tif')

# Extensions that commonly contain generation metadata
METADATA_EXTENSIONS = ('.png', '.webp', '.tiff', '.tif')


# ============================================================================
# Helper functions (standalone copy — no cross-node imports)
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
	reverse = sort_order == "descending"

	if sort_by == "name":
		files.sort(key=lambda x: x.lower(), reverse=reverse)
	elif sort_by == "date_modified":
		files.sort(key=lambda x: (os.path.getmtime(x), x.lower()), reverse=reverse)
	elif sort_by == "date_created":
		files.sort(key=lambda x: (os.path.getctime(x), x.lower()), reverse=reverse)
	elif sort_by == "size":
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
	cache_key = FileListCache.get_cache_key(folder_path, include_subfolders, sort_by, sort_order)

	if refresh:
		FileListCache.invalidate(folder_path)
		log.debug(_LOG_PREFIX, f"Refreshing file list for: {folder_path}")

	cached_list = FileListCache.get_cached_list(cache_key)
	if cached_list is not None:
		log.debug(_LOG_PREFIX, f"Using cached file list ({len(cached_list)} images)")
		return cached_list

	log.debug(_LOG_PREFIX, f"Building file list for: {folder_path}")
	image_files = _get_image_files(folder_path, include_subfolders)
	image_files = _sort_files(image_files, sort_by, sort_order)

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


def _load_image_with_metadata(filepath: str, extract_metadata: bool = False) -> Tuple[Optional[torch.Tensor], Optional[torch.Tensor], Dict[str, Any]]:
	# Load a single image, optionally extract metadata, and convert to tensor.
	# Only includes keys with actual values — omits empty/zero/default fields
	# so downstream nodes can distinguish "no metadata" from "metadata = 0".

	minimal_pipe = {
		"filepath": filepath,
		"filename": os.path.basename(filepath),
		"source_name": os.path.splitext(os.path.basename(filepath))[0],
	}

	try:
		img = Image.open(filepath)

		if extract_metadata and filepath.lower().endswith(METADATA_EXTENSIONS):
			pipe = extract_image_metadata(img)
		else:
			pipe = {}

		# Always set file identity + dimensions (overwrite any metadata values)
		pipe["filepath"] = filepath
		pipe["filename"] = os.path.basename(filepath)
		pipe["source_name"] = os.path.splitext(os.path.basename(filepath))[0]

		img = ImageOps.exif_transpose(img)
		pipe["width"] = img.width
		pipe["height"] = img.height

		if img.mode == 'I':
			img = img.point(lambda i: i * (1 / 255))

		image_rgb = img.convert("RGB")
		image_np = np.array(image_rgb).astype(np.float32) / 255.0
		image_tensor = torch.from_numpy(image_np)[None,]

		if 'A' in img.getbands():
			mask_np = np.array(img.getchannel('A')).astype(np.float32) / 255.0
			mask_tensor = 1. - torch.from_numpy(mask_np)
		else:
			mask_tensor = torch.zeros((64, 64), dtype=torch.float32, device="cpu")

		return image_tensor, mask_tensor.unsqueeze(0), pipe

	except Exception as e:
		log.error(_LOG_PREFIX, f"Failed to load image {filepath}: {e}")
		return None, None, minimal_pipe


def _resolve_folder_path(folder_path: str) -> str:
	# Resolve folder path - can be absolute or relative to input directory.
	if not folder_path:
		return folder_paths.get_input_directory()

	folder_path = folder_path.strip().strip('"').strip("'")

	if os.path.isabs(folder_path) and os.path.exists(folder_path):
		return folder_path

	input_dir = folder_paths.get_input_directory()
	relative_path = os.path.join(input_dir, folder_path)
	if os.path.exists(relative_path):
		return relative_path

	comfy_root = os.path.dirname(os.path.dirname(folder_paths.get_input_directory()))
	root_relative = os.path.join(comfy_root, folder_path)
	if os.path.exists(root_relative):
		return root_relative

	return folder_path


class RvImage_LoadImageFromFolder_Pipe(io.ComfyNode):

	@classmethod
	def define_schema(cls):
		return io.Schema(
			node_id="Load Image From Folder (Pipe) [Eclipse]",
			display_name="Load Image From Folder (Pipe)",
			category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
			is_output_node=True,
			inputs=[
				io.String.Input("folder_path", default="", multiline=True, tooltip="Path(s) to folder(s) containing images. One folder per line. Can be absolute or relative to ComfyUI input folder."),
				io.Boolean.Input("include_subfolders", default=True, socketless=True, tooltip="Include images from subfolders recursively."),
				io.Int.Input("index", default=0, min=-4, max=999999, step=1, tooltip="Image index. Special modes: -1=Random, -2=Increment, -3=Decrement, -4=Shuffle (no repeat)."),
				io.Combo.Input("sort_by", options=["name", "date_modified", "date_created", "size"], default="name", tooltip="How to sort the image list."),
				io.Combo.Input("sort_order", options=["ascending", "descending"], default="ascending", tooltip="Sort order for the image list."),
				io.Boolean.Input("stop_at_end", default=True, socketless=True, tooltip="Stop workflow when index reaches end of list. Disable to wrap around."),
				io.Boolean.Input("extract_metadata", default=False, socketless=True, tooltip="Extract generation metadata from images (slower). Disable for faster loading."),
				io.Boolean.Input("refresh_list", default=False, socketless=True, tooltip="Force refresh of the cached file list."),
				io.Int.Input("seed_input", force_input=True, optional=True, tooltip="When connected, special index modes only advance when this value changes."),
			],
			outputs=[
				io.Custom("PIPE").Output("pipe"),
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
	def execute(cls, folder_path, include_subfolders, index, sort_by, sort_order,
				stop_at_end=True, extract_metadata=False, refresh_list=False,
				seed_input=None):

		# Parse multiple folders (one per line)
		folder_lines = [f.strip() for f in folder_path.strip().split('\n') if f.strip()]

		if not folder_lines:
			log.error(_LOG_PREFIX, "No folder paths provided")
			raise ValueError("No folder paths provided")

		# Build combined file list from all folders
		all_files: List[Tuple[str, int, str]] = []
		folder_info: List[Tuple[str, int, int]] = []
		skipped_folders: List[str] = []

		cumulative_idx = 0
		for folder_line in folder_lines:
			resolved_path = _resolve_folder_path(folder_line)

			if not os.path.exists(resolved_path):
				log.warning(_LOG_PREFIX, f"Folder not found, skipping: {folder_line}")
				skipped_folders.append(folder_line)
				continue

			if refresh_list:
				FileListCache.invalidate(resolved_path)

			image_files = _get_or_create_file_list(
				resolved_path, include_subfolders, sort_by, sort_order, refresh=False
			)

			if not image_files:
				log.warning(_LOG_PREFIX, f"No images in folder, skipping: {folder_line}")
				skipped_folders.append(folder_line)
				continue

			folder_info.append((resolved_path, cumulative_idx, len(image_files)))

			for filepath in image_files:
				all_files.append((filepath, len(folder_info) - 1, resolved_path))

			cumulative_idx += len(image_files)
			log.debug(_LOG_PREFIX, f"Folder {len(folder_info)}: {os.path.basename(resolved_path)} ({len(image_files)} images)")

		total_count = len(all_files)
		total_folders = len(folder_info)

		if total_count == 0:
			if skipped_folders:
				raise ValueError(f"No images found in any provided folders. Skipped: {skipped_folders}")
			raise ValueError("No images found in any provided folders")

		log.msg(_LOG_PREFIX, f"Total: {total_count} images across {total_folders} folder(s)")

		start_index = index % total_count

		if index > total_count:
			log.warning(_LOG_PREFIX, f"Index {index} exceeds image count ({total_count}). Wrapping to index {start_index}.")

		if stop_at_end and index == total_count:
			log.msg(_LOG_PREFIX, f"Reached end ({total_count} images in {total_folders} folders). Stopping workflow.")
			PromptServer.instance.send_sync("stop-iteration", {})
			nodes.interrupt_processing()
			empty_pipe = {"stopped": True, "reason": "end_of_folders"}
			return io.NodeOutput(empty_pipe, ui={"images": []})

		# Try to load image, skip to next on failure
		current_index = start_index
		attempts = 0

		while attempts < total_count:
			current_filepath, current_folder_idx, current_folder_path = all_files[current_index]
			current_image, current_mask, pipe = _load_image_with_metadata(current_filepath, extract_metadata)

			if current_image is not None:
				folder_path_resolved, folder_start, folder_count = folder_info[current_folder_idx]
				local_index = current_index - folder_start

				if total_folders > 1:
					log.msg(_LOG_PREFIX, f"Folder {current_folder_idx + 1}/{total_folders}: {os.path.basename(folder_path_resolved)}")
					log.msg(_LOG_PREFIX, f"Image {local_index + 1}/{folder_count} (global: {current_index + 1}/{total_count}): {os.path.basename(current_filepath)}")
				else:
					log.msg(_LOG_PREFIX, f"Loading image {current_index + 1}/{total_count}: {os.path.basename(current_filepath)}")

				pipe["image"] = current_image
				pipe["mask"] = current_mask
				pipe["total_count"] = total_count
				pipe["current_index"] = current_index
				pipe["base_path"] = folder_path_resolved
				pipe["folder_index"] = current_folder_idx
				pipe["folder_count"] = total_folders
				pipe["local_index"] = local_index
				pipe["local_count"] = folder_count

				# Save preview for node display
				ui_images = _save_preview(current_image, cls.hidden.prompt, cls.hidden.extra_pnginfo)

				return io.NodeOutput(pipe, ui={"images": ui_images})

			log.warning(_LOG_PREFIX, f"Skipping unreadable image {current_index + 1}/{total_count}: {os.path.basename(current_filepath)}")
			current_index = (current_index + 1) % total_count
			attempts += 1

			if stop_at_end and current_index < start_index:
				log.msg(_LOG_PREFIX, f"Reached end after skipping failed images. Stopping workflow.")
				PromptServer.instance.send_sync("stop-iteration", {})
				nodes.interrupt_processing()
				empty_pipe = {"stopped": True, "reason": "end_of_folders"}
				return io.NodeOutput(empty_pipe, ui={"images": []})

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
