#
# Image Batch Strip — removes N images from the start, end, or both ends of a
# batch tensor [B,H,W,C]. Useful for discarding ramp-in / ramp-out frames
# produced by video generation or overlap blending.
#

import torch  # type: ignore
import comfy.utils  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "BatchStrip"

_POSITION_OPTIONS = ["start", "end", "both"]


def _stack_and_force_size(tensors_list: list) -> torch.Tensor:
    if not tensors_list:
        return torch.empty((0, 64, 64, 3))
    first_tensor = tensors_list[0]
    target_h, target_w = first_tensor.shape[1], first_tensor.shape[2]
    adjusted_list = []
    for t in tensors_list:
        if t.shape[1] != target_h or t.shape[2] != target_w:
            t_bchw = t.movedim(-1, 1)  # [1, C, H, W]
            t_resized = comfy.utils.common_upscale(t_bchw, target_w, target_h, "lanczos", "disabled")
            t = t_resized.movedim(1, -1)  # [1, H, W, C]
        adjusted_list.append(t)
    return torch.cat(adjusted_list, dim=0)


class RvImage_BatchStrip(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Batch Strip [Eclipse]",
            display_name="Image Batch Strip",
            description="Removes N frames from the start, end, or both ends of an "
                        "image batch [B,H,W,C]. When 'both' is selected, strip_count "
                        "frames are removed from each end (2×N total). If stripping "
                        "would leave zero or fewer frames, the original batch is "
                        "returned unchanged.",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_BATCH.value,
            inputs=[
                io.Image.Input("image", tooltip="Image batch [B,H,W,C] to strip frames from."),
                io.Int.Input("strip_count", default=1, min=0, max=256, step=1,
                             tooltip="Number of frames to remove. "
                                     "When position='both', this many frames are removed from each end."),
                io.Combo.Input("position", options=_POSITION_OPTIONS, default="end",
                               tooltip="Which end to strip from:\n"
                                       "  start — remove the first N frames\n"
                                       "  end   — remove the last N frames\n"
                                       "  both  — remove N frames from each end (2×N total)"),
            ],
            outputs=[
                io.Image.Output("image"),
                io.Int.Output("count"),
            ],
            is_input_list=True,
        )

    @classmethod
    def execute(cls, image, strip_count, position):
        strip_count = strip_count[0] if isinstance(strip_count, list) else strip_count
        position = position[0] if isinstance(position, list) else position

        # If it arrived wrapped in a list of length 1, unwrap it to a tensor
        if isinstance(image, list) and len(image) == 1:
            image = image[0]

        is_list = isinstance(image, (list, tuple))
        if is_list:
            b = len(image)
        else:
            if not isinstance(image, torch.Tensor) or image.dim() != 4:
                log.warning(_LOG_PREFIX, f"Expected [B,H,W,C] tensor, got {type(image)}.")
                return io.NodeOutput(image, 0)
            b = image.shape[0]

        if strip_count <= 0:
            return io.NodeOutput(image, b)

        if position == "start":
            if strip_count >= b:
                log.warning(_LOG_PREFIX, f"strip_count={strip_count} >= batch/list size={b}; returning original.")
                return io.NodeOutput(image, b)
            result = list(image[strip_count:]) if is_list else image[strip_count:].contiguous()

        elif position == "end":
            if strip_count >= b:
                log.warning(_LOG_PREFIX, f"strip_count={strip_count} >= batch/list size={b}; returning original.")
                return io.NodeOutput(image, b)
            result = list(image[:-strip_count]) if is_list else image[:-strip_count].contiguous()

        else:  # "both"
            total_strip = strip_count * 2
            if total_strip >= b:
                log.warning(_LOG_PREFIX,
                            f"strip_count×2={total_strip} >= batch/list size={b}; returning original.")
                return io.NodeOutput(image, b)
            result = list(image[strip_count:-strip_count]) if is_list else image[strip_count:-strip_count].contiguous()

        if is_list and result and all(isinstance(t, torch.Tensor) for t in result):
            result = _stack_and_force_size(result)

        count = len(result) if isinstance(result, list) else result.shape[0]
        log.debug(_LOG_PREFIX, f"position={position}, stripped={strip_count}, remaining={count}")
        return io.NodeOutput(result, count)
