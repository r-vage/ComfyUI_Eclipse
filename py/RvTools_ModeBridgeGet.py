# Mode Bridge Get - Subscriber node that receives mode changes from a named
# Mode Bridge Set. Select a bridge name from the combo to subscribe.
# Has no outputs — controls connected input nodes via bridgeLocalPropagate.
# All behavior is handled by the frontend JavaScript (eclipse-mode-nodes.js).

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvTools_ModeBridgeGet(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Mode Bridge Get [Eclipse]",
            display_name="Mode Bridge Get",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            inputs=[],
            outputs=[],
            description="Subscriber: receives mode changes from the Mode Bridge Set with the same name. Connect nodes to this node's inputs to control their mode.",
        )

    @classmethod
    def execute(cls, **kwargs):
        return io.NodeOutput()
