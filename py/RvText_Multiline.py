from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvText_Multiline(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="String Multiline [Eclipse]",
            display_name="String Multiline",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            inputs=[
                io.String.Input("input_string", optional=True, force_input=True, tooltip="Optional string input to prepend to the multiline content."),
                io.String.Input("string", multiline=True, default="", tooltip="Multiline string input. Lines are joined with spaces."),
            ],
            outputs=[
                io.String.Output("string"),
            ],
        )

    @classmethod
    def execute(cls, string=None, input_string=None):
        # Outputs the input multiline string as a single joined string.
        parts = []

        # Add optional input string if provided
        if isinstance(input_string, str) and input_string.strip():
            parts.append(input_string.strip())

        # Process multiline content
        if isinstance(string, str) and string.strip():
            lines = string.strip().split('\n')
            lines = [line.strip() for line in lines if line.strip()]
            if lines:
                parts.append(" ".join(lines))

        if not parts:
            return io.NodeOutput("")

        return io.NodeOutput(" ".join(parts))