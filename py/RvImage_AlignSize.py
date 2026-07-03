#
# Image Align Size — adjusts image dimensions to be divisible by a given number.
# Fixes errors from models that require specific divisibility (e.g. BiRefNet
# requires dims divisible by 31 for its patch rearrangement).
#
# Modes: shrink (crop center), grow (pad with solid/edge color), or resize
# (scale to nearest valid size using interpolation).
#

import torch  # type: ignore
import comfy.utils  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "ImageAlignSize"

MODE_OPTIONS = ["shrink", "grow", "resize"]
PAD_OPTIONS = ["black", "white", "edge_replicate"]
METHOD_OPTIONS = ["nearest-exact", "bilinear", "area", "bicubic", "lanczos"]


def _align_down(value: int, divisor: int) -> int:
    # Round down to nearest multiple of divisor (minimum = divisor itself).
    return max(divisor, (value // divisor) * divisor)


def _align_up(value: int, divisor: int) -> int:
    # Round up to nearest multiple of divisor.
    return max(divisor, -(-value // divisor) * divisor)


def _align_nearest(value: int, divisor: int) -> int:
    # Round to the nearest multiple of divisor.
    down = _align_down(value, divisor)
    up = _align_up(value, divisor)
    return down if (value - down) <= (up - value) else up


class RvImage_AlignSize(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Align Size [Eclipse]",
            display_name="Image Align Size",
            description="Adjusts image dimensions to be divisible by a given number. "
                        "Fixes errors from models requiring specific divisibility "
                        "(e.g. BiRefNet needs dims divisible by 31). "
                        "Modes: shrink (center crop), grow (pad), or resize (interpolate).",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            inputs=[
                io.Image.Input("image", tooltip="Input image to align."),
                io.Int.Input("divisor", default=31, min=1, max=512, step=1,
                             tooltip="Target divisibility. E.g. 31 for BiRefNet, 8 for VAE, 64 for some upscalers."),
                io.Combo.Input("mode", options=MODE_OPTIONS, default="shrink",
                               tooltip="How to adjust dimensions:\n"
                                       "• shrink — center-crop to nearest smaller valid size (no quality loss)\n"
                                       "• grow — pad to nearest larger valid size\n"
                                       "• resize — interpolate to nearest valid size"),
                io.Combo.Input("pad_fill", options=PAD_OPTIONS, default="black",
                               tooltip="Fill method when mode is 'grow':\n"
                                       "• black — pad with black pixels\n"
                                       "• white — pad with white pixels\n"
                                       "• edge_replicate — replicate edge pixels"),
                io.Combo.Input("resize_method", options=METHOD_OPTIONS, default="lanczos",
                               tooltip="Interpolation method when mode is 'resize'."),
                io.Mask.Input("mask", optional=True,
                              tooltip="Optional mask — adjusted together with the image."),
            ],
            outputs=[
                io.Image.Output("image", tooltip="Aligned image with dimensions divisible by divisor."),
                io.Mask.Output("mask", tooltip="Aligned mask (empty if no mask input)."),
                io.Int.Output("width", tooltip="Output image width."),
                io.Int.Output("height", tooltip="Output image height."),
            ],
        )

    @classmethod
    def execute(cls, image, divisor, mode, pad_fill, resize_method, mask=None):
        B, H, W, C = image.shape

        # Check if already aligned
        if H % divisor == 0 and W % divisor == 0:
            log.debug(_LOG_PREFIX, f"{W}x{H} already divisible by {divisor}, passing through")
            out_mask = mask if mask is not None else torch.zeros(B, H, W, dtype=image.dtype, device=image.device)
            return io.NodeOutput(image, out_mask, W, H)

        # Compute target dimensions based on mode
        if mode == "shrink":
            new_w = _align_down(W, divisor)
            new_h = _align_down(H, divisor)
        elif mode == "grow":
            new_w = _align_up(W, divisor)
            new_h = _align_up(H, divisor)
        else:  # resize
            new_w = _align_nearest(W, divisor)
            new_h = _align_nearest(H, divisor)

        log.msg(_LOG_PREFIX, f"{W}x{H} → {new_w}x{new_h} (divisor={divisor}, mode={mode})")

        # Treat ComfyUI's 64x64 placeholder mask as no mask
        if mask is not None and mask.shape[-2:] == (64, 64) and (H != 64 or W != 64):
            mask = None

        # Scale mask to match image if dimensions differ
        if mask is not None and mask.shape[-2:] != (H, W):
            m_bchw = mask.unsqueeze(1).expand(-1, 3, -1, -1).contiguous()
            m_bchw = comfy.utils.common_upscale(m_bchw, W, H, "bilinear", "disabled")
            mask = m_bchw[:, 0, :, :]

        if mode == "shrink":
            out_img, out_mask = cls._shrink(image, mask, new_w, new_h)
        elif mode == "grow":
            out_img, out_mask = cls._grow(image, mask, new_w, new_h, pad_fill)
        else:
            out_img, out_mask = cls._resize(image, mask, new_w, new_h, resize_method)

        # Build output mask — empty placeholder if none provided
        if out_mask is None:
            out_mask = torch.zeros(B, new_h, new_w, dtype=image.dtype, device=image.device)

        return io.NodeOutput(out_img, out_mask, new_w, new_h)

    @staticmethod
    def _shrink(image, mask, new_w, new_h):
        # Center-crop to target dimensions.
        B, H, W, C = image.shape
        x0 = (W - new_w) // 2
        y0 = (H - new_h) // 2
        out_img = image[:, y0:y0 + new_h, x0:x0 + new_w, :]
        out_mask = mask[:, y0:y0 + new_h, x0:x0 + new_w] if mask is not None else None
        return out_img, out_mask

    @staticmethod
    def _grow(image, mask, new_w, new_h, pad_fill):
        # Pad to target dimensions.
        B, H, W, C = image.shape
        pad_x = new_w - W
        pad_y = new_h - H
        px_left = pad_x // 2
        px_right = pad_x - px_left
        py_top = pad_y // 2
        py_bottom = pad_y - py_top

        if pad_fill == "edge_replicate":
            # BHWC → BCHW for F.pad, then back
            bchw = image.movedim(-1, 1)
            bchw = torch.nn.functional.pad(bchw, (px_left, px_right, py_top, py_bottom), mode="replicate")
            out_img = bchw.movedim(1, -1)
        else:
            fill_val = 1.0 if pad_fill == "white" else 0.0
            out_img = torch.full((B, new_h, new_w, C), fill_val, dtype=image.dtype, device=image.device)
            out_img[:, py_top:py_top + H, px_left:px_left + W, :] = image

        out_mask = None
        if mask is not None:
            mask_canvas = torch.zeros(B, new_h, new_w, dtype=mask.dtype, device=mask.device)
            mask_canvas[:, py_top:py_top + H, px_left:px_left + W] = mask
            out_mask = mask_canvas

        return out_img, out_mask

    @staticmethod
    def _resize(image, mask, new_w, new_h, method):
        # Interpolate to target dimensions.
        bchw = image.movedim(-1, 1)  # BHWC → BCHW
        bchw = comfy.utils.common_upscale(bchw, new_w, new_h, method, "disabled")
        out_img = bchw.movedim(1, -1)  # BCHW → BHWC

        out_mask = None
        if mask is not None:
            m_bchw = mask.unsqueeze(1).expand(-1, 3, -1, -1).contiguous()
            m_bchw = comfy.utils.common_upscale(m_bchw, new_w, new_h, method, "disabled")
            out_mask = m_bchw[:, 0, :, :]

        return out_img, out_mask
