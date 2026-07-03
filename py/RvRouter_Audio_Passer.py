# Audio Passer — pass audio through with fixed type.

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY


class RvRouter_Audio_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Audio Passer [Eclipse]",
            display_name="Audio Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.Audio.Input("audio", tooltip="Audio input to be passed through."),
            ],
            outputs=[
                io.Audio.Output("audio"),
            ],
        )

    @classmethod
    def execute(cls, audio):
        return io.NodeOutput(audio)
