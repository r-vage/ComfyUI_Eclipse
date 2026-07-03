# Mode Bridge Set - Publisher node that creates a named bridge channel.
# When this node's mode changes, all Mode Bridge Get nodes with the same name
# receive the mode change. Connect to a Switcher, Repeater, Relay, or Mode Bridge Get.
# All behavior is handled by the frontend JavaScript (eclipse-mode-nodes.js).

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvTools_ModeBridgeSet(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Mode Bridge Set [Eclipse]",
            display_name="Mode Bridge Set",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            inputs=[],
            outputs=[
                io.AnyType.Output("oc", tooltip="Connect to Switcher, Repeater, or Mode Bridge Get."),
            ],
            description="Publisher: creates a named bridge channel. When this node's mode changes, all Mode Bridge Get nodes with the same name receive the mode change.",
        )

    @classmethod
    def execute(cls, **kwargs):
        return io.NodeOutput(None)
