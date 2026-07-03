from comfy_api.latest import io #type: ignore
from ..core import CATEGORY, purge_vram
from ..core.logger import log

_LOG_PREFIX = "AnyPasser_Purge"

class RvRouter_Any_Passer_purge(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Any Passer Purge [Eclipse]",
            display_name="Any Passer Purge",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value,
            inputs=[
                io.AnyType.Input("input", tooltip="Any input to be passed through."),
                io.Boolean.Input("Purge_VRAM", default=False, tooltip="If enabled, purges VRAM and unloads all models before passing latent."),
            ],
            outputs=[
                io.AnyType.Output("output"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, input, Purge_VRAM):
        tag = f"{_LOG_PREFIX} #{cls.hidden.unique_id}"
        if Purge_VRAM:
            purge_vram()
        log.debug(tag, "Passing input")
        return io.NodeOutput(input)
