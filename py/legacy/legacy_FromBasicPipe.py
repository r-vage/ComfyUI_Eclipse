# From Basic Pipe [Eclipse]
# Unpacks a standard ComfyUI BASIC_PIPE into model, clip, vae, positive, and negative.

from comfy_api.latest import io  # type: ignore
from ...core import CATEGORY

class RvPipe_FromBasicPipe(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="From Basic Pipe [Eclipse]",
            display_name="⚠ From Basic Pipe",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            inputs=[
                io.Custom("BASIC_PIPE").Input("basic_pipe", tooltip="The standard basic pipe tuple to unpack."),
            ],
            outputs=[
                io.Model.Output("model", tooltip="The unpacked model."),
                io.Clip.Output("clip", tooltip="The unpacked CLIP model."),
                io.Vae.Output("vae", tooltip="The unpacked VAE model."),
                io.Conditioning.Output("positive", tooltip="The unpacked positive conditioning."),
                io.Conditioning.Output("negative", tooltip="The unpacked negative conditioning."),
            ],
            description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
        )

    @classmethod
    def execute(cls, basic_pipe):
        if not basic_pipe or not isinstance(basic_pipe, (list, tuple)) or len(basic_pipe) < 5:
            raise ValueError("From Basic Pipe [Eclipse]: The input 'basic_pipe' is invalid or not fully packed. "
                             "Please connect a valid BASIC_PIPE tuple (model, clip, vae, positive, negative).")
        model, clip, vae, positive, negative = basic_pipe
        return io.NodeOutput(model, clip, vae, positive, negative)
