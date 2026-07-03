from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvLogic_Boolean(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Boolean [Eclipse]",
            display_name="Boolean",
            category=CATEGORY.MAIN.value + CATEGORY.PRIMITIVE.value,
            inputs=[
                io.Boolean.Input("value", default=True, tooltip="Boolean value to output (True/False)."),
            ],
            outputs=[
                io.Boolean.Output("boolean"),
            ],
        )

    @classmethod
    def execute(cls, value=True):
        # Outputs a boolean value for logic operations or workflow branching.
        if not isinstance(value, bool):
            value = True
        return io.NodeOutput(value)