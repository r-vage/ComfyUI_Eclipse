import torch  # type: ignore
import numpy as np  # type: ignore
from PIL import Image  # type: ignore

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "Convert"


def _is_mask_tensor(tensor):
    if not isinstance(tensor, torch.Tensor):
        return False
    if tensor.dim() == 2:
        return True
    if tensor.dim() == 3:
        return tensor.shape[-1] not in (1, 3, 4)
    if tensor.dim() == 4:
        return tensor.shape[-1] not in (1, 3, 4)
    return False


def make_3d_mask(mask):
    if not hasattr(mask, "shape"):
        return mask
    if len(mask.shape) == 4:
        return mask.squeeze(0)
    elif len(mask.shape) == 2:
        return mask.unsqueeze(0)
    return mask


class RvConversion_ToList(io.ComfyNode):

    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("dynamic_type")
        return io.Schema(
            node_id="To List [Eclipse]",
            display_name="Convert to List",
            category=CATEGORY.MAIN.value + CATEGORY.CONVERSION.value,
            description="Dynamic To List converter — automatically detects the type of the connected "
            "input (Image, Mask, Latent, Conditioning, etc.) and outputs a list of individual items. "
            "Requires no dropdown selection.",
            is_input_list=True,
            inputs=[
                io.MatchType.Input(
                    "input",
                    template=type_template,
                    tooltip="Any value or batch to convert to list format",
                ),
            ],
            outputs=[
                io.MatchType.Output(
                    template=type_template,
                    is_output_list=True,
                    tooltip="The converted list format",
                ),
            ],
        )

    @classmethod
    def execute(cls, input):
        if input is None:
            return io.NodeOutput([])

        flat_list = []

        def _is_conditioning(item):
            if isinstance(item, (list, tuple)) and len(item) > 0:
                first = item[0]
                if isinstance(first, tuple) and len(first) == 2:
                    return isinstance(first[0], torch.Tensor) and isinstance(
                        first[1], dict
                    )
            return False

        def _process(item):
            if _is_conditioning(item):
                for cond_tuple in item:
                    flat_list.append([cond_tuple])
            elif isinstance(item, (list, tuple)):
                for sub in item:
                    _process(sub)
            elif isinstance(item, dict) and "samples" in item:
                samples = item["samples"]
                if isinstance(samples, torch.Tensor) and samples.dim() == 4:
                    for i in range(samples.shape[0]):
                        flat_list.append({"samples": samples[i : i + 1, ...]})
                else:
                    flat_list.append(item)
            elif isinstance(item, torch.Tensor):
                if _is_mask_tensor(item):
                    if item.dim() == 4:
                        for i in range(item.shape[0]):
                            flat_list.append(make_3d_mask(item[i]))
                    else:
                        flat_list.append(make_3d_mask(item))
                else:
                    if item.dim() == 4:
                        for i in range(item.shape[0]):
                            flat_list.append(item[i : i + 1, ...])
                    elif item.dim() == 3:
                        flat_list.append(item.unsqueeze(0))
                    else:
                        flat_list.append(item)
            elif isinstance(item, np.ndarray):
                if item.ndim == 4:
                    for i in range(item.shape[0]):
                        flat_list.append(item[i : i + 1, ...])
                elif item.ndim == 3:
                    flat_list.append(np.expand_dims(item, axis=0))
                else:
                    flat_list.append(item)
            elif isinstance(item, str):
                if "\n" in item:
                    flat_list.extend([line.strip() for line in item.split("\n") if line.strip()])
                elif "," in item:
                    flat_list.extend([part.strip() for part in item.split(",")])
                else:
                    flat_list.append(item)
            elif item is not None:
                flat_list.append(item)

        _process(input)
        return io.NodeOutput(flat_list)
