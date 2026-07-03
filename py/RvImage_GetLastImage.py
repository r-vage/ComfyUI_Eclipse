#
# Get Last Image — returns the last image from a batch [B,H,W,C] or a list of
# images. Useful for feeding the most recent frame of a video / chain into a
# Smart LM image-description task without forcing video summarisation.
#

import torch  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "GetLastImage"


class RvImage_GetLastImage(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Get Last Image [Eclipse]",
            display_name="Get Last Image",
            description="Returns only the last image from a batch tensor [B,H,W,C] "
                        "or a list of image tensors. Single images pass through. "
                        "Use it to feed the final frame of a video / chain into "
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

        last = image[-1]
        if isinstance(last, torch.Tensor) and last.dim() == 4 and last.shape[0] > 1:
            count = last.shape[0]
            out = last[-1:].contiguous()
        elif isinstance(last, torch.Tensor) and last.dim() == 4:
            count = 1
            out = last
        elif isinstance(last, torch.Tensor) and last.dim() == 3:
            count = 1
            out = last.unsqueeze(0)
        else:
            log.warning(_LOG_PREFIX, f"Unexpected image type: {type(last)}")
            empty = torch.zeros(1, 64, 64, 3)
            return io.NodeOutput(empty, 0)

        # If multiple list items came in, we still return only the last one's
        # last frame — the node's contract is "give me the last image".
        log.debug(_LOG_PREFIX, f"List items={len(image)}, last batch size={count}, output=[1,H,W,C]")
        return io.NodeOutput(out, count)
