from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY, purge_vram
from ..core.logger import log

_LOG_PREFIX = "AnyPasser_Purge"


class RvRouter_Any_Passer_purge(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("any_type")
        return io.Schema(
            node_id="Any Passer Purge [Eclipse]",
            display_name="Any Passer Purge",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value,
            is_input_list=True,
            inputs=[
                io.MatchType.Input(
                    "input",
                    template=type_template,
                    tooltip="Any input to be passed through.",
                ),
                io.Boolean.Input(
                    "Purge_VRAM",
                    default=False,
                    tooltip="If enabled, purges VRAM and unloads all models before passing latent.",
                ),
            ],
            outputs=[
                io.MatchType.Output(
                    type_template, id="output", is_output_list=True, tooltip="The passed-through output."
                ),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, input, Purge_VRAM):
        tag = f"{_LOG_PREFIX} #{cls.hidden.unique_id}"

        # Unwrap wrapped list arguments since is_input_list=True
        purge_val = Purge_VRAM[0] if isinstance(Purge_VRAM, list) and len(Purge_VRAM) > 0 else False

        if purge_val:
            purge_vram()
        log.debug(tag, "Passing input")

        if input is None:
            return io.NodeOutput([None])
        if isinstance(input, list):
            if len(input) == 0:
                return io.NodeOutput([None])
            if all(item is None for item in input):
                return io.NodeOutput([None])

        return io.NodeOutput(input)
