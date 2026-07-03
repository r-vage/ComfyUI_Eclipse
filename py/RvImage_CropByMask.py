#
# Image Crop by Mask — lightweight mask-guided crop with optional expansion.
# Finds the mask bounding box, optionally expands/thresholds the mask,
# grows the context area, crops + resizes to target resolution.
# Inspired by ComfyUI-InpaintCropAndStitch (lquesada).
#

import math
import torch # type: ignore
import torch.nn.functional as F # type: ignore
import torchvision.transforms.functional as TVF # type: ignore
from torchvision.transforms import InterpolationMode # type: ignore

import comfy.model_management as model_management # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "CropByMask"

RESCALE_ALGORITHMS = ["nearest-exact", "bilinear", "area", "bicubic", "lanczos"]
PADDING_OPTIONS = ["0", "8", "16", "32", "64"]
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


def _expand_mask(mask, pixels):
    # Dilate mask via max-pool. mask: [B, H, W].
    if pixels <= 0:
        return mask
    sigma = pixels / 4.0
    ks = max(3, math.ceil(sigma * 1.5 + 1))
    if ks % 2 == 0:
        ks += 1
    pad = ks // 2
    m = mask.unsqueeze(1)  # [B, 1, H, W]
    m = F.max_pool2d(m, kernel_size=ks, stride=1, padding=pad)
    return m.squeeze(1).clamp(0.0, 1.0)


def _hipass_filter(mask, threshold):
    # Zero out mask values below threshold.
    if threshold < 0.01:
        return mask
    m = mask.clone()
    m[m < threshold] = 0.0
    return m


def _grow_bbox(x, y, w, h, img_w, img_h, factor):
    # Grow bounding box by factor, clamped to image bounds.
    grow_x = round(w * (factor - 1.0) / 2.0)
    grow_y = round(h * (factor - 1.0) / 2.0)
    nx = max(0, x - grow_x)
    ny = max(0, y - grow_y)
    nx2 = min(img_w, x + w + grow_x)
    ny2 = min(img_h, y + h + grow_y)
    return nx, ny, nx2 - nx, ny2 - ny


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
    img_bchw = TVF.rotate(img_bchw, float(-angle), interpolation=InterpolationMode.BILINEAR, expand=True, fill=0)
    mask_b1hw = TVF.rotate(mask_b1hw, float(-angle), interpolation=InterpolationMode.BILINEAR, expand=True, fill=0)
    return img_bchw.permute(0, 2, 3, 1), mask_b1hw.squeeze(1)


