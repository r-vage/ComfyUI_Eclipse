#
# Image Resize — unified resize node combining scale-to-side (longest,
# shortest, width, height, total_pixels) with aspect ratio presets, crop/pad
# modes, and divisible-by alignment. GPU-accelerated via torch/comfy upscale.
#

import math
import torch  # type: ignore
import torch.nn.functional as F  # type: ignore

import comfy.model_management as model_management  # type: ignore
import comfy.utils  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "ImageResize"

SCALE_TO_OPTIONS = ["longest", "shortest", "width", "height", "total_pixels", "custom"]
ASPECT_RATIO_OPTIONS = ["original", "1:1", "3:2", "4:3", "16:9", "2:3", "3:4", "9:16"]
FIT_OPTIONS = ["resize", "crop", "pad", "pad_edge", "pad_edge_pixel", "pillarbox_blur", "stretch"]
METHOD_OPTIONS = ["nearest-exact", "bilinear", "area", "bicubic", "lanczos"]
CROP_POSITION_OPTIONS = ["center", "top", "bottom", "left", "right"]
DEVICE_OPTIONS = ["cpu", "gpu"]

# Aspect ratio string → float lookup
_RATIO_MAP = {
    "1:1": 1.0,
    "3:2": 3.0 / 2.0,
    "4:3": 4.0 / 3.0,
    "16:9": 16.0 / 9.0,
    "2:3": 2.0 / 3.0,
    "3:4": 3.0 / 4.0,
    "9:16": 9.0 / 16.0,
}


def _parse_hex_color(hex_str: str) -> tuple:
    # Parse hex color string to (R, G, B) floats in 0..1 range.
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
    # Round value to nearest multiple (rounds to nearest, not always up/down).
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
    # Compute target (width, height) based on scale mode and aspect ratio.
    #
    # Returns (target_width, target_height) after divisible_by alignment.

    # Step 1: Determine working aspect ratio
    if aspect_ratio == "original":
        ratio = orig_w / orig_h
    else:
        ratio = _RATIO_MAP.get(aspect_ratio, orig_w / orig_h)

    # Step 2: Compute target dimensions based on scale_to mode
    if scale_to == "custom":
        tw = custom_width if custom_width > 0 else orig_w
        th = custom_height if custom_height > 0 else orig_h
    elif scale_to == "total_pixels":
        # size represents total pixel count in thousands (kilo-pixels)
        total = size * 1000
        tw = int(math.sqrt(total * ratio))
        th = int(math.sqrt(total / ratio))
    elif scale_to == "longest":
        if ratio >= 1.0:
            # Landscape or square — width is longest
            tw = size
            th = round(size / ratio)
        else:
            # Portrait — height is longest
            th = size
            tw = round(size * ratio)
    elif scale_to == "shortest":
        if ratio >= 1.0:
            # Landscape — height is shortest
            th = size
            tw = round(size * ratio)
        else:
            # Portrait — width is shortest
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

    # Step 3: Divisible-by alignment
    tw = _round_to_multiple(tw, divisible_by)
    th = _round_to_multiple(th, divisible_by)

    return max(tw, 1), max(th, 1)


def _gaussian_blur_bchw(img_bchw: torch.Tensor, sigma: float) -> torch.Tensor:
    # Separable 1D Gaussian blur on BCHW tensor.
    if sigma <= 0:
        return img_bchw
    radius = max(1, int(3.0 * sigma))
    x = torch.arange(-radius, radius + 1, dtype=img_bchw.dtype, device=img_bchw.device)
    k1d = torch.exp(-(x * x) / (2.0 * sigma ** 2))
    k1d = k1d / k1d.sum()
    C = img_bchw.shape[1]
    # Horizontal pass
    kx = k1d.view(1, 1, 1, -1).expand(C, -1, -1, -1)
    out = F.pad(img_bchw, (radius, radius, 0, 0), mode="reflect")
    out = F.conv2d(out, kx, groups=C)
    # Vertical pass
    ky = k1d.view(1, 1, -1, 1).expand(C, -1, -1, -1)
    out = F.pad(out, (0, 0, radius, radius), mode="reflect")
    out = F.conv2d(out, ky, groups=C)
    return out


