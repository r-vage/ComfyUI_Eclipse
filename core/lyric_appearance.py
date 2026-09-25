# Caption-only effects. Prepared rasters never contain animation/global opacity.
import math
from copy import copy
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageChops, ImageColor

from .image_helpers import expand_mask, image2mask, lerp, step_color, tensor2pil


@dataclass(frozen=True)
class CaptionAppearance:
    caption_transparency: float = 0
    enable_glow: bool = False
    glow_intensity: int = 5
    glow_range: int = 25
    glow_blur: int = 15
    glow_inner_color: str = "#2ec0ff"
    glow_outer_color: str = "#006eff"

    def __post_init__(self):
        value = self.caption_transparency
        if (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 100
        ):
            raise ValueError("Caption transparency must be a finite percentage from 0 to 100.")
        if not isinstance(self.enable_glow, bool):
            raise TypeError("Enable glow must be a boolean.")
        if self.enable_glow:
            for name, value, low, high in (
                ("intensity", self.glow_intensity, 2, 20),
                ("range", self.glow_range, 1, 500),
                ("blur", self.glow_blur, 0, 500),
            ):
                if type(value) is not int or not low <= value <= high:
                    raise ValueError(f"Glow {name} must be an integer from {low} to {high}.")
            for color in (self.glow_inner_color, self.glow_outer_color):
                if not isinstance(color, str) or len(color) not in (4, 7) or not color.startswith("#"):
                    raise ValueError("Glow colors must be #RGB or #RRGGBB.")
                ImageColor.getrgb(color)

    @property
    def opacity(self):
        return 1 - self.caption_transparency / 100

    @property
    def padding(self):
        # FX's blur is scaled by each expansion distance. Reserve its full
        # Gaussian support as well as the expansion, before fitting/placement.
        if not self.enable_glow:
            return 0
        return self.glow_range + 3 * max(int(self.glow_range * self.glow_blur / 20), 1)

    def prepare_glow(self, alpha):
        if not self.enable_glow:
            return None
        import comfy.model_management as mm

        mask = image2mask(alpha)
        transmission = np.ones((*np.asarray(alpha).shape, 3), dtype=np.float32)
        grow = self.glow_range
        for step in range(self.glow_intensity):
            mm.throw_exception_if_processing_interrupted()
            blur = max(int(grow * self.glow_blur / 20), 1)
            expanded = tensor2pil(expand_mask(mask, grow, blur)).convert("L")
            color = step_color(self.glow_outer_color, self.glow_inner_color, self.glow_intensity, step)
            opacity = int(lerp(1, 100, step / self.glow_intensity))
            weight = (np.asarray(expanded, dtype=np.float32) * (opacity / 100)).astype(np.uint8)
            # Successive masked screen blends combine into one RGB emission.
            # Retain one raster per shape instead of every expanded mask.
            transmission *= 1 - weight[..., None] / 255 * (np.asarray(color) / 255)
            grow -= int(self.glow_range / self.glow_intensity)
            if grow <= 0:
                break
        return Image.fromarray(np.rint((1 - transmission) * 255).astype(np.uint8))


class CaptionRaster:
    def __init__(self, text, appearance):
        self.text = text
        self.glow = appearance.prepare_glow(text.getchannel("A"))
        self.opacity = appearance.opacity

    def rotated(self, angle, axis):
        # Camera distance is four times the raster's longest side. Even the
        # nearest corner stays beyond 7/8 of that distance (8/7 max expansion).
        # Transform text and screen-glow emission with the same homography.
        width, height = self.text.size
        cosine, sine = math.cos(angle), math.sin(angle)
        vertical = axis == "Turning sign"
        if cosine * (width if vertical else height) < 0.75:
            return None  # Degenerate edge-on plane; no inverse or mirrored back.
        distance = 4 * max(width, height)
        plane = np.array([[cosine, 0, 0], [0, 1, 0], [sine / distance, 0, 1]]) if vertical else np.array(
            [[1, 0, 0], [0, cosine, 0], [0, sine / distance, 1]]
        )
        centered = np.array([[1, 0, -width / 2], [0, 1, -height / 2], [0, 0, 1]])
        forward = plane @ centered
        corners = forward @ np.array([[0, width, width, 0], [0, 0, height, height], [1, 1, 1, 1]])
        corners = corners[:2] / corners[2]
        left, top = np.floor(corners.min(axis=1)).astype(int)
        right, bottom = np.ceil(corners.max(axis=1)).astype(int)
        inverse = np.linalg.inv(forward) @ np.array([[1, 0, left], [0, 1, top], [0, 0, 1]])
        inverse /= inverse[2, 2]
        coefficients = tuple(inverse.ravel()[:8])
        size = (int(right - left), int(bottom - top))
        projected = copy(self)
        projected.text = self.text.transform(size, Image.Transform.PERSPECTIVE, coefficients, Image.Resampling.BICUBIC)
        if self.glow is not None:
            projected.glow = self.glow.transform(size, Image.Transform.PERSPECTIVE, coefficients, Image.Resampling.BICUBIC)
        return projected, (int(left), int(top))

    def composite(self, frame, location=(0, 0), fade=1):
        opacity = self.opacity * fade
        if opacity <= 0:
            return
        if self.glow is None:
            text = self.text
            if opacity < 1:
                text = text.copy()
                text.putalpha(self.text.getchannel("A").point([round(i * opacity) for i in range(256)]))
            frame.alpha_composite(text, location)
            return
        x, y = location
        region = frame.crop((x, y, x + self.text.width, y + self.text.height))
        effect = ImageChops.screen(region.convert("RGB"), self.glow).convert("RGBA")
        effect.alpha_composite(self.text)
        # Fade the complete effect once, including screen glow and highlights.
        # Neither the background nor any cached raster is dimmed in place.
        frame.paste(Image.blend(region, effect, opacity) if opacity < 1 else effect, location)
