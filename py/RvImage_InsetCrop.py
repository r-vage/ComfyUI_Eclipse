#
# Inset & Crop — crops an image by shrinking from each edge by the given pixel
# amounts. Merges WAS Node Suite's Image Bounds, Inset Image Bounds, and
# Bounded Image Crop nodes into a single step.
# Ported from WAS Node Suite (MIT licence, original author WASasquatch / ltdata).
#

import torch  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY

_LOG_PREFIX = "InsetCrop"


class RvImage_InsetCrop(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Inset & Crop [Eclipse]",
            display_name="Inset & Crop",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            description="Crop an image by removing a fixed number of pixels from each edge. "
                        "All insets default to 0 (pass-through with no crop).",
            inputs=[
                io.Image.Input("image", tooltip="Input image or batch."),
                io.Int.Input("inset_top", default=0, min=0, max=0xFFFF, step=1,
                             tooltip="Pixels to remove from the top edge."),
                io.Int.Input("inset_bottom", default=0, min=0, max=0xFFFF, step=1,
                             tooltip="Pixels to remove from the bottom edge."),
                io.Int.Input("inset_left", default=0, min=0, max=0xFFFF, step=1,
                             tooltip="Pixels to remove from the left edge."),
                io.Int.Input("inset_right", default=0, min=0, max=0xFFFF, step=1,
                             tooltip="Pixels to remove from the right edge."),
            ],
            outputs=[
                io.Image.Output("image"),
            ],
        )

    @classmethod
    def execute(cls, image, inset_top, inset_bottom, inset_left, inset_right):
        if image.dim() == 3:
            image = image.unsqueeze(0)

        # Fast pass-through when no insets
        if inset_top == 0 and inset_bottom == 0 and inset_left == 0 and inset_right == 0:
            return io.NodeOutput(image)

        results = []
        for frame in image:
            # frame: [H, W, C]
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

            results.append(frame[rmin:rmax + 1, cmin:cmax + 1, :])

        return io.NodeOutput(torch.stack(results, dim=0))
