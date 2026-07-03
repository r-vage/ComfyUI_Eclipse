from __future__ import annotations

# Model Loader [Eclipse] — Standalone model loader with direct outputs
#
# Supports: Standard Checkpoints, UNet, Nunchaku (Flux/Qwen/ZImage), GGUF
# Features: LoRA (3 slots), BlockSwap, baked CLIP/VAE from checkpoints
# Output: model, clip, vae, model_name directly

from ..core import CATEGORY
from ..core.logger import log
from ..core.model_loader_common import get_model_loader_inputs, load_model
from comfy_api.latest import io  # type: ignore

_LOG_PREFIX = "Model Loader"


class RvLoader_ModelLoader(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Model Loader [Eclipse]",
            display_name="Model Loader",
            category=CATEGORY.MAIN.value + CATEGORY.LOADER.value,
            description="Standalone model loader with direct model/clip/vae outputs. Supports checkpoints, UNet, Nunchaku, and GGUF with LoRA and BlockSwap.",
            inputs=get_model_loader_inputs(),
            outputs=[
                io.Custom("MODEL").Output("model"),
                io.Custom("CLIP").Output("clip"),
                io.Custom("VAE").Output("vae"),
                io.Custom("VAE").Output("audio_vae"),
                io.String.Output("model_name"),
            ],
        )

    @classmethod
    def validate_inputs(cls, **kwargs):
        return True

    @classmethod
    def execute(cls, **kwargs):
        loaded_model, loaded_clip, loaded_vae, loaded_audio_vae, checkpoint_name, _lora_string = load_model(_LOG_PREFIX, **kwargs)
        return io.NodeOutput(loaded_model, loaded_clip, loaded_vae, loaded_audio_vae, checkpoint_name)
