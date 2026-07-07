import os
import json
import random
import time
import numpy as np  # type: ignore
import torch  # type: ignore
import folder_paths  # type: ignore
import comfy.utils  # type: ignore

from PIL import Image  # type: ignore
from PIL.PngImagePlugin import PngInfo  # type: ignore
from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.logger import log
from ..core.image_helpers import unwrap_value

_LOG_PREFIX = "PreviewImageDOM_Stop"

# Class-level state (initialized once at module load)
_output_dir = folder_paths.get_temp_directory()
_type = "temp"
_compress_level = 1
_prefix_append = "_temp_" + "".join(
    random.choice("abcdefghijklmnopqrstupvxyz") for x in range(5)
)


class RvImage_PreviewDom_Stop(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Preview Image (DOM) [Stop] [Eclipse]",
            display_name="Preview Image (DOM) [Stop]",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_SAVE_PREVIEW.value,
            is_output_node=True,
            is_input_list=True,
            inputs=[
                io.Image.Input("images", tooltip="Batch of images to preview"),
                io.Boolean.Input(
                    "stop_review",
                    default=False,
                    label_on="active",
                    label_off="bypass",
                    socketless=True,
                    display_name="Stop (Result Review)",
                ),
            ],
            outputs=[
                io.Image.Output("IMAGE", is_output_list=True),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo],
        )

    @classmethod
    def execute(cls, images, stop_review=False):
        tag = f"{_LOG_PREFIX}"
        filename_prefix = "ComfyUI"

        # Flatten input images (can be lists of tensors or batches of tensors)
        flat_list = []

        def _process(item):
            if isinstance(item, (list, tuple)):
                for sub in item:
                    _process(sub)
            elif isinstance(item, torch.Tensor):
                if item.dim() == 4:
                    for i in range(item.shape[0]):
                        flat_list.append(item[i : i + 1, ...])
                elif item.dim() == 3:
                    flat_list.append(item.unsqueeze(0))
            elif isinstance(item, np.ndarray):
                if item.ndim == 4:
                    for i in range(item.shape[0]):
                        flat_list.append(item[i : i + 1, ...])
                elif item.ndim == 3:
                    flat_list.append(np.expand_dims(item, axis=0))
            elif item is not None:
                flat_list.append(item)

        _process(images)
        # log.debug(tag, f"Input: images={log.format_value(images)}")

        if len(flat_list) == 0:
            log.warning(tag, "No input images received.")
            return io.NodeOutput(images, ui={"images": []})

        first_img = flat_list[0]
        if hasattr(first_img, "shape") and first_img.shape[0] == 1:
            height, width = first_img.shape[1], first_img.shape[2]
        else:
            height, width = first_img.shape[0], first_img.shape[1]

        prompt = cls.hidden.prompt
        extra_pnginfo = cls.hidden.extra_pnginfo
        metadata = PngInfo()
        if prompt is not None:
            metadata.add_text("prompt", json.dumps(prompt))
        if extra_pnginfo is not None:
            for x in extra_pnginfo:
                metadata.add_text(x, json.dumps(extra_pnginfo[x]))

        filename_prefix += _prefix_append
        full_output_folder, filename, counter, subfolder, filename_prefix = (
            folder_paths.get_save_image_path(
                filename_prefix, _output_dir, width, height
            )
        )
        results = []
        pbar = comfy.utils.ProgressBar(len(flat_list))

        for batch_number, image in enumerate(flat_list):
            if hasattr(image, "shape") and image.ndim == 4 and image.shape[0] == 1:
                image = image.squeeze(0)

            i = 255.0 * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            filename_with_batch_num = filename.replace("%batch_num%", str(batch_number))
            timestamp = int(time.time() * 1000) % 100000000
            file = f"{filename_with_batch_num}_{counter:05}_{timestamp}_.png"
            filepath = os.path.join(full_output_folder, file)
            img.save(filepath, pnginfo=metadata, compress_level=_compress_level)
            results.append({"filename": file, "subfolder": subfolder, "type": _type})
            pbar.update(1)
            counter += 1

        is_stop = unwrap_value(stop_review, False)
        if is_stop:
            import nodes

            log.debug(tag, "Workflow review stop triggered. Interrupting execution.")
            nodes.interrupt_processing()

        # log.debug(tag, f"Output: images={log.format_value(images)} (UI={results})")
        return io.NodeOutput(images, ui={"images": results})
