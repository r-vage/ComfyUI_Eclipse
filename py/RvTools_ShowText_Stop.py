import json
import torch  # type: ignore
from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "ShowText_Stop"


class RvTools_ShowText_Stop(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Show Text [Stop] [Eclipse]",
            display_name="Show Text [Stop]",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            description="Universal text preview — accepts any input type, converts it to a "
            "readable string, and displays it in a DOM widget. The text output "
            "persists in subgraphs. Inspired by ComfyUI core PreviewAny.",
            inputs=[
                io.AnyType.Input("source", tooltip="Any value to preview as text."),
                io.Boolean.Input(
                    "stop_review",
                    default=False,
                    label_on="active",
                    label_off="bypass",
                    socketless=True,
                    display_name="Stop (Result Review)",
                ),
            ],
            outputs=[
                io.String.Output("text"),
            ],
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, source=None, stop_review=False):
        tag = f"{_LOG_PREFIX}"
        # log.debug(tag, f"Input: source={log.format_value(source)}")
        # Convert any input type to a readable string (mirrors PreviewAny logic).
        torch.set_printoptions(edgeitems=6)

        # Ensure source is a list under is_input_list=True
        if not isinstance(source, (list, tuple)):
            source = [source]

        value_list = []
        for item in source:
            if isinstance(item, str):
                value = item
            elif isinstance(item, (int, float, bool)):
                value = str(item)
            elif item is None:
                value = "None"
            else:
                try:
                    value = json.dumps(item, indent=4)
                except Exception:
                    try:
                        value = str(item)
                    except Exception:
                        value = "source exists, but could not be serialized."
            value_list.append(value)

        torch.set_printoptions()

        unique_id = cls.hidden.unique_id
        extra_pnginfo = cls.hidden.extra_pnginfo

        # Persist displayed text into workflow metadata so it survives reload.
        if unique_id is not None and extra_pnginfo is not None:
            uid = unique_id[0] if isinstance(unique_id, list) else unique_id
            pnginfo = (
                extra_pnginfo[0] if isinstance(extra_pnginfo, list) else extra_pnginfo
            )
            if isinstance(pnginfo, dict) and "workflow" in pnginfo:
                node = next(
                    (
                        x
                        for x in pnginfo["workflow"].get("nodes", [])
                        if str(x.get("id")) == uid
                    ),
                    None,
                )
                if node is not None:
                    node["widgets_values"] = value_list

        # Handle Stop (Result Review)
        is_stop = (
            stop_review[0] if isinstance(stop_review, (list, tuple)) else stop_review
        )
        if is_stop:
            import nodes

            log.debug(tag, "Workflow review stop triggered. Interrupting execution.")
            nodes.interrupt_processing()

        # Return a single string if there is only one item, otherwise a list
        if len(value_list) == 1:
            ret_val = value_list[0]
        else:
            ret_val = value_list

        # log.debug(tag, f"Output: text={ret_val}")
        return io.NodeOutput(ret_val, ui={"text": value_list})
