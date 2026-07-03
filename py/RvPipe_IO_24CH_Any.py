from typing import Optional, Any
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

# original code is taken from rgthree context utils
_all_context_input_output_data = {
    "pipe": ("pipe", "pipe", "pipe"),
}
for i in range(1, 25):
    _all_context_input_output_data[f"any{i}"] = (f"any{i}", "*", f"any{i}")

def new_context(pipe: Optional[dict[Any, Any]] = None, **kwargs) -> dict:
    # Creates a new context from the provided data, with an optional base pipe to start.
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
    # Returns a tuple for returning in the order of the inputs list.
    if inputs_list is None:
        inputs_list = _all_context_input_output_data.keys()
    tup_list: list[Any] = [ctx]
    for key in inputs_list:
        if key == "pipe":
            continue
        tup_list.append(ctx[key] if ctx is not None and key in ctx else None)
    return tuple(tup_list)

class RvPipe_IO_24CH_Any(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        inputs = [io.Custom("PIPE").Input("pipe", optional=True, tooltip="Optional pipe context.")]
        outputs = [io.Custom("PIPE").Output("pipe")]
        for i in range(1, 25):
            name = f"any{i}"
            inputs.append(io.AnyType.Input(name, optional=True, tooltip=f"Optional input for channel '{name}'. Accepts any type."))
            outputs.append(io.AnyType.Output(name))
        return io.Schema(
            node_id="Pipe 24CH Any [Eclipse]",
            display_name="IO Pipe 24CH Any",
            category=CATEGORY.MAIN.value + CATEGORY.PIPE.value,
            inputs=inputs,
            outputs=outputs,
        )

    @classmethod
    def execute(cls, pipe=None, **kwargs):
        ctx = new_context(pipe, **kwargs)
        return io.NodeOutput(*get_context_return_tuple(ctx))
