import sys
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvLogic_IntegerGen(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Integer Generate [Eclipse]",
            display_name="Integer Generate",
            category=CATEGORY.MAIN.value + CATEGORY.PRIMITIVE.value,
            inputs=[
                io.Int.Input("value", default=1, min=-sys.maxsize, max=sys.maxsize, step=1, control_after_generate=True, tooltip="Integer value to output or use with increment per queue."),
            ],
            outputs=[
                io.Int.Output("int"),
            ],
        )

    @classmethod
    def execute(cls, value):
        # Outputs an integer value for logic operations or workflow branching.
        if not isinstance(value, int):
            value = 1
        return io.NodeOutput(value)