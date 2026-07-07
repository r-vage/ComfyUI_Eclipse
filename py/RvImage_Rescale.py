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


def _normalize_to_list(images) -> list:
    if isinstance(images, torch.Tensor):
        if images.dim() == 3:
            return [images.unsqueeze(0)]
        elif images.dim() == 4:
            return [images[i:i+1] for i in range(images.shape[0])]
    if isinstance(images, (list, tuple)):
        out = []
        for img in images:
            if isinstance(img, torch.Tensor):
                if img.dim() == 3:
                    out.append(img.unsqueeze(0))
                elif img.dim() == 4:
                    for j in range(img.shape[0]):
                        out.append(img[j:j+1])
        return out
    raise ValueError(f"Unsupported image input type: {type(images)}")


def _stack_and_force_size(tensors_list: list, resampling: str = "lanczos") -> torch.Tensor:
    if not tensors_list:
        return torch.empty((0, 64, 64, 3))
    first_tensor = tensors_list[0]
    target_h, target_w = first_tensor.shape[1], first_tensor.shape[2]
    adjusted_list = []
    for t in tensors_list:
        if t.shape[1] != target_h or t.shape[2] != target_w:
            t_bchw = t.movedim(-1, 1)  # [1, C, H, W]
            t_resized = comfy.utils.common_upscale(t_bchw, target_w, target_h, resampling, "disabled")
            t = t_resized.movedim(1, -1)  # [1, H, W, C]
        adjusted_list.append(t)
    return torch.cat(adjusted_list, dim=0)


class RvImage_Rescale(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Rescale [Eclipse]",
            display_name="Image Rescale",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_TRANSFORMS.value,
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
        is_list_input = isinstance(image, (list, tuple))
        image_list = _normalize_to_list(image)
        if not image_list:
            raise ValueError("Rescale: No images provided in input.")

        ss_factor = int(supersample_factor[:-1]) if supersample else 0
        total_frames = sum(img.shape[0] for img in image_list)
        pbar = make_comfy_progress(total_frames)

        processed_list = []
        for img in image_list:
            H, W = img.shape[1], img.shape[2]

            if mode == "rescale":
                new_w = max(1, int(W * rescale_factor))
                new_h = max(1, int(H * rescale_factor))
            else:
                new_w = resize_width  if resize_width  % 8 == 0 else resize_width  + (8 - resize_width  % 8)
                new_h = resize_height if resize_height % 8 == 0 else resize_height + (8 - resize_height % 8)

            frame = img.movedim(-1, 1)  # [1, C, H, W]
            if ss_factor:
                ss_method = "bicubic" if resampling == "area" else resampling
                frame = comfy.utils.common_upscale(frame, new_w * ss_factor, new_h * ss_factor, ss_method, "disabled")
            frame = comfy.utils.common_upscale(frame, new_w, new_h, resampling, "disabled")
            out_frame = frame.movedim(1, -1)  # [1, H, W, C]
            processed_list.append(out_frame)
            pbar.update(1)

        if processed_list:
            out_image = _stack_and_force_size(processed_list, resampling)
        else:
            out_image = torch.empty((0, 64, 64, 3))

        return io.NodeOutput(out_image)
