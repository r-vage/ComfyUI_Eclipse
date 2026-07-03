# SEGS Passer — pass SEGS through with fixed type.

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY


class RvRouter_Segs_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="SEGS Passer [Eclipse]",
            display_name="SEGS Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.SEGS.Input("segs", tooltip="SEGS input to be passed through."),
            ],
            outputs=[
                io.SEGS.Output("segs"),
            ],
        )

    @classmethod
    def execute(cls, segs):
        return io.NodeOutput(segs)
