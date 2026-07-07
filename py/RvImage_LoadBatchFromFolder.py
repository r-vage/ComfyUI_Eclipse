import os
import torch #type: ignore
import numpy as np #type: ignore
import comfy.utils  #type: ignore

from PIL import Image, ImageOps #type: ignore
from typing import List, Optional, Tuple
from ..core import CATEGORY
from ..core.logger import log
from ..core.file_cache import FileListCache
from comfy_api.latest import io #type: ignore


_LOG_PREFIX = "Load Batch From Folder"


# Supported media extensions
_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff', '.tif')
_VIDEO_EXTENSIONS = ('.mp4', '.avi', '.mov', '.mkv', '.webm', '.wmv', '.flv', '.m4v')
_ALL_EXTENSIONS   = _IMAGE_EXTENSIONS + _VIDEO_EXTENSIONS


# ============================================================================
# Helper functions (self-contained — no imports from other node files)
# ============================================================================

def _get_image_files(folder_path: str, include_subfolders: bool) -> List[str]:
    # Return all supported media file paths in folder_path (images + videos).
    # If folder_path is itself a supported file, return it directly (images + videos allowed).
    # When scanning a directory, only image files are returned — videos must be added explicitly.
    media_files = []
    if not os.path.exists(folder_path):
        return media_files
    if os.path.isfile(folder_path):
        if folder_path.lower().endswith(_ALL_EXTENSIONS):
            return [folder_path]
        return media_files
    if include_subfolders:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                if file.lower().endswith(_IMAGE_EXTENSIONS):
                    media_files.append(os.path.join(root, file))
    else:
        for file in os.listdir(folder_path):
            filepath = os.path.join(folder_path, file)
            if os.path.isfile(filepath) and file.lower().endswith(_IMAGE_EXTENSIONS):
                media_files.append(filepath)
    return media_files


def _sort_files(files: List[str], sort_by: str, sort_order: str) -> List[str]:
    # Sort files by the given criterion. Full path is always the secondary key for determinism.
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
) -> List[str]:
    # Return the cached file list for folder_path, building it if needed.
    cache_key = FileListCache.get_cache_key(folder_path, include_subfolders, sort_by, sort_order)
    cached = FileListCache.get_cached_list(cache_key)
    if cached is not None:
        return cached
    image_files = _get_image_files(folder_path, include_subfolders)
    image_files = _sort_files(image_files, sort_by, sort_order)
    params = {
        "folder_path": folder_path,
        "include_subfolders": include_subfolders,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "count": len(image_files),
    }
    FileListCache.set_cached_list(cache_key, image_files, params)
    return image_files


def _load_image(filepath: str) -> Tuple[Optional[torch.Tensor], Optional[torch.Tensor]]:
    # Load one image. Returns ([1,H,W,C] image tensor, [1,H,W] mask tensor) or (None, None).
    try:
        img = Image.open(filepath)
        img = ImageOps.exif_transpose(img)
        if img.mode == 'I':
            img = img.point(lambda i: i * (1 / 255))
        image_rgb = img.convert("RGB")
        image_np = np.array(image_rgb).astype(np.float32) / 255.0
        image_tensor = torch.from_numpy(image_np)[None,]
        if 'A' in img.getbands():
            mask_np = np.array(img.getchannel('A')).astype(np.float32) / 255.0
            mask_tensor = 1. - torch.from_numpy(mask_np)
        else:
            mask_tensor = torch.zeros((image_tensor.shape[1], image_tensor.shape[2]), dtype=torch.float32)
        return image_tensor, mask_tensor.unsqueeze(0)
    except Exception as e:
        log.error(_LOG_PREFIX, f"Failed to load image {filepath}: {e}")
        return None, None


def _stream_fps(stream) -> Optional[float]:
    # Get FPS from a PyAV video stream — attribute names vary across PyAV versions.
    for attr in ('avg_frame_rate', 'average_rate', 'guessed_rate'):
        val = getattr(stream, attr, None)
        if val:
            try:
                return float(val)
            except Exception:
                pass
    return None


def _stream_tb(stream) -> Optional[float]:
    val = getattr(stream, 'time_base', None)
    if val:
        try:
            return float(val)
        except Exception:
            pass
    return None


