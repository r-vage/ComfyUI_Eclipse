#
# Image Crop by Mask — mask-guided framing at a fixed output resolution.
# Thresholds the mask, fits its bounding box to the target aspect ratio,
# then applies zoom about the mask center and blurs the output mask edges.
# Missing source pixels stay black.
# Inspired by ComfyUI-InpaintCropAndStitch (lquesada).
#

import math

import torch  # type: ignore
import torch.nn.functional as F  # type: ignore
import torchvision.transforms.functional as TVF  # type: ignore
from comfy import model_management  # type: ignore
from comfy_api.latest import io  # type: ignore
from torchvision.transforms import InterpolationMode  # type: ignore

from ..core import CATEGORY
from ..core.image_helpers import expand_mask
from ..core.logger import log

_LOG_PREFIX = "CropByMask"

RESCALE_ALGORITHMS = ["nearest-exact", "bilinear", "area", "bicubic", "lanczos"]
DIVISIBILITY_OPTIONS = ["0", "8", "16", "32", "64"]
DEVICE_OPTIONS = ["auto", "cpu"]
MIRROR_OPTIONS = ["none", "horizontal", "vertical", "both"]


def _find_bbox(mask):
    # Find bounding box of non-zero mask region.
    # mask: [1, H, W] → returns (x, y, w, h) or None if empty.
    nonzero = torch.nonzero(mask[0])
    if nonzero.numel() == 0:
        return None
    y_min = nonzero[:, 0].min().item()
    y_max = nonzero[:, 0].max().item()
    x_min = nonzero[:, 1].min().item()
    x_max = nonzero[:, 1].max().item()
    return x_min, y_min, x_max - x_min + 1, y_max - y_min + 1


def _hipass_filter(mask, threshold):
    # Zero out mask values below threshold.
    if threshold < 0.01:
        return mask
    m = mask.clone()
    m[m < threshold] = 0.0
    return m


def _pad_to_multiple(value, multiple):
    if multiple <= 0:
        return value
    return int(math.ceil(value / multiple) * multiple)


def _apply_mirror(image, mask, mode):
    # Mirror image and mask. image: [B, H, W, C], mask: [B, H, W].
    if mode == "none":
        return image, mask
    if mode in ("horizontal", "both"):
        image = torch.flip(image, [2])
        mask = torch.flip(mask, [2])
    if mode in ("vertical", "both"):
        image = torch.flip(image, [1])
        mask = torch.flip(mask, [1])
    return image, mask


def _apply_rotation(image, mask, angle):
    # Rotate image and mask by angle degrees. image: [B, H, W, C], mask: [B, H, W].
    # expand=True grows canvas to fit rotated content; new pixels filled with 0.
    if angle == 0:
        return image, mask
    img_bchw = image.permute(0, 3, 1, 2)
    mask_b1hw = mask.unsqueeze(1)
    img_bchw = TVF.rotate(
        img_bchw,
        float(-angle),
        interpolation=InterpolationMode.BILINEAR,
        expand=True,
        fill=0,
    )
    mask_b1hw = TVF.rotate(
        mask_b1hw,
        float(-angle),
        interpolation=InterpolationMode.BILINEAR,
        expand=True,
        fill=0,
    )
    return img_bchw.permute(0, 2, 3, 1), mask_b1hw.squeeze(1)


