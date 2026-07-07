#
# Get Last Image — returns the last image from a batch [B,H,W,C] or a list of
# images. Useful for feeding the most recent frame of a video / chain into a
# Smart LM image-description task without forcing video summarisation.
#

import torch  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log
from ..core.image_helpers import flatten_images

_LOG_PREFIX = "GetLastImage"


class RvImage_GetLast(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Get Last Image [Eclipse]",
            display_name="Get Last Image",
            description="Returns only the last image from a batch tensor [B,H,W,C] "
            "or a list of image tensors. Single images pass through. "
            "Use it to feed the final frame of a video / chain into "
            "Smart LM image tasks (avoids video-mode trimming).",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_BATCH.value,
            inputs=[
                io.Image.Input(
                    "image", tooltip="Image batch [B,H,W,C] or list of images."
                ),
            ],
            outputs=[
                io.Image.Output("image"),
                io.Int.Output("count"),
            ],
            is_input_list=True,
        )

    @classmethod
    def execute(cls, image):
        # is_input_list=True: `image` is always a list (length 1 for normal
        # inputs, length N when an upstream list is connected).
        if not image:
            log.warning(_LOG_PREFIX, "No image input provided.")
            empty = torch.zeros(1, 64, 64, 3)
            return io.NodeOutput(empty, 0)

        flat_images = flatten_images(image)
        if not flat_images:
            log.warning(_LOG_PREFIX, "No valid image frames found in input.")
            empty = torch.zeros(1, 64, 64, 3)
            return io.NodeOutput(empty, 0)

        count = len(flat_images)
        out = flat_images[-1]

        log.debug(
            _LOG_PREFIX,
            f"List items={len(image)}, total frames={count}, output=[1,H,W,C]",
        )
        return io.NodeOutput(out, count)
