from typing import Optional, Any
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

# Data-driven field definitions: key → (display_name, type_str, return_name)
_all_context_input_output_data = {
    "pipe":         ("pipe",         "pipe",    "pipe"),
    "image":        ("image",        "IMAGE",   "image"),
    "mask":         ("mask",         "MASK",    "mask"),
    "width":        ("width",        "INT",     "width"),
    "height":       ("height",       "INT",     "height"),
    "text_pos":     ("text_pos",     "STRING",  "text_pos"),
    "text_neg":     ("text_neg",     "STRING",  "text_neg"),
    "steps":        ("steps",        "INT",     "steps"),
    "cfg":          ("cfg",          "FLOAT",   "cfg"),
    "sampler_name": ("sampler_name", "*",       "sampler_name"),
    "scheduler":    ("scheduler",    "*",       "scheduler"),
    "seed":         ("seed",         "INT",     "seed"),
    "model_name":   ("model_name",   "STRING",  "model_name"),
    "base_path":    ("base_path",    "STRING",  "base_path"),
    "filepath":     ("filepath",     "STRING",  "filepath"),
    "filename":     ("filename",     "STRING",  "filename"),
    "source_name":  ("source_name",  "STRING",  "source_name"),
}

_force_input_types = {"INT", "STRING", "FLOAT", "BOOLEAN"}

def _get_v3_type(type_str) -> Any:
    # Retrieve types dynamically to prevent crash if not yet initialized at import time
    v3_type_map = {
        "pipe":    io.Custom("PIPE"),
        "IMAGE":   getattr(io, "Image", None) or io.Custom("IMAGE"),
        "MASK":    getattr(io, "Mask", None) or io.Custom("MASK"),
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


class RvPipe_IO_LoadImage(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="IO Load Image [Eclipse]",
            display_name="IO Load Image",
            category=CATEGORY.MAIN.value + CATEGORY.PIPE.value,
            inputs=_build_v3_inputs(),
            outputs=_build_v3_outputs(),
        )

    @classmethod
    def execute(cls, pipe=None, **kwargs) -> io.NodeOutput:
        ctx = new_context(pipe, **kwargs)

        # Normalize text fields (support alternate key names from different metadata formats)
        if ctx.get("text_pos") is None:
            for alt in ("text", "prompt"):
                v = (pipe or {}).get(alt)
                if v:
                    ctx["text_pos"] = v
                    break
        if ctx.get("text_neg") is None:
            for alt in ("negative", "negative_prompt"):
                v = (pipe or {}).get(alt)
                if v:
                    ctx["text_neg"] = v
                    break

        # Safe type coercion
        for int_key in ("width", "height", "steps", "seed"):
            try:
                if ctx.get(int_key) is not None:
                    ctx[int_key] = int(ctx[int_key])
            except Exception:
                ctx[int_key] = 0
        try:
            if ctx.get("cfg") is not None:
                ctx["cfg"] = float(ctx["cfg"])
        except Exception:
            ctx["cfg"] = 0.0

        return io.NodeOutput(*get_context_return_tuple(ctx))