def _get_video_frame_count(filepath: str) -> Optional[int]:
    # Return frame count from video metadata without decoding any frames.
    # Returns None if count cannot be determined reliably (fallback: full decode).
    try:
        import av  # type: ignore
        with av.open(filepath) as container:
            stream = container.streams.video[0]
            # stream.frames is exact when populated by the muxer
            if stream.frames and stream.frames > 0:
                return stream.frames
            # Estimate from stream duration × average frame rate
            fps = _stream_fps(stream)
            tb  = _stream_tb(stream)
            dur = getattr(stream, 'duration', None)
            if dur and fps and tb:
                estimated = int(float(dur) * tb * fps + 0.5)
                if estimated > 0:
                    return estimated
    except Exception:
        pass
    return None


def _load_video_frames(
    filepath: str,
    local_start: int = 0,
    local_end: Optional[int] = None,
) -> Tuple[List[torch.Tensor], List[torch.Tensor]]:
    # Decode frames [local_start, local_end] (inclusive, 0-based) from a video.
    # Seeks near local_start, calibrates the absolute frame index from the first
    # decoded frame's PTS, then increments sequentially — no per-frame PTS
    # recalculation, which avoids index corruption from non-monotonic PTS values.
    try:
        import av  # type: ignore
    except ImportError:
        log.error(_LOG_PREFIX, "PyAV not installed — cannot load video files. Install with: pip install av")
        return [], []
    try:
        frames: List[torch.Tensor] = []
        masks:  List[torch.Tensor] = []
        with av.open(filepath) as container:
            stream = container.streams.video[0]
            fps = _stream_fps(stream)
            tb  = _stream_tb(stream)

            # Seek to a keyframe near local_start
            if local_start > 0 and fps and tb:
                seek_pts = max(0, int((local_start - 1) / fps / tb))
                try:
                    container.seek(seek_pts, stream=stream)
                except Exception:
                    try:
                        container.seek(0)
                    except Exception:
                        pass

            # frame_idx = absolute position in the video (0-based).
            # Set once from the first decoded frame's PTS; incremented sequentially
            # after that so non-monotonic PTS values don't corrupt the counter.
            frame_idx: Optional[int] = None

            for frame in container.decode(stream):
                if frame_idx is None:
                    if fps and tb and frame.pts is not None:
                        frame_idx = max(0, int(frame.pts * tb * fps))
                    else:
                        frame_idx = local_start  # assume seek landed at local_start

                if frame_idx < local_start:
                    frame_idx += 1
                    continue
                if local_end is not None and frame_idx > local_end:
                    break

                pil = frame.to_image().convert('RGB')
                arr = np.array(pil).astype(np.float32) / 255.0
                t = torch.from_numpy(arr)[None,]
                h, w = t.shape[1], t.shape[2]
                frames.append(t)
                masks.append(torch.zeros((1, h, w), dtype=torch.float32))
                frame_idx += 1

        return frames, masks
    except Exception as e:
        log.error(_LOG_PREFIX, f"Failed to decode video {filepath}: {e}")
        return [], []


def _resolve_slice(n: int, start: int, end: int) -> Tuple[int, int]:
    # Resolve Python-style start/end indices into concrete slice bounds [s, e] inclusive.
    # -1 for end means last frame. Negative indices count from the end.
    s = start if start >= 0 else max(0, n + start)
    s = max(0, min(s, n - 1))
    if end == -1 or end >= n:
        e = n - 1
    elif end < 0:
        e = max(0, n + end)
    else:
        e = min(end, n - 1)
    if s > e:
        s, e = e, s  # swap silently
    return s, e


def _resolve_folder_path(folder_path: str) -> str:
    # Resolve folder_path — supports absolute paths and paths relative to ComfyUI input dir.
    if not folder_path:
        import folder_paths  # type: ignore
        return folder_paths.get_input_directory()
    folder_path = folder_path.strip().strip('"').strip("'")
    if os.path.isabs(folder_path):
        return folder_path
    import folder_paths  # type: ignore
    input_dir = folder_paths.get_input_directory()
    rel = os.path.join(input_dir, folder_path)
    if os.path.exists(rel):
        return rel
    comfy_root = os.path.dirname(os.path.dirname(input_dir))
    root_rel = os.path.join(comfy_root, folder_path)
    if os.path.exists(root_rel):
        return root_rel
    return folder_path


# ============================================================================
# Node class (V3)
# ============================================================================

