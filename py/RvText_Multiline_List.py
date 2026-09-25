from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY


class RvText_Multiline_List(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="String Multiline List [Eclipse]",
            display_name="String Multiline List",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            inputs=[
                io.String.Input(
                    "input_string",
                    optional=True,
                    force_input=True,
                    tooltip="Optional text prepended verbatim to the string output with one separating space; trimmed before each list item.",
                ),
                io.String.Input(
                    "string",
                    multiline=True,
                    default="",
                    tooltip="String output preserves all text and whitespace. List output contains trimmed, non-empty lines.",
                ),
            ],
            outputs=[
                io.String.Output("string"),
                io.String.Output("string_list", is_output_list=True),
            ],
        )

    @classmethod
    def execute(cls, string=None, input_string=None):
        # Preserve full text independently of the normalized list items.
        parts = [part for part in (input_string, string) if isinstance(part, str) and part]
        full_string = " ".join(parts)
        input_prefix = (
            input_string.strip()
            if isinstance(input_string, str) and input_string.strip()
            else ""
        )

        # Process multiline content
        content_lines = []
        if isinstance(string, str) and string.strip():
            content_lines = [
                line.strip() for line in string.strip().split("\n") if line.strip()
            ]

        if input_prefix and content_lines:
            list_items = [f"{input_prefix} {line}" for line in content_lines]
        else:
            list_items = content_lines or [input_prefix]

        return io.NodeOutput(full_string, list_items)
