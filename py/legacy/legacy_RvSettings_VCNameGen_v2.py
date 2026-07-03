import os
from comfy_api.latest import io #type: ignore
from ...core import CATEGORY

class RvSettings_VCNameGen_v2(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="VC-Filename Generator II [Eclipse]",
            display_name="⚠ VC-Filename Generator II",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — This node is deprecated and will be removed in v4.0.0.",
            inputs=[
                io.String.Input("path", default="", tooltip="Directory path to your video files."),
                io.String.Input("filename_prefix", default="vc", tooltip="Prefix for generated filenames."),
                io.Int.Input("filename_suffix_start", default=1, min=1, tooltip="Start index for filename suffix."),
                io.Int.Input("filename_suffix_end", default=5, min=1, tooltip="End index for filename suffix."),
                io.Int.Input("join_suffix_start", default=1, min=1, tooltip="Start index for join filename suffix."),
                io.Boolean.Input("simple_combine", default=False, tooltip="Enable simple combine mode."),
                io.String.Input("file_extension", default=".mp4", tooltip="File extension for generated files."),
                io.Int.Input("frame_load_cap", default=81, tooltip="Maximum number of frames to load."),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def execute(cls, path, filename_prefix, filename_suffix_start, filename_suffix_end,
                join_suffix_start, simple_combine, file_extension, frame_load_cap):
        # Generate lists of filenames for file and join operations.
        if not path or not isinstance(path, str):
            raise ValueError("Path is missing. Enter the Path to your Video Files.")

        flist = []
        for counter in range(filename_suffix_start, filename_suffix_end + 1):
            number = str(counter)
            filename = os.path.join(path, f"{filename_prefix}_{number.zfill(5)}{file_extension}")
            flist.append(filename)
        fDict = {"FILE": flist}

        jlist = []
        join_end_idx = join_suffix_start + len(flist)
        for counter in range(join_suffix_start, join_end_idx):
            number = str(counter)
            filename = os.path.join(path, f"{filename_prefix}_join_{number.zfill(5)}{file_extension}")
            jlist.append(filename)
        jDict = {"JOIN": jlist}

        pipe = {
            "path": path,
            "frame_load_cap": frame_load_cap,
            "simple_combine": simple_combine,
            "file_dict": fDict,
            "join_dict": jDict,
        }

        return io.NodeOutput(pipe)