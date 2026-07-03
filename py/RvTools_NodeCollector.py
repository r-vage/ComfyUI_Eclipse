# Node Collector - Virtual node that aggregates multiple input connections
# into a single output for connecting to Fast Muter, Fast Bypasser, or Repeater.
# Based on rgthree-comfy by rgthree (https://github.com/rgthree/rgthree-comfy).
# Rewritten for ComfyUI V3 API and Vue/Nodes 2.0 compatibility.
# All behavior is handled by the frontend JavaScript (eclipse-mode-nodes.js).

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvTools_NodeCollector(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Node Collector [Eclipse]",
            display_name="Node Collector",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            inputs=[],
            outputs=[
                io.AnyType.Output("Output", tooltip="Aggregated output to Fast Muter, Bypasser, or Repeater."),
            ],
            description="Aggregates multiple node connections into a single output. Connect output to Fast Muter, Fast Bypasser, or Mode Repeater.",
        )

    @classmethod
    def execute(cls, **kwargs):
        return io.NodeOutput(None)
