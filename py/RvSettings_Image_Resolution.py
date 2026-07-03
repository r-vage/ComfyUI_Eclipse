from comfy_api.latest import io #type: ignore
from ..core import CATEGORY
from ..core.common import RESOLUTION_PRESETS, RESOLUTION_MAP

MAX_RESOLUTION = 32768

class RvSettings_Image_Resolution(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Resolution [Eclipse]",
            display_name="Image Resolution",
            category=CATEGORY.MAIN.value + CATEGORY.SETTINGS.value,
            inputs=[
                io.Combo.Input("resolution", options=RESOLUTION_PRESETS, default="1024x1024 (1:1 XL/SD3/Flux/HiDream)", tooltip="Select a preset resolution or 'Custom' to enter custom dimensions."),
                io.Int.Input("width", default=1024, min=16, max=MAX_RESOLUTION, step=8, tooltip="Custom width (used when 'Custom' is selected)."),
                io.Int.Input("height", default=1024, min=16, max=MAX_RESOLUTION, step=8, tooltip="Custom height (used when 'Custom' is selected)."),
                io.Int.Input("batch_size", default=1, min=1, max=64, step=1, tooltip="Number of images to generate at once."),
            ],
            outputs=[
                io.Int.Output("width"),
                io.Int.Output("height"),
                io.Int.Output("batch_size"),
            ],
        )

    @classmethod
    def execute(cls, resolution, width, height, batch_size):
        # Return custom width/height if "Custom" selected, otherwise use preset values.
        if resolution == "Custom":
            return io.NodeOutput(width, height, batch_size)
        
        preset_width, preset_height = RESOLUTION_MAP.get(resolution, (1024, 1024))
        return io.NodeOutput(preset_width, preset_height, batch_size)