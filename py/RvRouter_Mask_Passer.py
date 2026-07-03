# Mask Passer — pass a mask through with fixed type.

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY


class RvRouter_Mask_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Mask Passer [Eclipse]",
            display_name="Mask Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.Mask.Input("mask", tooltip="Mask input to be passed through."),
            ],
            outputs=[
                io.Mask.Output("mask"),
            ],
        )

    @classmethod
    def execute(cls, mask):
        return io.NodeOutput(mask)
