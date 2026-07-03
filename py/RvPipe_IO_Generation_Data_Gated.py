from typing import Any
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

# Field definitions: key -> (name, type_str, output_name)
# Same fields as Generation Data, excluding pipe (pipe is always passed through)
_gated_fields = {
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

# V3 type mapping for outputs
_V3_TYPE_MAP = {"INT": io.Int, "FLOAT": io.Float, "STRING": io.String}


def _build_gated_inputs():
    # Pipe input (required) + boolean gate per field (optional, force_input)
    inputs = [
        io.Custom("PIPE").Input("pipe", tooltip="Input context pipe containing generation data."),
    ]
    for key, (name, _, _) in _gated_fields.items():
        inputs.append(
            io.Boolean.Input(
                name,
                optional=True,
                force_input=True,
                default=True,
                tooltip=f"Gate for '{name}'. True = pass pipe value, None (muted/disconnected) = output None.",
            )
        )
    return inputs


def _build_gated_outputs():
    outputs = [io.Custom("PIPE").Output("pipe")]
    for key, (_, type_str, ret_name) in _gated_fields.items():
        if type_str in _V3_TYPE_MAP:
            outputs.append(_V3_TYPE_MAP[type_str].Output(ret_name))
        else:
            outputs.append(io.Custom(type_str).Output(ret_name))
    return outputs


def _is_empty_value(value):
    # Treat None, string "None", empty strings, and zero-ish numbers as empty/absent
    if value is None:
        return True
    if isinstance(value, str) and (value.strip() == "" or value.strip() == "None"):
        return True
    if isinstance(value, (int, float)) and value == 0:
        return True
    return False


class RvPipe_IO_Generation_Data_Gated(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Generation Data (Gated) [Eclipse]",
            display_name="IO Generation Data (Gated)",
            category=CATEGORY.MAIN.value + CATEGORY.PIPE.value,
            description="Like Generation Data but each output is gated by a boolean input. "
                        "Connect a Boolean (True) node to pass the pipe value through. "
                        "Mute/disconnect the Boolean to output None (clear the field). "
                        "Use with FastMuter + NodeModeRepeater for group control.",
            inputs=_build_gated_inputs(),
            outputs=_build_gated_outputs(),
        )

    @classmethod
    def execute(cls, pipe=None, **kwargs):
        # Build gated context: only include fields whose gate is True
        # Omit ungated fields entirely so downstream nodes see them as absent
        ctx: dict[str, Any] = {}
        pipe_data = pipe if isinstance(pipe, dict) else {}

        for key in _gated_fields:
            gate = kwargs.get(key, None)
            if gate is True:
                val = pipe_data.get(key)
                if not _is_empty_value(val):
                    ctx[key] = val
            # else: key omitted from ctx entirely

        # Build output tuple: pipe dict first, then each field in order
        result: list[Any] = [ctx]
        for key in _gated_fields:
            result.append(ctx.get(key))

        return io.NodeOutput(*result)
