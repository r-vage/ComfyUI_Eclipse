from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvLogic_String(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="String [Eclipse]",
            display_name="String",
            category=CATEGORY.MAIN.value + CATEGORY.PRIMITIVE.value,
            inputs=[
                io.String.Input("value", default="", tooltip="String value to output."),
            ],
            outputs=[
                io.String.Output("string"),
            ],
        )

    @classmethod
    def execute(cls, value=""):
        # Outputs a string value for logic operations or workflow branching.
        if not isinstance(value, str):
            value = ""
        return io.NodeOutput(value)