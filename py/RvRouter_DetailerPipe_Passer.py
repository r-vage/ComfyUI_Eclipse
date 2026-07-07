# Detailer Pipe Passer — pass a detailer pipe through with fixed type.

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY


class RvRouter_DetailerPipe_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("pipe", [io.Custom("DETAILER_PIPE")])
        return io.Schema(
            node_id="Detailer Pipe Passer [Eclipse]",
            display_name="Detailer Pipe Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.MatchType.Input(
                    "pipe",
                    template=type_template,
                    tooltip="Detailer pipe input to be passed through.",
                ),
            ],
            outputs=[
                io.MatchType.Output(type_template, id="pipe"),
            ],
        )

    @classmethod
    def execute(cls, pipe):
        return io.NodeOutput(pipe)
