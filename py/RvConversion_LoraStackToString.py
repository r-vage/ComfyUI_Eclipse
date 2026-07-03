from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvConversion_LoraStackToString(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Lora Stack to String [Eclipse]",
            display_name="Lora Stack to String",
            category=CATEGORY.MAIN.value + CATEGORY.CONVERSION.value,
            inputs=[
                io.Custom("LORA_STACK").Input("lora_stack", tooltip="List of LoRA tuples (STR, FLOAT1, FLOAT2). Returns a space-separated string in <lora:...> format."),
                io.Boolean.Input("remove_weight", default=False, tooltip="If true, removes the last 2 elements from each tuple, using only the LoRA name."),
            ],
            outputs=[
                io.String.Output("LoRA string"),
            ],
        )

    @classmethod
    def execute(cls, lora_stack, remove_weight):
        # Type safety: handle None and non-iterable input
        if lora_stack is None or not hasattr(lora_stack, "__iter__"):
            return io.NodeOutput("")
        try:
            if remove_weight:
                output = ' '.join(
                    f"<lora:{str(tup[0])}>"
                    for tup in lora_stack
                    if isinstance(tup, (list, tuple)) and len(tup) >= 1
                )
            else:
                output = ' '.join(
                    f"<lora:{str(tup[0])}:{str(tup[1])}:{str(tup[2])}>"
                    for tup in lora_stack
                    if isinstance(tup, (list, tuple)) and len(tup) >= 3
                )
            return io.NodeOutput(output)
        except Exception:
            return io.NodeOutput("")

