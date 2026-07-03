# Conditioning Passer — pass conditioning through with fixed type.

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY


class RvRouter_Conditioning_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Conditioning Passer [Eclipse]",
            display_name="Conditioning Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.Conditioning.Input("conditioning", tooltip="Conditioning input to be passed through."),
            ],
            outputs=[
                io.Conditioning.Output("conditioning"),
            ],
        )

    @classmethod
    def execute(cls, conditioning):
        return io.NodeOutput(conditioning)
