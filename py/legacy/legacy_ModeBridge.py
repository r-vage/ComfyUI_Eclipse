# Mode Bridge - DEPRECATED: Use Mode Bridge Set + Mode Bridge Get instead.
# Virtual node that syncs mode state across subgraph boundaries
# by matching named bridges. When named, all bridges with the same name across
# all graphs sync their mode instantly. Connect target nodes as dynamic inputs
# for selective mode control — no group fallback (unlike the Repeater).
# All behavior is handled by the frontend JavaScript (eclipse-mode-nodes.js).

from comfy_api.latest import io #type: ignore
from ...core import CATEGORY

class RvTools_ModeBridge(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Mode Bridge [Eclipse]",
            display_name="⚠ Mode Bridge",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            inputs=[],
            outputs=[
                io.AnyType.Output("oc", tooltip="Connect to Fast Muter/Bypasser, Repeater, Relay, or another Mode Bridge."),
            ],
            description="[DEPRECATED — use Mode Bridge Set + Mode Bridge Get instead] Syncs mode (Mute/Bypass/Active) across subgraph boundaries by name.",
            is_deprecated=True,
        )

    @classmethod
    def execute(cls, **kwargs):
        return io.NodeOutput(None)
