from comfy_api.latest import io #type: ignore
from ...core import CATEGORY, SAMPLERS_COMFY, SCHEDULERS_ANY

class RvSettings_Sampler_Settings_Small(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Sampler Settings Small [Eclipse]",
            display_name="⚠ Sampler Settings Small",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
            inputs=[
                io.Boolean.Input("allow_overwrite", default=True, tooltip="When enabled, allows direct inputs to IO nodes to overwrite this node's values."),
                io.Combo.Input("sampler_name", options=SAMPLERS_COMFY, tooltip="Select the sampler algorithm."),
                io.Combo.Input("scheduler", options=SCHEDULERS_ANY, tooltip="Select the scheduler algorithm."),
                io.Int.Input("steps", default=20, min=1, step=1, tooltip="Number of sampling steps."),
                io.Float.Input("cfg", default=3.50, min=1.0, step=0.1, tooltip="Classifier-Free Guidance scale."),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def execute(cls, allow_overwrite, sampler_name, scheduler, steps, cfg):
        pipe = {
            "sampler_name": sampler_name,
            "scheduler": scheduler,
            "steps": int(steps),
            "cfg": float(cfg),
            "_allow_overwrite": allow_overwrite,
        }
        return io.NodeOutput(pipe)
