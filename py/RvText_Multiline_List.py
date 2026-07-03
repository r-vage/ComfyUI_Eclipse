from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvText_Multiline_List(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="String Multiline List [Eclipse]",
            display_name="String Multiline List",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            inputs=[
                io.String.Input("input_string", optional=True, force_input=True, tooltip="Optional string input to prepend to the multiline content."),
                io.String.Input("string", multiline=True, default="", tooltip="Multiline string input. Splits into a list of lines and returns the full string joined by spaces."),
            ],
            outputs=[
                io.String.Output("string"),
                io.String.Output("string_list", is_output_list=True),
            ],
        )

    @classmethod
    def execute(cls, string=None, input_string=None):
        # Outputs the input multiline string as a single joined string and as a list of lines.
        lines = []

        # Add optional input string as first line if provided
        if isinstance(input_string, str) and input_string.strip():
            lines.append(input_string.strip())

        # Process multiline content
        if isinstance(string, str) and string.strip():
            content_lines = string.strip().split('\n')
            lines.extend(line.strip() for line in content_lines if line.strip())

        # If no valid lines found, return empty
        if not lines:
            return io.NodeOutput("", [""])

        joined_string = " ".join(lines)
        return io.NodeOutput(joined_string, lines)