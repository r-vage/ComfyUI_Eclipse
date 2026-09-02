# Fast Mode Toggle Native - Virtual node for toggling connected nodes between
# Active and Mute/Bypass with promotable native Boolean widgets.
# All behavior is handled by the frontend JavaScript (eclipse-mode-nodes.js).

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY


class RvTools_FastModeToggleNative(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Fast Mode Toggle Native [Eclipse]",
            display_name="Fast Mode Toggle Native",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            inputs=[],
            outputs=[
                io.AnyType.Output(
                    "oc", tooltip="Optional connection to other mode nodes."
                ),
            ],
            description="Toggle connected nodes between Active and Mute or Bypass with native Boolean controls that can be promoted to subgraphs.",
        )

    @classmethod
    def execute(cls, **kwargs):
        return io.NodeOutput(None)