def _crop_and_resize(image, mask, x, y, w, h, target_w, target_h, padding, algorithm):
    # Crop region from image+mask, expand canvas if bbox exceeds image bounds,
    # fill expanded areas with edge pixels, resize to target.
    # image: [B, H, W, C], mask: [B, H, W]

    # Pad target to multiple
    if padding > 0:
        target_w = _pad_to_multiple(target_w, padding)
        target_h = _pad_to_multiple(target_h, padding)

    B, img_h, img_w, C = image.shape

    # Adjust crop to match target aspect ratio
    target_ar = target_w / target_h
    crop_ar = w / h

    if crop_ar < target_ar:
        new_w = int(h * target_ar)
        new_h = h
        new_x = x - (new_w - w) // 2
        new_y = y
    else:
        new_w = w
        new_h = int(w / target_ar)
        new_x = x
        new_y = y - (new_h - h) // 2

    # Clamp to image bounds where possible
    if new_x < 0:
        shift = -new_x
        if new_x + new_w + shift <= img_w:
            new_x += shift
        else:
            new_x = -((new_w - img_w) // 2)
    elif new_x + new_w > img_w:
        overflow = new_x + new_w - img_w
        if new_x - overflow >= 0:
            new_x -= overflow
        else:
            new_x = -((new_w - img_w) // 2)

    if new_y < 0:
        shift = -new_y
        if new_y + new_h + shift <= img_h:
            new_y += shift
        else:
            new_y = -((new_h - img_h) // 2)
    elif new_y + new_h > img_h:
        overflow = new_y + new_h - img_h
        if new_y - overflow >= 0:
            new_y -= overflow
        else:
            new_y = -((new_h - img_h) // 2)

    # Calculate padding needed for out-of-bounds crop
    pad_l = max(0, -new_x)
    pad_r = max(0, (new_x + new_w) - img_w)
    pad_t = max(0, -new_y)
    pad_b = max(0, (new_y + new_h) - img_h)

    if pad_l > 0 or pad_r > 0 or pad_t > 0 or pad_b > 0:
        # Expand image with edge-pixel fill
        exp_h = img_h + pad_t + pad_b
        exp_w = img_w + pad_l + pad_r

        img_bchw = image.permute(0, 3, 1, 2)  # [B, C, H, W]
        exp_img = torch.zeros((B, C, exp_h, exp_w), device=image.device, dtype=image.dtype)
        exp_img[:, :, pad_t:pad_t + img_h, pad_l:pad_l + img_w] = img_bchw

        # Edge fill
        if pad_t > 0:
            exp_img[:, :, :pad_t, pad_l:pad_l + img_w] = img_bchw[:, :, :1, :].expand(-1, -1, pad_t, -1)
        if pad_b > 0:
            exp_img[:, :, pad_t + img_h:, pad_l:pad_l + img_w] = img_bchw[:, :, -1:, :].expand(-1, -1, pad_b, -1)
        if pad_l > 0:
            exp_img[:, :, :, :pad_l] = exp_img[:, :, :, pad_l:pad_l + 1].expand(-1, -1, -1, pad_l)
        if pad_r > 0:
            exp_img[:, :, :, pad_l + img_w:] = exp_img[:, :, :, pad_l + img_w - 1:pad_l + img_w].expand(-1, -1, -1, pad_r)

        exp_img = exp_img.permute(0, 2, 3, 1)  # [B, H, W, C]

        # Expand mask (fill with 1.0 = masked for out-of-bounds)
        exp_mask = torch.ones((B, exp_h, exp_w), device=mask.device, dtype=mask.dtype)
        exp_mask[:, pad_t:pad_t + img_h, pad_l:pad_l + img_w] = mask

        # Adjust crop coords to expanded canvas
        crop_x = new_x + pad_l
        crop_y = new_y + pad_t
    else:
        exp_img = image
        exp_mask = mask
        crop_x = new_x
        crop_y = new_y

    # Crop
    cropped_img = exp_img[:, crop_y:crop_y + new_h, crop_x:crop_x + new_w]
    cropped_mask = exp_mask[:, crop_y:crop_y + new_h, crop_x:crop_x + new_w]

    # Resize to target
    if new_w != target_w or new_h != target_h:
        mode = algorithm if algorithm != "lanczos" else "bicubic"
        align = False if mode not in ("nearest", "nearest-exact", "area") else None
        # Image: [B, H, W, C] → [B, C, H, W]
        img_r = cropped_img.permute(0, 3, 1, 2)
        img_r = F.interpolate(img_r, size=(target_h, target_w), mode=mode, align_corners=align)
        cropped_img = img_r.permute(0, 2, 3, 1)
        # Mask: [B, H, W] → [B, 1, H, W]
        mask_r = cropped_mask.unsqueeze(1)
        mask_r = F.interpolate(mask_r, size=(target_h, target_w), mode=mode, align_corners=align)
        cropped_mask = mask_r.squeeze(1)

    return cropped_img, cropped_mask.clamp(0.0, 1.0)


class RvImage_CropByMask(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Crop by Mask [Eclipse]",
            display_name="Image Crop by Mask",
            description="Crop image region around mask with optional expansion, threshold filtering, and resize to target resolution. Useful for inpaint pre-processing.",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            inputs=[
                io.Image.Input("image", tooltip="Source image to crop."),
                io.Mask.Input("mask", tooltip="Mask defining the region of interest."),
                io.Int.Input("rotation", default=0, min=-180, max=180, step=1, tooltip="Rotate input image and mask by this angle (degrees) before cropping. Positive = clockwise, negative = counter-clockwise."),
                io.Combo.Input("mirror", options=MIRROR_OPTIONS, default="none", tooltip="Mirror input image and mask before cropping."),
                io.Int.Input("mask_expand", default=0, min=0, max=512, step=1, tooltip="Dilate mask by this many pixels before computing bounding box."),
                io.Float.Input("mask_threshold", default=0.1, min=0.0, max=1.0, step=0.01, tooltip="Zero out mask values below this threshold (hi-pass filter)."),
                io.Float.Input("context_expand", default=1.0, min=1.0, max=4.0, step=0.05, tooltip="Grow the crop bounding box by this factor (1.0 = tight crop, 2.0 = 2x size)."),
                io.Int.Input("target_width", default=512, min=64, max=16384, step=8, tooltip="Resize cropped region to this width."),
                io.Int.Input("target_height", default=512, min=64, max=16384, step=8, tooltip="Resize cropped region to this height."),
                io.Combo.Input("padding", options=PADDING_OPTIONS, default="32", tooltip="Snap output dimensions to this multiple."),
                io.Combo.Input("rescale_algorithm", options=RESCALE_ALGORITHMS, default="bicubic", tooltip="Interpolation method for resize."),
                io.Combo.Input("device", options=DEVICE_OPTIONS, default="auto", tooltip="Processing device. 'auto' uses GPU if available."),
            ],
            outputs=[
                io.Image.Output("image", tooltip="Cropped and resized image region."),
                io.Mask.Output("mask", tooltip="Cropped and resized mask."),
            ],
        )

    @classmethod
    def execute(cls, image, mask, rotation, mirror, mask_expand, mask_threshold, context_expand, target_width, target_height, padding, rescale_algorithm, device):
        # Resolve device
        if device == "auto":
            dev = model_management.get_torch_device()
        else:
            dev = torch.device("cpu")

        image = image.clone().to(dev)
        mask = mask.clone().to(dev)

        B, H, W, C = image.shape

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
                mask = F.interpolate(mask.unsqueeze(1), size=(H, W), mode="nearest").squeeze(1)

        # Pre-edit: mirror and rotate input before crop processing
        if mirror != "none":
            image, mask = _apply_mirror(image, mask, mirror)
        if rotation != 0:
            image, mask = _apply_rotation(image, mask, rotation)
            B, H, W, C = image.shape

        # Process mask: threshold → expand
        mask = _hipass_filter(mask, mask_threshold)
        mask = _expand_mask(mask, mask_expand)

        pad_val = int(padding)

        # Process each batch item (bbox differs per image)
        result_images = []
        result_masks = []

        for i in range(B):
            sub_img = image[i:i + 1]
            sub_mask = mask[i:i + 1]

            bbox = _find_bbox(sub_mask)
            if bbox is None:
                # Empty mask — use full image
                x, y, w, h = 0, 0, W, H
                log.debug(_LOG_PREFIX, f"Batch {i}: empty mask, using full image")
            else:
                x, y, w, h = bbox

            # Grow context
            if context_expand > 1.0:
                x, y, w, h = _grow_bbox(x, y, w, h, W, H, context_expand)

            c_img, c_mask = _crop_and_resize(
                sub_img, sub_mask, x, y, w, h,
                target_width, target_height, pad_val, rescale_algorithm,
            )
            result_images.append(c_img.squeeze(0))
            result_masks.append(c_mask.squeeze(0))

        out_image = torch.stack(result_images, dim=0).cpu()
        out_mask = torch.stack(result_masks, dim=0).cpu()

        return io.NodeOutput(out_image, out_mask)
