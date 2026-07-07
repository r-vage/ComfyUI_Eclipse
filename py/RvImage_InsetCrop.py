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

_LOG_PREFIX = "InsetCrop"


def _normalize_to_list(images) -> list:
    if isinstance(images, torch.Tensor):
        if images.dim() == 3:
            return [images.unsqueeze(0)]
        elif images.dim() == 4:
            return [images[i:i+1] for i in range(images.shape[0])]
    if isinstance(images, (list, tuple)):
        out = []
        for img in images:
            if isinstance(img, torch.Tensor):
                if img.dim() == 3:
                    out.append(img.unsqueeze(0))
                elif img.dim() == 4:
                    for j in range(img.shape[0]):
                        out.append(img[j:j+1])
        return out
    raise ValueError(f"Unsupported image input type: {type(images)}")


def _stack_and_force_size(tensors_list: list) -> torch.Tensor:
    if not tensors_list:
        return torch.empty((0, 64, 64, 3))
    first_tensor = tensors_list[0]
    target_h, target_w = first_tensor.shape[1], first_tensor.shape[2]
    adjusted_list = []
    for t in tensors_list:
        if t.shape[1] != target_h or t.shape[2] != target_w:
            t_bchw = t.movedim(-1, 1)  # [1, C, H, W]
            t_resized = comfy.utils.common_upscale(t_bchw, target_w, target_h, "lanczos", "disabled")
            t = t_resized.movedim(1, -1)  # [1, H, W, C]
        adjusted_list.append(t)
    return torch.cat(adjusted_list, dim=0)


class RvImage_InsetCrop(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Inset & Crop [Eclipse]",
            display_name="Inset & Crop",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_TRANSFORMS.value,
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
        is_list_input = isinstance(image, (list, tuple))
        image_list = _normalize_to_list(image)
        if not image_list:
            raise ValueError("Inset & Crop: No images provided in input.")

        # Fast pass-through when no insets
        if inset_top == 0 and inset_bottom == 0 and inset_left == 0 and inset_right == 0:
            return io.NodeOutput(image)

        processed_list = []
        for img in image_list:
            results = []
            for frame in img:
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
            processed_list.append(torch.stack(results, dim=0))

        if processed_list:
            out_image = _stack_and_force_size(processed_list)
        else:
            out_image = torch.empty((0, 64, 64, 3))

        return io.NodeOutput(out_image)
