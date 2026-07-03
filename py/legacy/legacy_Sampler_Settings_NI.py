from comfy_api.latest import io #type: ignore
from ...core import CATEGORY, SAMPLERS_COMFY, SCHEDULERS_ANY

class RvSettings_Sampler_Settings_NI(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Sampler Settings NI [Eclipse]",
            display_name="⚠ Sampler Settings NI",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
            inputs=[
                io.Boolean.Input("allow_overwrite", default=True, tooltip="When enabled, allows direct inputs to IO nodes to overwrite this node's values."),
                io.Combo.Input("sampler_name", options=SAMPLERS_COMFY, tooltip="Select the sampler algorithm."),
                io.Combo.Input("scheduler", options=SCHEDULERS_ANY, tooltip="Select the scheduler algorithm."),
                io.Int.Input("steps", default=20, min=1, step=1, tooltip="Number of sampling steps."),
                io.Float.Input("cfg", default=3.50, min=1.0, step=0.1, tooltip="Classifier-Free Guidance scale."),
                io.Float.Input("guidance", default=3.50, min=0, step=0.1, tooltip="Flux guidance scale."),
                io.Float.Input("denoise", default=1.0, min=0, max=1.0, step=0.1, tooltip="Denoise strength (0-1)."),
                io.Float.Input("sigmas_denoise", default=0.45, min=0, step=0.1, tooltip="Sigma denoise value."),
                io.Float.Input("noise_strength", default=0.50, min=0, step=0.1, tooltip="Noise strength value."),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def execute(cls, allow_overwrite, sampler_name, scheduler, steps, cfg, guidance, denoise, sigmas_denoise, noise_strength):
        pipe = {
            "sampler_name": sampler_name,
            "scheduler": scheduler,
            "steps": int(steps),
            "cfg": float(cfg),
            "guidance": float(guidance),
            "denoise": float(denoise),
            "sigmas_denoise": float(sigmas_denoise),
            "noise_strength": float(noise_strength),
            "seed": 0,
            "_allow_overwrite": allow_overwrite,
        }
        return io.NodeOutput(pipe)
