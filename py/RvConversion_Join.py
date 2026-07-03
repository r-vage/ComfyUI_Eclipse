import torch #type: ignore
import re
from ..core import CATEGORY
from ..core.logger import log
from comfy_api.latest import io #type: ignore

# Inline pattern to avoid regex_patterns dependency
RE_NEWLINES = re.compile(r'[\r\n]+', re.IGNORECASE)
from typing import Any, Tuple

_LOG_PREFIX = "Join"


def _join_images(inputs):
    # Join IMAGE tensors into a batch, resizing to match first image dimensions
    tensors = []
    for img in inputs:
        if isinstance(img, torch.Tensor) and img.ndim == 4:
            tensors.append(img)
    
    if not tensors:
        return (None,)
    
    target_height = tensors[0].shape[1]
    target_width = tensors[0].shape[2]
    
    resized_tensors = []
    for tensor in tensors:
        if tensor.shape[1] != target_height or tensor.shape[2] != target_width:
            tensor_bchw = tensor.permute(0, 3, 1, 2)
            resized = torch.nn.functional.interpolate(
                tensor_bchw, 
                size=(target_height, target_width), 
                mode='bilinear', 
                align_corners=False
            )
            tensor = resized.permute(0, 2, 3, 1)
        resized_tensors.append(tensor)
    
    result = torch.cat(resized_tensors, dim=0)
    return (result,)


def _join_masks(inputs):
    # Join MASK tensors into a batch, resizing to match first mask dimensions
    tensors = []
    for mask in inputs:
        if isinstance(mask, torch.Tensor):
            if mask.ndim == 2:
                mask = mask.unsqueeze(0)
            elif mask.ndim == 4:
                mask = mask.squeeze(0)
            tensors.append(mask)
    
    if not tensors:
        return (None,)
    
    target_height = tensors[0].shape[1]
    target_width = tensors[0].shape[2]
    
    resized_tensors = []
    for tensor in tensors:
        if tensor.shape[1] != target_height or tensor.shape[2] != target_width:
            tensor_bchw = tensor.unsqueeze(1)
            resized = torch.nn.functional.interpolate(
                tensor_bchw, 
                size=(target_height, target_width), 
                mode='bilinear', 
                align_corners=False
            )
            tensor = resized.squeeze(1)
        resized_tensors.append(tensor)
    
    result = torch.cat(resized_tensors, dim=0)
    return (result,)


def _join_strings(inputs, delimiter: str):
    # Join STRING values with delimiter
    if delimiter in ("\n", "\\n"):
        delimiter = "\n"
    
    text_inputs = []
    for v in inputs:
        if isinstance(v, str):
            v = v.strip()
            v = v.rstrip('.,;:!?')
            if v:
                text_inputs.append(v)
    
    if not text_inputs:
        return ("",)
    
    merged_text = delimiter.join(text_inputs)
    merged_text = RE_NEWLINES.sub(" ", merged_text)
    return (merged_text,)


def _join_primitives(inputs, delimiter: str):
    # Join INT/FLOAT/LIST values as comma-separated string
    if delimiter in ("\n", "\\n"):
        delimiter = "\n"
    
    text_inputs = []
    for v in inputs:
        if v is not None:
            text_inputs.append(str(v))
    
    if not text_inputs:
        return ("",)
    
    merged_text = delimiter.join(text_inputs)
    return (merged_text,)


class RvConversion_Join(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Join [Eclipse]",
            display_name="Join",
            category=CATEGORY.MAIN.value + CATEGORY.CONVERSION.value,
            inputs=[
                io.Int.Input("inputcount", default=2, min=1, max=64, step=1, socketless=True, tooltip="Number of inputs to join. Only the first 'inputcount' input_X values will be used."),
                io.String.Input("delimiter", default=", ", optional=True, tooltip="Delimiter for STRING types. Use \\n for newline. Ignored for IMAGE/MASK."),
                io.AnyType.Input("input_1", optional=True, tooltip="Input #1."),
                io.AnyType.Input("input_2", optional=True, tooltip="Input #2."),
            ],
            outputs=[
                io.AnyType.Output("output"),
            ],
        )

    @classmethod
    def execute(cls, inputcount: int, delimiter: str = ", ", **kwargs) -> io.NodeOutput:
        inputs = []
        
        for i in range(1, min(inputcount, 64) + 1):
            key = f"input_{i}"
            v = kwargs.get(key)
            if v is not None:
                inputs.append(v)
        
        if not inputs:
            return io.NodeOutput(None)
        
        first_input = inputs[0]
        
        if isinstance(first_input, torch.Tensor) and first_input.ndim == 4:
            return io.NodeOutput(*_join_images(inputs))
        elif isinstance(first_input, torch.Tensor) and first_input.ndim in (2, 3):
            return io.NodeOutput(*_join_masks(inputs))
        elif isinstance(first_input, str):
            return io.NodeOutput(*_join_strings(inputs, delimiter))
        elif isinstance(first_input, int):
            return io.NodeOutput(*_join_primitives(inputs, delimiter))
        elif isinstance(first_input, float):
            return io.NodeOutput(*_join_primitives(inputs, delimiter))
        elif isinstance(first_input, (list, tuple)):
            return io.NodeOutput(*_join_primitives(inputs, delimiter))
        
        log.warning(_LOG_PREFIX, f"Unknown type: {type(first_input)}, returning first input")
        return io.NodeOutput(first_input)
