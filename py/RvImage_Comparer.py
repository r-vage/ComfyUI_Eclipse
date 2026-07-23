#
# Image Comparer node - compares two images side by side with a hover slider.
# Inspired by rgthree's Image Comparer, rewritten for ComfyUI V3 API / Nodes 2.0.
#

import os
import json
import random
import time
import numpy as np  # type: ignore
import torch  # type: ignore
import folder_paths  # type: ignore
from PIL import Image  # type: ignore
from PIL.PngImagePlugin import PngInfo  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.image_helpers import flatten_images

_PREFIX_APPEND = "_imgcmp_" + "".join(
    random.choice("abcdefghijklmnopqrstupvxyz") for _ in range(5)
)
_COMPRESS_LEVEL = 1


def _normalize_to_tensor(image):
    # Coerce an image input to a 4-D [B, H, W, C] tensor.
    # Handles: None, Python list of tensors (from image-list connections), 3-D tensors.
    if image is None:
        return None
    if isinstance(image, (list, tuple)):
        if not image:
            return None
        parts = []
        for item in image:
            if isinstance(item, torch.Tensor):
                parts.append(item.unsqueeze(0) if item.dim() == 3 else item)
        return torch.cat(parts, dim=0) if parts else None
    if not isinstance(image, torch.Tensor):
        return None
    if image.dim() == 3:
        image = image.unsqueeze(0)
    return image


def _save_images_to_temp(image_list, side, metadata=None):
    # Save image tensor in list to temp folder and return metadata list for UI.
    results = []
    if not image_list:
        return results

    first_img = image_list[0]
    output_dir = folder_paths.get_temp_directory()
    filename_prefix = f"EclipseCompare{side}" + _PREFIX_APPEND
    full_output_folder, filename, counter, subfolder, _ = (
        folder_paths.get_save_image_path(
            filename_prefix, output_dir, first_img.shape[2], first_img.shape[1]
        )
    )

    for batch_number, image in enumerate(image_list):
        if image.dim() == 4 and image.shape[0] == 1:
            image = image[0]
        i = 255.0 * image.cpu().numpy()
        img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

        filename_with_batch_num = filename.replace("%batch_num%", str(batch_number))
        timestamp = int(time.time() * 1000) % 100000000
        file = f"{filename_with_batch_num}_{counter:05}_{timestamp}_.png"
        img.save(
            os.path.join(full_output_folder, file),
            pnginfo=metadata,
            compress_level=_COMPRESS_LEVEL,
        )

        results.append({"filename": file, "subfolder": subfolder, "type": "temp"})
        counter += 1

    return results


class RvImage_Comparer(io.ComfyNode):

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Comparer [Eclipse]",
            display_name="Image Comparer",
            description="Compares two images with a hover slider or click mode. Connect image_a and image_b to compare, or connect a single batch to auto-split.",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_SAVE_PREVIEW.value,
            is_output_node=True,
            is_input_list=True,
            inputs=[
                io.Image.Input(
                    "image_a",
                    optional=True,
                    tooltip="First image (left side). If only this is provided with a batch, the first two images are compared.",
                ),
                io.Image.Input(
                    "image_b", optional=True, tooltip="Second image (right side)."
                ),
            ],
            outputs=[
                io.Image.Output(
                    "image",
                    is_output_list=True,
                    tooltip="Returns the side selected as default (right-click → 'Default output: A/B'). Defaults to image_b (the new/result image), falling back to image_a if empty.",
                ),
            ],
            hidden=[io.Hidden.unique_id, io.Hidden.prompt, io.Hidden.extra_pnginfo],
        )

    @classmethod
    def execute(cls, image_a=None, image_b=None):
        # Since is_input_list=True, inputs arrive as lists of inputs.
        list_a = flatten_images(image_a)
        list_b = flatten_images(image_b)

        prompt = cls.hidden.prompt
        extra_pnginfo = cls.hidden.extra_pnginfo
        unique_id = cls.hidden.unique_id
        metadata = PngInfo()
        if prompt is not None:
            metadata.add_text("prompt", json.dumps(prompt))
        if extra_pnginfo is not None:
            for x in extra_pnginfo:
                metadata.add_text(x, json.dumps(extra_pnginfo[x]))

        ui_data = {"a_images": [], "b_images": []}

        if list_a:
            ui_data["a_images"] = _save_images_to_temp(list_a, "A", metadata)

        if list_b:
            ui_data["b_images"] = _save_images_to_temp(list_b, "B", metadata)

        # Default output side ("a" or "b") — read from node properties set via right-click menu.
        # Convention: image_b is typically the new/result image, image_a the original.
        # Defaults to "b" so downstream nodes receive the latest result. User can flip via right-click.
        default_side = "b"
        try:
            workflow = (
                (extra_pnginfo or {}).get("workflow")
                if isinstance(extra_pnginfo, dict)
                else None
            )
            if workflow:
                uid_str = unique_id
                for n in workflow.get("nodes", []):
                    if str(n.get("id")) == uid_str:
                        prop = (n.get("properties") or {}).get("default_output")
                        if prop in ("a", "b"):
                            default_side = prop
                        break
        except Exception:
            pass

        # Select output side based on default side setting
        # Use original tensors to preserve their exact shape and dimensions (3D vs 4D)
        def _extract_original_tensors(val):
            tensors = []
            if isinstance(val, (list, tuple)):
                for item in val:
                    tensors.extend(_extract_original_tensors(item))
            elif isinstance(val, torch.Tensor):
                tensors.append(val)
            return tensors

        orig_list_a = _extract_original_tensors(image_a) if image_a is not None else []
        orig_list_b = _extract_original_tensors(image_b) if image_b is not None else []

        a_ok = len(orig_list_a) > 0
        b_ok = len(orig_list_b) > 0
        if default_side == "a":
            output_list = orig_list_a if a_ok else orig_list_b
        else:
            output_list = orig_list_b if b_ok else orig_list_a

        if not output_list:
            empty_batch = torch.zeros((1, 64, 64, 3), dtype=torch.float32)
            return io.NodeOutput([empty_batch], ui=ui_data)

        return io.NodeOutput(output_list, ui=ui_data)
