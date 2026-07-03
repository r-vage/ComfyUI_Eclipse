# WAN Video Model Passer — pass a WAN video model through with fixed type.

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY


class RvRouter_WanVideoModel_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="WAN Model Passer [Eclipse]",
            display_name="WAN Model Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.Custom("WANVIDEOMODEL").Input("model", tooltip="WAN video model input to be passed through."),
            ],
            outputs=[
                io.Custom("WANVIDEOMODEL").Output("model"),
            ],
        )

    @classmethod
    def execute(cls, model):
        return io.NodeOutput(model)
