from comfy_api.latest import io #type: ignore
from ...core import CATEGORY

class RvPipe_Out_LoadImage(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Pipe Out Load Image (Metadata Pipe) [Eclipse]",
            display_name="⚠ Pipe Out Load Image (Metadata Pipe)",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
            inputs=[
                io.Custom("PIPE").Input("pipe", tooltip="Input pipe produced by Load Image (Metadata Pipe)"),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
                io.Image.Output("image"),
                io.Int.Output("width"),
                io.Int.Output("height"),
                io.String.Output("text_pos"),
                io.String.Output("text_neg"),
                io.Int.Output("steps"),
                io.Float.Output("cfg"),
                io.AnyType.Output("sampler_name"),
                io.AnyType.Output("scheduler"),
                io.Int.Output("seed"),
                io.String.Output("model_name"),
                io.String.Output("path"),
                io.String.Output("filepath"),
                io.String.Output("filename"),
                io.String.Output("source_name"),
            ],
        )

    @classmethod
    def execute(cls, pipe=None):
        if pipe is None:
            raise ValueError("Input pipe must not be None and must be a dict-style pipe")
        if not isinstance(pipe, dict):
            raise ValueError("RvPipe_Out_LoadImage expects dict-style pipes only.")
        width = pipe.get("width")
        height = pipe.get("height")
        text_pos = pipe.get("text_pos") or pipe.get("text") or pipe.get("prompt") or ""
        text_neg = pipe.get("text_neg") or pipe.get("negative") or pipe.get("negative_prompt") or ""

        steps = pipe.get("steps", 0)
        sampler = pipe.get("sampler_name")
        scheduler = pipe.get("scheduler")
        cfg = pipe.get("cfg", 0.0)
        seed = pipe.get("seed", 0)

        model_name = pipe.get("model_name") or ""
        path = pipe.get("path") or ""

        try:
            if width is not None:
                width = int(width)
        except Exception:
            width = None
        try:
            if height is not None:
                height = int(height)
        except Exception:
            height = None
        try:
            steps = int(steps)
        except Exception:
            steps = 0
        try:
            cfg = float(cfg)
        except Exception:
            cfg = 0.0
        try:
            seed = int(seed)
        except Exception:
            seed = 0

        filepath = pipe.get("filepath") or pipe.get("path") or ""
        filename = pipe.get("filename") or ""
        source_name = pipe.get("source_name") or ""
        image = pipe.get("image")

        return io.NodeOutput(pipe, image, width, height, text_pos, text_neg, steps, cfg, sampler, scheduler, seed, model_name, path,
                filepath, filename, source_name)

