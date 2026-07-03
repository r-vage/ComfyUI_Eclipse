from typing import Optional, Any
from comfy_api.latest import io #type: ignore
from ...core import CATEGORY

# original code is taken from rgthree context utils
# v2: adds denoise_upscale channel
_all_context_input_output_data = {
    "pipe": ("pipe", "pipe", "pipe"),
    "steps": ("steps", "INT", "steps"),
    "cfg": ("cfg", "FLOAT", "cfg"),
    "sampler_name": ("sampler_name", "*", "sampler_name"),
    "scheduler": ("scheduler", "*", "scheduler"),
    "guidance": ("guidance", "FLOAT", "guidance"),
    "denoise": ("denoise", "FLOAT", "denoise"),
    "denoise_upscale": ("denoise_upscale", "FLOAT", "denoise_upscale"),
    "upscale_value": ("upscale_value", "FLOAT", "upscale_value"),
    "sigmas_denoise": ("sigmas_denoise", "FLOAT", "sigmas_denoise"),
    "noise_strength": ("noise_strength", "FLOAT", "noise_strength"),
    "seed": ("seed", "INT", "seed"),
}

force_input_types = ["INT", "STRING", "FLOAT"]
force_input_names = ["sampler", "scheduler"]

def new_context(pipe: Optional[dict[Any, Any]] = None, **kwargs) -> dict:
    # Priority logic based on _allow_overwrite flag:
    # _allow_overwrite=False (default): pipe values take priority
    # _allow_overwrite=True: direct inputs (kwargs) take priority
    context = pipe if pipe is not None else None
    new_ctx = {}
    allow_overwrite = False
    if context is not None and isinstance(context, dict):
        allow_overwrite = context.get("_allow_overwrite", False)
    
    for key in _all_context_input_output_data:
        if key == "pipe":
            continue
        kwarg_value = kwargs.get(key, None)
        pipe_value = context.get(key, None) if context is not None and key in context else None
        
        if allow_overwrite:
            new_ctx[key] = kwarg_value if kwarg_value is not None else (pipe_value if pipe_value is not None else None)
        else:
            new_ctx[key] = pipe_value if pipe_value is not None else (kwarg_value if kwarg_value is not None else None)
    
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

# V3 type mapping
_V3_TYPE_MAP = {"INT": io.Int, "FLOAT": io.Float, "STRING": io.String}

def _build_v3_inputs():
    inputs = []
    for key, (name, type_str, _) in _all_context_input_output_data.items():
        tooltip = f"Optional input for channel '{name}'."
        if key == "pipe":
            inputs.append(io.Custom("PIPE").Input(name, optional=True, tooltip=tooltip))
        elif type_str == "*":
            inputs.append(io.AnyType.Input(name, optional=True, tooltip=tooltip))
        elif type_str in _V3_TYPE_MAP:
            force = type_str in force_input_types or name in force_input_names
            inputs.append(_V3_TYPE_MAP[type_str].Input(name, optional=True, force_input=force, tooltip=tooltip))
        else:
            inputs.append(io.Custom(type_str).Input(name, optional=True, tooltip=tooltip))
    return inputs

def _build_v3_outputs():
    outputs = []
    for key, (_, type_str, ret_name) in _all_context_input_output_data.items():
        if key == "pipe":
            outputs.append(io.Custom("PIPE").Output(ret_name))
        elif type_str == "*":
            outputs.append(io.AnyType.Output(ret_name))
        elif type_str in _V3_TYPE_MAP:
            outputs.append(_V3_TYPE_MAP[type_str].Output(ret_name))
        else:
            outputs.append(io.Custom(type_str).Output(ret_name))
    return outputs

class RvPipe_IO_Sampler_Settings_v2(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Pipe IO Sampler Settings v2 [Eclipse]",
            display_name="⚠ IO Sampler Settings v2",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
            inputs=_build_v3_inputs(),
            outputs=_build_v3_outputs(),
        )

    @classmethod
    def execute(cls, pipe=None, **kwargs):
        ctx = new_context(pipe, **kwargs)
        return io.NodeOutput(*get_context_return_tuple(ctx))
