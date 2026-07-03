from comfy_api.latest import io #type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "Keep Calc"

class RvVideo_LoopKeepCalc(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Keep Calculator [Eclipse]",
            display_name="Keep Calculator",
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            inputs=[
                io.Int.Input("total_frames", default=16, min=1, max=10000, step=1, tooltip="Total number of frames in the video."),
                io.Int.Input("context_length", default=8, min=1, max=32, step=1, tooltip="Context length for frame calculation."),
                io.Int.Input("overlap_frames", default=4, min=0, max=32, step=1, tooltip="Number of overlapping frames between contexts."),
                io.Int.Input("image_loop_count", default=1, min=1, max=1000, step=1, tooltip="Current loop count for image processing."),
            ],
            outputs=[
                io.Int.Output("frames_to_keep"),
            ],
        )

    @classmethod
    def execute(cls, total_frames, context_length, overlap_frames, image_loop_count):
        # Calculates the number of frames to keep based on context length, overlap, and loop count.
        for name, val, default in [
            ("total_frames", total_frames, 16),
            ("context_length", context_length, 8),
            ("overlap_frames", overlap_frames, 4),
            ("image_loop_count", image_loop_count, 1)
        ]:
            if not isinstance(val, int):
                locals()[name] = default

        try:
            effective_stride = max(1, context_length - overlap_frames)
            remaining_frames = max(0, total_frames - image_loop_count)
            frames_to_keep = min(effective_stride, remaining_frames)
            return io.NodeOutput(max(0, frames_to_keep))
        except Exception as e:
            log.error(_LOG_PREFIX, f"Frame calculation failed: {str(e)}")
            return io.NodeOutput(0)