#

import json
from typing import List, Dict, Any

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "ShowAny"

try:
    import torch  # type: ignore
    import numpy as np

    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


class RvTools_ShowAny(io.ComfyNode):
    # Display any type of data as formatted text output without image preview or stop review support.
    # Accepts any input type and converts it to readable text format.

    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("any_type")
        return io.Schema(
            node_id="Show Any [Eclipse]",
            display_name="Show Any",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            inputs=[
                io.MatchType.Input("anything", template=type_template, optional=True),
            ],
            outputs=[
                io.MatchType.Output(type_template, id="output", is_output_list=True),
            ],
            is_input_list=True,
            is_output_node=True,
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
        )

    @classmethod
    def execute(cls, **kwargs):
        tag = f"{_LOG_PREFIX}"
        # Convert any input to displayable text format.
        # Handles strings, numbers, lists, dicts, tensors, and other objects.
        original_values = []  # Keep original values for pass-through
        display_values = []  # Create display strings for UI

        if "anything" in kwargs:
            anything_val = kwargs["anything"]
            # log.debug(tag, f"Input: anything={log.format_value(anything_val)}")
            for val in anything_val:
                # Always store the original value for output
                original_values.append(val)
                try:
                    # Create display string based on type
                    if isinstance(val, str):
                        display_values.append(val)
                    elif isinstance(val, (int, float, bool)):
                        display_values.append(str(val))
                    elif isinstance(val, list):
                        display_values.append(str(val))
                    # Handle torch tensors
                    elif TORCH_AVAILABLE and isinstance(val, torch.Tensor):
                        torch.set_printoptions(edgeitems=6)
                        tensor_str = str(val)
                        torch.set_printoptions()
                        display_values.append(tensor_str)
                    # Handle tuples (conditioning is often a tuple)
                    elif isinstance(val, tuple):
                        if (
                            len(val) > 0
                            and TORCH_AVAILABLE
                            and isinstance(val[0], torch.Tensor)
                        ):
                            # This is likely conditioning or similar
                            tensor_shapes = [
                                (
                                    list(t.shape)
                                    if isinstance(t, torch.Tensor)
                                    else type(t).__name__
                                )
                                for t in val
                            ]
                            tuple_info = f"Tuple[{len(val)} items: {tensor_shapes}]"
                            display_values.append(tuple_info)
                        else:
                            display_values.append(str(val))
                    # Handle dicts
                    elif isinstance(val, dict):
                        display_values.append(str(val))
                    else:
                        # Try to serialize to JSON
                        try:
                            json_val = json.dumps(val)
                            display_values.append(json_val)
                        except (TypeError, ValueError):
                            # If JSON serialization fails, use string representation
                            display_values.append(str(val))
                except Exception as e:
                    # Fallback to type name for display
                    display_values.append(f"<{type(val).__name__}>")

        # Ensure all display values are strings for UI
        string_values = []
        for v in display_values:
            if isinstance(v, str):
                string_values.append(v)
            else:
                string_values.append(str(v))

        # Build UI response
        ui_response = {"text": string_values}

        # log.debug(tag, f"Output: text={string_values}")
        # Return original values for pass-through, display strings for UI
        return io.NodeOutput(original_values, ui=ui_response)
