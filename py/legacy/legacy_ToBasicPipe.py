# To Basic Pipe [Eclipse]
# Packs model, clip, vae, positive, and negative conditioning into a standard ComfyUI BASIC_PIPE.

from comfy_api.latest import io  # type: ignore
from ...core import CATEGORY

class RvPipe_ToBasicPipe(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="To Basic Pipe [Eclipse]",
            display_name="⚠ To Basic Pipe",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            inputs=[
                io.Model.Input("model", tooltip="The model to include in the basic pipe."),
                io.Clip.Input("clip", tooltip="The CLIP model to include in the basic pipe."),
                io.Vae.Input("vae", tooltip="The VAE model to include in the basic pipe."),
                io.Conditioning.Input("positive", tooltip="The positive conditioning to include in the basic pipe."),
                io.Conditioning.Input("negative", tooltip="The negative conditioning to include in the basic pipe."),
            ],
            outputs=[
                io.Custom("BASIC_PIPE").Output("basic_pipe", tooltip="The packed basic pipe tuple (model, clip, vae, positive, negative)."),
            ],
            description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
        )

    @classmethod
    def execute(cls, model, clip, vae, positive, negative):
        pipe = (model, clip, vae, positive, negative)
        return io.NodeOutput(pipe)
