# Boolean Passer — pass a boolean through; outputs False when input is muted or bypassed.

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY


class RvRouter_Boolean_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Boolean Passer [Eclipse]",
            display_name="Boolean Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.Boolean.Input("input", optional=True, force_input=True, tooltip="Boolean input to pass through. Outputs False when the input is muted or bypassed."),
            ],
            outputs=[
                io.Boolean.Output("output"),
            ],
        )

    @classmethod
    def execute(cls, input=None):
        return io.NodeOutput(input if input is not None else False)
