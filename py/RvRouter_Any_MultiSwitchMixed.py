# Independent wildcard inputs preserve mixed-type fallback branches.
from comfy_api.latest import io

from ..core import CATEGORY


def _has_value(value):
    if value is None:
        return False
    if isinstance(value, list):
        return any(item is not None and not (
            isinstance(item, (str, dict, list, tuple)) and len(item) == 0
        ) for item in value)
    return not (isinstance(value, (str, dict, tuple)) and len(value) == 0)


class RvRouter_Any_MultiSwitchMixed(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Any Multi-Switch Mixed [Eclipse]",
            display_name="Any Multi-Switch Mixed",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value,
            description=(
                "Returns the first nonempty input, from top to bottom. Inputs may have different types, "
                "such as captioned VIDEO and fallback IMAGE. Mute a higher-priority source to use the next. "
                "The destination must accept whichever type is selected. Connected active branches execute normally."
            ),
            is_input_list=True,
            inputs=[io.Autogrow.Input(
                "inputs", optional=True,
                template=io.Autogrow.TemplateNames(
                    io.AnyType.Input("value", optional=True),
                    names=[f"any_{index}" for index in range(1, 65)], min=2,
                ),
            )],
            outputs=[io.AnyType.Output("output", is_output_list=True,
                                      tooltip="Selected value, unchanged. The downstream node must support its type.")],
        )

    @classmethod
    def execute(cls, inputs: io.Autogrow.Type = None):
        inputs = inputs or {}
        for index in range(1, 65):
            value = inputs.get(f"any_{index}")
            if _has_value(value):
                # Retain ComfyUI's list envelope and the original tensor/VIDEO.
                return io.NodeOutput(value if isinstance(value, list) else [value])
        return io.NodeOutput([None])
