# Int Passer — pass an integer through with fixed type.

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY


class RvRouter_Int_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Int Passer [Eclipse]",
            display_name="Int Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.Int.Input("input", default=0, min=-2147483648, max=2147483647, step=1, force_input=True, tooltip="Integer input to be passed through."),
            ],
            outputs=[
                io.Int.Output("output"),
            ],
        )

    @classmethod
    def execute(cls, input):
        return io.NodeOutput(input)
