from typing import Optional, Any
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

DEFAULT_DOWNSCALE = 8

# IO Checkpoint Loader v2 — adds an `audio_vae` output (LTXV/LTX2) after `vae`.
# A separate node from v1 so existing workflows keep their slot indices intact.
#
# Data-driven field definitions: key → (display_name, type_str, return_name)
_all_context_input_output_data = {
    "pipe":              ("pipe",              "pipe",    "pipe"),
    "model":             ("model",             "MODEL",   "model"),
    "clip":              ("clip",              "CLIP",    "clip"),
    "vae":               ("vae",               "VAE",     "vae"),
    "audio_vae":         ("audio_vae",         "VAE",     "audio_vae"),
    "latent":            ("latent",            "LATENT",  "latent"),
    "steps":             ("steps",             "INT",     "steps"),
    "cfg":               ("cfg",               "FLOAT",   "cfg"),
    "sampler_name":      ("sampler_name",      "*",       "sampler_name"),
    "scheduler":         ("scheduler",         "*",       "scheduler"),
    "flux_guidance":     ("flux_guidance",      "FLOAT",   "flux_guidance"),
    "clip_skip":         ("clip_skip",         "INT",     "clip_skip"),
    "width":             ("width",             "INT",     "width"),
    "height":            ("height",            "INT",     "height"),
    "batch_size":        ("batch_size",        "INT",     "batch_size"),
    "model_name":        ("model_name",        "STRING",  "model_name"),
    "vae_name":          ("vae_name",          "STRING",  "vae_name"),
    "lora_names":        ("lora_names",        "STRING",  "lora_names"),
    "seed":              ("seed",              "INT",     "seed"),
}

_force_input_types = {"INT", "STRING", "FLOAT", "BOOLEAN"}

def _get_v3_type(type_str) -> Any:
    # Retrieve types dynamically to prevent crash if not yet initialized at import time
    v3_type_map = {
        "pipe":    io.Custom("PIPE"),
        "MODEL":   getattr(io, "Model", None) or io.Custom("MODEL"),
        "CLIP":    getattr(io, "Clip", None) or io.Custom("CLIP"),
        "VAE":     getattr(io, "Vae", None) or io.Custom("VAE"),
        "LATENT":  getattr(io, "Latent", None) or io.Custom("LATENT"),
        "INT":     getattr(io, "Int", None) or io.Custom("INT"),
        "FLOAT":   getattr(io, "Float", None) or io.Custom("FLOAT"),
        "STRING":  getattr(io, "String", None) or io.Custom("STRING"),
        "BOOLEAN": getattr(io, "Boolean", None) or io.Custom("BOOLEAN"),
        "*":       getattr(io, "AnyType", None) or io.Custom("*"),
    }
    return v3_type_map.get(type_str, io.Custom(type_str))

def _build_v3_inputs():
    inputs = []
    for key, (name, type_str, _) in _all_context_input_output_data.items():
        v3_type = _get_v3_type(type_str)
        tooltip = f"Optional input for '{name}'."
        kwargs = {"optional": True, "tooltip": tooltip}
        if type_str in _force_input_types:
            kwargs["force_input"] = True
        inputs.append(v3_type.Input(name, **kwargs))
    return inputs

def _build_v3_outputs():
    outputs = []
    for key, (_, type_str, ret_name) in _all_context_input_output_data.items():
        v3_type = _get_v3_type(type_str)
        outputs.append(v3_type.Output(ret_name))
    return outputs

def new_context(pipe: Optional[dict] = None, **kwargs) -> dict:
    # Direct inputs override pipe values; pipe fills in anything not provided.
    context = pipe if pipe is not None else {}
    new_ctx: dict[str, Any] = {}
    for key in _all_context_input_output_data:
        if key == "pipe":
            continue
        v = kwargs.get(key, None)
        if v is not None:
            new_ctx[key] = v
        elif key in context:
            new_ctx[key] = context[key]
        else:
            new_ctx[key] = None
    return new_ctx

def get_context_return_tuple(ctx: dict) -> tuple:
    tup_list: list[Any] = [ctx]
    for key in _all_context_input_output_data:
        if key == "pipe":
            continue
        tup_list.append(ctx.get(key))
    return tuple(tup_list)


class RvPipe_IO_CheckpointLoader_v2(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="IO Checkpoint Loader v2 [Eclipse]",
            display_name="IO Checkpoint Loader v2",
            category=CATEGORY.MAIN.value + CATEGORY.PIPE.value,
            description="Checkpoint loader pipe IO with an additional audio_vae output (LTXV/LTX2).",
            inputs=_build_v3_inputs(),
            outputs=_build_v3_outputs(),
        )

    @classmethod
    def execute(cls, pipe=None, **kwargs) -> io.NodeOutput:
        ctx = new_context(pipe, **kwargs)

        # Derive width/height from latent when not explicitly set
        latent = ctx.get("latent")
        if latent is not None and isinstance(latent, dict) and "samples" in latent:
            latent_shape = latent["samples"].shape
            downscale = latent.get("downscale_ratio_spacial", DEFAULT_DOWNSCALE)
            if ctx.get("height") is None:
                ctx["height"] = latent_shape[2] * downscale
            if ctx.get("width") is None:
                ctx["width"] = latent_shape[3] * downscale

        return io.NodeOutput(*get_context_return_tuple(ctx))
