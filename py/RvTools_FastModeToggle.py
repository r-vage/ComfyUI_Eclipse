# Fast Mode Toggle - Virtual node for toggling connected nodes between Active and Mute/Bypass.
# Unified replacement for the legacy Fast Muter + Fast Bypasser nodes.
# The mode (Mute or Bypass) is selectable per-node from the right-click context menu.
# All behavior is handled by the frontend JavaScript (eclipse-mode-nodes.js).

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvTools_FastModeToggle(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Fast Mode Toggle [Eclipse]",
            display_name="Fast Mode Toggle",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            inputs=[],
            outputs=[
                io.AnyType.Output("oc", tooltip="Optional connection to other mode nodes."),
            ],
            description="Toggle connected nodes between Active and Mute or Bypass. Mode is selectable from the right-click context menu (default: Bypass).",
        )

    @classmethod
    def execute(cls, **kwargs):
        return io.NodeOutput(None)
