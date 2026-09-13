# MiniMax H3 Image Prompt Conditioning [Eclipse]
#
# Encodes one H3 visual prompt image without creating temporal keyframes. This
# lets continuation workflows reserve frame 0 for generated overlap or a hard
# cut while hidden-anchor and hidden-endpoint tasks tokenize only their
# destination image.

import comfy.utils  # type: ignore
import node_helpers  # type: ignore
from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY


class RvVideo_MiniMaxH3ImagePromptConditioning(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMax H3 Image Prompt Conditioning [Eclipse]",
            display_name="MiniMax H3 Image Prompt Conditioning",
            description=(
                "Encodes the destination image used by a hidden transition anchor "
                "or bridge endpoint when present, otherwise the current image, "
                "into H3 visual prompt tokens without adding temporal keyframes."
            ),
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            inputs=[
                io.Clip.Input("clip"),
                io.String.Input("prompt", multiline=True, dynamic_prompts=True),
                io.Conditioning.Input("base_conditioning", optional=True),
                io.Image.Input("current_image"),
                io.Image.Input("visible_transition_image", optional=True),
                io.Int.Input("width", default=1344, min=32, max=16384, step=32),
                io.Int.Input("height", default=768, min=32, max=16384, step=32),
            ],
            outputs=[io.Conditioning.Output("positive")],
        )

    @classmethod
    def execute(
        cls,
        clip,
        prompt,
        current_image,
        width,
        height,
        base_conditioning=None,
        visible_transition_image=None,
    ):
        images = [
            visible_transition_image
            if visible_transition_image is not None
            else current_image
        ]

        prompt_images = []
        for image in images:
            frame = image[:1, ..., :3].movedim(-1, 1)
            frame = comfy.utils.common_upscale(
                frame, width, height, "lanczos", "disabled"
            ).movedim(1, -1)
            prompt_images.append(frame)
        tokens = clip.tokenize(prompt, images=prompt_images)
        conditioning = clip.encode_from_tokens_scheduled(tokens)
        if base_conditioning:
            keyframes = base_conditioning[0][1].get("minimax_keyframes")
            if keyframes:
                conditioning = node_helpers.conditioning_set_values(
                    conditioning, {"minimax_keyframes": keyframes}
                )
        return io.NodeOutput(conditioning)