def _crop_and_resize(image, mask, x, y, w, h, target_w, target_h, divisible_by, algorithm, zoom):
    # Fit the mask bounds into the output frame, then zoom about their center.
    # image: [B, H, W, C], mask: [B, H, W]

    # Pad target to multiple
    if divisible_by > 0:
        target_w = _pad_to_multiple(target_w, divisible_by)
        target_h = _pad_to_multiple(target_h, divisible_by)

    _, img_h, img_w, _ = image.shape
    scale = max(w / target_w, h / target_h) / zoom
    view_w = max(1, round(target_w * scale))
    view_h = max(1, round(target_h * scale))
    view_x = round(x + (w - view_w) / 2)
    view_y = round(y + (h - view_h) / 2)

    # Clip only the pixels being read, never the frame itself. This keeps zoom
    # responsive and the mask centered even when the frame exceeds the source.
    src_l, src_t = max(0, view_x), max(0, view_y)
    src_r, src_b = min(img_w, view_x + view_w), min(img_h, view_y + view_h)
    dst_l = round((src_l - view_x) * target_w / view_w)
    dst_t = round((src_t - view_y) * target_h / view_h)
    dst_r = min(target_w, max(dst_l + 1, round((src_r - view_x) * target_w / view_w)))
    dst_b = min(target_h, max(dst_t + 1, round((src_b - view_y) * target_h / view_h)))
    content_w, content_h = dst_r - dst_l, dst_b - dst_t
    cropped_img = image[:, src_t:src_b, src_l:src_r]
    cropped_mask = mask[:, src_t:src_b, src_l:src_r]

    # Resize just the available content, then pad at output resolution. Avoid
    # allocating a huge source canvas at low zoom or extreme target ratios.
    if src_r - src_l != content_w or src_b - src_t != content_h:
        mode = algorithm if algorithm != "lanczos" else "bicubic"
        align = False if mode not in ("nearest", "nearest-exact", "area") else None
        # Image: [B, H, W, C] → [B, C, H, W]
        img_r = cropped_img.permute(0, 3, 1, 2)
        img_r = F.interpolate(
            img_r, size=(content_h, content_w), mode=mode, align_corners=align
        )
        cropped_img = img_r.permute(0, 2, 3, 1)
        # Mask: [B, H, W] → [B, 1, H, W]
        mask_r = cropped_mask.unsqueeze(1)
        mask_r = F.interpolate(
            mask_r, size=(content_h, content_w), mode=mode, align_corners=align
        )
        cropped_mask = mask_r.squeeze(1)

    border = (dst_l, target_w - dst_r, dst_t, target_h - dst_b)
    if any(border):
        cropped_img = F.pad(cropped_img.permute(0, 3, 1, 2), border).permute(0, 2, 3, 1)
        # Keep missing source areas masked for downstream inpainting.
        cropped_mask = F.pad(cropped_mask, border, value=1.0)

    return cropped_img, cropped_mask.clamp(0.0, 1.0)


