#
# Batch Interleave — merges two image batches frame by frame:
#   batch_a[0], batch_b[0], batch_a[1], batch_b[1], …
# If one batch is longer, the remaining frames are appended at the end.
#

import torch  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

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
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            inputs=[
                io.Image.Input("batch_a", tooltip="First image batch [B,H,W,C]. Its frames are placed at even positions."),
                io.Image.Input("batch_b", tooltip="Second image batch [B,H,W,C]. Its frames are placed at odd positions."),
            ],
            outputs=[
                io.Image.Output("images"),
                io.Int.Output("count"),
            ],
        )

    @classmethod
    def execute(cls, batch_a, batch_b):
        len_a = batch_a.shape[0]
        len_b = batch_b.shape[0]
        min_len = min(len_a, len_b)

        # Interleave the shared prefix: [min_len*2, H, W, C]
        interleaved = torch.stack(
            [batch_a[:min_len], batch_b[:min_len]], dim=1
        ).reshape(min_len * 2, *batch_a.shape[1:])

        # Append any leftover frames from the longer batch
        if len_a > min_len:
            interleaved = torch.cat([interleaved, batch_a[min_len:]], dim=0)
        elif len_b > min_len:
            interleaved = torch.cat([interleaved, batch_b[min_len:]], dim=0)

        count = interleaved.shape[0]
        log.msg(_LOG_PREFIX, f"Interleaved {len_a} + {len_b} frames → {count} frames")

        return io.NodeOutput(interleaved, count)
