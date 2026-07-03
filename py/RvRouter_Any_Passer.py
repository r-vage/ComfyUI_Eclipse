from comfy_api.latest import io #type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "AnyPasser"

class RvRouter_Any_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Any Passer [Eclipse]",
            display_name="Any Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value,
            inputs=[
                io.AnyType.Input("input", tooltip="Any input to be passed through."),
            ],
            outputs=[
                io.AnyType.Output("output"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, input):
        tag = f"{_LOG_PREFIX} #{cls.hidden.unique_id}"
        log.debug(tag, "Passing input")
        return io.NodeOutput(input)