#

import json
from typing import List, Dict, Any

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY

try:
    import torch  # type: ignore
    import numpy as np
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


def _format_tensor(tensor):
    # Format tensor for display - show actual values for small tensors, summary for large ones
    shape = list(tensor.shape)
    dtype = tensor.dtype
    device = tensor.device

    # Calculate total elements
    total_elements = tensor.numel()

    # For very small tensors (<=20 elements), show full data
    if total_elements <= 20:
        tensor_str = str(tensor)
        return f"Tensor(shape={shape}, dtype={dtype}, device={device})\n{tensor_str}"

    # For small tensors (<=100 elements), show summary
    elif total_elements <= 100:
        # Convert to numpy for better formatting
        np_array = tensor.cpu().numpy()
        with np.printoptions(precision=4, suppress=True, threshold=100):
            tensor_str = str(np_array)
        return f"Tensor(shape={shape}, dtype={dtype}, device={device})\n{tensor_str}"

    # For larger tensors, show shape, stats, and sample
    else:
        # Get statistics
        min_val = tensor.min().item()
        max_val = tensor.max().item()
        mean_val = tensor.float().mean().item()

        # Get a small sample from the tensor (first few elements)
        if tensor.ndim == 1:
            sample = tensor[:5]
        elif tensor.ndim == 2:
            sample = tensor[:3, :3]
        elif tensor.ndim == 3:
            sample = tensor[:2, :2, :2]
        else:  # 4D or higher
            sample = tensor[:1, :2, :2, :2]

        sample_str = str(sample.cpu().numpy())

        return (f"Tensor(shape={shape}, dtype={dtype}, device={device})\n"
               f"Stats: min={min_val:.4f}, max={max_val:.4f}, mean={mean_val:.4f}\n"
               f"Sample:\n{sample_str}\n...")


class RvTools_ShowAny(io.ComfyNode):
    # Display any type of data as formatted text output without image preview or stop review support.
    # Accepts any input type and converts it to readable text format.

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Show Any [Eclipse]",
            display_name="Show Any",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            inputs=[
                io.AnyType.Input("anything", optional=True),
            ],
            outputs=[
                io.AnyType.Output("output", is_output_list=True),
            ],
            is_input_list=True,
            is_output_node=True,
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
        )

    @classmethod
    def execute(cls, **kwargs):
        # Convert any input to displayable text format.
        # Handles strings, numbers, lists, dicts, tensors, and other objects.
        original_values = []  # Keep original values for pass-through
        display_values = []   # Create display strings for UI

        if "anything" in kwargs:
            for val in kwargs['anything']:
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
                        display_values.append(_format_tensor(val))
                    # Handle tuples (conditioning is often a tuple)
                    elif isinstance(val, tuple):
                        if len(val) > 0 and TORCH_AVAILABLE and isinstance(val[0], torch.Tensor):
                            # This is likely conditioning or similar
                            tensor_shapes = [list(t.shape) if isinstance(t, torch.Tensor) else type(t).__name__ for t in val]
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

        # Return original values for pass-through, display strings for UI
        return io.NodeOutput(original_values, ui=ui_response)