def _upscale_tensor(tensor_bhwc: torch.Tensor, w: int, h: int, method: str, crop: str) -> torch.Tensor:
    # Upscale BHWC tensor using comfy's common_upscale (expects BCHW).
    samples = tensor_bhwc.movedim(-1, 1)  # BHWC → BCHW
    samples = comfy.utils.common_upscale(samples, w, h, method, crop)
    return samples.movedim(1, -1)  # BCHW → BHWC


def _upscale_mask(mask: torch.Tensor, w: int, h: int, method: str, crop: str) -> torch.Tensor:
    # Upscale mask tensor (B, H, W) via common_upscale.
    # Expand to 3 channels — comfyui's lanczos (PIL path) transposes single-channel tensors.
    samples = mask.unsqueeze(1).expand(-1, 3, -1, -1).contiguous()  # B,H,W → B,3,H,W
    samples = comfy.utils.common_upscale(samples, w, h, method, crop)
    return samples[:, 0, :, :]  # B,3,H,W → B,H,W


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
    # Apply fit mode (resize, crop, pad, stretch) and return (image, mask).

    B, H, W, C = image.shape

    if fit == "stretch":
        # Direct resize — ignores aspect ratio
        out_img = _upscale_tensor(image, target_w, target_h, method, "disabled")
        out_mask = _upscale_mask(mask, target_w, target_h, method, "disabled") if mask is not None else None
        return out_img, out_mask

    if fit == "crop":
        # Scale to fill target then crop excess
        scale = max(target_w / W, target_h / H)
        inter_w = max(round(W * scale), target_w)
        inter_h = max(round(H * scale), target_h)

        img = _upscale_tensor(image, inter_w, inter_h, method, "disabled")
        m = _upscale_mask(mask, inter_w, inter_h, method, "disabled") if mask is not None else None

        # Crop to target
        cx, cy = _crop_offsets(inter_w, inter_h, target_w, target_h, crop_position)
        out_img = img[:, cy:cy + target_h, cx:cx + target_w, :]
        out_mask = m[:, cy:cy + target_h, cx:cx + target_w] if m is not None else None
        return out_img, out_mask

    if fit in ("pad", "pad_edge", "pad_edge_pixel", "pillarbox_blur"):
        # Scale to fit inside target then pad with chosen background
        scale = min(target_w / W, target_h / H)
        inter_w = max(round(W * scale), 1)
        inter_h = max(round(H * scale), 1)

        img = _upscale_tensor(image, inter_w, inter_h, method, "disabled")
        m = _upscale_mask(mask, inter_w, inter_h, method, "disabled") if mask is not None else None

        px, py = _pad_offsets(inter_w, inter_h, target_w, target_h, crop_position)

        if fit == "pillarbox_blur":
            # Background: scale image to fill, blur, desaturate, darken
            scale_fill = max(target_w / float(inter_w), target_h / float(inter_h))
            bg_w = max(1, round(inter_w * scale_fill))
            bg_h = max(1, round(inter_h * scale_fill))
            bg = _upscale_tensor(img, bg_w, bg_h, "bilinear", "disabled")
            # Center-crop background to target
            cy0 = max(0, (bg_h - target_h) // 2)
            cx0 = max(0, (bg_w - target_w) // 2)
            bg = bg[:, cy0:cy0 + target_h, cx0:cx0 + target_w, :]
            # Pad if slightly short due to rounding
            if bg.shape[1] < target_h or bg.shape[2] < target_w:
                tmp = torch.zeros(B, target_h, target_w, C, dtype=image.dtype, device=image.device)
                bh, bw = bg.shape[1], bg.shape[2]
                tmp[:, :bh, :bw, :] = bg
                bg = tmp
            # Blur, desaturate, darken (BHWC → BCHW for blur)
            bg_bchw = bg.movedim(-1, 1)
            sigma = max(1.0, 0.006 * min(target_h, target_w))
            bg_bchw = _gaussian_blur_bchw(bg_bchw, sigma)
            # Desaturate 20% using BT.709 luma
            if bg_bchw.shape[1] >= 3:
                luma = 0.2126 * bg_bchw[:, 0:1] + 0.7152 * bg_bchw[:, 1:2] + 0.0722 * bg_bchw[:, 2:3]
                gray = luma.expand_as(bg_bchw[:, :3])
                bg_bchw[:, :3] = bg_bchw[:, :3] * 0.8 + gray * 0.2
            # Darken to 35%
            bg_bchw = torch.clamp(bg_bchw * 0.35, 0.0, 1.0)
            canvas = bg_bchw.movedim(1, -1)  # Back to BHWC
        elif fit == "pad_edge":
            # Fill padding with mean color of nearest edge
            canvas = torch.zeros(B, target_h, target_w, C, dtype=image.dtype, device=image.device)
            for b_idx in range(B):
                top_mean = img[b_idx, 0, :, :].mean(dim=0)        # mean of first row
                bot_mean = img[b_idx, -1, :, :].mean(dim=0)       # mean of last row
                left_mean = img[b_idx, :, 0, :].mean(dim=0)       # mean of first column
                right_mean = img[b_idx, :, -1, :].mean(dim=0)     # mean of last column
                canvas[b_idx, :py, :, :] = top_mean
                canvas[b_idx, py + inter_h:, :, :] = bot_mean
                canvas[b_idx, :, :px, :] = left_mean
                canvas[b_idx, :, px + inter_w:, :] = right_mean
        elif fit == "pad_edge_pixel":
            # Replicate exact edge pixels outward
            canvas = torch.zeros(B, target_h, target_w, C, dtype=image.dtype, device=image.device)
            for b_idx in range(B):
                # Top/bottom rows replicated
                for y in range(py):
                    canvas[b_idx, y, px:px + inter_w, :] = img[b_idx, 0, :, :]
                for y in range(py + inter_h, target_h):
                    canvas[b_idx, y, px:px + inter_w, :] = img[b_idx, -1, :, :]
                # Left/right columns replicated
                for x in range(px):
                    canvas[b_idx, py:py + inter_h, x, :] = img[b_idx, :, 0, :]
                for x in range(px + inter_w, target_w):
                    canvas[b_idx, py:py + inter_h, x, :] = img[b_idx, :, -1, :]
                # Corners
                canvas[b_idx, :py, :px, :] = img[b_idx, 0, 0, :]
                canvas[b_idx, :py, px + inter_w:, :] = img[b_idx, 0, -1, :]
                canvas[b_idx, py + inter_h:, :px, :] = img[b_idx, -1, 0, :]
                canvas[b_idx, py + inter_h:, px + inter_w:, :] = img[b_idx, -1, -1, :]
        else:
            # fit == "pad" — solid color
            r, g, b = _parse_hex_color(pad_color)
            canvas = torch.zeros(B, target_h, target_w, C, dtype=image.dtype, device=image.device)
            canvas[:, :, :, 0] = r
            if C > 1:
                canvas[:, :, :, 1] = g
            if C > 2:
                canvas[:, :, :, 2] = b

        # Place resized image on canvas
        canvas[:, py:py + inter_h, px:px + inter_w, :] = img

        # Mask canvas
        mask_canvas = None
        if m is not None:
            mask_canvas = torch.zeros(B, target_h, target_w, dtype=mask.dtype, device=mask.device)
            mask_canvas[:, py:py + inter_h, px:px + inter_w] = m

        return canvas, mask_canvas

    # fit == "resize" — scale proportionally to fit, no padding/cropping
    scale = min(target_w / W, target_h / H)
    out_w = _round_to_multiple(max(round(W * scale), 1), divisible_by)
    out_h = _round_to_multiple(max(round(H * scale), 1), divisible_by)

    out_img = _upscale_tensor(image, out_w, out_h, method, "disabled")
    out_mask = _upscale_mask(mask, out_w, out_h, method, "disabled") if mask is not None else None
    return out_img, out_mask


def _crop_offsets(src_w: int, src_h: int, dst_w: int, dst_h: int, position: str) -> tuple:
    # Calculate (x, y) crop offsets based on position.
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


def _pad_offsets(src_w: int, src_h: int, dst_w: int, dst_h: int, position: str) -> tuple:
    # Calculate (x, y) paste offsets for padding based on position.
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


class RvImage_Resize(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Resize [Eclipse]",
            display_name="Image Resize",
            description="Resize images by longest/shortest side, width, height, total pixels, "
                        "or custom dimensions. Supports aspect ratio presets, crop/pad/stretch "
                        "fit modes, and divisible-by alignment.",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            inputs=[
                io.Image.Input("image", tooltip="Input image to resize."),
                io.Combo.Input("scale_to", options=SCALE_TO_OPTIONS, default="longest",
                               tooltip="Which dimension to constrain: longest side, shortest side, "
                                       "width, height, total pixels (kilo-pixels), or custom W×H."),
                io.Int.Input("size", default=1024, min=1, max=16384, step=1,
                             tooltip="Target size for the chosen scale_to mode. "
                                     "For total_pixels this is in kilo-pixels (e.g. 1024 = ~1M pixels)."),
                io.Int.Input("custom_width", default=512, min=0, max=16384, step=1,
                             tooltip="Target width when scale_to is 'custom'. 0 = keep original width."),
                io.Int.Input("custom_height", default=512, min=0, max=16384, step=1,
                             tooltip="Target height when scale_to is 'custom'. 0 = keep original height."),
                io.Combo.Input("aspect_ratio", options=ASPECT_RATIO_OPTIONS, default="original",
                               tooltip="Override aspect ratio. 'original' keeps the input image ratio."),
                io.Combo.Input("fit", options=FIT_OPTIONS, default="resize",
                               tooltip="How to fit the image into target dimensions:\n"
                                       "• resize — scale proportionally (output may be smaller than target)\n"
                                       "• crop — scale to fill then crop excess\n"
                                       "• pad — scale to fit then pad with solid color\n"
                                       "• pad_edge — pad with mean color of nearest edge\n"
                                       "• pad_edge_pixel — pad by replicating edge pixels outward\n"
                                       "• pillarbox_blur — pad with blurred, desaturated, darkened background\n"
                                       "• stretch — distort to exact target size"),
                io.Combo.Input("crop_position", options=CROP_POSITION_OPTIONS, default="center",
                               tooltip="Anchor point for crop and pad operations."),
                io.String.Input("pad_color", default="#000000",
                                tooltip="Background color for pad mode (hex, e.g. #000000)."),
                io.Combo.Input("method", options=METHOD_OPTIONS, default="lanczos",
                               tooltip="Interpolation method for resampling."),
                io.Int.Input("divisible_by", default=8, min=1, max=512, step=1,
                             tooltip="Round output dimensions to nearest multiple of this value."),
                io.Mask.Input("mask", optional=True,
                              tooltip="Optional mask — resized together with the image."),
                io.Combo.Input("device", options=DEVICE_OPTIONS, default="cpu",
                               tooltip="Device for resize operations. GPU is faster for large images. "
                                       "Lanczos is not supported on GPU and falls back to bicubic."),
            ],
            outputs=[
                io.Image.Output("image", tooltip="Resized image."),
                io.Mask.Output("mask", tooltip="Resized mask (empty if no mask input)."),
                io.Int.Output("width", tooltip="Output image width."),
                io.Int.Output("height", tooltip="Output image height."),
            ],
        )

    @classmethod
    def _execute_single_tensor(cls, image, mask, scale_to, size, custom_width, custom_height,
                               aspect_ratio, fit, crop_position, pad_color, method, divisible_by,
                               target_device):
        # Ensure image is 4D [B, H, W, C]
        if len(image.shape) == 3:
            image = image.unsqueeze(0)
        
        B, H, W, C = image.shape

        # Move to target device
        image = image.to(target_device)
        if mask is not None:
            # Ensure mask is a tensor and is 3D [B, H, W]
            if isinstance(mask, list):
                if len(mask) > 0 and isinstance(mask[0], torch.Tensor):
                    mask = mask[0]
                else:
                    mask = None

            if mask is not None:
                if len(mask.shape) == 2:
                    mask = mask.unsqueeze(0)
                mask = mask.to(target_device)

                # Treat ComfyUI's 64x64 placeholder mask as no mask
                if mask.shape[-2:] == (64, 64) and (H != 64 or W != 64):
                    mask = None

        # Align mask batch size with image batch size if necessary
        if mask is not None:
            if mask.shape[0] != B:
                if mask.shape[0] == 1:
                    mask = mask.expand(B, -1, -1).contiguous()
                else:
                    mask = mask.repeat(math.ceil(B / mask.shape[0]), 1, 1)[:B]

            # Scale mask to match image if dimensions differ
            if mask.shape[-2:] != (H, W):
                mask = _upscale_mask(mask, W, H, "bilinear", "disabled")

        # Compute target dimensions
        target_w, target_h = _compute_dimensions(
            W, H, scale_to, size, custom_width, custom_height,
            aspect_ratio, divisible_by,
        )

        log.debug(_LOG_PREFIX, f"{W}x{H} → {target_w}x{target_h} "
                  f"(scale_to={scale_to}, fit={fit}, method={method})")

        # Apply fit mode
        out_img, out_mask = _resize_fit(
            image, mask, target_w, target_h, fit, method, crop_position, pad_color,
            divisible_by,
        )

        # Build output mask — empty placeholder if none provided
        if out_mask is None:
            _, oh, ow, _ = out_img.shape
            out_mask = torch.zeros(B, oh, ow, dtype=torch.float32, device=image.device)

        _, out_h, out_w, _ = out_img.shape

        # Final guard — ensure output honours divisible_by regardless of fit path
        if divisible_by > 1:
            aligned_w = _round_to_multiple(out_w, divisible_by)
            aligned_h = _round_to_multiple(out_h, divisible_by)
            if aligned_w != out_w or aligned_h != out_h:
                log.debug(_LOG_PREFIX, f"divisible_by guard: {out_w}x{out_h} → {aligned_w}x{aligned_h}")
                out_img = _upscale_tensor(out_img, aligned_w, aligned_h, method, "disabled")
                out_mask = _upscale_mask(out_mask, aligned_w, aligned_h, method, "disabled")
                out_w, out_h = aligned_w, aligned_h

        # Move results back to CPU for ComfyUI pipeline
        out_img = out_img.cpu()
        out_mask = out_mask.cpu()

        return out_img, out_mask, out_w, out_h

    @classmethod
    def execute(cls, image, scale_to, size, custom_width, custom_height,
                aspect_ratio, fit, crop_position, pad_color, method, divisible_by,
                mask=None, device="cpu"):
        # Hidden widgets arrive as None in V3 — apply safe defaults
        size = size or 1024
        custom_width = custom_width or 0
        custom_height = custom_height or 0
        aspect_ratio = aspect_ratio or "original"
        crop_position = crop_position or "center"
        pad_color = pad_color or "#000000"

        # Resolve device
        if device == "gpu":
            target_device = model_management.get_torch_device()
            if method == "lanczos":
                log.warning(_LOG_PREFIX, "Lanczos not supported on GPU, falling back to bicubic")
                method = "bicubic"
        else:
            target_device = torch.device("cpu")

        # Handle list of images
        if isinstance(image, list):
            resized_images = []
            resized_masks = []
            widths = []
            heights = []

            for i, img in enumerate(image):
                # Retrieve matching mask for this image in the list
                msk = None
                if mask is not None:
                    if isinstance(mask, list):
                        msk = mask[i] if i < len(mask) else None
                    elif isinstance(mask, torch.Tensor):
                        if mask.shape[0] == len(image):
                            msk = mask[i:i+1]
                        elif mask.shape[0] == 1:
                            msk = mask
                        else:
                            msk = mask
                    else:
                        msk = None

                out_img, out_mask, out_w, out_h = cls._execute_single_tensor(
                    img, msk, scale_to, size, custom_width, custom_height,
                    aspect_ratio, fit, crop_position, pad_color, method, divisible_by,
                    target_device
                )
                resized_images.append(out_img)
                resized_masks.append(out_mask)
                widths.append(out_w)
                heights.append(out_h)

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
                final_img = torch.cat(resized_images, dim=0)
                final_mask = torch.cat(resized_masks, dim=0)
                final_w = first_shape[2]
                final_h = first_shape[1]
                return io.NodeOutput(final_img, final_mask, final_w, final_h)
            else:
                # If they have different shapes, return list of tensors
                return io.NodeOutput(resized_images, resized_masks, widths, heights)

        else:
            out_img, out_mask, out_w, out_h = cls._execute_single_tensor(
                image, mask, scale_to, size, custom_width, custom_height,
                aspect_ratio, fit, crop_position, pad_color, method, divisible_by,
                target_device
            )
            return io.NodeOutput(out_img, out_mask, out_w, out_h)
