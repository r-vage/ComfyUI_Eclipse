# Float Passer — pass a float through with fixed type.

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY


class RvRouter_Float_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Float Passer [Eclipse]",
            display_name="Float Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.Float.Input("input", default=0.0, min=-3.4028235e+38, max=3.4028235e+38, step=0.01, force_input=True, tooltip="Float input to be passed through."),
            ],
            outputs=[
                io.Float.Output("output"),
            ],
        )

    @classmethod
    def execute(cls, input):
        return io.NodeOutput(input)
