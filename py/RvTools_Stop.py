import nodes  # type: ignore
from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY


class RvTools_Stop(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("any_type")
        return io.Schema(
            node_id="Stop [Eclipse]",
            display_name="Stop",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            is_input_list=True,
            inputs=[
                io.MatchType.Input("input", template=type_template),
            ],
            outputs=[
                io.MatchType.Output(type_template, id="output", is_output_list=True),
            ],
        )

    @classmethod
    def validate_inputs(cls, **kwargs):
        return True

    @classmethod
    def execute(cls, input):
        out = input
        nodes.interrupt_processing()
        return io.NodeOutput(out)
