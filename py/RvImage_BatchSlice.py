#
# Batch Slice — returns a contiguous sub-range of an image batch [B,H,W,C].
#
# start and end follow Python slice semantics:
#   - 0-based indices
#   - Negative values count from the end (-1 = last frame, -2 = second-to-last, …)
#   - end=0 means "to the last frame inclusive" (i.e. no upper bound)
#

import torch  # type: ignore
import comfy.utils  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log
from ..core.image_helpers import flatten_images, cat_and_fit_images

_LOG_PREFIX = "BatchSlice"





class RvImage_BatchSlice(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Batch Slice [Eclipse]",
            display_name="Batch Slice",
            description=(
                "Returns a contiguous sub-range of an image batch [B,H,W,C].\n"
                "start/end use Python slice semantics: 0-based, negatives count from the end.\n"
                "end=0 means 'to the last frame inclusive'."
            ),
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_BATCH.value,
            inputs=[
                io.Image.Input("image", tooltip="Image batch [B,H,W,C]."),
                io.Int.Input(
                    "start",
                    default=0,
                    min=-99999,
                    max=99999,
                    step=1,
                    tooltip="First frame to include. 0 = first frame. Negative values count from the end (-1 = last frame).",
                ),
                io.Int.Input(
                    "end",
                    default=0,
                    min=-99999,
                    max=99999,
                    step=1,
                    tooltip="Last frame to include (inclusive). 0 = last frame. Negative values count from the end.",
                ),
            ],
            outputs=[
                io.Image.Output("image"),
                io.Int.Output("count"),
            ],
            is_input_list=True,
        )

    @classmethod
    def execute(cls, image, start=0, end=0):
        start = start[0] if isinstance(start, list) else start
        end = end[0] if isinstance(end, list) else end

        flat_images = flatten_images(image)
        total = len(flat_images)

        if total == 0:
            return io.NodeOutput(None, 0)

        # Resolve start
        resolved_start = start if start >= 0 else max(0, total + start)
        resolved_start = max(0, min(resolved_start, total - 1))

        # Resolve end: 0 means last frame inclusive
        if end == 0:
            resolved_end = total - 1
        elif end > 0:
            resolved_end = min(end, total - 1)
        else:  # negative
            resolved_end = max(0, total + end)

        if resolved_start > resolved_end:
            log.warning(
                _LOG_PREFIX,
                f"start ({start} → {resolved_start}) > end ({end} → {resolved_end}), swapping.",
            )
            resolved_start, resolved_end = resolved_end, resolved_start

        sliced = flat_images[resolved_start : resolved_end + 1]
        count = len(sliced)
        sliced_tensor = cat_and_fit_images(sliced, log_prefix=_LOG_PREFIX)

        log.msg(
            _LOG_PREFIX,
            f"Sliced frames {resolved_start}–{resolved_end} ({count} frames) from batch/list of {total}",
        )

        return io.NodeOutput(sliced_tensor, count)
