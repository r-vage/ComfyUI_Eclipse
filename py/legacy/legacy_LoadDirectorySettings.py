from comfy_api.latest import io #type: ignore
from ...core import CATEGORY

class RvSettings_LoadDirectorySettings(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Load Directory Settings [Eclipse]",
            display_name="⚠ Load Directory Settings",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
            inputs=[
                io.String.Input("Directory", default="", tooltip="Directory path to load files from."),
                io.Int.Input("start_index", default=0, min=0, control_after_generate=True, tooltip="Start index for loading files."),
                io.Int.Input("loadcap", default=20, tooltip="Maximum number of files to load."),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def execute(cls, Directory, start_index, loadcap):
        # Return directory settings as a dict-style pipe for downstream nodes.
        pipe = {
            "directory": str(Directory),
            "start_index": int(start_index),
            "load_cap": int(loadcap),
        }
        return io.NodeOutput(pipe)