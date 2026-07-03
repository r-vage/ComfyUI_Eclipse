import re
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "VCNameGen"

class RvPipe_Out_VCNameGen(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Pipe Out VC Name Generator [Eclipse]",
            display_name="Pipe Out VC Name Generator",
            category=CATEGORY.MAIN.value + CATEGORY.PIPE.value,
            inputs=[
                io.Custom("PIPE").Input("pipe", tooltip="Input pipe containing path, frame load cap, mask frames, and files."),
            ],
            outputs=[
                io.String.Output("path"),
                io.String.Output("rel_path"),
                io.Int.Output("frame_load_cap"),
                io.Int.Output("mask_first_frames"),
                io.Int.Output("mask_last_frames"),
                io.Boolean.Output("simple_combine"),
                io.String.Output("files"),
                io.String.Output("files_join"),
            ],
        )

    @classmethod
    def execute(cls, pipe=None):
        # Expect a dict-style pipe with canonical keys.
        if pipe is None:
            raise ValueError("Input pipe must not be None and must be a dict-style pipe")
        if not isinstance(pipe, dict):
            raise ValueError("RvPipe_Out_VCNameGen expects dict-style pipes only.")

        path = pipe.get("path", "")
        frame_load_cap = pipe.get("frame_load_cap", 0)
        mask_first_frames = pipe.get("mask_first_frames", 0)
        mask_last_frames = pipe.get("mask_last_frames", 0)
        file_dict = pipe.get("file_dict") or pipe.get("files")
        join_dict = pipe.get("join_dict") or pipe.get("files_join")

        files = ""
        files_join = ""
        simple_combine = pipe.get("simple_combine", False)
        rel_path = re.sub(r"(<?^.*output)", ".", path)

        try:
            log.msg(_LOG_PREFIX, f"rel_path: {rel_path}")
        except Exception:
            pass

        if file_dict not in (None, '', 'undefined', 'none'):
            try:
                if file_dict is not None:
                    files = str(file_dict.get("FILE"))
            except Exception:
                files = str(file_dict)
            files = re.sub(r"^\[", "", files)
            files = re.sub(r"\]", "", files)
            files = re.sub(r"'", "", files)

        if join_dict not in (None, '', 'undefined', 'none'):
            try:
                if join_dict is not None:
                    files_join = str(join_dict.get("JOIN"))
            except Exception:
                files_join = str(join_dict)
            files_join = re.sub(r"^\[", "", files_join)
            files_join = re.sub(r"\]", "", files_join)
            files_join = re.sub(r"'", "", files_join)

        return io.NodeOutput(path, rel_path, frame_load_cap, mask_first_frames, mask_last_frames, simple_combine, files, files_join)