# Centralized image helper functions for Eclipse nodes.
# Provides tensor/PIL conversion, mask operations, color parsing, and image transforms.
#
# USAGE:
#   from ..core.image_helpers import tensor2pil, pil2tensor, image2mask, hex_to_rgb

import numpy as np  # type: ignore
import torch  # type: ignore
from PIL import Image, ImageFilter  # type: ignore

from typing import Any, Optional, Union

# ---------------------------------------------------------------------------
# Tensor ↔ PIL conversion
# ---------------------------------------------------------------------------


def tensor2pil(tensor: torch.Tensor) -> Image.Image:
    # Convert a ComfyUI image tensor [1,H,W,C] or [H,W,C] to PIL Image.
    return Image.fromarray(
        np.clip(255.0 * tensor.cpu().numpy().squeeze(), 0, 255).astype(np.uint8)
    )


def pil2tensor(image: Image.Image) -> torch.Tensor:
    # Convert a PIL Image to a ComfyUI image tensor [1,H,W,C].
    return torch.from_numpy(np.array(image).astype(np.float32) / 255.0).unsqueeze(0)


def image2mask(image: Image.Image) -> torch.Tensor:
    # Convert a PIL Image to a mask tensor [1,H,W] (grayscale).
    return torch.from_numpy(
        np.array(image.convert("L")).astype(np.float32) / 255.0
    ).unsqueeze(0)


# ---------------------------------------------------------------------------
# Color parsing
# ---------------------------------------------------------------------------


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    # Parse hex color string (#RGB or #RRGGBB) → (R, G, B) as ints 0-255.
    hex_str = hex_str.strip().lstrip("#")
    if len(hex_str) == 3:
        hex_str = "".join(c * 2 for c in hex_str)
    if len(hex_str) != 6:
        return (0, 0, 0)
    try:
        return (int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16))
    except ValueError:
        return (0, 0, 0)


def hex_to_rgb_float(hex_str: str) -> tuple[float, float, float]:
    # Parse hex color string (#RGB or #RRGGBB) → (R, G, B) as floats 0.0-1.0.
    r, g, b = hex_to_rgb(hex_str)
    return (r / 255.0, g / 255.0, b / 255.0)


def rgb_to_hex(r: int, g: int, b: int) -> str:
    # Convert (R, G, B) ints 0-255 → hex string "#RRGGBB".
    return f"#{r:02X}{g:02X}{b:02X}"


# ---------------------------------------------------------------------------
# Mask operations
# ---------------------------------------------------------------------------


def expand_mask(mask: torch.Tensor, grow: int, blur: int) -> torch.Tensor:
    # Grow (dilate) or shrink (erode) a mask, then apply gaussian blur.
    # mask: [B,H,W] or [1,H,W] tensor
    # grow: positive = dilate, negative = erode, 0 = no change
    # blur: gaussian blur radius (0 = no blur)
    import scipy.ndimage  # type: ignore  # lazy import — only needed when called

    kernel = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]])
    grow_mask = mask.reshape((-1, mask.shape[-2], mask.shape[-1]))
    out = []
    for m in grow_mask:
        output = m.numpy()
        for _ in range(abs(grow)):
            if grow < 0:
                output = scipy.ndimage.grey_erosion(output, footprint=kernel)
            else:
                output = scipy.ndimage.grey_dilation(output, footprint=kernel)
        out.append(torch.from_numpy(output))
    if blur > 0:
        for idx, tensor in enumerate(out):
            pil_img = tensor2pil(tensor.cpu().detach())
            pil_img = pil_img.filter(ImageFilter.GaussianBlur(blur))
            out[idx] = pil2tensor(pil_img)
    else:
        out = [t.unsqueeze(0) for t in out]
    return torch.cat(out, dim=0)


# ---------------------------------------------------------------------------
# Image transforms
# ---------------------------------------------------------------------------


def shift_image(image: Image.Image, dx: int, dy: int) -> Image.Image:
    # Shift a PIL image by (dx, dy) pixels using numpy roll, filling edges with zero.
    arr = np.array(image)
    if dy != 0:
        arr = np.roll(arr, -dy, axis=0)
        if dy > 0:
            arr[-dy:] = 0
        else:
            arr[: abs(dy)] = 0
    if dx != 0:
        arr = np.roll(arr, -dx, axis=1)
        if dx > 0:
            arr[:, -dx:] = 0
        else:
            arr[:, : abs(dx)] = 0
    return Image.fromarray(arr)


# ---------------------------------------------------------------------------
# Interpolation helpers
# ---------------------------------------------------------------------------


def lerp(a: float, b: float, t: float) -> float:
    # Linear interpolation: a + (b - a) * t
    return a + (b - a) * t


