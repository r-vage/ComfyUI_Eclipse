# Latent Passer — pass a latent through with fixed type.

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY


class RvRouter_Latent_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("latent", [io.Latent])
        return io.Schema(
            node_id="Latent Passer [Eclipse]",
            display_name="Latent Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.MatchType.Input(
                    "latent",
                    template=type_template,
                    tooltip="Latent input to be passed through.",
                ),
            ],
            outputs=[
                io.MatchType.Output(type_template, id="latent"),
            ],
        )

    @classmethod
    def execute(cls, latent):
        return io.NodeOutput(latent)
