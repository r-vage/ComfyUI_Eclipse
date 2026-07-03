import folder_paths #type: ignore
from comfy_api.latest import io #type: ignore
from ...core import CATEGORY

# Created for seamless_join_video_clips & combine_video_clips
# v1 is used for combine only; it automatically sets the 2nd filename (filename_suffix_start +1), and provides mask settings

class RvSettings_VCNameGen_v1(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="VC-Filename Generator I [Eclipse]",
            display_name="⚠ VC-Filename Generator I",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — This node is deprecated and will be removed in v4.0.0.",
            inputs=[
                io.String.Input("path", default="", tooltip="Base path for the output files."),
                io.String.Input("filename_prefix", default="vc", tooltip="Prefix for the filename."),
                io.Int.Input("filename_suffix_start", default=1, min=1, max=0xffffffffffffffff, control_after_generate=True, tooltip="Starting number for the filename suffix."),
                io.String.Input("file_extension", default=".mp4", tooltip="File extension for the output files."),
                io.Int.Input("frame_load_cap", default=81, tooltip="Maximum number of frames to load."),
                io.Int.Input("mask_first_frames", default=10, tooltip="Number of frames to mask at the start."),
                io.Int.Input("mask_last_frames", default=0, tooltip="Number of frames to mask at the end."),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def execute(cls, path, filename_prefix, filename_suffix_start, file_extension,
                frame_load_cap, mask_first_frames, mask_last_frames):
        # Generates two filenames for video clips and provides mask settings.
        if not path:
            raise ValueError("Path is missing. Enter the Path to your Video Files.")
        counter = filename_suffix_start
        fDict = {}
        flist = []

        for _ in range(filename_suffix_start, filename_suffix_start + 2):
            number = str(counter)
            Filename = f"{path}\\{filename_prefix}_{number.zfill(5)}{file_extension}"
            flist.append(Filename)
            counter += 1

        fDict["FILE"] = flist

        pipe = {
            "path": path,
            "frame_load_cap": frame_load_cap,
            "mask_first_frames": mask_first_frames,
            "mask_last_frames": mask_last_frames,
            "file_dict": fDict,
        }

        return io.NodeOutput(pipe)