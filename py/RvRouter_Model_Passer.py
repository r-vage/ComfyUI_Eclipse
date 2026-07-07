# Model Passer — pass a model through with fixed type.

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY


class RvRouter_Model_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("model", [io.Model])
        return io.Schema(
            node_id="Model Passer [Eclipse]",
            display_name="Model Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value + CATEGORY.TYPED.value,
            inputs=[
                io.MatchType.Input(
                    "model",
                    template=type_template,
                    tooltip="Model input to be passed through.",
                ),
            ],
            outputs=[
                io.MatchType.Output(type_template, id="model"),
            ],
        )

    @classmethod
    def execute(cls, model):
        return io.NodeOutput(model)
