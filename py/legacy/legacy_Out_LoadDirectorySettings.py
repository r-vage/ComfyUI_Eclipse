from comfy_api.latest import io #type: ignore
from ...core import CATEGORY

class RvPipe_Out_LoadDirectorySettings(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Pipe Out Load Directory Settings [Eclipse]",
            display_name="⚠ Pipe Out Load Directory Settings",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
            inputs=[
                io.Custom("PIPE").Input("pipe", tooltip="Input dict-style pipe containing directory, start_index, and load_cap."),
            ],
            outputs=[
                io.String.Output("directory"),
                io.Int.Output("start_index"),
                io.Int.Output("load_cap"),
            ],
        )

    @classmethod
    def execute(cls, pipe=None):
        # Only accept dict-style pipes now.
        if pipe is None:
            raise ValueError("Input pipe must not be None and must be a dict-style pipe")
        if not isinstance(pipe, dict):
            raise ValueError("RvPipe_Out_LoadDirectorySettings expects dict-style pipes only.")

        directory = pipe.get("directory") or pipe.get("path") or ""
        try:
            start_index_val = pipe.get("start_index")
            start_index = int(start_index_val) if start_index_val is not None else 0
        except Exception:
            start_index = 0
        try:
            load_cap_val = pipe.get("load_cap")
            load_cap = int(load_cap_val) if load_cap_val is not None else 0
        except Exception:
            load_cap = 0

        return io.NodeOutput(directory, start_index, load_cap)