# Native VIDEO to pixels, audio and authoritative presentation timing.

from comfy_api.latest import io

from ..core import CATEGORY
from ..core.node_debug import debug_node
from ..core.video_loader import split_video
from ..core.video_timing import TIMING_TYPE


class RvVideo_Split(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Split Video [Eclipse]", display_name="Split Video",
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            description="Extract IMAGE, AUDIO and per-frame timing from native VIDEO. Send images through an upscaler and connect audio/timing directly to Save Video with Generation Data. Frame count and order must remain unchanged.",
            inputs=[io.Video.Input("video")],
            outputs=[io.Image.Output("images"), io.Audio.Output("audio"),
                     io.Custom(TIMING_TYPE).Output("timing"), io.String.Output("report")],
        )

    @classmethod
    @debug_node("SplitVideo")
    def execute(cls, video):
        return io.NodeOutput(*split_video(video))