def step_color(
    start_hex: str, end_hex: str, total: int, step: int
) -> tuple[int, int, int]:
    # Interpolate between two hex colors. Returns (R, G, B) ints 0-255.
    s = hex_to_rgb(start_hex)
    e = hex_to_rgb(end_hex)
    t = step / total if total > 0 else 0.0
    return (
        int(lerp(s[0], e[0], t)),
        int(lerp(s[1], e[1], t)),
        int(lerp(s[2], e[2], t)),
    )


# ---------------------------------------------------------------------------
# Centralized List and Batch Utilities
# ---------------------------------------------------------------------------


def unwrap_value(val: Any, default: Any = None) -> Any:
    # Recursively unwraps a value if it is wrapped inside a list.
    if isinstance(val, list):
        if len(val) > 0:
            return unwrap_value(val[0], default)
        return default
    return val if val is not None else default


def flatten_images(images: Any) -> list[torch.Tensor]:
    # Recursively flattens nested lists of tensors or 4D/3D tensors into a list of 4D [1, H, W, C] tensors.
    flat_images = []
    def _process(item):
        if isinstance(item, (list, tuple)):
            for sub in item:
                _process(sub)
        elif isinstance(item, torch.Tensor):
            if item.dim() == 4:
                for i in range(item.shape[0]):
                    flat_images.append(item[i : i + 1])
            elif item.dim() == 3:
                flat_images.append(item.unsqueeze(0))
            else:
                flat_images.append(item)
    if images is not None:
        _process(images)
    return flat_images


def was_input_batch(images: Any) -> bool:
    # Helper to check if the original image input was a batch (4D tensor or list containing a 4D tensor).
    if isinstance(images, list) and len(images) == 1:
        first_item = images[0]
        if isinstance(first_item, torch.Tensor) and first_item.dim() == 4:
            return True
    elif isinstance(images, torch.Tensor) and images.dim() == 4:
        return True
    return False


def cat_and_fit_images(flat_images: list[torch.Tensor], log_prefix: str = "ImageHelpers") -> Optional[torch.Tensor]:
    # Concatenates a list of image tensors, interpolating any that don't match the dimensions of the first.
    if not flat_images:
        return None

    first = flat_images[0]
    if first.dim() != 4:
        return torch.cat(flat_images, dim=0)

    target_h, target_w = first.shape[1], first.shape[2]
    processed = []
    for t in flat_images:
        if t.dim() == 4 and (t.shape[1] != target_h or t.shape[2] != target_w):
            try:
                chw = t.permute(0, 3, 1, 2)
                chw = torch.nn.functional.interpolate(
                    chw,
                    size=(target_h, target_w),
                    mode="bilinear",
                    align_corners=False,
                )
                t = chw.permute(0, 2, 3, 1)
            except Exception as e:
                from .logger import log
                log.warning(log_prefix, f"Failed to resize tensor from {t.shape} to {target_w}x{target_h}: {e}")
        processed.append(t)
    return torch.cat(processed, dim=0)


def prepare_image_output(images_tensor: torch.Tensor, was_batch: bool) -> list[torch.Tensor]:
    # Formats output images based on whether the original input was a batch or a list of frames.
    # We must ALWAYS return a Python list to prevent ComfyUI V3 from auto-slicing
    # a returned 4D tensor into a list of 3D tensors.
    if not was_batch:
        return [images_tensor[i : i + 1] for i in range(images_tensor.shape[0])]
    return [images_tensor]


def is_conditioning(item: Any) -> bool:
    # Check if the item is a ComfyUI conditioning object
    if isinstance(item, (list, tuple)) and len(item) > 0:
        first = item[0]
        if isinstance(first, (list, tuple)) and len(first) == 2:
            return isinstance(first[0], torch.Tensor) and isinstance(first[1], dict)
    return False


def flatten_conditionings(conds: Any) -> list[list]:
    # Recursively flattens nested lists of conditioning objects into a list of single conditioning elements.
    flat_conds = []
    def _process(item):
        if is_conditioning(item):
            flat_conds.append(item)
        elif isinstance(item, (list, tuple)):
            for sub in item:
                _process(sub)
    if conds is not None:
        _process(conds)
    return flat_conds


def flatten_masks(masks: Any) -> list[torch.Tensor]:
    # Recursively flattens nested lists of mask tensors into a list of 2D [H, W] mask tensors.
    flat_masks = []
    def _process(item):
        if isinstance(item, (list, tuple)):
            for sub in item:
                _process(sub)
        elif isinstance(item, torch.Tensor):
            if item.dim() == 3:
                for i in range(item.shape[0]):
                    flat_masks.append(item[i])
            elif item.dim() == 2:
                flat_masks.append(item)
            elif item.dim() == 4:
                for i in range(item.shape[0]):
                    t = item[i]
                    if t.shape[-1] == 1:
                        t = t.squeeze(-1)
                    elif t.shape[0] == 1:
                        t = t.squeeze(0)
                    flat_masks.append(t)
            else:
                flat_masks.append(item)
    if masks is not None:
        _process(masks)
    return flat_masks


