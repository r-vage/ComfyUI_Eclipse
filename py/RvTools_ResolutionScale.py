# Resolution Scale — multiply width/height by a factor, snap to divisible step.

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY


class RvTools_ResolutionScale(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Resolution Scale [Eclipse]",
            display_name="Resolution Scale",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            description="Scale width/height by a factor and snap to a divisible step.",
            inputs=[
                io.Int.Input("width", default=576, min=8, max=16384, step=8, force_input=True, tooltip="Input width."),
                io.Int.Input("height", default=1088, min=8, max=16384, step=8, force_input=True, tooltip="Input height."),
                io.Float.Input("factor", default=1.5, min=0.01, max=16.0, step=0.01, tooltip="Scale factor."),
                io.Int.Input("divisible_by", default=8, min=1, max=256, step=1, tooltip="Snap result to nearest multiple of this value."),
            ],
            outputs=[
                io.Int.Output("width"),
                io.Int.Output("height"),
                io.Float.Output("factor"),
            ],
        )

    @classmethod
    def execute(cls, width, height, factor, divisible_by):
        d = max(divisible_by, 1)
        # Snap inputs first
        w = max(d, round(width / d) * d)
        h = max(d, round(height / d) * d)
        # Apply factor, snap outputs
        new_w = max(d, round(w * factor / d) * d)
        new_h = max(d, round(h * factor / d) * d)
        actual_factor = (new_w / w + new_h / h) / 2.0
        return io.NodeOutput(new_w, new_h, round(actual_factor, 6))
