from comfy_api.latest import io  # type: ignore
from ...core import CATEGORY

# Legacy wrapper — keeps old v3 node_id for workflow backward compat
# The v3 [SmartLML] id existed briefly before the 3.0.0 rename to [SML]
class Legacy_SmartLML_v3(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        from ..RvLoader_SmartModelLoader_LM import RvLoader_SmartModelLoader_LM
        real_schema = RvLoader_SmartModelLoader_LM.define_schema()
        return io.Schema(
            node_id="Smart Language Model Loader v3 [SmartLML]",
            display_name="⚠ Smart Language Model Loader v3",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            inputs=real_schema.inputs,
            outputs=real_schema.outputs,
            hidden=real_schema.hidden,
            is_output_node=real_schema.is_output_node,
            description="DEPRECATED — use 'Smart LM Loader [Eclipse]' instead. Replace it before v4.0.0 — all legacy nodes will be removed then.",
        )

    @classmethod
    def execute(cls, **kwargs):
        from ..RvLoader_SmartModelLoader_LM import RvLoader_SmartModelLoader_LM
        return RvLoader_SmartModelLoader_LM.execute(**kwargs)
