from comfy_api.latest import io #type: ignore
from ..core import CATEGORY, SAMPLERS_COMFY, SCHEDULERS_ANY

class RvSettings_Sampler_Selection(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Sampler Selection [Eclipse]",
            display_name="Sampler Selection",
            category=CATEGORY.MAIN.value + CATEGORY.SETTINGS.value,
            inputs=[
                io.Combo.Input("sampler_name", options=SAMPLERS_COMFY, tooltip="Select the sampler algorithm."),
                io.Combo.Input("scheduler", options=SCHEDULERS_ANY, tooltip="Select the scheduler algorithm."),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def execute(cls, sampler_name, scheduler):
        pipe = {
            "sampler_name": sampler_name,
            "scheduler": scheduler,
        }
        return io.NodeOutput(pipe)
