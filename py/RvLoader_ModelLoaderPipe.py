from __future__ import annotations

from comfy_api.latest import io  # type: ignore

# Model Loader Pipe [Eclipse] — Standalone model loader with pipe output
#
# Supports: Standard Checkpoints, UNet, Nunchaku (Flux/Qwen/ZImage), GGUF
# Features: LoRA (3 slots), BlockSwap, baked CLIP/VAE from checkpoints
# Output: pipe dict compatible with Pipe Out Checkpoint Loader
from ..core import CATEGORY
from ..core.model_loader.loading import LoadRequest, load_request
from ..core.model_loader.pipes import OMIT, build_pipe
from ..core.model_loader.validation import LoaderValidationError
from ..core.model_loader_common import get_model_loader_inputs

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
        try:
            LoadRequest.from_kwargs(kwargs)
        except (LoaderValidationError, TypeError) as error:
            return str(error)
        return True

    @classmethod
    def execute(cls, **kwargs):
        model_type = kwargs.get("model_type", "Standard Checkpoint")
        enable_clip_layer = bool(kwargs.get("enable_clip_layer", True))
        stop_at_clip_layer = kwargs.get("stop_at_clip_layer", -2)
        is_standard = model_type == "Standard Checkpoint"
        is_nunchaku = model_type == "Nunchaku Flux"

        result = load_request(LoadRequest.from_kwargs(kwargs), _LOG_PREFIX)

        # ── Build pipe ──

        pipe = build_pipe(
            model=result.model,
            model_name=result.model_name,
            is_nunchaku=is_nunchaku,
            lora_names=result.lora_names,
            clip=result.clip if result.clip is not None else OMIT,
            vae=result.vae if result.vae is not None else OMIT,
            audio_vae=result.audio_vae if result.audio_vae is not None else OMIT,
            clip_skip=(
                stop_at_clip_layer if (is_standard and enable_clip_layer) else OMIT
            ),
        )

        return io.NodeOutput(pipe)
