# Clip Passer — pass a clip through with fixed type.

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY


class RvRouter_Clip_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("clip", [io.Clip])
        return io.Schema(
            node_id="Clip Passer [Eclipse]",
            display_name="Clip Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.MatchType.Input(
                    "clip",
                    template=type_template,
                    tooltip="Clip input to be passed through.",
                ),
            ],
            outputs=[
                io.MatchType.Output(type_template, id="clip"),
            ],
        )

    @classmethod
    def execute(cls, clip):
        return io.NodeOutput(clip)
