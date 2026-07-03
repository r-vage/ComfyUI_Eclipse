# Fast Mode Switcher - Virtual node for cycling connected nodes between Active / Mute / Bypass.
# Unified replacement for separate Fast Muter + Fast Bypasser when both are needed.
# All behavior is handled by the frontend JavaScript (eclipse-mode-nodes.js).

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvTools_FastModeSwitcher(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Fast Mode Switcher [Eclipse]",
            display_name="Fast Mode Switcher",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            inputs=[],
            outputs=[
                io.AnyType.Output("oc", tooltip="Optional connection to other mode nodes."),
            ],
            description="Cycle connected nodes between Active, Mute, and Bypass modes. Click to cycle through states.",
        )

    @classmethod
    def execute(cls, **kwargs):
        return io.NodeOutput(None)
