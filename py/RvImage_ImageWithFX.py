# Image with FX — composites an input image (logo, signature, watermark) onto a canvas
# with optional outer glow and drop shadow.  The input image's alpha channel defines the shape.

import numpy as np # type: ignore
import torch # type: ignore
from PIL import Image, ImageDraw # type: ignore

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.logger import log
from ..core.common import make_comfy_progress
from ..core.image_helpers import (
    tensor2pil, pil2tensor, image2mask,
    hex_to_rgb, expand_mask, shift_image, lerp, step_color,
)

_LOG_PREFIX = "ImageWithFX"

# ---------------------------------------------------------------------------
# Compositing helpers (same as TextImageWithFX)
# ---------------------------------------------------------------------------


def _blend_screen(bg: np.ndarray, fg: np.ndarray) -> np.ndarray:
    return 255.0 - (255.0 - bg) * (255.0 - fg) / 255.0


def _composite_with_mask(canvas: Image.Image, color: tuple, mask: Image.Image, opacity: int,
                         blend: str = "normal") -> Image.Image:
    color_img = Image.new("RGBA", canvas.size, (*color, 255))
    if blend == "screen":
        bg_arr = np.array(canvas.convert("RGBA"), dtype=float)
        fg_arr = np.array(color_img, dtype=float)
        blended = _blend_screen(bg_arr[:, :, :3], fg_arr[:, :, :3])
        blended = np.clip(blended, 0, 255)
        result_arr = bg_arr.copy()
        result_arr[:, :, :3] = blended
        result_arr[:, :, 3] = 255  # Full opacity — paste mask controls blending
        blended_img = Image.fromarray(np.uint8(result_arr))
    else:
        blended_img = color_img

    mask_arr = np.array(mask.convert("L"), dtype=float) * (opacity / 100.0)
    opacity_mask = Image.fromarray(np.uint8(np.clip(mask_arr, 0, 255)))

    canvas_copy = canvas.copy()
    canvas_copy.paste(blended_img, mask=opacity_mask)
    return canvas_copy


# ---------------------------------------------------------------------------
# Position calculation
# ---------------------------------------------------------------------------

_POSITIONS = [
    "top_left", "top_center", "top_right",
    "center_left", "center", "center_right",
    "bottom_left", "bottom_center", "bottom_right",
]


