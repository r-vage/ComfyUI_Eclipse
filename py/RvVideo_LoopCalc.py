import math
import torch  # type: ignore
from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "Loop Calc"


class RvVideo_LoopCalc(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Loop Calculator [Eclipse]",
            display_name="Loop Calculator",
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            inputs=[
                io.Int.Input(
                    "total_frames",
                    default=16,
                    min=1,
                    max=10000,
                    step=1,
                    tooltip="Total number of frames in the video.",
                ),
                io.Int.Input(
                    "context_length",
                    default=8,
                    min=1,
                    max=32,
                    step=1,
                    tooltip="Context length for frame calculation.",
                ),
                io.Int.Input(
                    "overlap_frames",
                    default=4,
                    min=0,
                    max=32,
                    step=1,
                    tooltip="Number of overlapping frames between contexts.",
                ),
                io.Image.Input("images", tooltip="Batch of images to process."),
            ],
            outputs=[
                io.Int.Output("total_loops"),
            ],
        )

    @classmethod
    def execute(cls, total_frames, context_length, overlap_frames, images):
        # Calculates the required number of loops for processing frames with overlap.
        for name, val, default in [
            ("total_frames", total_frames, 16),
            ("context_length", context_length, 8),
            ("overlap_frames", overlap_frames, 4),
        ]:
            if not isinstance(val, int):
                locals()[name] = default

        try:
            image_count = 0
            if isinstance(images, torch.Tensor) and images.ndim > 0:
                image_count = images.shape[0]

            remaining_frames = max(0, total_frames - image_count)
            effective_stride = max(1, context_length - overlap_frames)
            total_loops = (
                math.ceil(remaining_frames / effective_stride)
                if remaining_frames > 0
                else 0
            )
            result = max(1, total_loops)
            return io.NodeOutput(result)
        except Exception as e:
            log.error(_LOG_PREFIX, f"Loop calculation failed: {str(e)}")
            return io.NodeOutput(1)
