from typing import Optional, Any
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

# v2.3: adds allow_overwrite slot
_all_context_input_output_data = {
    "pipe": ("pipe", "PIPE", "pipe"),
    "steps": ("steps", "INT", "steps"),
    "cfg": ("cfg", "FLOAT", "cfg"),
    "sampler_name": ("sampler_name", "*", "sampler_name"),
    "scheduler": ("scheduler", "*", "scheduler"),
    "guidance": ("guidance", "FLOAT", "guidance"),
    "denoise": ("denoise", "FLOAT", "denoise"),
    "sigmas_denoise": ("sigmas_denoise", "FLOAT", "sigmas_denoise"),
    "noise_strength": ("noise_strength", "FLOAT", "noise_strength"),
    "upscale_steps": ("upscale_steps", "INT", "upscale_steps"),
    "upscale_denoise": ("upscale_denoise", "FLOAT", "upscale_denoise"),
    "upscale_value": ("upscale_value", "FLOAT", "upscale_value"),
    "seed": ("seed", "INT", "seed"),
    "prompt_seed": ("prompt_seed", "INT", "prompt_seed"),
}

force_input_types = ["INT", "STRING", "FLOAT"]
force_input_names = ["sampler", "scheduler"]

def new_context(pipe: Optional[dict[Any, Any]] = None, allow_overwrite: Optional[bool] = None, **kwargs) -> dict:
    context = pipe if pipe is not None else None
    new_ctx = {}
    
    # Priority logic based on allow_overwrite flag:
    # 1. Start with the value in the pipe (or False if no pipe/flag)
    allow_overwrite_flag = False
    if context is not None and isinstance(context, dict):
        allow_overwrite_flag = context.get("_allow_overwrite", False)
        
    # 2. Overwrite the var that is passed in the pipe when it is not None
    if allow_overwrite is not None:
        allow_overwrite_flag = allow_overwrite

    # 3. Perform priority routing
    for key in _all_context_input_output_data:
        if key == "pipe":
            continue
        kwarg_value = kwargs.get(key, None)
        pipe_value = context.get(key, None) if context is not None and key in context else None

        if allow_overwrite_flag:
            new_ctx[key] = kwarg_value if kwarg_value is not None else (pipe_value if pipe_value is not None else None)
        else:
            new_ctx[key] = pipe_value if pipe_value is not None else (kwarg_value if kwarg_value is not None else None)

    # 4. Save and propagate the _allow_overwrite flag in the returned context
    new_ctx["_allow_overwrite"] = allow_overwrite_flag

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
            
    # Add the new slot at the bottom (force_input=True makes it an input socket)
    inputs.append(io.Boolean.Input("allow_overwrite", optional=True, force_input=True, tooltip="Overwrites the _allow_overwrite flag in the pipe when not None. True = direct inputs take priority, False = pipe values take priority."))
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

class RvPipe_IO_Sampler_Settings_v23(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Pipe IO Sampler Settings v2.3 [Eclipse]",
            display_name="IO Sampler Settings v2.3",
            category=CATEGORY.MAIN.value + CATEGORY.PIPE.value,
            inputs=_build_v3_inputs(),
            outputs=_build_v3_outputs(),
        )

    @classmethod
    def execute(cls, pipe=None, allow_overwrite=None, **kwargs):
        ctx = new_context(pipe, allow_overwrite=allow_overwrite, **kwargs)
        return io.NodeOutput(*get_context_return_tuple(ctx))
