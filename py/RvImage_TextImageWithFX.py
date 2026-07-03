# Text Image with FX — renders text onto a canvas with optional outer glow and drop shadow.
# Uses ComfyUI models/fonts/ directory for font discovery; copies bundled defaults on first run.

import os
import glob
import shutil
import textwrap

import numpy as np # type: ignore
import torch # type: ignore
from PIL import Image, ImageDraw, ImageFont  # type: ignore

from comfy_api.latest import io  # type: ignore
import folder_paths  # type: ignore

from ..core import CATEGORY
from ..core.logger import log
from ..core.common import make_comfy_progress
from ..core.image_helpers import (
    tensor2pil, pil2tensor, image2mask,
    hex_to_rgb, expand_mask, shift_image, lerp, step_color,
)

_LOG_PREFIX = "TextImageWithFX"

# ---------------------------------------------------------------------------
# Font discovery — uses ComfyUI models/fonts/
# ---------------------------------------------------------------------------

_FONT_DIR = os.path.join(folder_paths.models_dir, "fonts")

# Bundled fallback fonts shipped inside this repo
_BUNDLED_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")


def _ensure_font_dir() -> str:
    # Create models/fonts/ if it doesn't exist
    if not os.path.isdir(_FONT_DIR):
        os.makedirs(_FONT_DIR, exist_ok=True)
        log.msg(_LOG_PREFIX, f"Created font directory: {_FONT_DIR}")

    # If the directory is empty (or only has placeholder files), copy bundled fonts
    existing_fonts = glob.glob(os.path.join(_FONT_DIR, "*.ttf")) + glob.glob(os.path.join(_FONT_DIR, "*.otf"))
    if len(existing_fonts) == 0 and os.path.isdir(_BUNDLED_FONT_DIR):
        bundled = glob.glob(os.path.join(_BUNDLED_FONT_DIR, "*.ttf")) + glob.glob(os.path.join(_BUNDLED_FONT_DIR, "*.otf"))
        for src in bundled:
            dst = os.path.join(_FONT_DIR, os.path.basename(src))
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
        if bundled:
            log.msg(_LOG_PREFIX, f"Copied {len(bundled)} bundled font(s) to {_FONT_DIR}")

    return _FONT_DIR


def _get_font_list() -> list[str]:
    font_dir = _ensure_font_dir()
    fonts = {}
    for ext in ("*.ttf", "*.otf"):
        for path in glob.glob(os.path.join(font_dir, ext)):
            name = os.path.basename(path)
            fonts[name] = path
    if not fonts:
        return ["(no fonts found)"]
    return sorted(fonts.keys())


def _get_font_path(font_name: str) -> str:
    return os.path.join(_FONT_DIR, font_name)


# ---------------------------------------------------------------------------
# Node-specific compositing helpers
# ---------------------------------------------------------------------------


def _blend_screen(bg: np.ndarray, fg: np.ndarray) -> np.ndarray:
    return 255.0 - (255.0 - bg) * (255.0 - fg) / 255.0