class RvImage_CropByMask(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Crop by Mask [Eclipse]",
            display_name="Image Crop by Mask",
            description="Frame an image around its mask at a fixed target resolution. Context values below 1 zoom out; values above 1 zoom in. Areas outside the source use black borders and remain masked for inpainting.",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_TRANSFORMS.value,
            inputs=[
                io.Image.Input("image", tooltip="Source image to crop."),
                io.Mask.Input("mask", tooltip="Mask defining the region of interest."),
                io.Int.Input(
                    "rotation",
                    default=0,
                    min=-180,
                    max=180,
                    step=1,
                    tooltip="Rotate input image and mask by this angle (degrees) before cropping. Positive = clockwise, negative = counter-clockwise.",
                ),
                io.Combo.Input(
                    "mirror",
                    options=MIRROR_OPTIONS,
                    default="none",
                    tooltip="Mirror input image and mask before cropping.",
                ),
                io.Int.Input(
                    "mask_blur",
                    default=0,
                    min=0,
                    max=512,
                    step=1,
                    tooltip="Gaussian blur radius in output pixels. Softens the final mask edges without changing the image crop or zoom. 0 disables blur.",
                ),
                io.Float.Input(
                    "mask_threshold",
                    default=0.1,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Zero out mask values below this threshold (hi-pass filter).",
                ),
                io.Float.Input(
                    "context_expand",
                    default=1.0,
                    min=0.1,
                    max=4.0,
                    step=0.05,
                    tooltip="Zoom around the mask center at the requested output size: 1.0 fits the mask, 0.5 zooms out to show twice the context, 2.0 zooms in. Outside-source areas use black borders. Higher values may crop the mask.",
                ),
                io.Int.Input(
                    "target_width",
                    default=512,
                    min=64,
                    max=16384,
                    step=8,
                    tooltip="Output width; together with target_height, defines the framing aspect ratio.",
                ),
                io.Int.Input(
                    "target_height",
                    default=512,
                    min=64,
                    max=16384,
                    step=8,
                    tooltip="Output height; together with target_width, defines the framing aspect ratio.",
                ),
                io.Combo.Input(
                    "divisible_by",
                    options=DIVISIBILITY_OPTIONS,
                    default="32",
                    tooltip="Round output dimensions up to this multiple, not a border width. Already aligned sizes such as 512 or 768 remain unchanged; 0 disables rounding.",
                ),
                io.Combo.Input(
                    "rescale_algorithm",
                    options=RESCALE_ALGORITHMS,
                    default="bicubic",
                    tooltip="Interpolation method for resize.",
                ),
                io.Combo.Input(
                    "device",
                    options=DEVICE_OPTIONS,
                    default="auto",
                    tooltip="Processing device. 'auto' uses GPU if available.",
                ),
            ],
            outputs=[
                io.Image.Output("image", tooltip="Cropped and resized image region.", is_output_list=True),
                io.Mask.Output("mask", tooltip="Cropped and resized mask.", is_output_list=True),
            ],
        )

    @classmethod
    def execute(
        cls,
        image,
        mask,
        rotation,
        mirror,
        mask_blur,
        mask_threshold,
        context_expand,
        target_width,
        target_height,
        divisible_by,
        rescale_algorithm,
        device,
    ):
        if not math.isfinite(context_expand) or context_expand <= 0:
            raise ValueError("context_expand must be a positive, finite zoom value")

        # Resolve device
        if device == "auto":
            dev = model_management.get_torch_device()
        else:
            dev = torch.device("cpu")

        image = image.clone().to(dev)
        mask = mask.clone().to(dev)

        B, H, W, _ = image.shape

        # Fix mask shape mismatches (single mask for batch, or vice versa)
        if mask.shape[0] == 1 and B > 1:
            mask = mask.expand(B, -1, -1).clone()
        elif B == 1 and mask.shape[0] > 1:
            B = mask.shape[0]
            image = image.expand(B, -1, -1, -1).clone()

        # Handle mask dimension mismatch (wrong HxW from LoadImage without edit)
        if mask.shape[1] != H or mask.shape[2] != W:
            if torch.count_nonzero(mask) == 0:
                mask = torch.zeros((mask.shape[0], H, W), device=dev, dtype=image.dtype)
            else:
                mask = F.interpolate(
                    mask.unsqueeze(1), size=(H, W), mode="nearest"
                ).squeeze(1)

        # Pre-edit: mirror and rotate input before crop processing
        if mirror != "none":
            image, mask = _apply_mirror(image, mask, mirror)
        if rotation != 0:
            image, mask = _apply_rotation(image, mask, rotation)
            B, H, W, _ = image.shape

        # Framing uses the thresholded mask. Blur must not enlarge its bounds.
        mask = _hipass_filter(mask, mask_threshold)

        divisor = int(divisible_by)

        # Process each batch item (bbox differs per image)
        result_images = []
        result_masks = []

        for i in range(B):
            sub_img = image[i : i + 1]
            sub_mask = mask[i : i + 1]

            bbox = _find_bbox(sub_mask)
            if bbox is None:
                # Empty mask — use full image
                x, y, w, h = 0, 0, W, H
                log.debug(_LOG_PREFIX, f"Batch {i}: empty mask, using full image")
            else:
                x, y, w, h = bbox

            c_img, c_mask = _crop_and_resize(
                sub_img,
                sub_mask,
                x,
                y,
                w,
                h,
                target_width,
                target_height,
                divisor,
                rescale_algorithm,
                context_expand,
            )
            result_images.append(c_img.cpu())
            c_mask = c_mask.cpu()
            if mask_blur > 0:
                c_mask = expand_mask(c_mask, grow=0, blur=mask_blur)
            # Each list item is a complete MASK batch [1, H, W]. Removing the
            # batch axis makes downstream nodes interpret image rows as masks.
            result_masks.append(c_mask)

        return io.NodeOutput(result_images, result_masks)
