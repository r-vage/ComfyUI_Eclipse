# Detailer Pipe Passer — pass a detailer pipe through with fixed type.

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY


class RvRouter_DetailerPipe_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Detailer Pipe Passer [Eclipse]",
            display_name="Detailer Pipe Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.Custom("DETAILER_PIPE").Input("pipe", tooltip="Detailer pipe input to be passed through."),
            ],
            outputs=[
                io.Custom("DETAILER_PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def execute(cls, pipe):
        return io.NodeOutput(pipe)