def _composite_with_mask(canvas: Image.Image, color: tuple, mask: Image.Image, opacity: int,
                         blend: str = "normal") -> Image.Image:
    # Blend a solid color onto canvas using mask and opacity
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

    # Apply opacity to the mask
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
                      text_w: int, text_h: int,
                      margin_x: int, margin_y: int) -> tuple[int, int]:
    # Horizontal
    if "left" in position:
        x = margin_x
    elif "right" in position:
        x = canvas_w - text_w - margin_x
    else:  # center
        x = (canvas_w - text_w) // 2

    # Vertical
    if "top" in position:
        y = margin_y
    elif "bottom" in position:
        y = canvas_h - text_h - margin_y
    else:  # center
        y = (canvas_h - text_h) // 2

    return (x, y)


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class RvImage_TextImageWithFX(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Text Image with FX [Eclipse]",
            display_name="Text Image with FX",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            description="Render text with optional outer glow and drop shadow. Supports anchor-based positioning for copyright/watermark placement.",
            inputs=[
                # Text
                io.String.Input("text", default="© Eclipse", multiline=True, tooltip="Text to render."),
                io.Combo.Input("font_file", options=_get_font_list(), default=_get_font_list()[0],
                               tooltip="Font file from ComfyUI/models/fonts/ directory."),
                io.Int.Input("font_size", default=48, min=1, max=2500, step=1, tooltip="Font size in pixels (base size before scaling)."),
                io.Int.Input("text_scale", default=100, min=1, max=500, step=1,
                             tooltip="Scale the text as a percentage of font_size. 100% = original size."),
                io.String.Input("text_color", default="#ffffff", tooltip="Text fill color (hex)."),
                io.Combo.Input("text_align", options=["left", "center", "right"], default="left",
                               tooltip="Horizontal alignment of multi-line text within the text block."),
                io.Int.Input("char_per_line", default=80, min=1, max=8096, step=1,
                             tooltip="Maximum characters per line before wrapping."),
                io.Int.Input("leading", default=4, min=0, max=500, step=1,
                             tooltip="Extra vertical spacing between lines."),
                io.Int.Input("stroke_width", default=0, min=0, max=100, step=1,
                             tooltip="Text outline (stroke) width. 0 = no stroke."),
                io.String.Input("stroke_color", default="#000000", tooltip="Text outline (stroke) color (hex)."),

                # Opacity
                io.Int.Input("opacity", default=100, min=0, max=100, step=1,
                             tooltip="Overall opacity of the composited text. 100 = fully visible, 0 = invisible."),

                # Positioning
                io.Combo.Input("position", options=_POSITIONS, default="bottom_right",
                               tooltip="Anchor position of the text block on the canvas."),
                io.Int.Input("margin_x", default=20, min=0, max=4000, step=1,
                             tooltip="Horizontal margin from the anchor edge."),
                io.Int.Input("margin_y", default=10, min=0, max=4000, step=1,
                             tooltip="Vertical margin from the anchor edge."),

                # Outer Glow
                io.Boolean.Input("enable_glow", default=False,
                                 tooltip="Enable outer glow effect around the text."),
                io.Int.Input("glow_intensity", default=5, min=2, max=20, step=1,
                             tooltip="Glow buildup iterations (more = denser glow)."),
                io.Int.Input("glow_range", default=25, min=1, max=500, step=1,
                             tooltip="Maximum glow expansion distance in pixels."),
                io.Int.Input("glow_blur", default=15, min=0, max=500, step=1,
                             tooltip="Blur radius applied to each glow step."),
                io.String.Input("glow_inner_color", default="#2ec0ff",
                               tooltip="Glow color near the text (inner, hex)."),
                io.String.Input("glow_outer_color", default="#006eff",
                               tooltip="Glow color at the edge (outer, hex)."),

                # Drop Shadow
                io.Boolean.Input("enable_shadow", default=False,
                                 tooltip="Enable drop shadow effect behind the text."),
                io.Int.Input("shadow_offset_x", default=5, min=-500, max=500, step=1,
                             tooltip="Shadow horizontal offset in pixels."),
                io.Int.Input("shadow_offset_y", default=5, min=-500, max=500, step=1,
                             tooltip="Shadow vertical offset in pixels."),
                io.Int.Input("shadow_grow", default=6, min=0, max=200, step=1,
                             tooltip="Shadow expansion beyond text outline."),
                io.Int.Input("shadow_blur", default=18, min=0, max=200, step=1,
                             tooltip="Shadow blur radius."),
                io.String.Input("shadow_color", default="#000000", tooltip="Shadow color (hex)."),
                io.Int.Input("shadow_opacity", default=50, min=0, max=100, step=1,
                             tooltip="Shadow opacity percentage."),

                # Optional background
                io.Image.Input("background_image", optional=True,
                               tooltip="Optional background image. Canvas auto-sizes to text when omitted."),
            ],
            outputs=[
                io.Image.Output("image"),
                io.Mask.Output("mask"),
            ],
        )

    @classmethod
    def execute(cls,
                text, font_file, font_size, text_scale, text_color, text_align, char_per_line, leading,
                stroke_width, stroke_color, opacity,
                position, margin_x, margin_y,
                enable_glow, glow_intensity, glow_range, glow_blur,
                glow_inner_color, glow_outer_color,
                enable_shadow, shadow_offset_x, shadow_offset_y, shadow_grow, shadow_blur,
                shadow_color, shadow_opacity,
                background_image=None):

        # --- Scale font size ---
        effective_font_size = max(1, int(font_size * text_scale / 100.0))

        # --- Load font ---
        font_path = _get_font_path(font_file)
        try:
            font = ImageFont.truetype(font_path, effective_font_size)
        except Exception:
            log.warning(_LOG_PREFIX, f"Could not load font '{font_file}', using default.")
            font = ImageFont.load_default()

        # --- Wrap and measure text (once — same for all frames) ---
        paragraphs = text.split("\n")
        all_lines: list[str] = []
        for paragraph in paragraphs:
            if paragraph.strip() == "":
                all_lines.append("")
            else:
                lines = textwrap.wrap(paragraph, width=char_per_line,
                                      expand_tabs=False, replace_whitespace=False,
                                      drop_whitespace=False)
                all_lines.extend(lines if lines else [""])

        # Measure each line
        line_metrics: list[tuple[int, int, int]] = []  # (width, height, baseline_offset)
        for line in all_lines:
            if line == "":
                # Empty line — use font height for spacing
                bbox = font.getbbox("Mg")
                h = bbox[3] - bbox[1]
                line_metrics.append((0, h, bbox[1]))
            else:
                bbox = font.getbbox(line)
                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]
                line_metrics.append((w, h, bbox[1]))

        # Total text block dimensions
        total_text_h = sum(m[1] for m in line_metrics) + leading * max(0, len(all_lines) - 1)
        max_text_w = max((m[0] for m in line_metrics), default=0)
        # Account for stroke width
        if stroke_width > 0:
            max_text_w += stroke_width * 2
            total_text_h += stroke_width * 2

        # --- Determine batch size and canvas dimensions (from first background frame) ---
        n_frames = background_image.shape[0] if background_image is not None else 1

        if background_image is not None:
            canvas_w, canvas_h = tensor2pil(background_image[0]).size
        else:
            # Auto-size canvas to fit text + effect padding (uniform)
            pad = 0
            if enable_glow:
                pad = max(pad, glow_range + glow_blur)
            if enable_shadow:
                pad = max(pad, abs(shadow_offset_x) + shadow_grow + shadow_blur,
                          abs(shadow_offset_y) + shadow_grow + shadow_blur)
            canvas_w = max_text_w + pad * 2 + margin_x * 2
            canvas_h = total_text_h + pad * 2 + margin_y * 2

        # --- Compute text position (based on first-frame canvas size) ---
        if background_image is not None:
            text_x, text_y = _compute_position(position, canvas_w, canvas_h,
                                               max_text_w, total_text_h,
                                               margin_x, margin_y)
        else:
            # Auto-size: center text within padded canvas
            text_x = (canvas_w - max_text_w) // 2
            text_y = (canvas_h - total_text_h) // 2

        # --- Draw text layer once (shared across all frames of the same canvas size) ---
        text_layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(text_layer)
        y_cursor = text_y + (stroke_width if stroke_width > 0 else 0)

        text_rgb = hex_to_rgb(text_color)
        stroke_rgb = hex_to_rgb(stroke_color) if stroke_width > 0 else None

        for i, line in enumerate(all_lines):
            lw, lh, lbase = line_metrics[i]
            if line == "":
                y_cursor += lh + leading
                continue

            # Horizontal alignment within text block
            if text_align == "left":
                lx = text_x
            elif text_align == "right":
                lx = text_x + max_text_w - lw
            else:  # center
                lx = text_x + (max_text_w - lw) // 2

            draw.text(
                (lx, y_cursor - lbase),
                line,
                fill=(*text_rgb, 255),
                font=font,
                stroke_width=stroke_width,
                stroke_fill=(*stroke_rgb, 255) if stroke_rgb else None,
            )
            y_cursor += lh + leading

        # Extract alpha as mask — computed once, reused for all frames
        text_alpha = text_layer.split()[3]  # L mode
        text_mask = image2mask(text_alpha)  # [1, H, W] tensor

        # Pre-compute shadow mask (same for every frame — only depends on text shape)
        shadow_mask_precomp: Image.Image | None = None
        if enable_shadow and shadow_opacity > 0:
            shadow_mask_precomp = text_alpha.copy()
            if shadow_offset_x != 0 or shadow_offset_y != 0:
                shadow_mask_precomp = shift_image(shadow_mask_precomp, shadow_offset_x, shadow_offset_y)
            if shadow_grow > 0 or shadow_blur > 0:
                sm_tensor = expand_mask(image2mask(shadow_mask_precomp), shadow_grow, shadow_blur)
                shadow_mask_precomp = tensor2pil(sm_tensor).convert("L")
            shadow_rgb = hex_to_rgb(shadow_color)
        else:
            shadow_rgb = (0, 0, 0)  # unused

        # Pre-compute glow masks (same for every frame — only depends on text shape)
        glow_steps: list[tuple[tuple[int, int, int], Image.Image, int]] = []  # (color, mask_pil, opacity)
        if enable_glow:
            blur_factor = glow_blur / 20.0
            grow = glow_range
            for step in range(glow_intensity):
                step_blur = int(grow * blur_factor)
                color = step_color(glow_outer_color, glow_inner_color, glow_intensity, step)
                glow_mask_tensor = expand_mask(text_mask, grow, max(step_blur, 1))
                glow_mask_pil = tensor2pil(glow_mask_tensor).convert("L")
                step_opacity = int(lerp(1, 100, step / glow_intensity)) if glow_intensity > 0 else 100
                glow_steps.append((color, glow_mask_pil, step_opacity))
                grow = grow - int(glow_range / glow_intensity)
                if grow <= 0:
                    break

        # --- Process each background frame ---
        out_images: list[torch.Tensor] = []
        out_masks: list[torch.Tensor] = []
        pbar = make_comfy_progress(n_frames)

        for frame_idx in range(n_frames):
            if background_image is not None:
                bg_pil = tensor2pil(background_image[frame_idx]).convert("RGBA")
                canvas = bg_pil.copy()
            else:
                bg_pil = None
                canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

            # --- Drop shadow (behind everything) ---
            if shadow_mask_precomp is not None:
                canvas = _composite_with_mask(canvas, shadow_rgb, shadow_mask_precomp, shadow_opacity)

            # --- Outer glow (between shadow and text) ---
            for glow_color, glow_mask_pil, step_opacity in glow_steps:
                canvas = _composite_with_mask(canvas, glow_color, glow_mask_pil, step_opacity, blend="screen")

            # --- Composite text on top ---
            canvas.paste(text_layer, mask=text_alpha)

            # --- Build output tensors for this frame ---
            if bg_pil is None:
                # No background: flatten RGBA onto black for RGB output
                flat = Image.new("RGBA", canvas.size, (0, 0, 0, 255))
                flat.paste(canvas, mask=canvas.split()[3])
                out_img_frame = pil2tensor(flat.convert("RGB"))
            else:
                # Apply opacity: blend in RGB to avoid RGBA→RGB black artifacts
                canvas_rgb = canvas.convert("RGB")
                if opacity < 100:
                    bg_rgb = bg_pil.convert("RGB")
                    canvas_rgb = Image.blend(bg_rgb, canvas_rgb, opacity / 100.0)
                out_img_frame = pil2tensor(canvas_rgb)

            out_images.append(out_img_frame)
            out_masks.append(text_mask)
            pbar.update(1)

        out_image = torch.cat(out_images, dim=0)   # [N, H, W, C]
        out_mask = torch.cat(out_masks, dim=0)     # [N, H, W]

        log.msg(_LOG_PREFIX, f"Rendered text ({canvas_w}x{canvas_h}), frames={n_frames}, glow={enable_glow}, shadow={enable_shadow}")
        return io.NodeOutput(out_image, out_mask)
