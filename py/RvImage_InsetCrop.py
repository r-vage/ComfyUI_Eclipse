#
# Inset & Crop — crops an image by shrinking from each edge by the given pixel
# amounts. Merges WAS Node Suite's Image Bounds, Inset Image Bounds, and
# Bounded Image Crop nodes into a single step.
# Ported from WAS Node Suite (MIT licence, original author WASasquatch / ltdata).
#

import torch  # type: ignore
import comfy.utils  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.image_helpers import unwrap_value, flatten_images, was_input_batch, prepare_image_output

_LOG_PREFIX = "InsetCrop"





class RvImage_InsetCrop(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Inset & Crop [Eclipse]",
            display_name="Inset & Crop",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_TRANSFORMS.value,
            description="Crop an image by removing a fixed number of pixels from each edge. "
            "All insets default to 0 (pass-through with no crop).",
            is_input_list=True,
            inputs=[
                io.Image.Input("image", tooltip="Input image or batch."),
                io.Int.Input(
                    "inset_top",
                    default=0,
                    min=0,
                    max=0xFFFF,
                    step=1,
                    tooltip="Pixels to remove from the top edge.",
                ),
                io.Int.Input(
                    "inset_bottom",
                    default=0,
                    min=0,
                    max=0xFFFF,
                    step=1,
                    tooltip="Pixels to remove from the bottom edge.",
                ),
                io.Int.Input(
                    "inset_left",
                    default=0,
                    min=0,
                    max=0xFFFF,
                    step=1,
                    tooltip="Pixels to remove from the left edge.",
                ),
                io.Int.Input(
                    "inset_right",
                    default=0,
                    min=0,
                    max=0xFFFF,
                    step=1,
                    tooltip="Pixels to remove from the right edge.",
                ),
            ],
            outputs=[
                io.Image.Output("image", is_output_list=True),
            ],
        )

    @classmethod
    def execute(cls, image, inset_top, inset_bottom, inset_left, inset_right):
        inset_top = unwrap_value(inset_top, 0)
        inset_bottom = unwrap_value(inset_bottom, 0)
        inset_left = unwrap_value(inset_left, 0)
        inset_right = unwrap_value(inset_right, 0)

        flat_image = flatten_images(image)
        if not flat_image:
            raise ValueError("Inset & Crop: No images provided in input.")

        was_batch = was_input_batch(image)

        # Fast pass-through when no insets
        if (
            inset_top == 0
            and inset_bottom == 0
            and inset_left == 0
            and inset_right == 0
        ):
            return io.NodeOutput(prepare_image_output(torch.cat(flat_image, dim=0), was_batch))

        processed_list = []
        for img in flat_image:
            # Since flat_image is already a list of [1, H, W, C] tensors, we slice frame 0
            frame = img[0]
            H, W = frame.shape[0], frame.shape[1]
            rmin = inset_top
            rmax = H - 1 - inset_bottom
            cmin = inset_left
            cmax = W - 1 - inset_right

            if rmin > rmax or cmin > cmax:
                raise ValueError(
                    f"Insets exceed image dimensions ({W}×{H}): "
                    f"top={inset_top}, bottom={inset_bottom}, "
                    f"left={inset_left}, right={inset_right}"
                )

            cropped = frame[rmin : rmax + 1, cmin : cmax + 1, :].unsqueeze(0)
            processed_list.append(cropped)

        merged_tensor = torch.cat(processed_list, dim=0)
        result = prepare_image_output(merged_tensor, was_batch)
        return io.NodeOutput(result)
