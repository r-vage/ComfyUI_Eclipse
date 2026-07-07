from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY, purge_vram
from ..core.logger import log

_LOG_PREFIX = "AnyDualSwitch_Purge"


class RvRouter_Any_DualSwitch_purge(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("any_type")
        return io.Schema(
            node_id="Any Dual-Switch Purge [Eclipse]",
            display_name="Any Dual-Switch Purge",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value,
            is_input_list=True,
            inputs=[
                io.Int.Input(
                    "Input",
                    default=1,
                    min=1,
                    max=2,
                    tooltip="Select which input to output (1 or 2).",
                ),
                io.Boolean.Input(
                    "Purge_VRAM",
                    default=False,
                    tooltip="If True, purges VRAM before switching.",
                ),
                io.MatchType.Input(
                    "input1",
                    template=type_template,
                    optional=True,
                    tooltip="First input (any type).",
                ),
                io.MatchType.Input(
                    "input2",
                    template=type_template,
                    optional=True,
                    tooltip="Second input (any type).",
                ),
            ],
            outputs=[
                io.MatchType.Output(
                    type_template, id="*", is_output_list=True, tooltip="The selected output."
                ),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, Input, Purge_VRAM, input1=None, input2=None):
        tag = f"{_LOG_PREFIX} #{cls.hidden.unique_id}"

        # Unwrap wrapped list arguments since is_input_list=True
        select_val = Input[0] if isinstance(Input, list) and len(Input) > 0 else 1
        purge_val = Purge_VRAM[0] if isinstance(Purge_VRAM, list) and len(Purge_VRAM) > 0 else False

        if purge_val:
            purge_vram()

        log.debug(tag, f"Passing input{select_val}")
        if select_val == 1:
            val = input1
        else:
            val = input2

        # Return [None] if the selected value is None or empty list to satisfy is_output_list=True
        if val is None:
            return io.NodeOutput([None])
        if isinstance(val, list):
            if len(val) == 0:
                return io.NodeOutput([None])
            if all(item is None for item in val):
                return io.NodeOutput([None])

        return io.NodeOutput(val)
