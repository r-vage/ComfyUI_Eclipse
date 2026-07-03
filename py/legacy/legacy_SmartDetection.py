from comfy_api.latest import io  # type: ignore
from ...core import CATEGORY

# Legacy wrapper — keeps old node_id for workflow backward compat
# New workflows should use "Smart Detection [Eclipse]"
class Legacy_SmartDetection(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        from ..RvLoader_SmartDetection import RvLoader_Detection
        real_schema = RvLoader_Detection.define_schema()
        return io.Schema(
            node_id="Smart Detection [SML]",
            display_name="⚠ Smart Detection [SML]",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            inputs=real_schema.inputs,
            outputs=real_schema.outputs,
            hidden=real_schema.hidden,
            is_output_node=real_schema.is_output_node,
            description="DEPRECATED — use 'Smart Detection [Eclipse]' instead. Replace it before v4.0.0 — all legacy nodes will be removed then.",
        )

    @classmethod
    def execute(cls, **kwargs):
        from ..RvLoader_SmartDetection import RvLoader_Detection
        return RvLoader_Detection.execute(**kwargs)
