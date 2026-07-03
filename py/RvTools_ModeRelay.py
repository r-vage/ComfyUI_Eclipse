# Mode Relay - Virtual node that propagates its mode to all nodes in its
# overlapping group, AND to any connected input nodes (for chaining).
# Unlike the Repeater, both behaviors are always active — no mutual exclusion.
# All behavior is handled by the frontend JavaScript (eclipse-mode-nodes.js).

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvTools_ModeRelay(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Mode Relay [Eclipse]",
            display_name="Mode Relay",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            inputs=[],
            outputs=[
                io.AnyType.Output("oc", tooltip="Connect to Repeater, another Relay, or Fast Muter/Bypasser."),
            ],
            description="Relays mode changes (Mute/Bypass/Active) to all nodes in its overlapping group. Connect to a Repeater for cascading control across multiple groups.",
        )

    @classmethod
    def execute(cls, **kwargs):
        return io.NodeOutput(None)
