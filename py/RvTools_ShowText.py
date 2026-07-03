#

import json

import torch #type: ignore
from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY


class RvTools_ShowText(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Show Text [Eclipse]",
            display_name="Show Text",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            description="Universal text preview — accepts any input type, converts it to a "
                        "readable string, and displays it in a DOM widget. The text output "
                        "persists in subgraphs. Inspired by ComfyUI core PreviewAny.",
            inputs=[
                io.AnyType.Input("source",
                    tooltip="Any value to preview as text."),
            ],
            outputs=[
                io.String.Output("text"),
            ],
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, source=None):
        # Convert any input type to a readable string (mirrors PreviewAny logic).
        torch.set_printoptions(edgeitems=6)
        if isinstance(source, str):
            value = source
        elif isinstance(source, (int, float, bool)):
            value = str(source)
        elif source is None:
            value = "None"
        else:
            try:
                value = json.dumps(source, indent=4)
            except Exception:
                try:
                    value = str(source)
                except Exception:
                    value = "source exists, but could not be serialized."
        torch.set_printoptions()

        unique_id = cls.hidden.unique_id
        extra_pnginfo = cls.hidden.extra_pnginfo

        # Persist displayed text into workflow metadata so it survives reload.
        if unique_id is not None and extra_pnginfo is not None:
            uid = unique_id[0] if isinstance(unique_id, list) else unique_id
            pnginfo = extra_pnginfo[0] if isinstance(extra_pnginfo, list) else extra_pnginfo
            if isinstance(pnginfo, dict) and "workflow" in pnginfo:
                node = next(
                    (
                        x for x in pnginfo["workflow"].get("nodes", [])
                        if str(x.get("id")) == uid
                    ),
                    None,
                )
                if node is not None:
                    node["widgets_values"] = [value]

        return io.NodeOutput(value, ui={"text": (value,)})
