# ruff: noqa: N999
import sys
from comfy_api.latest import io  # type: ignore
from ...core import CATEGORY


class RvLogic_Integer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Integer [Eclipse]",
            display_name="⚠ Integer (Deprecated)",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="Retained for existing workflows. Use ComfyUI's built-in Int for new workflows.",
            inputs=[
                io.Int.Input(
                    "value",
                    default=1,
                    min=-sys.maxsize,
                    max=sys.maxsize,
                    step=1,
                    tooltip="Integer value to output.",
                ),
            ],
            outputs=[
                io.Int.Output("int"),
            ],
        )

    @classmethod
    def execute(cls, value):
        # Outputs an integer value for logic operations or workflow branching.
        if not isinstance(value, int):
            value = 1
        return io.NodeOutput(value)
