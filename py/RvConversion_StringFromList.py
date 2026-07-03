from comfy_api.latest import io #type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "Convert"
def wrapIndex(index, length):
    # Calculate wrapped index and number of wraps
    if length <= 0:
        log.error(_LOG_PREFIX, "Invalid list length, returning 0.")
        return 0, 0
        
    # Convert to integer and handle wrap-around
    index = int(index)
    index_mod = ((index % length) + length) % length  # Handles negative indices correctly
    wraps = index // length if length > 0 else 0
    return index_mod, wraps

class RvConversion_StringFromList(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="String from List [Eclipse]",
            display_name="String from List",
            category=CATEGORY.MAIN.value + CATEGORY.CONVERSION.value,
            is_input_list=True,
            inputs=[
                io.String.Input("list_input", force_input=True, tooltip="List of strings to select from."),
                io.Int.Input("index", default=0, min=-999, max=999, step=1, tooltip="Index to select (supports wrap-around)."),
            ],
            outputs=[
                io.String.Output("list_item", display_name="list item", is_output_list=True),
                io.Int.Output("size"),
                io.Int.Output("wraps", is_output_list=True),
            ],
        )

    @classmethod
    def execute(cls, list_input, index):
        # Selects a string from a list by index, with wrap-around and reporting of list size and wraps.
        if not isinstance(list_input, (list, str)) or not list_input:
            return io.NodeOutput([], 0, [])
        if isinstance(list_input, str):
            list_input = [list_input]
        length = len(list_input)
        wraps_list = []
        item_list = []
        indices = index if isinstance(index, list) else [index]
        for i in indices:
            if not isinstance(i, int):
                i = 0
            index_mod, wraps = wrapIndex(i, length)
            if 0 <= index_mod < length:
                wraps_list.append(wraps)
                item_list.append(list_input[index_mod])
            else:
                wraps_list.append(0)
                item_list.append("")
        return io.NodeOutput(item_list, length, wraps_list)