from typing import Optional, Any
import comfy #type: ignore

from ...core import CATEGORY
from comfy_api.latest import io #type: ignore

UNET_DOWNSAMPLE = 8

class RvPipe_Out_CheckpointLoader(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Pipe Out Checkpoint Loader [Eclipse]",
            display_name="⚠ Pipe Out Checkpoint Loader",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
            inputs=[
                io.Custom("PIPE").Input("pipe", tooltip="Input pipe containing model, clip, vae, latent, width, height, batch size, and names."),
                io.Latent.Input("latent", optional=True, tooltip="Optional latent input to use if pipe does not supply latent, width, or height."),
            ],
            outputs=[
                io.Custom("MODEL").Output("model"),
                io.Custom("CLIP").Output("clip"),
                io.Custom("VAE").Output("vae"),
                io.Latent.Output("latent"),
                io.Int.Output("steps"),
                io.Float.Output("cfg"),
                io.AnyType.Output("sampler_name"),
                io.AnyType.Output("scheduler"),
                io.Float.Output("flux_guidance"),
                io.Int.Output("clip_skip"),
                io.Int.Output("width"),
                io.Int.Output("height"),
                io.Int.Output("batch_size"),
                io.String.Output("model_name"),
                io.String.Output("vae_name"),
                io.String.Output("lora_names"),
                io.Boolean.Output("configure_sampler"),
            ],
        )

    @classmethod
    def execute(cls, pipe: Optional[dict] = None, latent: Optional[dict] = None) -> io.NodeOutput:
        if pipe is None:
            raise ValueError("Input pipe must not be None and must be a dict-style pipe")
        if not isinstance(pipe, dict):
            raise ValueError("RvPipe_Out_CheckpointLoader expects dict-style pipes only.")

        model = pipe.get("model")
        clip = pipe.get("clip")
        vae = pipe.get("vae")
        latent_from_pipe = pipe.get("latent")

        width = pipe.get("width")
        height = pipe.get("height")
        batch_size = pipe.get("batch_size")
        clip_skip = pipe.get("clip_skip")

        try:
            if width is not None:
                width = int(width)
        except Exception:
            width = None
        try:
            if height is not None:
                height = int(height)
        except Exception:
            height = None
        try:
            if batch_size is not None:
                batch_size = int(batch_size)
        except Exception:
            batch_size = None

        if (width is None or height is None) and latent is not None:
            latent_shape = latent["samples"].shape
            if height is None:
                height = latent_shape[2] * UNET_DOWNSAMPLE
            if width is None:
                width = latent_shape[3] * UNET_DOWNSAMPLE

        if latent_from_pipe is not None and latent_from_pipe.get("samples") is not None:
            output_latent = latent_from_pipe
        else:
            output_latent = latent

        model_name = pipe.get("model_name")
        vae_name = pipe.get("vae_name")
        lora_names = pipe.get("lora_names", "")
        
        sampler = pipe.get("sampler_name")
        scheduler = pipe.get("scheduler")
        steps = pipe.get("steps")
        cfg = pipe.get("cfg")
        flux_guidance = pipe.get("flux_guidance")
        
        try:
            if steps is not None:
                steps = int(steps)
        except Exception:
            steps = None
        try:
            if cfg is not None:
                cfg = float(cfg)
        except Exception:
            cfg = None
        try:
            if flux_guidance is not None:
                flux_guidance = float(flux_guidance)
        except Exception:
            flux_guidance = None

        configure_sampler = pipe.get("configure_sampler", True)

        return io.NodeOutput(model, clip, vae, output_latent, steps, cfg, sampler, scheduler, flux_guidance, clip_skip, width, height, batch_size, model_name, vae_name, lora_names, configure_sampler)
