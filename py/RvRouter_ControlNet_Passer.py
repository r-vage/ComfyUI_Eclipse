# ControlNet Passer — pass a control net through with fixed type.

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY


class RvRouter_ControlNet_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="ControlNet Passer [Eclipse]",
            display_name="ControlNet Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.ControlNet.Input("control_net", tooltip="ControlNet input to be passed through."),
            ],
            outputs=[
                io.ControlNet.Output("control_net"),
            ],
        )

    @classmethod
    def execute(cls, control_net):
        return io.NodeOutput(control_net)
