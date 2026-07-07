import torch  # type: ignore
import comfy.supported_models  # type: ignore
from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "CondZeroOut"

# Model architecture → base token length mapping
_MODEL_TOKEN_MAP = [
    (comfy.supported_models.SD15, 77),
    (comfy.supported_models.SD20, 77),
    (comfy.supported_models.SDXL, 77),
    (comfy.supported_models.SDXLRefiner, 77),
    (comfy.supported_models.SD3, 154),
    (comfy.supported_models.Stable_Cascade_C, 77),
    (comfy.supported_models.Flux, 256),
    (comfy.supported_models.FluxSchnell, 256),
    (comfy.supported_models.AuraFlow, 256),
    (comfy.supported_models.HiDream, 128),
    (comfy.supported_models.LTXV, 128),
    (comfy.supported_models.HunyuanVideo, 128),
    (comfy.supported_models.HunyuanVideoI2V, 128),
    (comfy.supported_models.HunyuanVideoSkyreelsI2V, 128),
]

# Optional models (may not exist in older ComfyUI)
for _name, _tokens in [
    ("Chroma", 256),
    ("WAN21_T2V", 512),
    ("WAN21_I2V", 512),
    ("ZImage", 256),
    ("Lumina2", 256),
    ("HunyuanDiT", 256),
    ("GenmoMochi", 256),
    ("CosmosT2V", 512),
    ("CosmosI2V", 512),
    ("CosmosT2IPredict2", 512),
    ("CosmosI2VPredict2", 512),
    ("CogVideoX_T2V", 226),
]:
    if hasattr(comfy.supported_models, _name):
        _MODEL_TOKEN_MAP.append((getattr(comfy.supported_models, _name), _tokens))


def _get_base_tokens_from_model(model) -> int:
    # Detect base token length from model config.
    # Returns 0 if model type is unknown.
    config = model.model.model_config
    for model_cls, tokens in _MODEL_TOKEN_MAP:
        if isinstance(config, model_cls):
            return tokens
    return 0


class RvCond_ConditioningZeroOut(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Conditioning Zero Out [Eclipse]",
            display_name="Conditioning Zero Out",
            category=CATEGORY.MAIN.value + CATEGORY.CONDITIONING.value,
            description="Zeros out and optionally truncates conditioning tensors. "
            "Connect a model to auto-detect the correct token length, "
            "or set max_tokens manually. 0 = keep original size (pure zero-out).",
            inputs=[
                io.Conditioning.Input(
                    "conditioning", tooltip="The conditioning to zero out."
                ),
                io.Model.Input(
                    "model",
                    optional=True,
                    tooltip="Optional model input to auto-detect base token length for truncation.",
                ),
                io.Int.Input(
                    "max_tokens",
                    default=0,
                    min=0,
                    max=4096,
                    step=1,
                    tooltip="Max token length. 0 = auto from model (or keep original if no model). Overrides model detection when > 0.",
                ),
            ],
            outputs=[
                io.Conditioning.Output(
                    "conditioning", tooltip="Zeroed-out conditioning."
                ),
            ],
        )

    @classmethod
    def execute(cls, conditioning, model=None, max_tokens=0):
        # Determine truncation length
        truncate = max_tokens
        if truncate == 0 and model is not None:
            truncate = _get_base_tokens_from_model(model)
            if truncate > 0:
                config_name = type(model.model.model_config).__name__
                log.debug(
                    _LOG_PREFIX,
                    f"Auto-detected {config_name}: base tokens = {truncate}",
                )
            else:
                log.debug(
                    _LOG_PREFIX, "Model connected but type unknown, no truncation"
                )
        elif truncate > 0:
            log.debug(_LOG_PREFIX, f"Manual max_tokens override: {truncate}")
        else:
            log.debug(_LOG_PREFIX, "No model, no max_tokens — pure zero-out")

        c = []
        for i, t in enumerate(conditioning):
            d = t[1].copy()
            pooled_output = d.get("pooled_output", None)
            if pooled_output is not None:
                d["pooled_output"] = torch.zeros_like(pooled_output)
                log.debug(
                    _LOG_PREFIX,
                    f"Cond [{i}]: zeroed pooled_output {tuple(pooled_output.shape)}",
                )
            if truncate > 0 and t[0].shape[-2] > truncate:
                # Truncate: create smaller zero tensor
                channels = t[0].shape[-1]
                log.debug(
                    _LOG_PREFIX,
                    f"Cond [{i}]: truncating {t[0].shape[-2]} → {truncate} tokens (channels={channels})",
                )
                n = [
                    torch.zeros(
                        (1, truncate, channels), dtype=t[0].dtype, device=t[0].device
                    ),
                    d,
                ]
            else:
                log.debug(
                    _LOG_PREFIX, f"Cond [{i}]: zero-out, shape {tuple(t[0].shape)}"
                )
                n = [torch.zeros_like(t[0]), d]
            c.append(n)
        return io.NodeOutput(c)
