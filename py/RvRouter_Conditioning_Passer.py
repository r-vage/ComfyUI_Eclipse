# Conditioning Passer — pass conditioning through with fixed type.

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY


class RvRouter_Conditioning_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("conditioning", [io.Conditioning])
        return io.Schema(
            node_id="Conditioning Passer [Eclipse]",
            display_name="Conditioning Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.MatchType.Input(
                    "conditioning",
                    template=type_template,
                    tooltip="Conditioning input to be passed through.",
                ),
            ],
            outputs=[
                io.MatchType.Output(type_template, id="conditioning"),
            ],
        )

    @classmethod
    def execute(cls, conditioning):
        return io.NodeOutput(conditioning)
