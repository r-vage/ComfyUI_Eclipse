from __future__ import annotations

# Model Loader Pipe [Eclipse] — Standalone model loader with pipe output
#
# Supports: Standard Checkpoints, UNet, Nunchaku (Flux/Qwen/ZImage), GGUF
# Features: LoRA (3 slots), BlockSwap, baked CLIP/VAE from checkpoints
# Output: pipe dict compatible with Pipe Out Checkpoint Loader

from ..core import CATEGORY
from ..core.logger import log
from ..core.model_loader_common import get_model_loader_inputs, load_model, build_pipe, OMIT
from comfy_api.latest import io  # type: ignore

_LOG_PREFIX = "Model Loader Pipe"


class RvLoader_ModelLoaderPipe(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Model Loader Pipe [Eclipse]",
            display_name="Model Loader Pipe",
            category=CATEGORY.MAIN.value + CATEGORY.LOADER.value,
            description="Standalone model loader with pipe output.",
            inputs=get_model_loader_inputs(),
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def validate_inputs(cls, **kwargs):
        return True

    @classmethod
    def execute(cls, **kwargs):
        model_type = kwargs.get('model_type', 'Standard Checkpoint')
        enable_clip_layer = bool(kwargs.get('enable_clip_layer', True))
        stop_at_clip_layer = kwargs.get('stop_at_clip_layer', -2)
        is_standard = (model_type == "Standard Checkpoint")
        is_nunchaku = (model_type == "Nunchaku Flux")

        loaded_model, loaded_clip, loaded_vae, loaded_audio_vae, checkpoint_name, lora_string = load_model(_LOG_PREFIX, **kwargs)

        # ── Build pipe ──

        pipe = build_pipe(
            model=loaded_model,
            model_name=checkpoint_name,
            is_nunchaku=is_nunchaku,
            lora_names=lora_string,
            clip=loaded_clip if loaded_clip is not None else OMIT,
            vae=loaded_vae if loaded_vae is not None else OMIT,
            audio_vae=loaded_audio_vae if loaded_audio_vae is not None else OMIT,
            clip_skip=stop_at_clip_layer if (is_standard and enable_clip_layer) else OMIT,
        )

        return io.NodeOutput(pipe)
