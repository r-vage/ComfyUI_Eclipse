import numpy as np
import torch
import cv2
from collections import namedtuple
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY
from ..extern.impact.utils import make_crop_region

# Ensure SEG is defined
try:
    from ..extern.impact.core import SEG
except ImportError:
    SEG = namedtuple("SEG", ['cropped_image', 'cropped_mask', 'confidence', 'crop_region', 'bbox', 'label', 'control_net_wrapper'], defaults=[None])

class MetadataWrapper:
    def __init__(self, original_wrapper, batch_index):
        self.original_wrapper = original_wrapper
        self.batch_index = batch_index

    def apply(self, *args, **kwargs):
        if self.original_wrapper is not None:
            return self.original_wrapper.apply(*args, **kwargs)
        return args[0], args[1], []

    def doit_ipadapter(self, model):
        if self.original_wrapper is not None:
            return self.original_wrapper.doit_ipadapter(model)
        return model, []

def custom_mask_to_segs(mask, combined, crop_factor, bbox_fill, drop_size=1, label='A', crop_min_size=None, is_contour=True, batch_index=0):
    drop_size = max(drop_size, 1)
    if mask is None:
        return []

    if isinstance(mask, np.ndarray):
        pass
    else:
        try:
            mask = mask.numpy()
        except AttributeError:
            return []

    if mask is None:
        return []

    result = []

    # Ensure mask has batch dimension
    if len(mask.shape) == 2:
        mask = np.expand_dims(mask, axis=0)

    for i in range(mask.shape[0]):
        mask_i = mask[i]

        if combined:
            indices = np.nonzero(mask_i)
            if len(indices[0]) > 0 and len(indices[1]) > 0:
                # 1. Bbox calculation with +1 outer boundary fix
                bbox = (
                    int(np.min(indices[1])),
                    int(np.min(indices[0])),
                    int(np.max(indices[1]) + 1),
                    int(np.max(indices[0]) + 1),
                )
                crop_region = make_crop_region(
                    mask_i.shape[1], mask_i.shape[0], bbox, crop_factor, crop_min_size
                )
                x1, y1, x2, y2 = crop_region

                if x2 - x1 > 0 and y2 - y1 > 0:
                    # 4. Copy to make it contiguous
                    cropped_mask = np.array(mask_i[y1:y2, x1:x2]).copy()

                    if bbox_fill:
                        # 3. Relative coordinate fix
                        bx1, by1, bx2, by2 = bbox
                        rx1 = bx1 - x1
                        ry1 = by1 - y1
                        rx2 = bx2 - x1
                        ry2 = by2 - y1
                        # Avoid out of bounds
                        h_mask, w_mask = cropped_mask.shape
                        ry1 = max(0, min(ry1, h_mask))
                        ry2 = max(0, min(ry2, h_mask))
                        rx1 = max(0, min(rx1, w_mask))
                        rx2 = max(0, min(rx2, w_mask))
                        cropped_mask[ry1:ry2, rx1:rx2] = 1.0

                    if cropped_mask is not None:
                        # Apply torch clip to ensure valid values [0.0, 1.0] and contiguous memory
                        cropped_mask = torch.clip(torch.from_numpy(cropped_mask), 0.0, 1.0).numpy()
                        wrapper = MetadataWrapper(None, batch_index)
                        item = SEG(None, cropped_mask, 1.0, crop_region, bbox, label, wrapper)
                        result.append(item)

        else:
            mask_i_uint8 = (mask_i * 255.0).astype(np.uint8)
            contours, ctree = cv2.findContours(mask_i_uint8, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            for j, contour in enumerate(contours):
                hierarchy = ctree[0][j]
                if hierarchy[3] != -1:
                    continue

                separated_mask = np.zeros_like(mask_i_uint8)
                cv2.drawContours(separated_mask, [contour], 0, 255, -1)
                separated_mask = np.array(separated_mask / 255.0).astype(np.float32)

                x, y, w, h = cv2.boundingRect(contour)
                bbox = x, y, x + w, y + h
                crop_region = make_crop_region(
                    mask_i.shape[1], mask_i.shape[0], bbox, crop_factor, crop_min_size
                )
                x1, y1, x2, y2 = crop_region

                if w > drop_size and h > drop_size:
                    if is_contour:
                        mask_src = separated_mask
                    else:
                        mask_src = mask_i * separated_mask

                    cropped_mask = np.array(mask_src[y1:y2, x1:x2]).copy()

                    if bbox_fill:
                        cx1, cy1, _, _ = crop_region
                        bx1 = x - cx1
                        bx2 = x+w - cx1
                        by1 = y - cy1
                        by2 = y+h - cy1
                        # Avoid out of bounds
                        h_mask, w_mask = cropped_mask.shape
                        by1 = max(0, min(by1, h_mask))
                        by2 = max(0, min(by2, h_mask))
                        bx1 = max(0, min(bx1, w_mask))
                        bx2 = max(0, min(bx2, w_mask))
                        cropped_mask[by1:by2, bx1:bx2] = 1.0

                    if cropped_mask is not None:
                        cropped_mask = torch.clip(torch.from_numpy(cropped_mask), 0.0, 1.0).numpy()
                        wrapper = MetadataWrapper(None, batch_index)
                        item = SEG(None, cropped_mask, 1.0, crop_region, bbox, label, wrapper)
                        result.append(item)

    return result

class RvMask_ToSEGS(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Mask to SEGS [Eclipse]",
            display_name="Mask to SEGS",
            category=CATEGORY.MAIN.value + CATEGORY.MASK.value,
            is_input_list=True,
            inputs=[
                io.Mask.Input("mask", tooltip="Input mask tensor."),
                io.Boolean.Input("combined", default=False, label_on="True", label_off="False", tooltip="Combine all masks into one bounding box."),
                io.Float.Input("crop_factor", default=3.0, min=1.0, max=100.0, step=0.1, tooltip="Crop factor around bounding box."),
                io.Boolean.Input("bbox_fill", default=False, label_on="enabled", label_off="disabled", tooltip="Fill bounding box with 1.0 in cropped mask."),
                io.Int.Input("drop_size", default=10, min=1, max=8192, step=1, tooltip="Minimum contour size to keep."),
                io.Boolean.Input("contour_fill", default=False, label_on="enabled", label_off="disabled", tooltip="Fill contours completely."),
            ],
            outputs=[
                io.Custom("SEGS").Output("segs"),
            ]
        )

    @classmethod
    def execute(cls, mask, combined, crop_factor, bbox_fill, drop_size, contour_fill=False):
        if not mask:
            return io.NodeOutput(((0, 0), []))

        # Flatten list/tuple inputs to ensure we process individual tensors
        flat_mask = []
        for m in mask:
            if isinstance(m, (list, tuple)):
                for sub in m:
                    if sub is not None:
                        flat_mask.append(sub)
            elif m is not None:
                flat_mask.append(m)
        mask = flat_mask

        if not mask:
            return io.NodeOutput(((0, 0), []))

        combined = combined[0] if isinstance(combined, list) else combined
        crop_factor = crop_factor[0] if isinstance(crop_factor, list) else crop_factor
        bbox_fill = bbox_fill[0] if isinstance(bbox_fill, list) else bbox_fill
        drop_size = drop_size[0] if isinstance(drop_size, list) else drop_size
        contour_fill = contour_fill[0] if isinstance(contour_fill, list) and contour_fill else (contour_fill if isinstance(contour_fill, bool) else False)

        h, w = 0, 0
        all_segs = []
        is_list_of_masks = len(mask) > 1

        for item_idx, item in enumerate(mask):
            if item is None:
                continue

            if isinstance(item, torch.Tensor):
                if item.ndim == 2:
                    item = item.unsqueeze(0)
            elif isinstance(item, np.ndarray):
                if item.ndim == 2:
                    item = np.expand_dims(item, axis=0)

            # Record dimensions of the first valid mask
            if h == 0 or w == 0:
                h, w = item.shape[1], item.shape[2]

            batch_size = item.shape[0]
            for i in range(batch_size):
                slice_mask = item[i:i+1] # Keep 3D shape (1, H, W)
                
                target_batch_idx = item_idx if is_list_of_masks else i
                
                segs = custom_mask_to_segs(
                    slice_mask, 
                    combined=combined, 
                    crop_factor=crop_factor, 
                    bbox_fill=bbox_fill, 
                    drop_size=drop_size, 
                    is_contour=contour_fill, 
                    batch_index=target_batch_idx
                )
                all_segs.extend(segs)

        return io.NodeOutput(((h, w), all_segs))