class RvImage_LoadBatchFromFolder(io.ComfyNode):
    # Load ALL images and video frames from one or more folders as a single batch.
    # Unlike Load Image From Folder, there is no index or auto-queue stepping —
    # every frame is returned at once as a [N, H, W, C] batch tensor.
    #
    # MULTI-FOLDER SUPPORT:
    # - Enter multiple folder paths, one per line
    # - All media from all folders are combined in listed order
    # - Absolute paths are fully supported (can point anywhere on disk)
    # - Videos are decoded frame by frame and interleaved with images in sort order
    #
    # Use frame_start / frame_end to select a sub-range of the combined frame list.
    # Range is applied before resize_mode normalization.
    # Use resize_mode to handle mixed-size frames.

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Load Batch From Folder [Eclipse]",
            display_name="Load Batch From Folder",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_LOADERS.value,
            inputs=[
                io.String.Input("folder_path", default="", multiline=True,
                    tooltip="Path(s) to folder(s) containing images. One folder per line. "
                            "Absolute paths are supported anywhere on the filesystem. "
                            "All images are loaded and returned as a single batch."),
                io.Boolean.Input("include_subfolders", default=False, socketless=True,
                    tooltip="Include images from subfolders recursively."),
                io.Combo.Input("sort_by", options=["name", "date_modified", "date_created", "size"],
                    default="name", tooltip="How to sort images within each folder."),
                io.Combo.Input("sort_order", options=["ascending", "descending"],
                    default="ascending", tooltip="Sort order for the image list."),
                io.Int.Input("frame_start", default=0, min=-99999, max=99999, step=1,
                    tooltip="First frame to include from the combined frame list. "
                            "0 = first frame. Negative values count from the end (-1 = last frame)."),
                io.Int.Input("frame_end", default=-1, min=-99999, max=99999, step=1,
                    tooltip="Last frame to include, inclusive. "
                            "-1 = last frame. Negative values count from the end. "
                            "Range is applied before resize_mode normalization."),
                io.Combo.Input("resize_mode", options=["first", "largest", "smallest", "none", "list"],
                    default="first",
                    tooltip="How to handle images with different sizes. "
                            "'first': resize all to the first image's dimensions; "
                            "'largest': resize to the largest W\u00d7H found; "
                            "'smallest': resize to the smallest W\u00d7H found; "
                            "'none': raise an error if any sizes differ; "
                            "'list': skip stacking entirely and return images as a list — allows mixed sizes, useful for preview testing."),

            ],
            outputs=[
                io.Image.Output("images"),
                io.Mask.Output("masks"),
            ],
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        import hashlib
        key = "|".join([
            kwargs.get("folder_path", ""),
            kwargs.get("sort_by", "name"),
            kwargs.get("sort_order", "ascending"),
            str(kwargs.get("include_subfolders", False)),
            str(kwargs.get("frame_start", 0)),
            str(kwargs.get("frame_end", -1)),
        ])
        return hashlib.md5(key.encode()).hexdigest()

    @classmethod
    def execute(cls, folder_path, include_subfolders, sort_by, sort_order,
                frame_start=0, frame_end=-1, resize_mode="first"):

        # 1. Resolve unique ID and extra_pnginfo for title updates
        unique_id = cls.hidden.unique_id
        extra_pnginfo = cls.hidden.extra_pnginfo
        
        node_id = None
        initial_title = "Load Batch From Folder"
        
        if unique_id is not None:
            uid = unique_id[0] if isinstance(unique_id, list) else unique_id
            if uid is not None:
                try:
                    node_id = int(uid)
                except Exception:
                    node_id = uid
                    
        if node_id is not None and extra_pnginfo is not None:
            pnginfo = extra_pnginfo[0] if isinstance(extra_pnginfo, list) else extra_pnginfo
            if isinstance(pnginfo, dict) and "workflow" in pnginfo:
                nodes = pnginfo["workflow"].get("nodes", [])
                node_info = next((n for n in nodes if str(n.get("id")) == str(node_id)), None)
                if node_info:
                    initial_title = node_info.get("title") or node_info.get("type") or "Load Batch From Folder"

        def update_title(sub_status: str):
            if node_id is not None:
                try:
                    from server import PromptServer
                    PromptServer.instance.send_sync("eclipse/update_node_title", {
                        "node_id": node_id,
                        "title": sub_status
                    })
                except Exception:
                    pass

        def restore_title():
            if node_id is not None:
                try:
                    from server import PromptServer
                    PromptServer.instance.send_sync("eclipse/update_node_title", {
                        "node_id": node_id,
                        "title": initial_title
                    })
                except Exception:
                    pass

        update_title("Scanning...")
        try:
            folder_lines = [f.strip() for f in folder_path.strip().split('\n') if f.strip()]
            if not folder_lines:
                log.error(_LOG_PREFIX, "No folder paths provided")
                raise ValueError("No folder paths provided")

            # Build combined ordered file list from all folders
            all_files: List[str] = []
            skipped: List[str] = []

            for folder_line in folder_lines:
                resolved = _resolve_folder_path(folder_line)

                if not os.path.exists(resolved):
                    log.warning(_LOG_PREFIX, f"Folder not found, skipping: {folder_line}")
                    skipped.append(folder_line)
                    continue

                image_files = _get_or_create_file_list(resolved, include_subfolders, sort_by, sort_order)

                if not image_files:
                    log.warning(_LOG_PREFIX, f"No media in folder, skipping: {folder_line}")
                    skipped.append(folder_line)
                    continue

                all_files.extend(image_files)
                log.debug(_LOG_PREFIX, f"{os.path.basename(resolved)}: {len(image_files)} file(s)")

            if not all_files:
                raise ValueError(f"No media found in any provided folder(s). Skipped: {skipped}")

            log.msg(_LOG_PREFIX, f"Loading {len(all_files)} file(s) as batch")

            # Phase 1: probe frame counts from metadata (no decoding)
            update_title("Probing...")
            file_counts: List[Optional[int]] = []
            for fp in all_files:
                if fp.lower().endswith(_VIDEO_EXTENSIONS):
                    file_counts.append(_get_video_frame_count(fp))
                else:
                    file_counts.append(1)

            all_known = all(c is not None for c in file_counts)

            images: List[torch.Tensor] = []
            masks:  List[torch.Tensor] = []
            filenames_list: List[str]   = []
            failed: List[str]           = []
            range_applied = False

            if all_known:
                # Phase 2a: precise per-file load — seek to local range, skip files outside range
                total_probe: int = sum(file_counts)  # type: ignore[arg-type]
                s, e = _resolve_slice(total_probe, frame_start, frame_end)
                frames_to_load = e - s + 1
                log.msg(_LOG_PREFIX, f"Loading frames {s}–{e} of {total_probe} (seek-optimised)")
                pbar = comfy.utils.ProgressBar(frames_to_load)
                cumulative = 0
                total_files = len(all_files)
                for idx, (fp, file_count) in enumerate(zip(all_files, file_counts)):
                    if idx % max(1, total_files // 10) == 0:
                        update_title(f"Loading {idx}/{total_files}...")
                    fc: int = file_count  # type: ignore[assignment]
                    fname = os.path.basename(fp)
                    file_global_end = cumulative + fc - 1
                    # Skip files entirely outside the requested range
                    if file_global_end < s or cumulative > e:
                        cumulative += fc
                        continue
                    local_s = max(0, s - cumulative)
                    local_e = min(fc - 1, e - cumulative)
                    if fp.lower().endswith(_VIDEO_EXTENSIONS):
                        vid_frames, vid_masks = _load_video_frames(fp, local_s, local_e)
                        if not vid_frames:
                            failed.append(fname)
                        else:
                            images.extend(vid_frames)
                            masks.extend(vid_masks)
                            filenames_list.extend([fname] * len(vid_frames))
                            pbar.update(len(vid_frames))
                            log.debug(_LOG_PREFIX, f"{fname}: {len(vid_frames)} frame(s) [local {local_s}–{local_e}]")
                    else:
                        img_t, msk_t = _load_image(fp)
                        if img_t is None:
                            failed.append(fname)
                        else:
                            images.append(img_t)
                            masks.append(msk_t)
                            filenames_list.append(fname)
                            pbar.update(1)
                    cumulative += fc
                range_applied = True
            else:
                # Phase 2b: fallback — some video frame counts unavailable; load all, slice globally
                unknown_n = sum(1 for c in file_counts if c is None)
                log.msg(_LOG_PREFIX, f"Loading all frames (frame count unknown for {unknown_n} video file(s))")
                pbar = comfy.utils.ProgressBar(len(all_files))
                total_files = len(all_files)
                for idx, fp in enumerate(all_files):
                    if idx % max(1, total_files // 10) == 0:
                        update_title(f"Loading {idx}/{total_files}...")
                    fname = os.path.basename(fp)
                    if fp.lower().endswith(_VIDEO_EXTENSIONS):
                        vid_frames, vid_masks = _load_video_frames(fp)
                        if not vid_frames:
                            failed.append(fname)
                        else:
                            images.extend(vid_frames)
                            masks.extend(vid_masks)
                            filenames_list.extend([fname] * len(vid_frames))
                            log.debug(_LOG_PREFIX, f"{fname}: {len(vid_frames)} frame(s)")
                    else:
                        img_t, msk_t = _load_image(fp)
                        if img_t is None:
                            failed.append(fname)
                        else:
                            images.append(img_t)
                            masks.append(msk_t)
                            filenames_list.append(fname)
                    pbar.update(1)

            if not images:
                raise ValueError("Could not load any frames from the provided folder(s)")

            if failed:
                preview = ', '.join(failed[:5]) + ('...' if len(failed) > 5 else '')
                log.warning(_LOG_PREFIX, f"Skipped {len(failed)} unreadable file(s): {preview}")

            if not range_applied:
                # Apply frame range now (deferred from fallback path)
                total_raw = len(images)
                s, e = _resolve_slice(total_raw, frame_start, frame_end)
                if s != 0 or e != total_raw - 1:
                    log.msg(_LOG_PREFIX, f"Frame range [{frame_start}, {frame_end}] → keeping frames {s}–{e} of {total_raw}")
                    images         = images[s : e + 1]
                    masks          = masks[s : e + 1]
                    filenames_list = filenames_list[s : e + 1]

            if not images:
                raise ValueError(f"Frame range [{frame_start}, {frame_end}] produced an empty selection")

            # List mode: skip resizing and stacking — return as a Python list.
            if resize_mode == "list":
                log.msg(_LOG_PREFIX, f"List mode: returning {len(images)} frame(s) as list (no resize/stack)")
                return io.NodeOutput(images, masks)

            # Normalise to a common size when images differ
            if resize_mode != "none":
                heights = [t.shape[1] for t in images]
                widths  = [t.shape[2] for t in images]

                if len(set(heights)) > 1 or len(set(widths)) > 1:
                    if resize_mode == "first":
                        target_h, target_w = heights[0], widths[0]
                    elif resize_mode == "largest":
                        target_h, target_w = max(heights), max(widths)
                    else:  # "smallest"
                        target_h, target_w = min(heights), min(widths)

                    log.msg(_LOG_PREFIX, f"Resizing {len(images)} images to {target_w}×{target_h} (mode: {resize_mode})")

                    resized_imgs: List[torch.Tensor] = []
                    resized_msks: List[torch.Tensor] = []
                    pbar = comfy.utils.ProgressBar(len(images))
                    total_images = len(images)
                    for idx, (img_t, msk_t) in enumerate(zip(images, masks)):
                        if idx % max(1, total_images // 10) == 0:
                            update_title(f"Resizing {idx}/{total_images}...")
                        if img_t.shape[1] != target_h or img_t.shape[2] != target_w:
                            # [1, H, W, C] → [1, C, H, W] for interpolate → back
                            chw = img_t.permute(0, 3, 1, 2)
                            chw = torch.nn.functional.interpolate(chw, size=(target_h, target_w),
                                                                   mode="bilinear", align_corners=False)
                            img_t = chw.permute(0, 2, 3, 1)
                        if msk_t.shape[1] != target_h or msk_t.shape[2] != target_w:
                            m4d = msk_t.unsqueeze(1)  # [1, 1, H, W]
                            m4d = torch.nn.functional.interpolate(m4d, size=(target_h, target_w),
                                                                   mode="bilinear", align_corners=False)
                            msk_t = m4d.squeeze(1)    # [1, H, W]
                        resized_imgs.append(img_t)
                        resized_msks.append(msk_t)
                        pbar.update(1)
                    images = resized_imgs
                    masks  = resized_msks
            else:
                heights = [t.shape[1] for t in images]
                widths  = [t.shape[2] for t in images]
                if len(set(heights)) > 1 or len(set(widths)) > 1:
                    size_strs = sorted(set(f"{w}×{h}" for h, w in zip(heights, widths)))
                    raise ValueError(
                        f"Images have different sizes and resize_mode is 'none'. "
                        f"Sizes found: {', '.join(size_strs)}. "
                        f"Choose a different resize_mode to auto-resize."
                    )

            batch_images = torch.cat(images, dim=0)   # [N, H, W, C]
            batch_masks  = torch.cat(masks,  dim=0)   # [N, H, W]
            count = batch_images.shape[0]

            log.msg(_LOG_PREFIX, f"Batch ready: {count} image(s) at {batch_images.shape[2]}×{batch_images.shape[1]}")

            return io.NodeOutput(batch_images, batch_masks)
        finally:
            restore_title()
