# Image Passer — pass an image through with fixed type.

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY


class RvRouter_Image_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("image", [io.Image])
        return io.Schema(
            node_id="Image Passer [Eclipse]",
            display_name="Image Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.MatchType.Input(
                    "image",
                    template=type_template,
                    tooltip="Image input to be passed through.",
                ),
            ],
            outputs=[
                io.MatchType.Output(type_template, id="image"),
            ],
        )

    @classmethod
    def execute(cls, image):
        return io.NodeOutput(image)
