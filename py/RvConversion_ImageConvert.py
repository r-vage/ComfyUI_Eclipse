import torch #type: ignore
import numpy as np #type: ignore
from PIL import Image #type: ignore
import subprocess

from ..core import CATEGORY
from comfy_api.latest import io #type: ignore

# Import pilgram for style filters
try:
    import pilgram #type: ignore
except ImportError:
    import sys
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pilgram'])
    import pilgram #type: ignore

def tensor2pil(image):
    # Convert tensor to PIL Image
    return Image.fromarray(np.clip(255. * image.cpu().numpy().squeeze(), 0, 255).astype(np.uint8))

def pil2tensor(image):
    # Convert PIL Image to tensor
    return torch.from_numpy(np.array(image).astype(np.float32) / 255.0).unsqueeze(0)

def is_rgb_tensor(tensor):
    # Check if tensor is RGB (3 channels)
    return tensor.shape[-1] == 3

def is_rgba_tensor(tensor):
    # Check if tensor is RGBA (4 channels)
    return tensor.shape[-1] == 4

def is_grayscale_tensor(tensor):
    # Check if tensor is grayscale (1 channel)
    return tensor.shape[-1] == 1

def convert_to_rgb(tensor):
    # Convert tensor to RGB
    if is_rgb_tensor(tensor):
        return tensor
    elif is_rgba_tensor(tensor):
        # Remove alpha channel
        return tensor[..., :3]
    elif is_grayscale_tensor(tensor):
        # Expand grayscale to RGB by repeating the channel
        return tensor.repeat(1, 1, 1, 3)
    else:
        raise ValueError(f"Unsupported tensor shape: {tensor.shape}")

def convert_to_grayscale(tensor):
    # Convert tensor to grayscale using luminance formula.
    # Returns RGB format (3 channels) with same grayscale value in all channels
    # to maintain ComfyUI IMAGE format compatibility.
    if is_grayscale_tensor(tensor):
        # Already grayscale, convert to 3-channel format
        return tensor.repeat(1, 1, 1, 3)
    elif is_rgb_tensor(tensor) or is_rgba_tensor(tensor):
        # Use standard luminance formula: Y = 0.299R + 0.587G + 0.114B
        rgb = tensor[..., :3]  # Use only RGB channels
        weights = torch.tensor([0.299, 0.587, 0.114], dtype=tensor.dtype, device=tensor.device)
        grayscale = torch.sum(rgb * weights, dim=-1, keepdim=True)
        # Repeat to 3 channels for ComfyUI compatibility
        return grayscale.repeat(1, 1, 1, 3)
    else:
        raise ValueError(f"Unsupported tensor shape: {tensor.shape}")

def remove_alpha_channel(tensor):
    # Remove alpha channel from tensor
    if is_rgba_tensor(tensor):
        return tensor[..., :3]
    else:
        # No alpha channel to remove
        return tensor


_STYLE_OPTIONS = ["none", "1977", "aden", "brannan", "brooklyn", "clarendon", "earlybird",
                  "gingham", "hudson", "inkwell", "kelvin", "lark", "lofi", "maven", "mayfair",
                  "moon", "nashville", "perpetua", "reyes", "rise", "slumber", "stinson",
                  "toaster", "valencia", "walden", "willow", "xpro2"]

_STYLE_MAP = {
    "1977": pilgram._1977,
    "aden": pilgram.aden,
    "brannan": pilgram.brannan,
    "brooklyn": pilgram.brooklyn,
    "clarendon": pilgram.clarendon,
    "earlybird": pilgram.earlybird,
    "gingham": pilgram.gingham,
    "hudson": pilgram.hudson,
    "inkwell": pilgram.inkwell,
    "kelvin": pilgram.kelvin,
    "lark": pilgram.lark,
    "lofi": pilgram.lofi,
    "maven": pilgram.maven,
    "mayfair": pilgram.mayfair,
    "moon": pilgram.moon,
    "nashville": pilgram.nashville,
    "perpetua": pilgram.perpetua,
    "reyes": pilgram.reyes,
    "rise": pilgram.rise,
    "slumber": pilgram.slumber,
    "stinson": pilgram.stinson,
    "toaster": pilgram.toaster,
    "valencia": pilgram.valencia,
    "walden": pilgram.walden,
    "willow": pilgram.willow,
    "xpro2": pilgram.xpro2,
}


def _apply_style(images, style):
    # Apply Instagram-like style filter to images
    if style not in _STYLE_MAP:
        return images

    filter_func = _STYLE_MAP[style]
    tensors = []

    for img in images:
        styled_img = pil2tensor(filter_func(tensor2pil(img)))
        tensors.append(styled_img)

    return torch.cat(tensors, dim=0)


class RvConversion_ImageConvert(io.ComfyNode):
    # Convert images between different color spaces and formats.
    # Supports RGB and Grayscale conversions.
    # Multiple conversions can be applied in sequence.
    # Optionally apply Instagram-like style filters.

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Convert [Eclipse]",
            display_name="Image Convert",
            category=CATEGORY.MAIN.value + CATEGORY.CONVERSION.value,
            inputs=[
                io.Image.Input("images"),
                io.Boolean.Input("to_rgb", default=False, optional=True, tooltip="Convert to RGB (3 channels)"),
                io.Boolean.Input("to_grayscale", default=False, optional=True, tooltip="Convert to grayscale"),
                io.Boolean.Input("remove_alpha", default=False, optional=True, tooltip="Remove alpha channel"),
                io.Combo.Input("style", options=_STYLE_OPTIONS, default="none", optional=True, tooltip="Instagram-like style filter to apply"),
            ],
            outputs=[
                io.Image.Output("images"),
            ],
        )

    @classmethod
    def execute(cls, images, to_rgb=False, to_grayscale=False, remove_alpha_val=False, style="none") -> io.NodeOutput:
        result = images

        if remove_alpha_val:
            result = remove_alpha_channel(result)

        if to_rgb:
            result = convert_to_rgb(result)

        if to_grayscale:
            result = convert_to_grayscale(result)

        if style != "none":
            result = _apply_style(result, style)

        return io.NodeOutput(result)