# ruff: noqa: N999
# Historical sampler-pipe schemas retained with their original output indices.
from comfy_api.latest import io

from ...core import CATEGORY

_CHANNELS = (
    ("steps", io.Int),
    ("cfg", io.Float),
    ("sampler_name", io.AnyType),
    ("scheduler", io.AnyType),
    ("guidance", io.Float),
    ("denoise", io.Float),
    ("sigmas_denoise", io.Float),
    ("noise_strength", io.Float),
    ("upscale_steps", io.Int),
    ("upscale_denoise", io.Float),
    ("upscale_value", io.Float),
    ("seed", io.Int),
)


class _LegacySamplerPipe(io.ComfyNode):
    version = "2.1"

    @classmethod
    def channels(cls):
        return _CHANNELS + ((("prompt_seed", io.Int),) if cls.version != "2.1" else ())

    @classmethod
    def define_schema(cls):
        inputs = [io.Custom("PIPE").Input("pipe", optional=True)]
        outputs = [io.Custom("PIPE").Output("pipe")]
        for name, dtype in cls.channels():
            options = {"optional": True}
            if dtype in (io.Int, io.Float):
                options["force_input"] = True
            inputs.append(dtype.Input(name, **options))
            outputs.append(dtype.Output(name))
        if cls.version == "2.3":
            inputs.append(io.Boolean.Input("allow_overwrite", optional=True, force_input=True))
        return io.Schema(
            node_id=f"Pipe IO Sampler Settings v{cls.version} [Eclipse]",
            display_name=f"IO Sampler Settings v{cls.version} (Legacy)",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="Preserves historical upscale and seed channels and socket order for existing workflows.",
            inputs=inputs,
            outputs=outputs,
        )

    @classmethod
    def execute(cls, pipe=None, **kwargs):
        source = pipe if pipe is not None else {}
        overwrite = source.get("_allow_overwrite", False)
        if cls.version == "2.3" and kwargs.get("allow_overwrite") is not None:
            overwrite = kwargs["allow_overwrite"]
        context = {}
        for name, _dtype in cls.channels():
            primary, fallback = (kwargs.get(name), source.get(name)) if overwrite else (source.get(name), kwargs.get(name))
            context[name] = primary if primary is not None else fallback
        if cls.version == "2.3" or "_allow_overwrite" in source:
            context["_allow_overwrite"] = overwrite
        return io.NodeOutput(context, *(context[name] for name, _dtype in cls.channels()))


class RvPipe_LegacySamplerSettings21(_LegacySamplerPipe):
    version = "2.1"


class RvPipe_LegacySamplerSettings22(_LegacySamplerPipe):
    version = "2.2"


class RvPipe_LegacySamplerSettings23(_LegacySamplerPipe):
    version = "2.3"
