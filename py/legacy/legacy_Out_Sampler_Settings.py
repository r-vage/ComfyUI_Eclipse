from comfy_api.latest import io #type: ignore
from ...core import CATEGORY

class RvPipe_Out_Sampler_Settings(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Pipe Out Sampler Settings [Eclipse]",
            display_name="⚠ Pipe Out Sampler Settings",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
            inputs=[
                io.Custom("PIPE").Input("pipe", tooltip="Input pipe containing sampler settings."),
            ],
            outputs=[
                io.Int.Output("steps"),
                io.Float.Output("cfg"),
                io.AnyType.Output("sampler_name"),
                io.AnyType.Output("scheduler"),
                io.Float.Output("guidance"),
                io.Float.Output("denoise"),
                io.Float.Output("sigmas_denoise"),
                io.Float.Output("noise_strength"),
                io.Int.Output("seed"),
            ],
        )

    @classmethod
    def execute(cls, pipe=None):
        # Extract sampler settings from pipe.
        if pipe is None:
            return io.NodeOutput(None, None, None, None, None, None, None, None, None)
        
        if not isinstance(pipe, dict):
            raise ValueError("RvPipe_Out_Sampler_Settings expects dict-style pipes only.")

        sampler = pipe.get("sampler_name")
        scheduler = pipe.get("scheduler")
        steps = pipe.get("steps")
        cfg = pipe.get("cfg")
        guidance = pipe.get("guidance")
        denoise = pipe.get("denoise", 1.0)
        sigmas_denoise = pipe.get("sigmas_denoise")
        noise_strength = pipe.get("noise_strength")
        seed = pipe.get("seed")

        return io.NodeOutput(steps, cfg, sampler, scheduler, guidance, denoise, sigmas_denoise, noise_strength, seed)
