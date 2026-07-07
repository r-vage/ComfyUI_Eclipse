# VAE Passer — pass a VAE through with fixed type.

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY


class RvRouter_Vae_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("vae", [io.Vae])
        return io.Schema(
            node_id="VAE Passer [Eclipse]",
            display_name="VAE Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.MatchType.Input(
                    "vae",
                    template=type_template,
                    tooltip="VAE input to be passed through.",
                ),
            ],
            outputs=[
                io.MatchType.Output(type_template, id="vae"),
            ],
        )

    @classmethod
    def execute(cls, vae):
        return io.NodeOutput(vae)
