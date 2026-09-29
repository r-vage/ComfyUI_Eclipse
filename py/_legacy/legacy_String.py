# ruff: noqa: N999
from comfy_api.latest import io  # type: ignore
from ...core import CATEGORY


class RvLogic_String(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="String [Eclipse]",
            display_name="⚠ String (Deprecated)",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="Retained for existing workflows. Use ComfyUI's built-in Text for new workflows.",
            inputs=[
                io.String.Input("value", default="", tooltip="String value to output."),
            ],
            outputs=[
                io.String.Output("string"),
            ],
        )

    @classmethod
    def execute(cls, value=""):
        # Outputs a string value for logic operations or workflow branching.
        if not isinstance(value, str):
            value = ""
        return io.NodeOutput(value)
