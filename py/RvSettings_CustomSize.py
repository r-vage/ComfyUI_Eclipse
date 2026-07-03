from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

MAX_RESOLUTION = 32768

class RvSettings_CustomSize(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Custom Size [Eclipse]",
            display_name="Custom Size",
            category=CATEGORY.MAIN.value + CATEGORY.SETTINGS.value,
            inputs=[
                io.Int.Input("width", default=512, min=16, max=MAX_RESOLUTION, step=8, tooltip="Set the custom width (16-32768, step 8)."),
                io.Int.Input("height", default=512, min=16, max=MAX_RESOLUTION, step=8, tooltip="Set the custom height (16-32768, step 8)."),
            ],
            outputs=[
                io.Int.Output("width"),
                io.Int.Output("height"),
            ],
        )

    @classmethod
    def execute(cls, width, height):
        return io.NodeOutput(width, height)