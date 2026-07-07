# SEGS Preview Simple node implementation
# Previews individual segments from a SEGS input with alpha masking enabled, min alpha 0.2, and no crop padding.

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

_LOG_PREFIX = "SEGSPreviewSimple"

# Class-level state
_output_dir = folder_paths.get_temp_directory()
_type = "temp"
_prefix_append = "_temp_" + "".join(
    random.choice("abcdefghijklmnopqrstupvxyz") for x in range(5)
)


# -- Standalone utility functions --


def _tensor2pil(image):
    return Image.fromarray(
        np.clip(255.0 * image.cpu().numpy().squeeze(0), 0, 255).astype(np.uint8)
    )


def _to_pil(image):
    if isinstance(image, Image.Image):
        return image
    if isinstance(image, torch.Tensor):
        return _tensor2pil(image)
    if isinstance(image, np.ndarray):
        return Image.fromarray(
            np.clip(255.0 * image.squeeze(0), 0, 255).astype(np.uint8)
        )
    raise ValueError(f"Cannot convert {type(image)} to PIL.Image")


def _to_binary_mask(mask, threshold=0):
    if isinstance(mask, np.ndarray):
        mask = torch.from_numpy(mask)
    if len(mask.shape) == 4:
        mask = mask.squeeze(0)
    mask = mask.clone().cpu()
    mask[mask > threshold] = 1.0
    mask[mask <= threshold] = 0.0
    return mask


def _crop_image(image, crop_region):
    x1, y1, x2, y2 = crop_region
    return image[:, y1:y2, x1:x2, :]


def _segs_scale_match(segs, target_shape):
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
                cropped_mask.unsqueeze(0),
                size=(new_h, new_w),
                mode="bilinear",
                align_corners=False,
            ).squeeze(0)
        else:
            cropped_mask = (
                torch.nn.functional.interpolate(
                    cropped_mask.unsqueeze(0).unsqueeze(0),
                    size=(new_h, new_w),
                    mode="bilinear",
                    align_corners=False,
                )
                .squeeze(0)
                .squeeze(0)
                .numpy()
            )

        if cropped_image is not None:
            img_t = (
                cropped_image
                if isinstance(cropped_image, torch.Tensor)
                else torch.from_numpy(cropped_image)
            )
            img_t = img_t.permute(0, 3, 1, 2)
            img_t = torch.nn.functional.interpolate(
                img_t, size=(new_h, new_w), mode="bilinear", align_corners=False
            )
            img_t = img_t.permute(0, 2, 3, 1)
            cropped_image = img_t.numpy()

        new_seg = type(seg)(
            cropped_image,
            cropped_mask,
            seg.confidence,
            crop_region,
            bbox,
            seg.label,
            seg.control_net_wrapper,
        )
        new_segs.append(new_seg)

    return (th, tw), new_segs


class RvImage_Preview_SEGS_Simple(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="SEGS Preview Simple [Eclipse]",
            display_name="SEGS Preview Simple",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_SAVE_PREVIEW.value,
            is_output_node=True,
            is_input_list=True,
            inputs=[
                io.Custom("SEGS").Input(
                    "segs", tooltip="SEGS data from detection/segmentation nodes."
                ),
                io.Image.Input(
                    "fallback_image_opt",
                    optional=True,
                    tooltip="Source image to crop from. Required when SEGS has no cropped images.",
                ),
            ],
            outputs=[
                io.Image.Output(
                    "IMAGE",
                    tooltip="List of per-segment cropped images (with alpha masking).",
                ),
            ],
        )

    @classmethod
    def execute(cls, segs, fallback_image_opt=None):
        if not segs:
            return io.NodeOutput(
                torch.zeros((1, 64, 64, 3), dtype=torch.float32), ui={"images": []}
            )
        segs = segs[0]

        # Flatten fallback_image_opt list into individual frames
        fallback_list = []
        if fallback_image_opt is not None:
            for img in fallback_image_opt:
                if img is not None:
                    if img.ndim == 3:
                        fallback_list.append(img.unsqueeze(0))
                    else:
                        for j in range(img.shape[0]):
                            fallback_list.append(img[j : j + 1])
        if not fallback_list:
            fallback_list = None

        h, w = segs[0][1], segs[0][0]
        if fallback_list is not None:
            # Use the first fallback image shape for scaling match
            segs = _segs_scale_match(segs, fallback_list[0].shape)
            h, w = fallback_list[0].shape[2], fallback_list[0].shape[1]

        full_output_folder, filename, counter, subfolder, filename_prefix = (
            folder_paths.get_save_image_path(
                "eclipse_seg_preview_simple" + _prefix_append, _output_dir, h, w
            )
        )

        results = []
        result_image_list = []

        # Hardcoded: alpha_mode=True, min_alpha=0.2
        min_alpha_int = int(255 * 0.2)

        if len(segs[1]) > 0:
            if segs[1][0].cropped_image is not None:
                batch_count = len(segs[1][0].cropped_image)
            elif fallback_list is not None:
                batch_count = len(fallback_list)
            else:
                return io.NodeOutput([], ui={"images": results})

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
                        result_image_batch = torch.concat(
                            (result_image_batch, image), dim=0
                        )

                target_indices = range(batch_count)
                if hasattr(seg, "control_net_wrapper") and hasattr(
                    seg.control_net_wrapper, "batch_index"
                ):
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

                        if isinstance(seg.cropped_mask, np.ndarray):
                            cropped_mask = seg.cropped_mask
                        else:
                            if seg.cropped_image is not None and len(
                                seg.cropped_image
                            ) != len(seg.cropped_mask):
                                cropped_mask = get_combined_mask()
                            else:
                                cropped_mask = seg.cropped_mask[
                                    i if i < len(seg.cropped_mask) else 0
                                ].numpy()

                        mask_array = (cropped_mask * 255).astype(np.uint8)

                        if min_alpha_int != 0:
                            mask_array[mask_array < min_alpha_int] = min_alpha_int

                        mask_pil = Image.fromarray(mask_array, mode="L").resize(
                            cropped_pil.size
                        )
                        cropped_pil.putalpha(mask_pil)
                        stack_image(cropped_image, cropped_mask)

                        timestamp = int(time.time() * 1000) % 100000000
                        file = f"{filename}_{counter:05}_{timestamp}_.webp"
                        cropped_pil.save(os.path.join(full_output_folder, file))
                        results.append(
                            {"filename": file, "subfolder": subfolder, "type": _type}
                        )
                        counter += 1

                if result_image_batch is not None:
                    result_image_list.append(result_image_batch)

        if len(result_image_list) > 0:
            target_h = result_image_list[0].shape[1]
            target_w = result_image_list[0].shape[2]
            resized = []
            for img_batch in result_image_list:
                if img_batch.shape[1] != target_h or img_batch.shape[2] != target_w:
                    img_batch = img_batch.permute(0, 3, 1, 2)
                    img_batch = torch.nn.functional.interpolate(
                        img_batch,
                        size=(target_h, target_w),
                        mode="bilinear",
                        align_corners=False,
                    )
                    img_batch = img_batch.permute(0, 2, 3, 1)
                resized.append(img_batch)
            image_output = torch.cat(resized, dim=0)
        else:
            image_output = torch.zeros((1, 64, 64, 3), dtype=torch.float32)

        return io.NodeOutput(image_output, ui={"images": results})
