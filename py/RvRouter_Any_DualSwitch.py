from comfy_api.latest import io #type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "AnyDualSwitch"

class RvRouter_Any_DualSwitch(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Any Dual-Switch [Eclipse]",
            display_name="Any Dual-Switch",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value,
            inputs=[
                io.Int.Input("Input", default=1, min=1, max=2, tooltip="Select which input to output (1 or 2)."),
                io.AnyType.Input("input1", optional=True, tooltip="First input (any type)."),
                io.AnyType.Input("input2", optional=True, tooltip="Second input (any type)."),
            ],
            outputs=[
                io.AnyType.Output("*"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, Input, input1=None, input2=None):
        tag = f"{_LOG_PREFIX} #{cls.hidden.unique_id}"
        log.debug(tag, f"Passing input{Input}")
        if Input == 1:
            return io.NodeOutput(input1)
        else:
            return io.NodeOutput(input2)
