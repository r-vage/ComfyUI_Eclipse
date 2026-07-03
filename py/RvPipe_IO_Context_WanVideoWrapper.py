from typing import Any
from ..core import CATEGORY
from comfy_api.latest import io #type: ignore

# original code is taken from rgthree context utils
_all_context_input_output_data = {
    "pipe": ("pipe", "pipe", "context"),

    "torch_compile_args": ("torch_compile_args", "WANCOMPILEARGS", "torch_compile_args"),
    "block_swap_args": ("block_swap_args", "BLOCKSWAPARGS", "block_swap_args"),
    "vram_management_args": ("vram_management_args", "VRAM_MANAGEMENTARGS", "vram_management_args"),     
    "loras": ("loras", "WANVIDLORA", "loras"),

    "model": ("model", "WANVIDEOMODEL", "model"),
    "clip": ("clip", "WANTEXTENCODER", "clip"),
    "vae": ("vae", "WANVAE", "vae"),

    "text_embeds": ("text_embeds", "WANVIDEOTEXTEMBEDS", "text_embeds"),
    "image_embeds": ("image_embeds", "WANVIDIMAGE_EMBEDS", "image_embeds"),
        
    "images_input": ("images_input", "IMAGE", "images_input"),
    "images_ref_start": ("images_ref_start", "IMAGE", "images_ref_start"),
    "images_ref_new": ("images_ref_new", "IMAGE", "images_ref_new"),
    "images_ref_end": ("images_ref_end", "IMAGE", "images_ref_end"),
    "images_1st": ("images_1st", "IMAGE", "images_1st"),
    "images_loop": ("images_loop", "IMAGE", "images_loop"),
    
    "images_pp1": ("images_pp1", "IMAGE", "images_pp1"),
    "images_pp2": ("images_pp2", "IMAGE", "images_pp2"),
    "images_pp3": ("images_pp3", "IMAGE", "images_pp3"),

    "images_output": ("images_output", "IMAGE", "images_output"),

    "mask_1": ("mask_1", "MASK", "mask_1"),
    "mask_2": ("mask_2", "MASK", "mask_2"),
    "mask_3": ("mask_3", "MASK", "mask_3"),

    "steps": ("steps", "INT", "steps"),
    "cfg": ("cfg", "FLOAT", "cfg"),
    "sampler_name": ("sampler_name", "*", "sampler_name"),
    "scheduler": ("scheduler", "*", "scheduler"),
    "denoise": ("denoise", "FLOAT", "denoise"),
    "seed": ("seed", "INT", "seed"),

    "width": ("width", "INT", "width"),
    "height": ("height", "INT", "height"),

    "text_pos": ("text_pos", "STRING", "text_pos"),
    "text_i2p": ("text_i2p", "STRING", "text_i2p"),
    "text_neg": ("text_neg", "STRING", "text_neg"),

    "frame_rate": ("frame_rate", "FLOAT", "frame_rate"),
    "frame_load_cap": ("frame_load_cap", "INT", "frame_load_cap"),
    "context_length": ("context_length", "INT", "context_length"),
    "overlap": ("overlap", "INT", "overlap"),
    "skip_first_frames": ("skip_first_frames", "INT", "skip_first_frames"),
    "select_every_nth": ("select_every_nth", "INT", "select_every_nth"),
    "loop_idx": ("loop_idx", "INT", "loop_idx"),

    "audio": ("audio", "AUDIO", "audio"),

    "any_1": ("any_1", "*", "any_1"),
    "any_2": ("any_2", "*", "any_2"),

    "path": ("path", "STRING", "path"),
    "purge": ("purge", "BOOLEAN", "purge"),
}

_force_input_types = {"INT", "STRING", "FLOAT", "BOOLEAN"}

def _get_v3_type(type_str) -> Any:
    # Retrieve types dynamically to prevent crash if not yet initialized at import time
    v3_type_map = {
        "pipe":    io.Custom("PIPE"),
        "LATENT":  getattr(io, "Latent", None) or io.Custom("LATENT"),
        "IMAGE":   getattr(io, "Image", None) or io.Custom("IMAGE"),
        "MASK":    getattr(io, "Mask", None) or io.Custom("MASK"),
        "INT":     getattr(io, "Int", None) or io.Custom("INT"),
        "FLOAT":   getattr(io, "Float", None) or io.Custom("FLOAT"),
        "STRING":  getattr(io, "String", None) or io.Custom("STRING"),
        "BOOLEAN": getattr(io, "Boolean", None) or io.Custom("BOOLEAN"),
        "*":       getattr(io, "AnyType", None) or io.Custom("*"),
    }
    return v3_type_map.get(type_str, io.Custom(type_str))

def _build_v3_inputs():
    inputs = []
    for key, (display_name, type_str, return_name) in _all_context_input_output_data.items():
        v3_type = _get_v3_type(type_str)
        tooltip = f"Optional input for '{display_name}'."
        kwargs = {"optional": True, "tooltip": tooltip}
        if type_str in _force_input_types:
            kwargs["force_input"] = True
        inputs.append(v3_type.Input(display_name, **kwargs))
    return inputs

def _build_v3_outputs():
    outputs = []
    for key, (display_name, type_str, return_name) in _all_context_input_output_data.items():
        v3_type = _get_v3_type(type_str)
        outputs.append(v3_type.Output(return_name))
    return outputs

def new_context(pipe=None, **kwargs):
    # Creates a new context from the provided data, with an optional base ctx to start.
    if isinstance(pipe, tuple):
        context = pipe[0] if pipe else {}
    elif isinstance(pipe, dict):
        context = pipe
    else:
        context = {}
    new_ctx = {}
    for key in _all_context_input_output_data:
        if key == "pipe":
            continue
        if key in context:
            new_ctx[key] = context[key]
    for key in _all_context_input_output_data:
        if key == "pipe":
            continue
        v = kwargs.get(key, None)
        if v is not None:
            new_ctx[key] = v
    return new_ctx

def get_context_return_tuple(ctx, inputs_list=None):
    # Returns a tuple for returning in the order of the inputs list.
    if inputs_list is None:
        inputs_list = _all_context_input_output_data.keys()
    tup_list = [ctx]
    for key in inputs_list:
        if key == "pipe":
            continue
        tup_list.append(ctx.get(key, None))
    return tuple(tup_list)

class RvPipe_IO_Context_WanVideoWrapper(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Context Video (WVW) [Eclipse]",
            display_name="IO Context Video (WVW)",
            category=CATEGORY.MAIN.value + CATEGORY.PIPE.value,
            inputs=_build_v3_inputs(),
            outputs=_build_v3_outputs(),
        )

    @classmethod
    def execute(cls, pipe=None, **kwargs) -> io.NodeOutput:
        ctx = new_context(pipe, **kwargs)
        return io.NodeOutput(*get_context_return_tuple(ctx))
