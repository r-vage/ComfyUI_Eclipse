#
# Smart Sampler Settings v3 — single-seed driven sampler configuration (simplified upscale)

from typing import Any
from ..core import CATEGORY, SAMPLERS_COMFY, SCHEDULERS_ANY, SLIDER_DISPLAY
from ..core.logger import log
from comfy_api.latest import io  # type: ignore

_LOG_PREFIX = "SmartSamplerSettings"

# All features the user can toggle
FEATURE_OPTIONS = [
    "allow_overwrite",
    "sampler",
    "scheduler",
    "steps",
    "cfg",
    "guidance",
    "denoise",
    "seed",
    "noise_injection",
    "upscale",
]


class RvSettings_SmartSamplerSettings(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Smart Sampler Settings [Eclipse]",
            display_name="Smart Sampler Settings",
            category=CATEGORY.MAIN.value + CATEGORY.SETTINGS.value,
            inputs=[
                io.String.Input(
                    "features",
                    default="sampler,scheduler,steps,cfg,denoise",
                    socketless=True,
                    tooltip="Comma-separated feature list. JS combo-chip replaces this widget.",
                ),
                io.Boolean.Input(
                    "allow_overwrite",
                    default=False,
                    label_on="yes",
                    label_off="no",
                    tooltip="When enabled, allows direct inputs to IO nodes to overwrite this node's values.",
                ),
                io.Combo.Input(
                    "sampler_name",
                    options=SAMPLERS_COMFY,
                    tooltip="Select the sampler algorithm.",
                ),
                io.Combo.Input(
                    "scheduler",
                    options=SCHEDULERS_ANY,
                    tooltip="Select the scheduler algorithm.",
                ),
                io.Int.Input(
                    "steps",
                    default=30,
                    min=1,
                    max=150,
                    step=1,
                    display_mode=SLIDER_DISPLAY,
                ),
                io.Float.Input(
                    "cfg",
                    default=5.00,
                    min=1.0,
                    max=30.0,
                    step=0.1,
                    display_mode=SLIDER_DISPLAY,
                ),
                io.Float.Input(
                    "guidance",
                    default=3.50,
                    min=0,
                    max=10.0,
                    step=0.1,
                    display_mode=SLIDER_DISPLAY,
                ),
                io.Float.Input(
                    "denoise",
                    default=1.0,
                    min=0,
                    max=1.0,
                    step=0.01,
                    display_mode=SLIDER_DISPLAY,
                ),
                io.Float.Input(
                    "sigmas_denoise",
                    default=0.45,
                    min=0,
                    max=1.0,
                    step=0.01,
                    display_mode=SLIDER_DISPLAY,
                ),
                io.Float.Input(
                    "noise_strength",
                    default=0.50,
                    min=0,
                    max=1.0,
                    step=0.01,
                    display_mode=SLIDER_DISPLAY,
                ),
                io.Float.Input(
                    "upscale_value",
                    default=1.5,
                    min=0.1,
                    max=10.0,
                    step=0.1,
                    display_mode=SLIDER_DISPLAY,
                ),
                io.Int.Input(
                    "seed",
                    default=0,
                    min=-3,
                    max=2**64 - 1,
                    tooltip="Random seed for generation.",
                ),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
            is_output_node=True,
        )

    @classmethod
    def execute(
        cls,
        features,
        sampler_name: str,
        scheduler: str,
        steps: int,
        cfg: float,
        guidance: float,
        denoise: float,
        sigmas_denoise: float,
        noise_strength: float,
        upscale_value: float,
        seed: int = 0,
        allow_overwrite: bool = True,
    ) -> io.NodeOutput:
        # Defensive: handle dict wrapper, list, and string
        if isinstance(features, dict) and "__value__" in features:
            selected = features["__value__"]
        elif isinstance(features, str):
            selected = [x.strip() for x in features.split(",") if x.strip()]
        else:
            selected = features if features else []

        # log.debug(_LOG_PREFIX, f"Selected features: {selected}")

        pipe: dict[str, Any] = {}

        if "allow_overwrite" in selected:
            pipe["_allow_overwrite"] = (
                allow_overwrite if allow_overwrite is not None else True
            )

        # Only include selected features
        if "sampler" in selected:
            pipe["sampler_name"] = sampler_name
        if "scheduler" in selected:
            pipe["scheduler"] = scheduler
        if "steps" in selected:
            pipe["steps"] = steps
        if "cfg" in selected:
            pipe["cfg"] = round(cfg, 2)
        if "guidance" in selected:
            pipe["guidance"] = round(guidance, 2)
        if "denoise" in selected:
            pipe["denoise"] = round(denoise, 2)
        if "noise_injection" in selected:
            pipe["sigmas_denoise"] = round(sigmas_denoise, 2)
            pipe["noise_strength"] = round(noise_strength, 2)
        if "upscale" in selected:
            pipe["upscale_value"] = round(upscale_value, 2)
        if "seed" in selected:
            pipe["seed"] = seed

        # log.debug(_LOG_PREFIX, f"Pipe keys: {list(pipe.keys())}")
        return io.NodeOutput(pipe)
