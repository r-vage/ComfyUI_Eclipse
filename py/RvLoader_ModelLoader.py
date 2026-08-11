from __future__ import annotations

from comfy_api.latest import io  # type: ignore

# Model Loader [Eclipse] — Standalone model loader with direct outputs
#
# Supports: Standard Checkpoints, UNet, Nunchaku (Flux/Qwen/ZImage), GGUF
# Features: LoRA (3 slots), BlockSwap, baked CLIP/VAE from checkpoints
# Output: model, clip, vae, model_name directly
from ..core import CATEGORY
from ..core.model_loader.loading import LoadRequest, load_request
from ..core.model_loader.validation import LoaderValidationError
from ..core.model_loader_common import get_model_loader_inputs

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
        try:
            LoadRequest.from_kwargs(kwargs)
        except (LoaderValidationError, TypeError) as error:
            return str(error)
        return True

    @classmethod
    def execute(cls, **kwargs):
        result = load_request(LoadRequest.from_kwargs(kwargs), _LOG_PREFIX)
        return io.NodeOutput(
            result.model,
            result.clip,
            result.vae,
            result.audio_vae,
            result.model_name,
        )
