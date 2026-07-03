from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvLogic_None(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="None [Eclipse]",
            display_name="None",
            category=CATEGORY.MAIN.value + CATEGORY.PRIMITIVE.value,
            inputs=[],
            outputs=[
                io.AnyType.Output("value"),
            ],
            description="Always outputs None as AnyType. Connect to any input that accepts an optional value to pass an explicit empty.",
        )

    @classmethod
    def execute(cls):
        # Returns None as an explicit empty value for any connected input.
        return io.NodeOutput(None)
