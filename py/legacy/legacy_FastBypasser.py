# Fast Bypasser - Virtual node for toggling bypass on connected nodes.
# DEPRECATED — replaced by Fast Mode Toggle [Eclipse] (unified mute/bypass).
# Based on rgthree-comfy by rgthree (https://github.com/rgthree/rgthree-comfy).
# All behavior is handled by the frontend JavaScript (eclipse-mode-nodes.js).

from comfy_api.latest import io #type: ignore
from ...core import CATEGORY

class RvTools_FastBypasser(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Fast Bypasser [Eclipse]",
            display_name="⚠ Fast Bypasser",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            inputs=[],
            outputs=[
                io.AnyType.Output("oc", tooltip="Optional connection to other mode nodes."),
            ],
            description="DEPRECATED — replaced by 'Fast Mode Toggle' (unified mute/bypass). All legacy nodes will be removed in v4.0.0.",
        )

    @classmethod
    def execute(cls, **kwargs):
        return io.NodeOutput(None)
