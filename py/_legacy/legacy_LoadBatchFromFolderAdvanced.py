import os
import math
import torch  # type: ignore
import torch.nn.functional as F  # type: ignore
import numpy as np  # type: ignore
import comfy.model_management as model_management  # type: ignore
import comfy.utils  # type: ignore

from PIL import Image, ImageOps  # type: ignore
from typing import List, Optional, Tuple
from ...core import CATEGORY
from ...core.logger import log
from ...core.file_cache import FileListCache
from comfy_api.latest import io  # type: ignore

_LOG_PREFIX = "Load Batch From Folder (Advanced)"

# Supported media extensions
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif")
_VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".webm", ".wmv", ".flv", ".m4v")
_ALL_EXTENSIONS = _IMAGE_EXTENSIONS + _VIDEO_EXTENSIONS

# Resize choices and maps (from RvImage_Resize.py)
SCALE_TO_OPTIONS = ["longest", "shortest", "width", "height", "total_pixels", "custom"]
ASPECT_RATIO_OPTIONS = ["original", "1:1", "3:2", "4:3", "16:9", "2:3", "3:4", "9:16"]
FIT_OPTIONS = [
    "resize",
    "crop",
    "pad",
    "pad_edge",
    "pad_edge_pixel",
    "pillarbox_blur",
    "stretch",
]
METHOD_OPTIONS = ["nearest-exact", "bilinear", "area", "bicubic", "lanczos"]
CROP_POSITION_OPTIONS = ["center", "top", "bottom", "left", "right"]
DEVICE_OPTIONS = ["cpu", "gpu"]

_RATIO_MAP = {
    "1:1": 1.0,
    "3:2": 3.0 / 2.0,
    "4:3": 4.0 / 3.0,
    "16:9": 16.0 / 9.0,
    "2:3": 2.0 / 3.0,
    "3:4": 3.0 / 4.0,
    "9:16": 9.0 / 16.0,
}


# ============================================================================
# Helper functions for Loading (from RvImage_LoadBatchFromFolder.py)
# ============================================================================


def _get_image_files(folder_path: str, include_subfolders: bool) -> List[str]:
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
    cache_key = FileListCache.get_cache_key(
        folder_path, include_subfolders, sort_by, sort_order
    )
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


def _load_image(
    filepath: str, target_size: Tuple[int, int] | None = None
) -> Tuple[Optional[torch.Tensor], Optional[torch.Tensor]]:
    try:
        img = Image.open(filepath)
        img = ImageOps.exif_transpose(img)

        # High-quality resize using Lanczos before converting to float32 tensor
        if target_size is not None and (
            img.width != target_size[0] or img.height != target_size[1]
        ):
            filter_mode = (
                Image.Resampling.LANCZOS
                if hasattr(Image, "Resampling")
                else Image.LANCZOS
            )
            img = img.resize(target_size, filter_mode)

        if img.mode == "I":
            img = img.point(lambda i: i * (1 / 255))
        image_rgb = img.convert("RGB")
        image_np = np.array(image_rgb).astype(np.float32) / 255.0
        image_tensor = torch.from_numpy(image_np)[None,]
        if "A" in img.getbands():
            mask_np = np.array(img.getchannel("A")).astype(np.float32) / 255.0
            mask_tensor = 1.0 - torch.from_numpy(mask_np)
        else:
            mask_tensor = torch.zeros(
                (image_tensor.shape[1], image_tensor.shape[2]), dtype=torch.float32
            )
        return image_tensor, mask_tensor.unsqueeze(0)
    except Exception as e:
        log.error(_LOG_PREFIX, f"Failed to load image {filepath}: {e}")
        return None, None


def _get_intermediate_size(
    w: int, h: int, target_w: int, target_h: int, fit: str
) -> Tuple[int, int]:
    if fit == "stretch":
        return target_w, target_h
    elif fit == "crop":
        scale = max(target_w / w, target_h / h)
        inter_w = max(round(w * scale), target_w)
        inter_h = max(round(h * scale), target_h)
        return inter_w, inter_h
    elif fit in ("pad", "pad_edge", "pad_edge_pixel", "pillarbox_blur", "resize"):
        scale = min(target_w / w, target_h / h)
        inter_w = max(round(w * scale), 1)
        inter_h = max(round(h * scale), 1)
        return inter_w, inter_h
    return w, h


def _stream_fps(stream) -> Optional[float]:
    for attr in ("avg_frame_rate", "average_rate", "guessed_rate"):
        val = getattr(stream, attr, None)
        if val:
            try:
                return float(val)
            except Exception:
                pass
    return None


def _stream_tb(stream) -> Optional[float]:
    val = getattr(stream, "time_base", None)
    if val:
        try:
            return float(val)
        except Exception:
            pass
    return None


