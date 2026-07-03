# Node Mode Repeater - Virtual node that propagates its mode (Mute/Bypass/Active)
# to all connected input nodes.
# Based on rgthree-comfy by rgthree (https://github.com/rgthree/rgthree-comfy).
# Rewritten for ComfyUI V3 API and Vue/Nodes 2.0 compatibility.
# All behavior is handled by the frontend JavaScript (eclipse-mode-nodes.js).

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvTools_NodeModeRepeater(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Mute / Bypass Repeater [Eclipse]",
            display_name="Mute / Bypass Repeater",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            inputs=[],
            outputs=[
                io.AnyType.Output("oc", tooltip="Optional connection to Fast Muter/Bypasser."),
            ],
            description="Propagates its mode (Mute/Bypass/Active) to all connected input nodes. Use Mode Relay for group-based control. Optional output connects to Fast Muter/Bypasser.",
        )

    @classmethod
    def execute(cls, **kwargs):
        return io.NodeOutput(None)
