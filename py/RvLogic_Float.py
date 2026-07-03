import sys
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvLogic_Float(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Float [Eclipse]",
            display_name="Float",
            category=CATEGORY.MAIN.value + CATEGORY.PRIMITIVE.value,
            inputs=[
                io.Float.Input("value", default=1.00, min=-sys.float_info.max, max=sys.float_info.max, step=0.01, tooltip="Float value to output."),
            ],
            outputs=[
                io.Float.Output("float"),
            ],
        )

    @classmethod
    def execute(cls, value):
        # Outputs a float value for logic operations or workflow branching.
        if not isinstance(value, (float, int)):
            value = 1.0
        return io.NodeOutput(float(value))