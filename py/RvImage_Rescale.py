#
# Image Rescale — resize by a scale factor or fixed dimensions, with optional
# super-sampling for higher quality output. Mode "rescale" multiplies current
# dimensions by a factor; mode "resize" targets exact width × height (rounded
# up to the nearest 8 pixels).
# Ported from WAS Node Suite (MIT licence, original author WASasquatch / ltdata).
#

import torch  # type: ignore
import comfy.utils  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.common import make_comfy_progress

_RESAMPLE_OPTIONS = ["nearest-exact", "bilinear", "area", "bicubic", "lanczos"]
_SS_FACTORS     = ["2x", "4x", "6x", "8x"]


class RvImage_Rescale(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Rescale [Eclipse]",
            display_name="Image Rescale",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            description="Scale an image by a multiplier or resize to fixed dimensions, "
                        "with optional super-sampling for higher quality output.",
            inputs=[
                io.Image.Input("image"),
                io.Combo.Input("mode", options=["rescale", "resize"], default="rescale",
                               tooltip="rescale: multiply current size by factor. "
                                       "resize: target exact width × height."),
                io.Combo.Input("resampling",
                               options=_RESAMPLE_OPTIONS,
                               default="lanczos",
                               tooltip="Resampling filter. 'area' is ideal for downscaling video frames."),
                io.Float.Input("rescale_factor", default=2.0, min=0.01, max=16.0, step=0.01,
                               tooltip="Scale multiplier (rescale mode only)."),
                io.Int.Input("resize_width", default=1024, min=1, max=48000, step=1,
                             tooltip="Target width in pixels (resize mode only). "
                                     "Rounded up to nearest 8."),
                io.Int.Input("resize_height", default=1024, min=1, max=48000, step=1,
                             tooltip="Target height in pixels (resize mode only). "
                                     "Rounded up to nearest 8."),
                io.Boolean.Input("supersample", default=True,
                                 tooltip="Upscale to a larger intermediate (target × supersample_factor) "
                                         "before the final resize to improve anti-aliasing quality. "
                                         "Applies whether enlarging or shrinking."),
                io.Combo.Input("supersample_factor", options=_SS_FACTORS, default="8x",
                               tooltip="Intermediate size multiplier. Bicubic-upscales to N× the target "
                                       "resolution, then downscales with the chosen filter. "
                                       "Higher = better quality, more VRAM."),
            ],
            outputs=[
                io.Image.Output("image"),
            ],
        )

    @classmethod
    def execute(cls, image, mode, resampling, rescale_factor,
                resize_width, resize_height, supersample, supersample_factor):
        if image.dim() == 3:
            image = image.unsqueeze(0)

        H, W = image.shape[1], image.shape[2]

        if mode == "rescale":
            new_w = max(1, int(W * rescale_factor))
            new_h = max(1, int(H * rescale_factor))
        else:
            new_w = resize_width  if resize_width  % 8 == 0 else resize_width  + (8 - resize_width  % 8)
            new_h = resize_height if resize_height % 8 == 0 else resize_height + (8 - resize_height % 8)

        # Supersample: upscale to (target × ss_factor) then downscale to target.
        # The final downscale from the large intermediate provides the anti-aliasing
        # benefit, so it applies whether the target is larger or smaller than source.
        ss_factor = int(supersample_factor[:-1]) if supersample else 0

        # [B, H, W, C] → [B, C, H, W] for common_upscale; process frame-by-frame for progress
        B = image.shape[0]
        pbar = make_comfy_progress(B)
        out_frames = []
        for i in range(B):
            frame = image[i:i+1].movedim(-1, 1)  # [1, C, H, W]
            if ss_factor:
                # 'area' only supports downsampling — use bicubic for the upsample pass
                ss_method = "bicubic" if resampling == "area" else resampling
                frame = comfy.utils.common_upscale(frame, new_w * ss_factor, new_h * ss_factor, ss_method, "disabled")
            frame = comfy.utils.common_upscale(frame, new_w, new_h, resampling, "disabled")
            out_frames.append(frame.movedim(1, -1))  # [1, H, W, C]
            pbar.update(1)
        return io.NodeOutput(torch.cat(out_frames, dim=0))
