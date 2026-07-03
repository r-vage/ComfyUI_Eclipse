import re
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

# Inline pattern to avoid regex_patterns dependency
RE_NEWLINES = re.compile(r'[\r\n]+', re.IGNORECASE)

class RvConversion_MergeStrings(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Merge Strings [Eclipse]",
            display_name="Merge Strings",
            category=CATEGORY.MAIN.value + CATEGORY.CONVERSION.value,
            inputs=[
                io.Int.Input("inputcount", default=2, min=1, max=64, step=1, socketless=True, tooltip="Number of string inputs to merge. Only the first 'inputcount' string_X inputs will be used."),
                io.String.Input("Delimiter", default=", ", tooltip="Delimiter to use between strings when merging. Use \\n for newline."),
                io.Boolean.Input("return_as_list", default=False, tooltip="If true, return list of individual strings; if false, return single merged string."),
                io.String.Input("string_1", force_input=True, default="", optional=True, tooltip="String input #1."),
                io.String.Input("string_2", force_input=True, default="", optional=True, tooltip="String input #2."),
            ],
            outputs=[
                io.String.Output("string", is_output_list=True),
            ],
        )

    @classmethod
    def execute(cls, inputcount, Delimiter=", ", return_as_list=False, **kwargs):
        text_inputs = []

        # Handle special case for literal newlines
        if Delimiter in ("\n", "\\n"):
            Delimiter = "\n"

        # Collect and process strings from the first 'inputcount' inputs
        for i in range(1, min(inputcount, 64) + 1):
            key = f"string_{i}"
            v = kwargs.get(key, "")
            if isinstance(v, str):
                v = v.strip()
                v = v.rstrip('.,;:!?')
                if v:
                    text_inputs.append(v)

        if return_as_list:
            return io.NodeOutput(text_inputs)
        else:
            merged_text = Delimiter.join(text_inputs)
            merged_text = RE_NEWLINES.sub(" ", merged_text)
            return io.NodeOutput([merged_text])