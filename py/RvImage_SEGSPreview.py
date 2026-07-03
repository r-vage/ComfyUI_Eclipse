# SEGS Preview node implementation (from Impact Pack)
# Previews individual segments from a SEGS input with optional alpha masking.

import os
import random
import time

import numpy as np  # type: ignore
import torch  # type: ignore
import folder_paths  # type: ignore
from PIL import Image  # type: ignore
from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "SEGSPreview"

# Class-level state
_output_dir = folder_paths.get_temp_directory()
_type = "temp"
_prefix_append = "_temp_" + ''.join(random.choice("abcdefghijklmnopqrstupvxyz") for x in range(5))


# -- Standalone utility functions --

def _tensor2pil(image):
    # Convert a single-image tensor [1, H, W, C] to PIL
    return Image.fromarray(np.clip(255. * image.cpu().numpy().squeeze(0), 0, 255).astype(np.uint8))


def _to_pil(image):
    # Convert tensor or numpy to PIL
    if isinstance(image, Image.Image):
        return image
    if isinstance(image, torch.Tensor):
        return _tensor2pil(image)
    if isinstance(image, np.ndarray):
        return Image.fromarray(np.clip(255. * image.squeeze(0), 0, 255).astype(np.uint8))
    raise ValueError(f"Cannot convert {type(image)} to PIL.Image")


def _to_binary_mask(mask, threshold=0):
    # Convert mask to binary (0/1) based on threshold
    if isinstance(mask, np.ndarray):
        mask = torch.from_numpy(mask)
    if len(mask.shape) == 4:
        mask = mask.squeeze(0)
    mask = mask.clone().cpu()
    mask[mask > threshold] = 1.
    mask[mask <= threshold] = 0.
    return mask


def _crop_image(image, crop_region):
    # Crop a [B, H, W, C] tensor to the given (x1, y1, x2, y2) region
    x1, y1, x2, y2 = crop_region
    return image[:, y1:y2, x1:x2, :]


def _segs_scale_match(segs, target_shape):
    # Scale SEGS coordinates/masks to match a different-sized target image
    h = segs[0][0]
    w = segs[0][1]
    th = target_shape[1]
    tw = target_shape[2]

    if (h == th and w == tw) or h == 0 or w == 0:
        return segs

    rh = th / h
    rw = tw / w

    new_segs = []
    for seg in segs[1]:
        cropped_image = seg.cropped_image
        cropped_mask = seg.cropped_mask
        x1, y1, x2, y2 = seg.crop_region
        bx1, by1, bx2, by2 = seg.bbox

        crop_region = int(x1 * rw), int(y1 * rh), int(x2 * rw), int(y2 * rh)
        bbox = int(bx1 * rw), int(by1 * rh), int(bx2 * rw), int(by2 * rh)
        new_w = crop_region[2] - crop_region[0]
        new_h = crop_region[3] - crop_region[1]

        if isinstance(cropped_mask, np.ndarray):
            cropped_mask = torch.from_numpy(cropped_mask)

        if isinstance(cropped_mask, torch.Tensor) and len(cropped_mask.shape) == 3:
            cropped_mask = torch.nn.functional.interpolate(
                cropped_mask.unsqueeze(0), size=(new_h, new_w), mode='bilinear', align_corners=False
            ).squeeze(0)
        else:
            cropped_mask = torch.nn.functional.interpolate(
                cropped_mask.unsqueeze(0).unsqueeze(0), size=(new_h, new_w), mode='bilinear', align_corners=False
            ).squeeze(0).squeeze(0).numpy()

        if cropped_image is not None:
            img_t = cropped_image if isinstance(cropped_image, torch.Tensor) else torch.from_numpy(cropped_image)
            # Resize via permute to [B,C,H,W] -> interpolate -> back to [B,H,W,C]
            img_t = img_t.permute(0, 3, 1, 2)
            img_t = torch.nn.functional.interpolate(img_t, size=(new_h, new_w), mode='bilinear', align_corners=False)
            img_t = img_t.permute(0, 2, 3, 1)
            cropped_image = img_t.numpy()

        # Reconstruct SEG namedtuple - Impact Pack's SEG has 7 fields
        new_seg = type(seg)(cropped_image, cropped_mask, seg.confidence, crop_region, bbox, seg.label, seg.control_net_wrapper)
        new_segs.append(new_seg)

    return (th, tw), new_segs


