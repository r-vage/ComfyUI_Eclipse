import torch  # type: ignore
import numpy as np  # type: ignore
from PIL import Image  # type: ignore
import comfy.utils  # type: ignore

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.logger import log
from ..core.image_helpers import flatten_images, cat_and_fit_images

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


def _convert_image_list_to_batch(images):
    flat_images = flatten_images(images)
    if not flat_images:
        return None
    return cat_and_fit_images(flat_images, log_prefix="ToBatch")


def _convert_mask_list_to_batch(mask):
    if mask is None:
        return None
    if isinstance(mask, torch.Tensor) and mask.ndim in (3, 4):
        return make_3d_mask(mask)
    if not isinstance(mask, (list, tuple)):
        return mask
    if len(mask) == 0:
        return torch.zeros((1, 64, 64), dtype=torch.float32, device="cpu")

    masks_3d = [make_3d_mask(m) for m in mask]
    target_shape = masks_3d[0].shape[1:]  # [H, W]

    upscaled_masks = []
    for i, m in enumerate(masks_3d):
        if m.shape[1:] != target_shape:
            m = m.unsqueeze(1).repeat(1, 3, 1, 1)
            m = comfy.utils.common_upscale(
                m, target_shape[1], target_shape[0], "lanczos", "center"
            )
            m = m[:, 0, :, :]
        upscaled_masks.append(m)

    return torch.cat(upscaled_masks, dim=0)


def _convert_latent_list_to_batch(latents):
    if latents is None:
        return None
    if isinstance(latents, dict) and "samples" in latents:
        return latents
    if not isinstance(latents, (list, tuple)) or len(latents) == 0:
        return latents

    latent_dicts = [
        item for item in latents if isinstance(item, dict) and "samples" in item
    ]
    if len(latent_dicts) == 0:
        return latents
    if len(latent_dicts) == 1:
        return latent_dicts[0]

    latent1 = latent_dicts[0]
    samples1 = latent1["samples"]

    for i, latent2 in enumerate(latent_dicts[1:], 1):
        samples2 = latent2["samples"]
        if samples1.shape[1:] != samples2.shape[1:]:
            samples2 = comfy.utils.common_upscale(
                samples2,
                samples1.shape[3],  # width
                samples1.shape[2],  # height
                "lanczos",
                "center",
            )
        samples1 = torch.cat((samples1, samples2), dim=0)
    return {"samples": samples1}


class RvConversion_ToBatch(io.ComfyNode):

    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("dynamic_type")
        return io.Schema(
            node_id="To Batch [Eclipse]",
            display_name="Convert to Batch",
            category=CATEGORY.MAIN.value + CATEGORY.CONVERSION.value,
            description="Dynamic To Batch converter — automatically detects the type of the connected "
            "input list (Image, Mask, Latent, Conditioning, or Primitives) and outputs them as a combined "
            "batch format. Primitives (strings, numbers) are merged into a single comma-separated string.",
            is_input_list=True,
            inputs=[
                io.MatchType.Input(
                    "input",
                    template=type_template,
                    tooltip="Any list of values or tensors to combine into a batch",
                ),
            ],
            outputs=[
                io.MatchType.Output(
                    template=type_template,
                    is_output_list=False,
                    tooltip="The combined batch format",
                ),
            ],
        )

    @classmethod
    def execute(cls, input):
        if input is None or len(input) == 0:
            return io.NodeOutput(None)

        first_item = input[0]

        def _is_conditioning(item):
            if isinstance(item, (list, tuple)) and len(item) > 0:
                first = item[0]
                if isinstance(first, tuple) and len(first) == 2:
                    return isinstance(first[0], torch.Tensor) and isinstance(
                        first[1], dict
                    )
            return False

        if _is_conditioning(first_item):
            batched_cond = []
            for cond in input:
                if isinstance(cond, list):
                    batched_cond.extend(cond)
                else:
                    batched_cond.append(cond)
            return io.NodeOutput(batched_cond)

        if isinstance(first_item, dict) and "samples" in first_item:
            res = _convert_latent_list_to_batch(input)
            return io.NodeOutput(res)

        if isinstance(first_item, torch.Tensor):
            if _is_mask_tensor(first_item):
                res = _convert_mask_list_to_batch(input)
            else:
                res = _convert_image_list_to_batch(input)
            return io.NodeOutput(res)

        if isinstance(first_item, (str, int, float, bool)):
            res_str = ", ".join(map(str, input))
            return io.NodeOutput(res_str)

        return io.NodeOutput(input)
