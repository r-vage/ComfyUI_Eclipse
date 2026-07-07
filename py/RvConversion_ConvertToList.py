import torch #type: ignore
import numpy as np #type: ignore
from PIL import Image #type: ignore

from comfy_api.latest import io #type: ignore

from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "Convert"
# Helper functions for image conversion
def tensor2pil(image):
    return Image.fromarray(np.clip(255. * image.cpu().numpy().squeeze(), 0, 255).astype(np.uint8))

def pil2tensor(image):
    return torch.from_numpy(np.array(image).astype(np.float32) / 255.0).unsqueeze(0)

# Helper function for mask conversion
def make_3d_mask(mask):
    # Convert mask to 3D format
    if not hasattr(mask, "shape"):
        return mask
    if len(mask.shape) == 4:
        return mask.squeeze(0)
    elif len(mask.shape) == 2:
        return mask.unsqueeze(0)
    return mask


def _convert_image_batch_to_list(images):
    if not isinstance(images, (torch.Tensor, list, tuple)):
        log.warning(_LOG_PREFIX, "image_batch_to_list: Input is not a batch, returning as single-item list")
        return [images]
    
    try:
        flat_list = []
        def _process(item):
            if isinstance(item, (list, tuple)):
                for sub in item:
                    _process(sub)
            elif isinstance(item, torch.Tensor):
                if item.dim() == 4:
                    for i in range(item.shape[0]):
                        flat_list.append(item[i:i+1, ...])
                elif item.dim() == 3:
                    flat_list.append(item.unsqueeze(0))
            elif isinstance(item, np.ndarray):
                if item.ndim == 4:
                    for i in range(item.shape[0]):
                        flat_list.append(item[i:i+1, ...])
                elif item.ndim == 3:
                    flat_list.append(np.expand_dims(item, axis=0))
            elif item is not None:
                flat_list.append(item)

        _process(images)
        return flat_list
    except Exception as e:
        log.error(_LOG_PREFIX, f"image_batch_to_list conversion failed: {e}, returning as single-item list")
        return [images]

def _convert_mask_batch_to_list(masks):
    if masks is None:
        return []
    
    try:
        flat_list = []
        def _process(item):
            if isinstance(item, (list, tuple)):
                for sub in item:
                    _process(sub)
            elif isinstance(item, torch.Tensor):
                if item.dim() == 4:
                    for i in range(item.shape[0]):
                        flat_list.append(make_3d_mask(item[i]))
                else:
                    flat_list.append(make_3d_mask(item))
            elif isinstance(item, np.ndarray):
                if item.ndim == 4:
                    for i in range(item.shape[0]):
                        flat_list.append(make_3d_mask(item[i]))
                else:
                    flat_list.append(make_3d_mask(item))
            elif item is not None:
                flat_list.append(item)

        _process(masks)
        return flat_list
    except Exception as e:
        log.error(_LOG_PREFIX, f"mask_batch_to_list conversion failed: {e}, returning as single-item list")
        return [masks]

def _convert_latent_batch_to_list(latents):
    if latents is None:
        return []
    
    try:
        flat_list = []
        def _process(item):
            if isinstance(item, (list, tuple)):
                for sub in item:
                    _process(sub)
            elif isinstance(item, dict) and "samples" in item:
                samples = item["samples"]
                if isinstance(samples, torch.Tensor) and samples.dim() == 4:
                    for i in range(samples.shape[0]):
                        flat_list.append({"samples": samples[i:i+1, ...]})
                else:
                    flat_list.append(item)
            elif item is not None:
                flat_list.append(item)

        _process(latents)
        return flat_list
    except Exception as e:
        log.error(_LOG_PREFIX, f"latent_batch_to_list conversion failed: {e}, returning as-is")
        return [latents]


class RvConversion_ConvertToList(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Convert to List [Eclipse]",
            display_name="Convert to List",
            category=CATEGORY.MAIN.value + CATEGORY.CONVERSION.value,
            is_input_list=True,
            inputs=[
                io.AnyType.Input("input", tooltip="Any value to convert to list format"),
                io.Combo.Input("convert_to", options=["IMAGE_BATCH_TO_LIST", "MASK_BATCH_TO_LIST", "LATENT_BATCH_TO_LIST"],
                               default="IMAGE_BATCH_TO_LIST", tooltip="Target list conversion type"),
            ],
            outputs=[
                io.AnyType.Output("output", is_output_list=True),
            ],
        )

    @classmethod
    def execute(cls, input, convert_to):
        # Convert input to list format.
        # For image color conversions (RGB, RGBA, GRAYSCALE), use ImageConvert node instead.
        # Returns list-based conversions based on convert_to selection.
        if isinstance(convert_to, (list, tuple)):
            convert_to = convert_to[0] if len(convert_to) > 0 else ""

        # Handle image conversions
        if convert_to == "IMAGE_BATCH_TO_LIST":
            res = _convert_image_batch_to_list(input)
        
        # Handle mask conversions
        elif convert_to == "MASK_BATCH_TO_LIST":
            res = _convert_mask_batch_to_list(input)
        
        # Handle latent conversions
        elif convert_to == "LATENT_BATCH_TO_LIST":
            res = _convert_latent_batch_to_list(input)
        
        # Fallback
        else:
            res = [input]

        return io.NodeOutput(res)
