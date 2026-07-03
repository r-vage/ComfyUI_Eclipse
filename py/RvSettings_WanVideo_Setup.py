from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvSettings_WanVideo_Setup(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="WanVideo Setup [Eclipse]",
            display_name="WanVideo Setup",
            category=CATEGORY.MAIN.value + CATEGORY.SETTINGS.value,
            inputs=[
                io.Int.Input("steps", default=4, min=1, tooltip="Number of steps for video processing."),
                io.Float.Input("cfg", default=1.0, min=1.0, tooltip="Classifier-Free Guidance scale."),
                io.Float.Input("model_shift", default=5.0, min=0, tooltip="Model shift value for video batch."),
                io.Int.Input("steps_start", default=2, min=-1, tooltip="Start index for split steps."),
                io.Int.Input("steps_stop", default=2, min=-1, max=10000, tooltip="End index for split steps."),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def execute(cls, steps, cfg, model_shift, steps_start, steps_stop):
        pipe = {
            "steps": int(steps),
            "cfg": float(cfg),
            "model_shift": float(model_shift),
            "steps_start": int(steps_start),
            "steps_stop": int(steps_stop),
        }
        return io.NodeOutput(pipe)
