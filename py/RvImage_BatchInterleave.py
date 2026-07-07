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

_LOG_PREFIX = "BatchInterleave"


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
                io.Image.Input("batch_a", tooltip="First image batch [B,H,W,C]. Its frames are placed at even positions."),
                io.Image.Input("batch_b", tooltip="Second image batch [B,H,W,C]. Its frames are placed at odd positions."),
            ],
            outputs=[
                io.Image.Output("images"),
                io.Int.Output("count"),
            ],
            is_input_list=True,
        )

    @classmethod
    def execute(cls, batch_a, batch_b):
        # If they arrived wrapped in a list of length 1, unwrap them
        if isinstance(batch_a, list) and len(batch_a) == 1:
            batch_a = batch_a[0]
        if isinstance(batch_b, list) and len(batch_b) == 1:
            batch_b = batch_b[0]

        is_list_a = isinstance(batch_a, (list, tuple))
        is_list_b = isinstance(batch_b, (list, tuple))
        is_list = is_list_a or is_list_b

        def get_len_and_items(b, as_list):
            if isinstance(b, (list, tuple)):
                return len(b), list(b)
            elif isinstance(b, torch.Tensor):
                if b.dim() == 3:
                    b = b.unsqueeze(0)
                if as_list:
                    return b.shape[0], [b[i:i+1] for i in range(b.shape[0])]
                else:
                    return b.shape[0], b
            return 0, []

        len_a, items_a = get_len_and_items(batch_a, is_list)
        len_b, items_b = get_len_and_items(batch_b, is_list)

        if is_list:
            interleaved = []
            min_len = min(len_a, len_b)
            for i in range(min_len):
                interleaved.append(items_a[i])
                interleaved.append(items_b[i])
            if len_a > min_len:
                interleaved.extend(items_a[min_len:])
            elif len_b > min_len:
                interleaved.extend(items_b[min_len:])
            count = len(interleaved)
            if interleaved and all(isinstance(t, torch.Tensor) for t in interleaved):
                interleaved = _stack_and_force_size(interleaved)
        else:
            min_len = min(len_a, len_b)
            interleaved = torch.stack(
                [items_a[:min_len], items_b[:min_len]], dim=1
            ).reshape(min_len * 2, *items_a.shape[1:])

            if len_a > min_len:
                interleaved = torch.cat([interleaved, items_a[min_len:]], dim=0)
            elif len_b > min_len:
                interleaved = torch.cat([interleaved, items_b[min_len:]], dim=0)
            count = interleaved.shape[0]

        log.msg(_LOG_PREFIX, f"Interleaved {len_a} + {len_b} frames → {count} frames")
        return io.NodeOutput(interleaved, count)
