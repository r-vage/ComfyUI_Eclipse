import re
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

# Inline pattern to avoid regex_patterns dependency
RE_NEWLINES = re.compile(r'[\r\n]+', re.IGNORECASE)

class RvText_ReplaceString(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Replace String [Eclipse]",
            display_name="Replace String",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            inputs=[
                io.String.Input("String", default="", tooltip="Input string to process."),
                io.String.Input("Regex", default="", tooltip="Regular expression pattern to match."),
                io.String.Input("ReplaceWith", default="", tooltip="Replacement string for matches."),
            ],
            outputs=[
                io.String.Output("string"),
            ],
        )

    @classmethod
    def execute(cls, String, Regex, ReplaceWith):
        # Replace substrings in String using Regex, then remove line breaks for prompt output.
        replaced = re.sub(Regex, ReplaceWith, String)
        replaced = RE_NEWLINES.sub(" ", replaced)
        return io.NodeOutput(replaced)