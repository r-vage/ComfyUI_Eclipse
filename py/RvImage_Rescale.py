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
from ..core.image_helpers import unwrap_value, flatten_images, was_input_batch, prepare_image_output

_RESAMPLE_OPTIONS = ["nearest-exact", "bilinear", "area", "bicubic", "lanczos"]
_SS_FACTORS = ["2x", "4x", "6x", "8x"]





class RvImage_Rescale(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Rescale [Eclipse]",
            display_name="Image Rescale",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_TRANSFORMS.value,
            description="Scale an image by a multiplier or resize to fixed dimensions, "
            "with optional super-sampling for higher quality output.",
            is_input_list=True,
            inputs=[
                io.Image.Input("image"),
                io.Combo.Input(
                    "mode",
                    options=["rescale", "resize"],
                    default="rescale",
                    tooltip="rescale: multiply current size by factor. "
                    "resize: target exact width × height.",
                ),
                io.Combo.Input(
                    "resampling",
                    options=_RESAMPLE_OPTIONS,
                    default="lanczos",
                    tooltip="Resampling filter. 'area' is ideal for downscaling video frames.",
                ),
                io.Float.Input(
                    "rescale_factor",
                    default=2.0,
                    min=0.01,
                    max=16.0,
                    step=0.01,
                    tooltip="Scale multiplier (rescale mode only).",
                ),
                io.Int.Input(
                    "resize_width",
                    default=1024,
                    min=1,
                    max=48000,
                    step=1,
                    tooltip="Target width in pixels (resize mode only). "
                    "Rounded up to nearest 8.",
                ),
                io.Int.Input(
                    "resize_height",
                    default=1024,
                    min=1,
                    max=48000,
                    step=1,
                    tooltip="Target height in pixels (resize mode only). "
                    "Rounded up to nearest 8.",
                ),
                io.Boolean.Input(
                    "supersample",
                    default=True,
                    tooltip="Upscale to a larger intermediate (target × supersample_factor) "
                    "before the final resize to improve anti-aliasing quality. "
                    "Applies whether enlarging or shrinking.",
                ),
                io.Combo.Input(
                    "supersample_factor",
                    options=_SS_FACTORS,
                    default="8x",
                    tooltip="Intermediate size multiplier. Bicubic-upscales to N× the target "
                    "resolution, then downscales with the chosen filter. "
                    "Higher = better quality, more VRAM.",
                ),
            ],
            outputs=[
                io.Image.Output("image", is_output_list=True),
            ],
        )

    @classmethod
    def execute(
        cls,
        image,
        mode,
        resampling,
        rescale_factor,
        resize_width,
        resize_height,
        supersample,
        supersample_factor,
    ):
        mode = unwrap_value(mode)
        resampling = unwrap_value(resampling, "lanczos")
        rescale_factor = unwrap_value(rescale_factor, 2.0)
        resize_width = unwrap_value(resize_width, 1024)
        resize_height = unwrap_value(resize_height, 1024)
        supersample = unwrap_value(supersample, True)
        supersample_factor = unwrap_value(supersample_factor, "8x")

        flat_image = flatten_images(image)
        if not flat_image:
            raise ValueError("Rescale: No images provided in input.")

        was_batch = was_input_batch(image)

        ss_factor = int(supersample_factor[:-1]) if supersample else 0
        total_frames = len(flat_image)
        pbar = make_comfy_progress(total_frames)

        processed_list = []
        for img in flat_image:
            H, W = img.shape[1], img.shape[2]

            if mode == "rescale":
                new_w = max(1, int(W * rescale_factor))
                new_h = max(1, int(H * rescale_factor))
            else:
                new_w = (
                    resize_width
                    if resize_width % 8 == 0
                    else resize_width + (8 - resize_width % 8)
                )
                new_h = (
                    resize_height
                    if resize_height % 8 == 0
                    else resize_height + (8 - resize_height % 8)
                )

            frame = img.movedim(-1, 1)  # [1, C, H, W]
            if ss_factor:
                ss_method = "bicubic" if resampling == "area" else resampling
                frame = comfy.utils.common_upscale(
                    frame, new_w * ss_factor, new_h * ss_factor, ss_method, "disabled"
                )
            frame = comfy.utils.common_upscale(
                frame, new_w, new_h, resampling, "disabled"
            )
            out_frame = frame.movedim(1, -1)  # [1, H, W, C]
            processed_list.append(out_frame)
            pbar.update(1)

        merged_tensor = torch.cat(processed_list, dim=0)
        result = prepare_image_output(merged_tensor, was_batch)
        return io.NodeOutput(result)
