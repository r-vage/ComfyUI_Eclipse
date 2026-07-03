# Basic Pipe Passer — pass a basic pipe through with fixed type.

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY


class RvRouter_BasicPipe_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Basic Pipe Passer [Eclipse]",
            display_name="Basic Pipe Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.Custom("BASIC_PIPE").Input("pipe", tooltip="Basic pipe input to be passed through."),
            ],
            outputs=[
                io.Custom("BASIC_PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def execute(cls, pipe):
        return io.NodeOutput(pipe)
