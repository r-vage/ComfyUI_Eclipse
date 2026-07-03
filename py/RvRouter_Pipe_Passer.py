# Pipe Passer — pass a pipe through with fixed type.

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY


class RvRouter_Pipe_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Pipe Passer [Eclipse]",
            display_name="Pipe Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.Custom("PIPE").Input("pipe", tooltip="Pipe input to be passed through."),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def execute(cls, pipe):
        return io.NodeOutput(pipe)
