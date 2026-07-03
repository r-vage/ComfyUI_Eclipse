from comfy_api.latest import io  # type: ignore
from ...core import CATEGORY

# Legacy wrapper — keeps old v2 node_id for workflow backward compat
# New workflows should use "Smart LM Loader [Eclipse]"
class Legacy_SmartLML_v2(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        from ..RvLoader_SmartModelLoader_LM import RvLoader_SmartModelLoader_LM
        real_schema = RvLoader_SmartModelLoader_LM.define_schema()
        return io.Schema(
            node_id="Smart Language Model Loader v2 [SmartLML]",
            display_name="⚠ Smart Language Model Loader v2",
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


class Legacy_SmartLML_v2_Eclipse(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        from ..RvLoader_SmartModelLoader_LM import RvLoader_SmartModelLoader_LM
        real_schema = RvLoader_SmartModelLoader_LM.define_schema()
        return io.Schema(
            node_id="Smart Language Model Loader v2 [Eclipse]",
            display_name="⚠ Smart Language Model Loader v2 [Eclipse]",
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
