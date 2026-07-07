from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "AnyPasser"


class RvRouter_Any_Passer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("input", [io.AnyType])
        return io.Schema(
            node_id="Any Passer [Eclipse]",
            display_name="Any Passer",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value,
            is_input_list=True,
            inputs=[
                io.MatchType.Input(
                    "input",
                    template=type_template,
                    tooltip="Any input to be passed through.",
                ),
            ],
            outputs=[
                io.MatchType.Output(type_template, id="output", is_output_list=True),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, input):
        tag = f"{_LOG_PREFIX} #{cls.hidden.unique_id}"
        log.debug(tag, "Passing input")
        
        if input is None:
            return io.NodeOutput([None])
        if isinstance(input, list):
            if len(input) == 0:
                return io.NodeOutput([None])
            if all(item is None for item in input):
                return io.NodeOutput([None])
                
        return io.NodeOutput(input)