def _get_video_frame_count(filepath: str) -> Optional[int]:
    try:
        import av  # type: ignore

        with av.open(filepath) as container:
            stream = container.streams.video[0]
            if stream.frames and stream.frames > 0:
                return stream.frames
            fps = _stream_fps(stream)
            tb = _stream_tb(stream)
            dur = getattr(stream, "duration", None)
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
    try:
        import av  # type: ignore
    except ImportError:
        log.error(
            _LOG_PREFIX,
            "PyAV not installed — cannot load video files. Install with: pip install av",
        )
        return [], []
    try:
        frames: List[torch.Tensor] = []
        masks: List[torch.Tensor] = []
        with av.open(filepath) as container:
            stream = container.streams.video[0]
            fps = _stream_fps(stream)
            tb = _stream_tb(stream)

            if local_start > 0 and fps and tb:
                seek_pts = max(0, int((local_start - 1) / fps / tb))
                try:
                    container.seek(seek_pts, stream=stream)
                except Exception:
                    try:
                        container.seek(0)
                    except Exception:
                        pass

            frame_idx: Optional[int] = None

            for frame in container.decode(stream):
                if frame_idx is None:
                    if fps and tb and frame.pts is not None:
                        frame_idx = max(0, int(frame.pts * tb * fps))
                    else:
                        frame_idx = local_start

                if frame_idx < local_start:
                    frame_idx += 1
                    continue
                if local_end is not None and frame_idx > local_end:
                    break

                pil = frame.to_image().convert("RGB")
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
    s = start if start >= 0 else max(0, n + start)
    s = max(0, min(s, n - 1))
    if end == -1 or end >= n:
        e = n - 1
    elif end < 0:
        e = max(0, n + end)
    else:
        e = min(end, n - 1)
    if s > e:
        s, e = e, s
    return s, e


def _resolve_folder_path(folder_path: str) -> str:
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
# Helper functions for Resizing (from RvImage_Resize.py)
# ============================================================================


def _parse_hex_color(hex_str: str) -> tuple:
    hex_str = hex_str.strip().lstrip("#")
    if len(hex_str) == 3:
        hex_str = "".join(c * 2 for c in hex_str)
    if len(hex_str) != 6:
        return (0.0, 0.0, 0.0)
    try:
        r = int(hex_str[0:2], 16) / 255.0
        g = int(hex_str[2:4], 16) / 255.0
        b = int(hex_str[4:6], 16) / 255.0
        return (r, g, b)
    except ValueError:
        return (0.0, 0.0, 0.0)


def _round_to_multiple(value: int, multiple: int) -> int:
    if multiple <= 1:
        return value
    return max(multiple, round(value / multiple) * multiple)


def _compute_dimensions(
    orig_w: int,
    orig_h: int,
    scale_to: str,
    size: int,
    custom_width: int,
    custom_height: int,
    aspect_ratio: str,
    divisible_by: int,
) -> tuple:
    if aspect_ratio == "original":
        ratio = orig_w / orig_h
    else:
        ratio = _RATIO_MAP.get(aspect_ratio, orig_w / orig_h)

    if scale_to == "custom":
        tw = custom_width if custom_width > 0 else orig_w
        th = custom_height if custom_height > 0 else orig_h
    elif scale_to == "total_pixels":
        total = size * 1000
        tw = int(math.sqrt(total * ratio))
        th = int(math.sqrt(total / ratio))
    elif scale_to == "longest":
        if ratio >= 1.0:
            tw = size
            th = round(size / ratio)
        else:
            th = size
            tw = round(size * ratio)
    elif scale_to == "shortest":
        if ratio >= 1.0:
            th = size
            tw = round(size * ratio)
        else:
            tw = size
            th = round(size / ratio)
    elif scale_to == "width":
        tw = size
        th = round(size / ratio)
    elif scale_to == "height":
        th = size
        tw = round(size * ratio)
    else:
        tw, th = orig_w, orig_h

    tw = _round_to_multiple(tw, divisible_by)
    th = _round_to_multiple(th, divisible_by)

    return max(tw, 1), max(th, 1)


def _gaussian_blur_bchw(img_bchw: torch.Tensor, sigma: float) -> torch.Tensor:
    if sigma <= 0:
        return img_bchw
    radius = max(1, int(3.0 * sigma))
    x = torch.arange(-radius, radius + 1, dtype=img_bchw.dtype, device=img_bchw.device)
    k1d = torch.exp(-(x * x) / (2.0 * sigma**2))
    k1d = k1d / k1d.sum()
    C = img_bchw.shape[1]
    kx = k1d.view(1, 1, 1, -1).expand(C, -1, -1, -1)
    out = F.pad(img_bchw, (radius, radius, 0, 0), mode="reflect")
    out = F.conv2d(out, kx, groups=C)
    ky = k1d.view(1, 1, -1, 1).expand(C, -1, -1, -1)
    out = F.pad(out, (0, 0, radius, radius), mode="reflect")
    out = F.conv2d(out, ky, groups=C)
    return out


def _upscale_tensor(
    tensor_bhwc: torch.Tensor, w: int, h: int, method: str, crop: str
) -> torch.Tensor:
    samples = tensor_bhwc.movedim(-1, 1)
    samples = comfy.utils.common_upscale(samples, w, h, method, crop)
    return samples.movedim(1, -1)


def _upscale_mask(
    mask: torch.Tensor, w: int, h: int, method: str, crop: str
) -> torch.Tensor:
    samples = mask.unsqueeze(1).expand(-1, 3, -1, -1).contiguous()
    samples = comfy.utils.common_upscale(samples, w, h, method, crop)
    return samples[:, 0, :, :]


