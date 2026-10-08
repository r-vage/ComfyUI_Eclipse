# Ordered video playlist with synchronized video and audio joining.

from comfy_api.latest import io

from ..core import CATEGORY
from ..core.node_debug import debug_node
from ..core.video_helpers import preview_video_file
from ..core.video_loader import (
    file_version,
    load_playlist,
    parse_playlist,
    resolve_video_file,
)


class RvVideo_Load(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Load Video [Eclipse]", display_name="Load Video",
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            description="Load, trim and join videos with synchronized audio into native VIDEO. Use Split Video or Get Video Components when separate images or audio are needed.",
            inputs=[
                io.String.Input("playlist", default='{"version":1,"clips":[]}', socketless=True),
                io.Int.Input("width", default=0, min=0, max=16384, tooltip="0 = automatic. Both dimensions 0 use the first enabled clip."),
                io.Int.Input("height", default=0, min=0, max=16384),
                io.Combo.Input("fit", options=["pad", "crop", "stretch"], default="pad"),
                io.Combo.Input("timing_mode", options=["source", "fixed"], default="source", tooltip="Source keeps every frame's duration; fixed duplicates/drops frames at the selected FPS."),
                io.Float.Input("fps", default=24, min=1, max=240, step=.01),
            ],
            outputs=[io.Video.Output("video"), io.Custom("PIPE").Output("generation_data"),
                     io.Float.Output("duration", tooltip="Duration of the joined VIDEO in seconds."),
                     io.String.Output("report")],
        )

    @classmethod
    def fingerprint_inputs(cls, playlist, **kwargs):
        return tuple((row["id"], file_version(resolve_video_file(row["file"])))
                     for row in parse_playlist(playlist) if row["enabled"])

    @classmethod
    @debug_node("LoadVideo", values=("fit", "timing_mode"))
    def execute(cls, playlist, width=0, height=0, fit="pad", timing_mode="source", fps=24.0):
        result = load_playlist(playlist, width, height, fit, timing_mode, fps)
        return io.NodeOutput(*result, ui={"eclipse_video": [preview_video_file(result[0])[1]], "eclipse_report": [result[3]]})
