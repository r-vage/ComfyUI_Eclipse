import nodes #type: ignore
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvTools_Stop(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Stop [Eclipse]",
            display_name="Stop",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            inputs=[
                io.AnyType.Input("input"),
            ],
            outputs=[
                io.AnyType.Output("output"),
            ],
        )

    @classmethod
    def validate_inputs(cls, **kwargs):
        return True

    @classmethod
    def execute(cls, input):
        out = input
        nodes.interrupt_processing()
        return io.NodeOutput(out)