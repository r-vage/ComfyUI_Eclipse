# VAE Passer — pass a VAE through with fixed type.

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY


class RvRouter_Vae_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="VAE Passer [Eclipse]",
            display_name="VAE Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.Vae.Input("vae", tooltip="VAE input to be passed through."),
            ],
            outputs=[
                io.Vae.Output("vae"),
            ],
        )

    @classmethod
    def execute(cls, vae):
        return io.NodeOutput(vae)
