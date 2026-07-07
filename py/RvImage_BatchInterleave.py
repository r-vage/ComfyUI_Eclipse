#
# Batch Interleave — merges two image batches frame by frame:
#   batch_a[0], batch_b[0], batch_a[1], batch_b[1], …
# If one batch is longer, the remaining frames are appended at the end.
#

import torch  # type: ignore
import comfy.utils  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log
from ..core.image_helpers import flatten_images, cat_and_fit_images

_LOG_PREFIX = "BatchInterleave"





class RvImage_BatchInterleave(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Batch Interleave [Eclipse]",
            display_name="Batch Interleave",
            description=(
                "Merges two image batches frame by frame:\n"
                "  batch_a[0], batch_b[0], batch_a[1], batch_b[1], …\n"
                "If one batch is longer, its remaining frames are appended at the end."
            ),
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_BATCH.value,
            inputs=[
                io.Image.Input(
                    "batch_a",
                    tooltip="First image batch [B,H,W,C]. Its frames are placed at even positions.",
                ),
                io.Image.Input(
                    "batch_b",
                    tooltip="Second image batch [B,H,W,C]. Its frames are placed at odd positions.",
                ),
            ],
            outputs=[
                io.Image.Output("images"),
                io.Int.Output("count"),
            ],
            is_input_list=True,
        )

    @classmethod
    def execute(cls, batch_a, batch_b):
        flat_a = flatten_images(batch_a)
        flat_b = flatten_images(batch_b)

        interleaved = []
        min_len = min(len(flat_a), len(flat_b))
        for i in range(min_len):
            interleaved.append(flat_a[i])
            interleaved.append(flat_b[i])

        if len(flat_a) > min_len:
            interleaved.extend(flat_a[min_len:])
        elif len(flat_b) > min_len:
            interleaved.extend(flat_b[min_len:])

        count = len(interleaved)
        out_tensor = cat_and_fit_images(interleaved, log_prefix=_LOG_PREFIX)

        log.msg(_LOG_PREFIX, f"Interleaved {len(flat_a)} + {len(flat_b)} frames → {count} frames")
        return io.NodeOutput(out_tensor, count)
