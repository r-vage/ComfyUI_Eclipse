from typing import Optional, Any
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

# original code is taken from rgthree context utils
_all_context_input_output_data = {
    "pipe": ("pipe", "pipe", "pipe"),
    "steps": ("steps", "INT", "steps"),
    "cfg": ("cfg", "FLOAT", "cfg"),
    "sampler_name": ("sampler_name", "STRING", "sampler_name"),
    "scheduler": ("scheduler", "STRING", "scheduler"),
    "denoise": ("denoise", "FLOAT", "denoise"),
    "clip_skip": ("clip_skip", "INT", "clip_skip"),    
    "seed": ("seed", "INT", "seed"),
    "width": ("width", "INT", "width"),
    "height": ("height", "INT", "height"),
    "text_pos": ("text_pos", "STRING", "text_pos"),
    "text_neg": ("text_neg", "STRING", "text_neg"),
    "model_name": ("model_name", "STRING", "model_name"),
    "vae_name": ("vae_name", "STRING", "vae_name"),
    "lora_names": ("lora_names", "STRING", "lora_names"),

}

force_input_types = ["INT", "STRING", "FLOAT"]
force_input_names = ["sampler_name", "scheduler"]

def new_context(pipe: Optional[dict[Any, Any]] = None, **kwargs) -> dict:
    context = pipe if pipe is not None else None
    new_ctx = {}
    for key in _all_context_input_output_data:
        if key == "pipe":
            continue
        v = kwargs.get(key, None)
        if v is not None:
            new_ctx[key] = v
        elif context is not None and key in context:
            new_ctx[key] = context[key]
        else:
            new_ctx[key] = None
    return new_ctx

def get_context_return_tuple(ctx: dict, inputs_list=None) -> tuple:
    if inputs_list is None:
        inputs_list = _all_context_input_output_data.keys()
    tup_list: list[Any] = [ctx]
    for key in inputs_list:
        if key == "pipe":
            continue
        tup_list.append(ctx[key] if ctx is not None and key in ctx else None)
    return tuple(tup_list)

# V3 type mapping for building Schema inputs/outputs
_V3_TYPE_MAP = {"INT": io.Int, "FLOAT": io.Float, "STRING": io.String}

def _build_v3_inputs():
    inputs = []
    for key, (name, type_str, _) in _all_context_input_output_data.items():
        tooltip = f"Optional input for '{name}'."
        force = type_str in force_input_types or name in force_input_names
        if key == "pipe":
            inputs.append(io.Custom("PIPE").Input(name, optional=True, tooltip=tooltip))
        elif type_str in _V3_TYPE_MAP:
            inputs.append(_V3_TYPE_MAP[type_str].Input(name, optional=True, force_input=force, tooltip=tooltip))
        else:
            inputs.append(io.Custom(type_str).Input(name, optional=True, tooltip=tooltip))
    return inputs

def _build_v3_outputs():
    outputs = []
    for key, (_, type_str, ret_name) in _all_context_input_output_data.items():
        if key == "pipe":
            outputs.append(io.Custom("PIPE").Output(ret_name))
        elif type_str in _V3_TYPE_MAP:
            outputs.append(_V3_TYPE_MAP[type_str].Output(ret_name))
        else:
            outputs.append(io.Custom(type_str).Output(ret_name))
    return outputs

class RvPipe_IO_Generation_Data(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Generation Data [Eclipse]",
            display_name="IO Generation Data",
            category=CATEGORY.MAIN.value + CATEGORY.PIPE.value,
            inputs=_build_v3_inputs(),
            outputs=_build_v3_outputs(),
        )

    @classmethod
    def execute(cls, pipe=None, **kwargs):
        ctx = new_context(pipe, **kwargs)
        return io.NodeOutput(*get_context_return_tuple(ctx))