def _compute_position(position: str, canvas_w: int, canvas_h: int,
                      obj_w: int, obj_h: int,
                      margin_x: int, margin_y: int) -> tuple[int, int]:
    if "left" in position:
        x = margin_x
    elif "right" in position:
        x = canvas_w - obj_w - margin_x
    else:
        x = (canvas_w - obj_w) // 2

    if "top" in position:
        y = margin_y
    elif "bottom" in position:
        y = canvas_h - obj_h - margin_y
    else:
        y = (canvas_h - obj_h) // 2

    return (x, y)


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class RvImage_ImageWithFX(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image with FX [Eclipse]",
            display_name="Image with FX",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            description="Composite an image (logo, signature, watermark) with optional outer glow and drop shadow. Uses the input mask (or image alpha) as the shape.",
            inputs=[
                # Background
                io.Image.Input("background_image",
                               tooltip="Background image. The input image is composited onto this. Tip: connect the same image to both slots if you don't have a separate background."),

                # Input image
                io.Image.Input("input_image", tooltip="Image to composite (logo, signature, watermark)."),
                io.Mask.Input("mask", optional=True,
                              tooltip="Shape mask for the input image. If omitted, the image alpha channel is used."),
                io.Boolean.Input("invert_mask", default=True,
                                 tooltip="Invert the mask so the opaque areas become the shape. Enable when the mask is white-on-black (transparent inside)."),

                # Scale & opacity
                io.Int.Input("image_scale", default=25, min=1, max=500, step=1,
                             tooltip="Scale percentage. 100%% = input fits entirely within the background. Use lower values for watermarks."),
                io.Int.Input("opacity", default=100, min=0, max=100, step=1,
                             tooltip="Overall opacity of the composited image. 100 = fully visible, 0 = invisible."),

                # Positioning
                io.Combo.Input("position", options=_POSITIONS, default="bottom_right",
                               tooltip="Anchor position of the image on the canvas."),
                io.Int.Input("margin_x", default=20, min=0, max=4000, step=1,
                             tooltip="Horizontal margin from the anchor edge."),
                io.Int.Input("margin_y", default=10, min=0, max=4000, step=1,
                             tooltip="Vertical margin from the anchor edge."),

                # Outer Glow
                io.Boolean.Input("enable_glow", default=False,
                                 tooltip="Enable outer glow effect around the image."),
                io.Int.Input("glow_intensity", default=5, min=2, max=20, step=1,
                             tooltip="Glow buildup iterations (more = denser glow)."),
                io.Int.Input("glow_range", default=25, min=1, max=500, step=1,
                             tooltip="Maximum glow expansion distance in pixels."),
                io.Int.Input("glow_blur", default=15, min=0, max=500, step=1,
                             tooltip="Blur radius applied to each glow step."),
                io.String.Input("glow_inner_color", default="#2ec0ff",
                               tooltip="Glow color near the image (inner, hex)."),
                io.String.Input("glow_outer_color", default="#006eff",
                               tooltip="Glow color at the edge (outer, hex)."),

                # Drop Shadow
                io.Boolean.Input("enable_shadow", default=False,
                                 tooltip="Enable drop shadow effect behind the image."),
                io.Int.Input("shadow_offset_x", default=5, min=-500, max=500, step=1,
                             tooltip="Shadow horizontal offset in pixels."),
                io.Int.Input("shadow_offset_y", default=5, min=-500, max=500, step=1,
                             tooltip="Shadow vertical offset in pixels."),
                io.Int.Input("shadow_grow", default=6, min=0, max=200, step=1,
                             tooltip="Shadow expansion beyond image outline."),
                io.Int.Input("shadow_blur", default=18, min=0, max=200, step=1,
                             tooltip="Shadow blur radius."),
                io.String.Input("shadow_color", default="#000000", tooltip="Shadow color (hex)."),
                io.Int.Input("shadow_opacity", default=50, min=0, max=100, step=1,
                             tooltip="Shadow opacity percentage."),
            ],
            outputs=[
                io.Image.Output("image"),
                io.Mask.Output("mask"),
            ],
        )

    @classmethod
    def execute(cls,
                input_image, image_scale, opacity,
                position, margin_x, margin_y,
                enable_glow, glow_intensity, glow_range, glow_blur,
                glow_inner_color, glow_outer_color,
                enable_shadow, shadow_offset_x, shadow_offset_y, shadow_grow, shadow_blur,
                shadow_color, shadow_opacity,
                background_image, mask=None, invert_mask=True):

        # --- Process input image once (single watermark/logo, applied to every background frame) ---
        n_frames = background_image.shape[0]

        canvas_w, canvas_h = tensor2pil(background_image[0]).size

        # --- Convert input image to RGBA ---
        src_pil = tensor2pil(input_image[0]).convert("RGBA")

        # --- Resolve shape mask (before cropping) ---
        if mask is not None:
            mask_pil = tensor2pil(mask[0]).convert("L")
            if invert_mask:
                mask_pil = Image.fromarray(255 - np.array(mask_pil))
            if mask_pil.size != src_pil.size:
                if hasattr(Image, "Resampling"):
                    resample_filter = Image.Resampling.LANCZOS
                else:
                    resample_filter = getattr(Image, "LANCZOS")
                mask_pil = mask_pil.resize(src_pil.size, resample_filter)
            src_pil.putalpha(mask_pil)

        # --- Auto-crop to non-transparent content ---
        # Threshold alpha to ignore near-transparent edge artifacts (alpha < 10)
        alpha = src_pil.split()[3]
        alpha_thresh = Image.fromarray((np.array(alpha) >= 10).astype(np.uint8) * 255)
        bbox = alpha_thresh.getbbox()  # (left, top, right, bottom) of solid pixels
        if bbox and bbox != (0, 0, src_pil.width, src_pil.height):
            src_pil = src_pil.crop(bbox)
            log.debug(_LOG_PREFIX, f"Auto-cropped transparent padding: {src_pil.size[0]}x{src_pil.size[1]}")

        # --- Fit input image to background (keep aspect ratio) then apply scale ---
        # 100% = input fits entirely within the background (contained)
        fit_ratio = min(canvas_w / src_pil.width, canvas_h / src_pil.height)
        final_scale = fit_ratio * (image_scale / 100.0)
        new_w = max(1, int(src_pil.width * final_scale))
        new_h = max(1, int(src_pil.height * final_scale))
        if (new_w, new_h) != src_pil.size:
            if hasattr(Image, "Resampling"):
                resample_filter = Image.Resampling.LANCZOS
            else:
                resample_filter = getattr(Image, "LANCZOS")
            src_pil = src_pil.resize((new_w, new_h), resample_filter)

        src_w, src_h = src_pil.size
        src_alpha = src_pil.split()[3]  # L mode

        # --- Compute position on canvas ---
        img_x, img_y = _compute_position(position, canvas_w, canvas_h,
                                         src_w, src_h,
                                         margin_x, margin_y)

        # --- Place the source image + alpha onto canvas-sized layers (once) ---
        img_layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        img_layer.paste(src_pil, (img_x, img_y))

        canvas_alpha = Image.new("L", (canvas_w, canvas_h), 0)
        canvas_alpha.paste(src_alpha, (img_x, img_y))

        shape_mask = image2mask(canvas_alpha)  # [1, H, W] tensor

        # --- Pre-compute shadow mask (once — only depends on input image shape) ---
        shadow_mask_pil: Image.Image | None = None
        if enable_shadow and shadow_opacity > 0:
            shadow_mask_pil = canvas_alpha.copy()
            if shadow_offset_x != 0 or shadow_offset_y != 0:
                shadow_mask_pil = shift_image(shadow_mask_pil, shadow_offset_x, shadow_offset_y)
            if shadow_grow > 0 or shadow_blur > 0:
                sm_tensor = expand_mask(image2mask(shadow_mask_pil), shadow_grow, shadow_blur)
                shadow_mask_pil = tensor2pil(sm_tensor).convert("L")
            shadow_rgb = hex_to_rgb(shadow_color)
        else:
            shadow_rgb = (0, 0, 0)  # unused

        # --- Pre-compute glow steps (once — only depends on input image shape) ---
        glow_steps: list[tuple[tuple[int, int, int], Image.Image, int]] = []
        if enable_glow:
            blur_factor = glow_blur / 20.0
            grow = glow_range
            for step in range(glow_intensity):
                step_blur = int(grow * blur_factor)
                color = step_color(glow_outer_color, glow_inner_color, glow_intensity, step)
                glow_mask_tensor = expand_mask(shape_mask, grow, max(step_blur, 1))
                glow_mask_pil = tensor2pil(glow_mask_tensor).convert("L")
                step_opacity = int(lerp(1, 100, step / glow_intensity)) if glow_intensity > 0 else 100
                glow_steps.append((color, glow_mask_pil, step_opacity))
                grow = grow - int(glow_range / glow_intensity)
                if grow <= 0:
                    break

        # --- Composite onto each background frame ---
        out_images: list[torch.Tensor] = []
        out_masks: list[torch.Tensor] = []
        pbar = make_comfy_progress(n_frames)

        for frame_idx in range(n_frames):
            bg_pil = tensor2pil(background_image[frame_idx]).convert("RGBA")
            canvas = bg_pil.copy()

            # --- Drop shadow ---
            if shadow_mask_pil is not None:
                canvas = _composite_with_mask(canvas, shadow_rgb, shadow_mask_pil, shadow_opacity)

            # --- Outer glow ---
            for glow_color, glow_mask_pil, step_opacity in glow_steps:
                canvas = _composite_with_mask(canvas, glow_color, glow_mask_pil, step_opacity, blend="screen")

            # --- Composite input image on top ---
            canvas.paste(img_layer, mask=canvas_alpha)

            # --- Apply opacity: blend in RGB to avoid RGBA→RGB black artifacts ---
            canvas_rgb = canvas.convert("RGB")
            if opacity < 100:
                bg_rgb = bg_pil.convert("RGB")
                canvas_rgb = Image.blend(bg_rgb, canvas_rgb, opacity / 100.0)

            out_images.append(pil2tensor(canvas_rgb))
            out_masks.append(shape_mask)
            pbar.update(1)

        out_image = torch.cat(out_images, dim=0)   # [N, H, W, C]
        out_mask = torch.cat(out_masks, dim=0)     # [N, H, W]

        log.msg(_LOG_PREFIX, f"Composited image ({canvas_w}x{canvas_h}), frames={n_frames}, glow={enable_glow}, shadow={enable_shadow}")
        return io.NodeOutput(out_image, out_mask)
