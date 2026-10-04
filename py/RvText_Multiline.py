from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY


class RvText_Multiline(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="String Multiline [Eclipse]",
            display_name="String Multiline",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            inputs=[
                io.String.Input(
                    "input_string",
                    optional=True,
                    force_input=True,
                    tooltip="Optional text preserved verbatim and prepended with one separating space when both inputs are non-empty.",
                ),
                io.String.Input(
                    "string",
                    multiline=True,
                    default="",
                    tooltip="Multiline text preserved exactly, including newlines, blank lines and whitespace.",
                ),
            ],
            outputs=[
                io.String.Output("string"),
            ],
        )

    @classmethod
    def execute(cls, string=None, input_string=None):
        # Preserve both inputs verbatim; only add the existing prefix separator.
        parts = [part for part in (input_string, string) if isinstance(part, str) and part]
        return io.NodeOutput(" ".join(parts))
