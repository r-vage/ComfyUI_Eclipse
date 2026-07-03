# String Passer — pass a string through with fixed type.

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY


class RvRouter_String_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="String Passer [Eclipse]",
            display_name="String Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.String.Input("input", force_input=True, tooltip="String input to be passed through."),
            ],
            outputs=[
                io.String.Output("output"),
            ],
        )

    @classmethod
    def execute(cls, input):
        return io.NodeOutput(input)
