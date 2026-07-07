# Mask Passer — pass a mask through with fixed type.

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY


class RvRouter_Mask_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("mask", [io.Mask])
        return io.Schema(
            node_id="Mask Passer [Eclipse]",
            display_name="Mask Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.MatchType.Input(
                    "mask",
                    template=type_template,
                    tooltip="Mask input to be passed through.",
                ),
            ],
            outputs=[
                io.MatchType.Output(type_template, id="mask"),
            ],
        )

    @classmethod
    def execute(cls, mask):
        return io.NodeOutput(mask)
