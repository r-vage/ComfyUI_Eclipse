#
# Get First Image — returns the first image from a batch [B,H,W,C] or a list
# of images. Useful for feeding the first frame of a video / chain into a
# Smart LM image-description task without forcing video summarisation.
#

import torch  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "GetFirstImage"


class RvImage_GetFirstImage(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Get First Image [Eclipse]",
            display_name="Get First Image",
            description="Returns only the first image from a batch tensor [B,H,W,C] "
                        "or a list of image tensors. Single images pass through. "
                        "Use it to feed the first frame of a video / chain into "
                        "Smart LM image tasks (avoids video-mode trimming).",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            inputs=[
                io.Image.Input("image", tooltip="Image batch [B,H,W,C] or list of images."),
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

        first = image[0]
        if isinstance(first, torch.Tensor) and first.dim() == 4 and first.shape[0] > 1:
            count = first.shape[0]
            out = first[:1].contiguous()
        elif isinstance(first, torch.Tensor) and first.dim() == 4:
            count = 1
            out = first
        elif isinstance(first, torch.Tensor) and first.dim() == 3:
            count = 1
            out = first.unsqueeze(0)
        else:
            log.warning(_LOG_PREFIX, f"Unexpected image type: {type(first)}")
            empty = torch.zeros(1, 64, 64, 3)
            return io.NodeOutput(empty, 0)

        # If multiple list items came in, we still return only the first one's
        # first frame — the node's contract is "give me the first image".
        log.debug(_LOG_PREFIX, f"List items={len(image)}, first batch size={count}, output=[1,H,W,C]")
        return io.NodeOutput(out, count)
