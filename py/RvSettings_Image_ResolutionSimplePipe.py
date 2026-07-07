from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.common import RESOLUTION_PRESETS, RESOLUTION_MAP

MAX_RESOLUTION = 32768


class RvSettings_Image_ResolutionSimplePipe(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Resolution Simple [Pipe] [Eclipse]",
            display_name="Image Resolution Simple Pipe",
            category=CATEGORY.MAIN.value + CATEGORY.SETTINGS.value,
            description="Generates an image resolution (without batch size) and outputs it in a custom pipe. "
            "Connect this output to a Pipe Out node (such as Pipe Out Smart Folder) to retrieve the dimensions.",
            inputs=[
                io.Combo.Input(
                    "resolution",
                    options=RESOLUTION_PRESETS,
                    default="1024x1024 (1:1 XL/SD3/Flux/HiDream)",
                    tooltip="Select a preset resolution or 'Custom' to enter custom dimensions.",
                ),
                io.Int.Input(
                    "width",
                    default=1024,
                    min=16,
                    max=MAX_RESOLUTION,
                    step=8,
                    tooltip="Custom width (used when 'Custom' is selected).",
                ),
                io.Int.Input(
                    "height",
                    default=1024,
                    min=16,
                    max=MAX_RESOLUTION,
                    step=8,
                    tooltip="Custom height (used when 'Custom' is selected).",
                ),
            ],
            outputs=[
                io.Custom("PIPE").Output(
                    "pipe",
                    tooltip="Output pipe containing width and height parameters.",
                ),
            ],
        )

    @classmethod
    def execute(cls, resolution, width, height):
        # Resolve width and height
        if resolution == "Custom":
            resolved_w, resolved_h = width, height
        else:
            resolved_w, resolved_h = RESOLUTION_MAP.get(resolution, (1024, 1024))

        # Build output pipe dictionary
        pipe = {
            "width": resolved_w,
            "height": resolved_h,
        }

        return io.NodeOutput(pipe)
