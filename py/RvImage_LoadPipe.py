import os
import sys
import torch  # type: ignore
import numpy as np  # type: ignore
import folder_paths  # type: ignore
import hashlib

from PIL import Image, ImageOps, ImageSequence  # type: ignore
from typing import List

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "comfy"))

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.image_metadata import extract_image_metadata


def _resolve_image_path(image: str, folder_source: str) -> str:
    # Resolve image file path based on folder source.
    if folder_source == "output":
        output_dir = folder_paths.get_output_directory()
        return os.path.join(output_dir, image)
    return folder_paths.get_annotated_filepath(image)


def _list_input_images() -> list:
    # Walk input directory recursively to include subfolder images.
    _exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif"}
    input_dir = folder_paths.get_input_directory()
    results = []
    for root, _dirs, filenames in os.walk(input_dir):
        for f in filenames:
            if os.path.splitext(f)[1].lower() not in _exts:
                continue
            full = os.path.join(root, f)
            if not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            results.append(rel)
    return sorted(results)


class RvImage_LoadPipe(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        files = _list_input_images()

        return io.Schema(
            node_id="Load Image (Pipe) [Eclipse]",
            display_name="Load Image (Pipe)",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_LOADERS.value,
            inputs=[
                io.Combo.Input(
                    "folder_source",
                    options=["input", "output"],
                    default="input",
                    socketless=True,
                    tooltip="Load images from input or output folder",
                ),
                io.Combo.Input(
                    "image", options=files, tooltip="Select image from input folder"
                ),
                io.Combo.Input(
                    "output_image",
                    options=["none"],
                    default="none",
                    tooltip="Select image from output folder",
                ),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def execute(cls, folder_source: str, image: str, output_image: str = "none"):
        # Use the correct combo value based on folder source
        if folder_source == "output":
            selected = output_image
        else:
            selected = image
        image_path = _resolve_image_path(selected, folder_source)

        img = Image.open(image_path)

        output_images: List[torch.Tensor] = []
        output_masks: List[torch.Tensor] = []
        w, h = None, None

        excluded_formats = ["MPO"]

        for i in ImageSequence.Iterator(img):
            i = ImageOps.exif_transpose(i)

            if i.mode == "I":
                i = i.point(lambda i: i * (1 / 255))
            image_rgb = i.convert("RGB")

            if len(output_images) == 0:
                w = image_rgb.size[0]
                h = image_rgb.size[1]

            if image_rgb.size[0] != w or image_rgb.size[1] != h:
                continue

            image_np = np.array(image_rgb).astype(np.float32) / 255.0
            image_tensor = torch.from_numpy(image_np)[None,]
            if "A" in i.getbands():
                mask_np = np.array(i.getchannel("A")).astype(np.float32) / 255.0
                mask_tensor = 1.0 - torch.from_numpy(mask_np)
            else:
                mask_tensor = torch.zeros((64, 64), dtype=torch.float32, device="cpu")
            output_images.append(image_tensor)
            output_masks.append(mask_tensor.unsqueeze(0))

        if len(output_images) > 1 and img.format not in excluded_formats:
            final_image = torch.cat(output_images, dim=0)
            final_mask = torch.cat(output_masks, dim=0)
        else:
            final_image = output_images[0]
            final_mask = output_masks[0]

        # Extract metadata and pack image + mask into pipe
        pipe = extract_image_metadata(img)
        pipe["width"] = w
        pipe["height"] = h
        pipe["image"] = final_image
        pipe["mask"] = final_mask
        pipe["filepath"] = image_path
        pipe["filename"] = os.path.basename(image_path)
        pipe["source_name"] = os.path.splitext(os.path.basename(image_path))[0]

        return io.NodeOutput(pipe)

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        folder_source = kwargs.get("folder_source", "input")
        if folder_source == "output":
            selected = kwargs.get("output_image", "")
        else:
            selected = kwargs.get("image", "")
        image_path = _resolve_image_path(selected, folder_source)
        m = hashlib.sha256()
        with open(image_path, "rb") as f:
            m.update(f.read())
        return m.digest().hex()

    @classmethod
    def validate_inputs(cls, **kwargs):
        folder_source = kwargs.get("folder_source", "input")
        if folder_source == "output":
            selected = kwargs.get("output_image", "")
            if selected and selected != "none":
                image_path = _resolve_image_path(selected, folder_source)
                if not os.path.isfile(image_path):
                    return "Invalid image file: {}".format(selected)
        else:
            selected = kwargs.get("image", "")
            if not folder_paths.exists_annotated_filepath(selected):
                return "Invalid image file: {}".format(selected)

        return True