def _resize_fit(
    image: torch.Tensor,
    mask: torch.Tensor | None,
    target_w: int,
    target_h: int,
    fit: str,
    method: str,
    crop_position: str,
    pad_color: str,
    divisible_by: int = 1,
) -> tuple:
    B, H, W, C = image.shape

    if fit == "stretch":
        out_img = _upscale_tensor(image, target_w, target_h, method, "disabled")
        out_mask = (
            _upscale_mask(mask, target_w, target_h, method, "disabled")
            if mask is not None
            else None
        )
        return out_img, out_mask

    if fit == "crop":
        scale = max(target_w / W, target_h / H)
        inter_w = max(round(W * scale), target_w)
        inter_h = max(round(H * scale), target_h)

        img = _upscale_tensor(image, inter_w, inter_h, method, "disabled")
        m = (
            _upscale_mask(mask, inter_w, inter_h, method, "disabled")
            if mask is not None
            else None
        )

        cx, cy = _crop_offsets(inter_w, inter_h, target_w, target_h, crop_position)
        out_img = img[:, cy : cy + target_h, cx : cx + target_w, :]
        out_mask = (
            m[:, cy : cy + target_h, cx : cx + target_w] if m is not None else None
        )
        return out_img, out_mask

    if fit in ("pad", "pad_edge", "pad_edge_pixel", "pillarbox_blur"):
        scale = min(target_w / W, target_h / H)
        inter_w = max(round(W * scale), 1)
        inter_h = max(round(H * scale), 1)

        img = _upscale_tensor(image, inter_w, inter_h, method, "disabled")
        m = (
            _upscale_mask(mask, inter_w, inter_h, method, "disabled")
            if mask is not None
            else None
        )

        px, py = _pad_offsets(inter_w, inter_h, target_w, target_h, crop_position)

        if fit == "pillarbox_blur":
            scale_fill = max(target_w / float(inter_w), target_h / float(inter_h))
            bg_w = max(1, round(inter_w * scale_fill))
            bg_h = max(1, round(inter_h * scale_fill))
            bg = _upscale_tensor(img, bg_w, bg_h, "bilinear", "disabled")
            cy0 = max(0, (bg_h - target_h) // 2)
            cx0 = max(0, (bg_w - target_w) // 2)
            bg = bg[:, cy0 : cy0 + target_h, cx0 : cx0 + target_w, :]
            if bg.shape[1] < target_h or bg.shape[2] < target_w:
                tmp = torch.zeros(
                    B, target_h, target_w, C, dtype=image.dtype, device=image.device
                )
                bh, bw = bg.shape[1], bg.shape[2]
                tmp[:, :bh, :bw, :] = bg
                bg = tmp
            bg_bchw = bg.movedim(-1, 1)
            sigma = max(1.0, 0.006 * min(target_h, target_w))
            bg_bchw = _gaussian_blur_bchw(bg_bchw, sigma)
            if bg_bchw.shape[1] >= 3:
                luma = (
                    0.2126 * bg_bchw[:, 0:1]
                    + 0.7152 * bg_bchw[:, 1:2]
                    + 0.0722 * bg_bchw[:, 2:3]
                )
                gray = luma.expand_as(bg_bchw[:, :3])
                bg_bchw[:, :3] = bg_bchw[:, :3] * 0.8 + gray * 0.2
            bg_bchw = torch.clamp(bg_bchw * 0.35, 0.0, 1.0)
            canvas = bg_bchw.movedim(1, -1)
        elif fit == "pad_edge":
            canvas = torch.zeros(
                B, target_h, target_w, C, dtype=image.dtype, device=image.device
            )
            for b_idx in range(B):
                top_mean = img[b_idx, 0, :, :].mean(dim=0)
                bot_mean = img[b_idx, -1, :, :].mean(dim=0)
                left_mean = img[b_idx, :, 0, :].mean(dim=0)
                right_mean = img[b_idx, :, -1, :].mean(dim=0)
                canvas[b_idx, :py, :, :] = top_mean
                canvas[b_idx, py + inter_h :, :, :] = bot_mean
                canvas[b_idx, :, :px, :] = left_mean
                canvas[b_idx, :, px + inter_w :, :] = right_mean
        elif fit == "pad_edge_pixel":
            canvas = torch.zeros(
                B, target_h, target_w, C, dtype=image.dtype, device=image.device
            )
            for b_idx in range(B):
                for y in range(py):
                    canvas[b_idx, y, px : px + inter_w, :] = img[b_idx, 0, :, :]
                for y in range(py + inter_h, target_h):
                    canvas[b_idx, y, px : px + inter_w, :] = img[b_idx, -1, :, :]
                for x in range(px):
                    canvas[b_idx, py : py + inter_h, x, :] = img[b_idx, :, 0, :]
                for x in range(px + inter_w, target_w):
                    canvas[b_idx, py : py + inter_h, x, :] = img[b_idx, :, -1, :]
                canvas[b_idx, :py, :px, :] = img[b_idx, 0, 0, :]
                canvas[b_idx, :py, px + inter_w :, :] = img[b_idx, 0, -1, :]
                canvas[b_idx, py + inter_h :, :px, :] = img[b_idx, -1, 0, :]
                canvas[b_idx, py + inter_h :, px + inter_w :, :] = img[b_idx, -1, -1, :]
        else:
            r, g, b = _parse_hex_color(pad_color)
            canvas = torch.zeros(
                B, target_h, target_w, C, dtype=image.dtype, device=image.device
            )
            canvas[:, :, :, 0] = r
            if C > 1:
                canvas[:, :, :, 1] = g
            if C > 2:
                canvas[:, :, :, 2] = b

        canvas[:, py : py + inter_h, px : px + inter_w, :] = img

        mask_canvas = None
        if m is not None:
            mask_canvas = torch.zeros(
                B, target_h, target_w, dtype=mask.dtype, device=mask.device
            )
            mask_canvas[:, py : py + inter_h, px : px + inter_w] = m

        return canvas, mask_canvas

    scale = min(target_w / W, target_h / H)
    out_w = _round_to_multiple(max(round(W * scale), 1), divisible_by)
    out_h = _round_to_multiple(max(round(H * scale), 1), divisible_by)

    out_img = _upscale_tensor(image, out_w, out_h, method, "disabled")
    out_mask = (
        _upscale_mask(mask, out_w, out_h, method, "disabled")
        if mask is not None
        else None
    )
    return out_img, out_mask


def _crop_offsets(
    src_w: int, src_h: int, dst_w: int, dst_h: int, position: str
) -> tuple:
    if position == "center":
        x = (src_w - dst_w) // 2
        y = (src_h - dst_h) // 2
    elif position == "top":
        x = (src_w - dst_w) // 2
        y = 0
    elif position == "bottom":
        x = (src_w - dst_w) // 2
        y = src_h - dst_h
    elif position == "left":
        x = 0
        y = (src_h - dst_h) // 2
    elif position == "right":
        x = src_w - dst_w
        y = (src_h - dst_h) // 2
    else:
        x = (src_w - dst_w) // 2
        y = (src_h - dst_h) // 2
    return max(x, 0), max(y, 0)


def _pad_offsets(
    src_w: int, src_h: int, dst_w: int, dst_h: int, position: str
) -> tuple:
    if position == "center":
        x = (dst_w - src_w) // 2
        y = (dst_h - src_h) // 2
    elif position == "top":
        x = (dst_w - src_w) // 2
        y = 0
    elif position == "bottom":
        x = (dst_w - src_w) // 2
        y = dst_h - src_h
    elif position == "left":
        x = 0
        y = (dst_h - src_h) // 2
    elif position == "right":
        x = dst_w - src_w
        y = (dst_h - src_h) // 2
    else:
        x = (dst_w - src_w) // 2
        y = (dst_h - src_h) // 2
    return max(x, 0), max(y, 0)


# ============================================================================
# Node class (V3)
# ============================================================================


# Module-level loaded tensors cache to avoid repeated file reads/decoding on queue reruns
_loaded_tensors_cache = {}


def _invalidate_tensor_cache(folder_path=None):
    global _loaded_tensors_cache
    _loaded_tensors_cache.clear()
    log.msg(_LOG_PREFIX, "Loaded tensor cache cleared")


FileListCache.register_invalidate_callback(_invalidate_tensor_cache)


class legacy_LoadBatchFromFolderAdvanced(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Load Batch From Folder (Advanced) [Eclipse]",
            display_name="⚠ Load Batch From Folder (Advanced)",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="⚠ DEPRECATED: Please use Load Batch From Folder (Step Advanced) instead.",
            inputs=[
                io.String.Input(
                    "folder_path",
                    default="",
                    multiline=True,
                    tooltip="Path(s) to folder(s) containing images. One folder per line. "
                    "Absolute paths are supported anywhere on the filesystem. "
                    "All images are loaded and returned as a single batch.",
                ),
                io.Boolean.Input(
                    "include_subfolders",
                    default=False,
                    socketless=True,
                    tooltip="Include images from subfolders recursively.",
                ),
                io.Combo.Input(
                    "sort_by",
                    options=["name", "date_modified", "date_created", "size"],
                    default="name",
                    tooltip="How to sort images within each folder.",
                ),
                io.Combo.Input(
                    "sort_order",
                    options=["ascending", "descending"],
                    default="ascending",
                    tooltip="Sort order for the image list.",
                ),
                io.Int.Input(
                    "frame_start",
                    default=0,
                    min=-99999,
                    max=99999,
                    step=1,
                    tooltip="First frame to include from the combined frame list. "
                    "0 = first frame. Negative values count from the end (-1 = last frame).",
                ),
                io.Int.Input(
                    "frame_end",
                    default=-1,
                    min=-99999,
                    max=99999,
                    step=1,
                    tooltip="Last frame to include, inclusive. "
                    "-1 = last frame. Negative values count from the end. "
                    "Range is applied before resize_mode normalization.",
                ),
                io.Combo.Input(
                    "resize_mode",
                    options=["first", "largest", "smallest", "none", "list", "custom"],
                    default="first",
                    tooltip="How to handle images with different sizes. "
                    "'first': resize all to the first image's dimensions; "
                    "'largest': resize to the largest W×H found; "
                    "'smallest': resize to the smallest W×H found; "
                    "'none': raise an error if any sizes differ; "
                    "'list': skip stacking entirely and return images as a list — allows mixed sizes, useful for preview testing; "
                    "'custom': resize using the advanced sizing and aspect ratio rules below.",
                ),
                # Advanced Resize inputs (visible when resize_mode is 'custom')
                io.Combo.Input(
                    "scale_to",
                    options=SCALE_TO_OPTIONS,
                    default="longest",
                    tooltip="Which dimension to constrain: longest side, shortest side, "
                    "width, height, total pixels (kilo-pixels), or custom W×H.",
                ),
                io.Int.Input(
                    "size",
                    default=1024,
                    min=1,
                    max=16384,
                    step=1,
                    tooltip="Target size for the chosen scale_to mode. "
                    "For total_pixels this is in kilo-pixels (e.g. 1024 = ~1M pixels).",
                ),
                io.Int.Input(
                    "custom_width",
                    default=512,
                    min=0,
                    max=16384,
                    step=1,
                    tooltip="Target width when scale_to is 'custom'. 0 = keep original width.",
                ),
                io.Int.Input(
                    "custom_height",
                    default=512,
                    min=0,
                    max=16384,
                    step=1,
                    tooltip="Target height when scale_to is 'custom'. 0 = keep original height.",
                ),
                io.Combo.Input(
                    "aspect_ratio",
                    options=ASPECT_RATIO_OPTIONS,
                    default="original",
                    tooltip="Override aspect ratio. 'original' keeps the input image ratio.",
                ),
                io.Combo.Input(
                    "fit",
                    options=FIT_OPTIONS,
                    default="resize",
                    tooltip="How to fit the image into target dimensions:\n"
                    "• resize — scale proportionally (output may be smaller than target)\n"
                    "• crop — scale to fill then crop excess\n"
                    "• pad — scale to fit then pad with solid color\n"
                    "• pad_edge — pad with mean color of nearest edge\n"
                    "• pad_edge_pixel — pad by replicating edge pixels outward\n"
                    "• pillarbox_blur — pad with blurred, desaturated, darkened background\n"
                    "• stretch — distort to exact target size",
                ),
                io.Combo.Input(
                    "crop_position",
                    options=CROP_POSITION_OPTIONS,
                    default="center",
                    tooltip="Anchor point for crop and pad operations.",
                ),
                io.String.Input(
                    "pad_color",
                    default="#000000",
                    tooltip="Background color for pad mode (hex, e.g. #000000).",
                ),
                io.Combo.Input(
                    "method",
                    options=METHOD_OPTIONS,
                    default="lanczos",
                    tooltip="Interpolation method for resampling.",
                ),
                io.Int.Input(
                    "divisible_by",
                    default=8,
                    min=1,
                    max=512,
                    step=1,
                    tooltip="Round output dimensions to nearest multiple of this value.",
                ),
                io.Combo.Input(
                    "device",
                    options=DEVICE_OPTIONS,
                    default="cpu",
                    tooltip="Device for resize operations. GPU is faster for large images. "
                    "Lanczos is not supported on GPU and falls back to bicubic.",
                ),
            ],
            outputs=[
                io.Image.Output(
                    "images",
                    tooltip="Loaded (and optionally resized) images batch or list.",
                ),
                io.Mask.Output("masks", tooltip="Corresponding masks batch or list."),
            ],
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        import hashlib

        key = "|".join(
            [
                kwargs.get("folder_path", ""),
                kwargs.get("sort_by", "name"),
                kwargs.get("sort_order", "ascending"),
                str(kwargs.get("include_subfolders", False)),
                str(kwargs.get("frame_start", 0)),
                str(kwargs.get("frame_end", -1)),
                kwargs.get("resize_mode", "first"),
                # Include custom resize options if in custom mode
                kwargs.get("scale_to", ""),
                str(kwargs.get("size", 0)),
                str(kwargs.get("custom_width", 0)),
                str(kwargs.get("custom_height", 0)),
                kwargs.get("aspect_ratio", ""),
                kwargs.get("fit", ""),
                kwargs.get("crop_position", ""),
                kwargs.get("pad_color", ""),
                kwargs.get("method", ""),
                str(kwargs.get("divisible_by", 0)),
                kwargs.get("device", ""),
            ]
        )
        return hashlib.md5(key.encode()).hexdigest()

    @classmethod
    def _execute_single_tensor(
        cls,
        image,
        mask,
        scale_to,
        size,
        custom_width,
        custom_height,
        aspect_ratio,
        fit,
        crop_position,
        pad_color,
        method,
        divisible_by,
        target_device,
    ):
        if len(image.shape) == 3:
            image = image.unsqueeze(0)

        B, H, W, C = image.shape
        image = image.to(target_device)

        if mask is not None:
            if isinstance(mask, list):
                if len(mask) > 0 and isinstance(mask[0], torch.Tensor):
                    mask = mask[0]
                else:
                    mask = None

            if mask is not None:
                if len(mask.shape) == 2:
                    mask = mask.unsqueeze(0)
                mask = mask.to(target_device)
                if mask.shape[-2:] == (64, 64) and (H != 64 or W != 64):
                    mask = None

        if mask is not None:
            if mask.shape[0] != B:
                if mask.shape[0] == 1:
                    mask = mask.expand(B, -1, -1).contiguous()
                else:
                    mask = mask.repeat(math.ceil(B / mask.shape[0]), 1, 1)[:B]
            if mask.shape[-2:] != (H, W):
                mask = _upscale_mask(mask, W, H, "bilinear", "disabled")

        target_w, target_h = _compute_dimensions(
            W,
            H,
            scale_to,
            size,
            custom_width,
            custom_height,
            aspect_ratio,
            divisible_by,
        )

        out_img, out_mask = _resize_fit(
            image,
            mask,
            target_w,
            target_h,
            fit,
            method,
            crop_position,
            pad_color,
            divisible_by,
        )

        out_img = out_img.cpu()
        if out_mask is not None:
            out_mask = out_mask.cpu()

        return out_img, out_mask, target_w, target_h

    @classmethod
    def execute(
        cls,
        folder_path,
        include_subfolders,
        sort_by,
        sort_order,
        frame_start=0,
        frame_end=-1,
        resize_mode="first",
        scale_to="longest",
        size=1024,
        custom_width=512,
        custom_height=512,
        aspect_ratio="original",
        fit="resize",
        crop_position="center",
        pad_color="#000000",
        method="lanczos",
        divisible_by=8,
        device="cpu",
    ):

        # 1. Check cache to bypass reloading/decoding if inputs did not change
        kwargs = {
            "folder_path": folder_path,
            "include_subfolders": include_subfolders,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "frame_start": frame_start,
            "frame_end": frame_end,
            "resize_mode": resize_mode,
            "scale_to": scale_to,
            "size": size,
            "custom_width": custom_width,
            "custom_height": custom_height,
            "aspect_ratio": aspect_ratio,
            "fit": fit,
            "crop_position": crop_position,
            "pad_color": pad_color,
            "method": method,
            "divisible_by": divisible_by,
            "device": device,
        }
        cache_key = cls.fingerprint_inputs(**kwargs)
        global _loaded_tensors_cache
        if cache_key in _loaded_tensors_cache:
            log.msg(
                _LOG_PREFIX,
                f"Returning cached loaded tensors (fingerprint: {cache_key})",
            )
            cached_images, cached_masks = _loaded_tensors_cache[cache_key]
            return io.NodeOutput(cached_images, cached_masks)

        # 2. Resolve unique ID and extra_pnginfo for title updates
        unique_id = cls.hidden.unique_id
        extra_pnginfo = cls.hidden.extra_pnginfo

        node_id = None
        initial_title = cls.define_schema().display_name

        if unique_id is not None:
            uid = unique_id[0] if isinstance(unique_id, list) else unique_id
            if uid is not None:
                try:
                    node_id = int(uid)
                except Exception:
                    node_id = uid

        if node_id is not None and extra_pnginfo is not None:
            pnginfo = (
                extra_pnginfo[0] if isinstance(extra_pnginfo, list) else extra_pnginfo
            )
            if isinstance(pnginfo, dict) and "workflow" in pnginfo:
                nodes = pnginfo["workflow"].get("nodes", [])
                node_info = next(
                    (n for n in nodes if str(n.get("id")) == str(node_id)), None
                )
                if node_info:
                    initial_title = node_info.get("title") or initial_title

        # Safeguard: if the canvas title was left stuck as a progress state (e.g. from an interrupted or errored previous run),
        # discard it and fallback to the default clean name derived from the node schema.
        if initial_title:
            temp_statuses = ["Scanning", "Probing", "Loading", "Resizing"]
            is_temp = any(status in initial_title for status in temp_statuses) or initial_title.endswith("...")
            if is_temp:
                initial_title = cls.define_schema().display_name

        def update_title(sub_status: str):
            if node_id is not None:
                try:
                    from server import PromptServer

                    PromptServer.instance.send_sync(
                        "eclipse/update_node_title",
                        {"node_id": node_id, "title": sub_status},
                    )
                except Exception:
                    pass

        def restore_title():
            if node_id is not None:
                try:
                    from server import PromptServer

                    PromptServer.instance.send_sync(
                        "eclipse/update_node_title",
                        {"node_id": node_id, "title": initial_title},
                    )
                except Exception:
                    pass

        update_title("Scanning...")
        try:
            folder_lines = [
                f.strip() for f in folder_path.strip().split("\n") if f.strip()
            ]
            if not folder_lines:
                log.error(_LOG_PREFIX, "No folder paths provided")
                raise ValueError("No folder paths provided")

            all_files: List[str] = []
            skipped: List[str] = []

            for folder_line in folder_lines:
                resolved = _resolve_folder_path(folder_line)

                if not os.path.exists(resolved):
                    log.warning(
                        _LOG_PREFIX, f"Folder not found, skipping: {folder_line}"
                    )
                    skipped.append(folder_line)
                    continue

                image_files = _get_or_create_file_list(
                    resolved, include_subfolders, sort_by, sort_order
                )

                if not image_files:
                    log.warning(
                        _LOG_PREFIX, f"No media in folder, skipping: {folder_line}"
                    )
                    skipped.append(folder_line)
                    continue

                all_files.extend(image_files)
                log.debug(
                    _LOG_PREFIX,
                    f"{os.path.basename(resolved)}: {len(image_files)} file(s)",
                )

            if not all_files:
                raise ValueError(
                    f"No media found in any provided folder(s). Skipped: {skipped}"
                )

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

            # Pre-calculate target dimensions using fast header-only PIL opens
            target_h, target_w = None, None
            if resize_mode != "none" and resize_mode != "list" and resize_mode != "custom":
                sizes = []
                for fp in all_files:
                    if not fp.lower().endswith(_VIDEO_EXTENSIONS):
                        try:
                            with Image.open(fp) as tmp_img:
                                sizes.append(tmp_img.size)  # (width, height)
                        except Exception:
                            pass
                if sizes:
                    widths, heights = zip(*sizes)
                    if resize_mode == "first":
                        target_w, target_h = sizes[0]
                    elif resize_mode == "largest":
                        target_w, target_h = max(widths), max(heights)
                    elif resize_mode == "smallest":
                        target_w, target_h = min(widths), min(heights)

            images: List[torch.Tensor] = []
            masks: List[torch.Tensor] = []
            filenames_list: List[str] = []
            failed: List[str] = []
            range_applied = False

            if all_known:
                total_probe: int = sum(file_counts)  # type: ignore[arg-type]
                s, e = _resolve_slice(total_probe, frame_start, frame_end)
                frames_to_load = e - s + 1
                log.msg(
                    _LOG_PREFIX,
                    f"Loading frames {s}–{e} of {total_probe} (seek-optimised)",
                )
                pbar = comfy.utils.ProgressBar(frames_to_load)
                cumulative = 0
                total_files = len(all_files)
                for idx, (fp, file_count) in enumerate(zip(all_files, file_counts)):
                    if idx % max(1, total_files // 10) == 0:
                        percent = idx * 100 // total_files
                        update_title(f"Loading {percent}%")
                    fc: int = file_count  # type: ignore[assignment]
                    fname = os.path.basename(fp)
                    file_global_end = cumulative + fc - 1
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
                            log.debug(
                                _LOG_PREFIX,
                                f"{fname}: {len(vid_frames)} frame(s) [local {local_s}–{local_e}]",
                            )
                    else:
                        t_sz = None
                        if resize_mode == "custom":
                            try:
                                with Image.open(fp) as tmp_img:
                                    orig_w, orig_h = tmp_img.size
                                t_w, t_h = _compute_dimensions(
                                    orig_w,
                                    orig_h,
                                    scale_to,
                                    size,
                                    custom_width,
                                    custom_height,
                                    aspect_ratio,
                                    divisible_by,
                                )
                                t_sz = _get_intermediate_size(orig_w, orig_h, t_w, t_h, fit)
                            except Exception:
                                pass
                        elif target_w is not None and target_h is not None:
                            t_sz = (target_w, target_h)
                        img_t, msk_t = _load_image(fp, target_size=t_sz)
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
                unknown_n = sum(1 for c in file_counts if c is None)
                log.msg(
                    _LOG_PREFIX,
                    f"Loading all frames (frame count unknown for {unknown_n} video file(s))",
                )
                pbar = comfy.utils.ProgressBar(len(all_files))
                total_files = len(all_files)
                for idx, fp in enumerate(all_files):
                    if idx % max(1, total_files // 10) == 0:
                        percent = idx * 100 // total_files
                        update_title(f"Loading {percent}%")
                    fname = os.path.basename(fp)
                    if fp.lower().endswith(_VIDEO_EXTENSIONS):
                        vid_frames, vid_masks = _load_video_frames(fp)
                        if not vid_frames:
                            failed.append(fname)
                        else:
                            images.extend(vid_frames)
                            masks.extend(vid_masks)
                            filenames_list.extend([fname] * len(vid_frames))
                            log.debug(
                                _LOG_PREFIX, f"{fname}: {len(vid_frames)} frame(s)"
                            )
                    else:
                        t_sz = None
                        if resize_mode == "custom":
                            try:
                                with Image.open(fp) as tmp_img:
                                    orig_w, orig_h = tmp_img.size
                                t_w, t_h = _compute_dimensions(
                                    orig_w,
                                    orig_h,
                                    scale_to,
                                    size,
                                    custom_width,
                                    custom_height,
                                    aspect_ratio,
                                    divisible_by,
                                )
                                t_sz = _get_intermediate_size(orig_w, orig_h, t_w, t_h, fit)
                            except Exception:
                                pass
                        elif target_w is not None and target_h is not None:
                            t_sz = (target_w, target_h)
                        img_t, msk_t = _load_image(fp, target_size=t_sz)
                        if img_t is None:
                            failed.append(fname)
                        else:
                            images.append(img_t)
                            masks.append(msk_t)
                            filenames_list.append(fname)
                    pbar.update(1)

            if not images:
                raise ValueError(
                    "Could not load any frames from the provided folder(s)"
                )

            if failed:
                preview = ", ".join(failed[:5]) + ("..." if len(failed) > 5 else "")
                log.warning(
                    _LOG_PREFIX, f"Skipped {len(failed)} unreadable file(s): {preview}"
                )

            if not range_applied:
                total_raw = len(images)
                s, e = _resolve_slice(total_raw, frame_start, frame_end)
                if s != 0 or e != total_raw - 1:
                    log.msg(
                        _LOG_PREFIX,
                        f"Frame range [{frame_start}, {frame_end}] → keeping frames {s}–{e} of {total_raw}",
                    )
                    images = images[s : e + 1]
                    masks = masks[s : e + 1]
                    filenames_list = filenames_list[s : e + 1]

            if not images:
                raise ValueError(
                    f"Frame range [{frame_start}, {frame_end}] produced an empty selection"
                )

            # ----------------------------------------------------
            # RESIZING LOGIC
            # ----------------------------------------------------

            # Custom resize mode (calls the advanced custom resize logic)
            if resize_mode == "custom":
                if device == "gpu":
                    target_device = model_management.get_torch_device()
                    if method == "lanczos":
                        log.warning(
                            _LOG_PREFIX,
                            "Lanczos not supported on GPU, falling back to bicubic",
                        )
                        method = "bicubic"
                else:
                    target_device = torch.device("cpu")

                resized_images = []
                resized_masks = []
                pbar = comfy.utils.ProgressBar(len(images))
                total_images = len(images)
                for idx, (img_t, msk_t) in enumerate(zip(images, masks)):
                    if idx % max(1, total_images // 10) == 0:
                        percent = idx * 100 // total_images
                        update_title(f"Resizing {percent}%")
                    out_img, out_mask, _, _ = cls._execute_single_tensor(
                        img_t,
                        msk_t,
                        scale_to,
                        size,
                        custom_width,
                        custom_height,
                        aspect_ratio,
                        fit,
                        crop_position,
                        pad_color,
                        method,
                        divisible_by,
                        target_device,
                    )
                    resized_images.append(out_img)
                    if out_mask is not None:
                        resized_masks.append(out_mask)
                    else:
                        # Provide default mask if somehow missing
                        h, w = out_img.shape[1], out_img.shape[2]
                        resized_masks.append(
                            torch.zeros((1, h, w), dtype=torch.float32)
                        )
                    pbar.update(1)

                # Check if all output images have the same shape so we can stack/batch them
                all_same_shape = False
                first_shape = None
                if resized_images:
                    first_shape = resized_images[0].shape
                    all_same_shape = True
                    for ri in resized_images:
                        if ri.shape != first_shape:
                            all_same_shape = False
                            break

                if all_same_shape and first_shape is not None:
                    batch_images = torch.cat(resized_images, dim=0)
                    batch_masks = torch.cat(resized_masks, dim=0)
                    log.msg(
                        _LOG_PREFIX,
                        f"Custom resize batch ready: {batch_images.shape[0]} image(s) at {batch_images.shape[2]}×{batch_images.shape[1]}",
                    )
                    return io.NodeOutput(batch_images, batch_masks)
                else:
                    log.msg(
                        _LOG_PREFIX,
                        f"Custom resize list ready: returning {len(resized_images)} frame(s) as list due to varying sizes",
                    )
                    return io.NodeOutput(resized_images, resized_masks)

            # List mode: skip resizing and stacking — return as a Python list.
            if resize_mode == "list":
                log.msg(
                    _LOG_PREFIX,
                    f"List mode: returning {len(images)} frame(s) as list (no resize/stack)",
                )
                return io.NodeOutput(images, masks)

            # Normalise to a common size when images differ (first, largest, smallest)
            if resize_mode != "none":
                heights = [t.shape[1] for t in images]
                widths = [t.shape[2] for t in images]

                if len(set(heights)) > 1 or len(set(widths)) > 1:
                    if resize_mode == "first":
                        target_h, target_w = heights[0], widths[0]
                    elif resize_mode == "largest":
                        target_h, target_w = max(heights), max(widths)
                    else:  # "smallest"
                        target_h, target_w = min(heights), min(widths)

                    log.msg(
                        _LOG_PREFIX,
                        f"Resizing {len(images)} images to {target_w}×{target_h} (mode: {resize_mode})",
                    )

                    resized_imgs: List[torch.Tensor] = []
                    resized_msks: List[torch.Tensor] = []
                    pbar = comfy.utils.ProgressBar(len(images))
                    total_images = len(images)
                    for idx, (img_t, msk_t) in enumerate(zip(images, masks)):
                        if idx % max(1, total_images // 10) == 0:
                            percent = idx * 100 // total_images
                            update_title(f"Resizing {percent}%")
                        if img_t.shape[1] != target_h or img_t.shape[2] != target_w:
                            chw = img_t.permute(0, 3, 1, 2)
                            chw = torch.nn.functional.interpolate(
                                chw,
                                size=(target_h, target_w),
                                mode="bilinear",
                                align_corners=False,
                            )
                            img_t = chw.permute(0, 2, 3, 1)
                        if msk_t.shape[1] != target_h or msk_t.shape[2] != target_w:
                            m4d = msk_t.unsqueeze(1)
                            m4d = torch.nn.functional.interpolate(
                                m4d,
                                size=(target_h, target_w),
                                mode="bilinear",
                                align_corners=False,
                            )
                            msk_t = m4d.squeeze(1)
                        resized_imgs.append(img_t)
                        resized_msks.append(msk_t)
                        pbar.update(1)
                    images = resized_imgs
                    masks = resized_msks
            else:
                heights = [t.shape[1] for t in images]
                widths = [t.shape[2] for t in images]
                if len(set(heights)) > 1 or len(set(widths)) > 1:
                    size_strs = sorted(set(f"{w}×{h}" for h, w in zip(heights, widths)))
                    raise ValueError(
                        f"Images have different sizes and resize_mode is 'none'. "
                        f"Sizes found: {', '.join(size_strs)}. "
                        f"Choose a different resize_mode to auto-resize."
                    )

            batch_images = torch.cat(images, dim=0)
            batch_masks = torch.cat(masks, dim=0)
            count = batch_images.shape[0]

            log.msg(
                _LOG_PREFIX,
                f"Batch ready: {count} image(s) at {batch_images.shape[2]}×{batch_images.shape[1]}",
            )

            _loaded_tensors_cache[cache_key] = (batch_images, batch_masks)
            return io.NodeOutput(batch_images, batch_masks)
        finally:
            restore_title()