def _compute_tight_crop(segs, source_image, padding):
    # Compute union bounding box of all segments and crop from source image.
    # Uses bbox (tighter) rather than crop_region (includes detector padding).
    # Returns cropped tensor [B, H, W, C] or None if no segments/source.
    if source_image is None or len(segs[1]) == 0:
        return None

    img_h = source_image.shape[1]
    img_w = source_image.shape[2]

    # Union of all segment bboxes
    min_x = img_w
    min_y = img_h
    max_x = 0
    max_y = 0

    for seg in segs[1]:
        bx1, by1, bx2, by2 = seg.bbox
        min_x = min(min_x, bx1)
        min_y = min(min_y, by1)
        max_x = max(max_x, bx2)
        max_y = max(max_y, by2)

    if max_x <= min_x or max_y <= min_y:
        return None

    # Apply padding (can be negative to shrink), clamped to image bounds
    min_x = max(0, min(img_w - 1, min_x - padding))
    min_y = max(0, min(img_h - 1, min_y - padding))
    max_x = max(min_x + 1, min(img_w, max_x + padding))
    max_y = max(min_y + 1, min(img_h, max_y + padding))

    return source_image[:, min_y:max_y, min_x:max_x, :]


class RvImage_SEGSPreview(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="SEGS Preview [Eclipse]",
            display_name="SEGS Preview",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            is_output_node=True,
            is_input_list=True,
            inputs=[
                io.Custom("SEGS").Input("segs", tooltip="SEGS data from detection/segmentation nodes."),
                io.Boolean.Input("alpha_mode", default=True, label_on="enable", label_off="disable",
                                 tooltip="When enabled, applies alpha transparency using the segment mask."),
                io.Float.Input("min_alpha", default=0.2, min=0.0, max=1.0, step=0.01,
                               tooltip="Minimum alpha value to apply. 0 = fully transparent outside mask."),
                io.Int.Input("crop_padding", default=10, min=-512, max=512, step=1,
                             tooltip="Padding around the tight crop bounding box. Negative values shrink the crop inward."),
                io.Image.Input("fallback_image_opt", optional=True,
                               tooltip="Source image to crop from. Required for tight_crop output and when SEGS has no cropped images."),
            ],
            outputs=[
                io.Image.Output("IMAGE", tooltip="List of per-segment cropped images (with optional alpha masking)."),
                io.Image.Output("tight_crop", tooltip="Clean crop from source image around all segments. No masking, no black areas."),
            ],
        )

    @classmethod
    def execute(cls, segs, alpha_mode=True, min_alpha=0.2, crop_padding=10, fallback_image_opt=None):
        if not segs:
            empty_img = torch.zeros((1, 64, 64, 3), dtype=torch.float32)
            return io.NodeOutput(empty_img, empty_img, ui={"images": []})
        segs = segs[0]

        alpha_mode = alpha_mode[0] if isinstance(alpha_mode, list) else alpha_mode
        min_alpha = min_alpha[0] if isinstance(min_alpha, list) else min_alpha
        crop_padding = crop_padding[0] if isinstance(crop_padding, list) else crop_padding

        # Flatten fallback_image_opt list into individual frames
        fallback_list = []
        if fallback_image_opt is not None:
            for img in fallback_image_opt:
                if img is not None:
                    if img.ndim == 3:
                        fallback_list.append(img.unsqueeze(0))
                    else:
                        for j in range(img.shape[0]):
                            fallback_list.append(img[j:j+1])
        if not fallback_list:
            fallback_list = None

        h, w = segs[0][1], segs[0][0]
        ref_image_for_tight = None
        if fallback_list is not None:
            # Use the first fallback image shape for scaling match
            segs = _segs_scale_match(segs, fallback_list[0].shape)
            h, w = fallback_list[0].shape[2], fallback_list[0].shape[1]
            
            # Stack fallback_list back to B, H, W, C tensor for compute_tight_crop
            ref_image_for_tight = torch.cat(fallback_list, dim=0)

        # Tight crop: clean bounding box crop from source image, no masking
        tight_crop = _compute_tight_crop(segs, ref_image_for_tight, crop_padding)
        if tight_crop is None:
            # Empty 64x64 placeholder when no source image or no segments
            tight_crop = torch.zeros((1, 64, 64, 3), dtype=torch.float32)

        full_output_folder, filename, counter, subfolder, filename_prefix = \
            folder_paths.get_save_image_path("eclipse_seg_preview" + _prefix_append, _output_dir, h, w)

        results = []
        result_image_list = []

        if min_alpha != 0:
            min_alpha_int = int(255 * min_alpha)
        else:
            min_alpha_int = 0

        if len(segs[1]) > 0:
            if segs[1][0].cropped_image is not None:
                batch_count = len(segs[1][0].cropped_image)
            elif fallback_list is not None:
                batch_count = len(fallback_list)
            else:
                return io.NodeOutput([], tight_crop, ui={"images": results})

            for seg in segs[1]:
                result_image_batch = None
                cached_mask = None

                def get_combined_mask():
                    nonlocal cached_mask
                    if cached_mask is not None:
                        return cached_mask

                    if isinstance(seg.cropped_mask, np.ndarray):
                        masks = torch.tensor(seg.cropped_mask)
                    else:
                        masks = seg.cropped_mask

                    cached_mask = (masks[0] * 255).to(torch.uint8)
                    for x in masks[1:]:
                        cached_mask |= (x * 255).to(torch.uint8)
                    cached_mask = (cached_mask / 255.0).to(torch.float32)
                    cached_mask = _to_binary_mask(cached_mask, 0.1)
                    cached_mask = cached_mask.numpy()
                    return cached_mask

                def stack_image(image, mask=None):
                    nonlocal result_image_batch
                    if isinstance(image, np.ndarray):
                        image = torch.from_numpy(image)
                    if mask is not None:
                        image = image * torch.tensor(mask)[None, ..., None]
                    if result_image_batch is None:
                        result_image_batch = image
                    else:
                        result_image_batch = torch.concat((result_image_batch, image), dim=0)

                target_indices = range(batch_count)
                if hasattr(seg, 'control_net_wrapper') and hasattr(seg.control_net_wrapper, 'batch_index'):
                    b_idx = seg.control_net_wrapper.batch_index
                    if 0 <= b_idx < batch_count:
                        target_indices = [b_idx]

                for i in target_indices:
                    cropped_image = None

                    if seg.cropped_image is not None:
                        c_idx = i if i < len(seg.cropped_image) else 0
                        cropped_image = seg.cropped_image[c_idx, None]
                    elif fallback_list is not None:
                        ref_image = fallback_list[i]
                        cropped_image = _crop_image(ref_image, seg.crop_region)

                    if cropped_image is not None:
                        if isinstance(cropped_image, np.ndarray):
                            cropped_image = torch.from_numpy(cropped_image)

                        cropped_image = cropped_image.clone()
                        cropped_pil = _to_pil(cropped_image)

                        if alpha_mode:
                            if isinstance(seg.cropped_mask, np.ndarray):
                                cropped_mask = seg.cropped_mask
                            else:
                                if seg.cropped_image is not None and len(seg.cropped_image) != len(seg.cropped_mask):
                                    cropped_mask = get_combined_mask()
                                else:
                                    cropped_mask = seg.cropped_mask[i if i < len(seg.cropped_mask) else 0].numpy()

                            mask_array = (cropped_mask * 255).astype(np.uint8)

                            if min_alpha_int != 0:
                                mask_array[mask_array < min_alpha_int] = min_alpha_int

                            mask_pil = Image.fromarray(mask_array, mode='L').resize(cropped_pil.size)
                            cropped_pil.putalpha(mask_pil)
                            stack_image(cropped_image, cropped_mask)
                        else:
                            stack_image(cropped_image)

                        timestamp = int(time.time() * 1000) % 100000000
                        file = f"{filename}_{counter:05}_{timestamp}_.webp"
                        cropped_pil.save(os.path.join(full_output_folder, file))
                        results.append({
                            "filename": file,
                            "subfolder": subfolder,
                            "type": _type
                        })
                        counter += 1

                if result_image_batch is not None:
                    result_image_list.append(result_image_batch)

        # Build a proper [B, H, W, C] tensor from all segments.
        # Segments may have different crop sizes, so resize all to match the first.
        if len(result_image_list) > 0:
            target_h = result_image_list[0].shape[1]
            target_w = result_image_list[0].shape[2]
            resized = []
            for img_batch in result_image_list:
                if img_batch.shape[1] != target_h or img_batch.shape[2] != target_w:
                    img_batch = img_batch.permute(0, 3, 1, 2)
                    img_batch = torch.nn.functional.interpolate(img_batch, size=(target_h, target_w), mode='bilinear', align_corners=False)
                    img_batch = img_batch.permute(0, 2, 3, 1)
                resized.append(img_batch)
            image_output = torch.cat(resized, dim=0)
        else:
            image_output = torch.zeros((1, 64, 64, 3), dtype=torch.float32)

        return io.NodeOutput(image_output, tight_crop, ui={"images": results})
