from comfy_api.latest import io  # type: ignore
from ...core import CATEGORY

# Legacy no-op wrapper — the Pipe Out LM Advanced Options node was removed in v3.
# The Smart LM Loader now has all advanced options built-in.
# This wrapper prevents workflow errors by outputting an empty pipe dict.

class Legacy_PipeOut_LM_AdvancedOptions(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Pipe Out LM Advanced Options [SmartLML]",
            display_name="⚠ Pipe Out LM Advanced Options",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            inputs=[],
            outputs=[
                io.Custom("SMARTLM_ADVANCED_PIPE").Output("pipe", tooltip="No-op — advanced options are now built into Smart LM Loader"),
            ],
            description="DEPRECATED — this node is a no-op. Advanced options are now built into the Smart LM Loader node. Replace before v4.0.0 — all legacy nodes will be removed then.",
        )

    @classmethod
    def execute(cls, **kwargs):
        return io.NodeOutput({})


class Legacy_PipeOut_LM_AdvancedOptions_Eclipse(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Pipe Out LM Advanced Options [Eclipse]",
            display_name="⚠ Pipe Out LM Advanced Options [Eclipse]",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            inputs=[],
            outputs=[
                io.Custom("SMARTLM_ADVANCED_PIPE").Output("pipe", tooltip="No-op — advanced options are now built into Smart LM Loader"),
            ],
            description="DEPRECATED — this node is a no-op. Advanced options are now built into the Smart LM Loader node. Replace before v4.0.0 — all legacy nodes will be removed then.",
        )

    @classmethod
    def execute(cls, **kwargs):
        return io.NodeOutput({})
