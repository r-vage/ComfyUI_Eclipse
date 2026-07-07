# ControlNet Passer — pass a control net through with fixed type.

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY


class RvRouter_ControlNet_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("control_net", [io.ControlNet])
        return io.Schema(
            node_id="ControlNet Passer [Eclipse]",
            display_name="ControlNet Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.MatchType.Input(
                    "control_net",
                    template=type_template,
                    tooltip="ControlNet input to be passed through.",
                ),
            ],
            outputs=[
                io.MatchType.Output(type_template, id="control_net"),
            ],
        )

    @classmethod
    def execute(cls, control_net):
        return io.NodeOutput(control_net)
