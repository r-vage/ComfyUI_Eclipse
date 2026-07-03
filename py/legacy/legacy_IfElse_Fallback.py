# IF A Else B (Fallback) - Router with optional on_false and optional boolean input.
# DEPRECATED — replaced by IF A Else B [Eclipse] which now supports the same behavior.

from comfy_api.latest import io #type: ignore
from ...core import CATEGORY, purge_vram
from ...core.logger import log

_LOG_PREFIX = "IfAElseB_Fallback"

class RvRouter_IfElse_Fallback(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="IF A Else B Fallback [Eclipse]",
            display_name="⚠ IF A Else B (Fallback)",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — replaced by 'IF A Else B' which now supports optional on_false and boolean widget with default. All legacy nodes will be removed in v4.0.0.",
            inputs=[
                io.AnyType.Input("on_true", lazy=True, tooltip="Value to return if boolean is True."),
                io.AnyType.Input("on_false", optional=True, lazy=True, tooltip="Value to return if boolean is False. Unconnected returns None."),
                io.Boolean.Input("boolean", default=False, optional=True, force_input=True, tooltip="Condition to select on_true or on_false. Unconnected or muted defaults to False."),
                io.Boolean.Input("Purge_VRAM", default=False, tooltip="If True, purges VRAM before switching."),
            ],
            outputs=[
                io.AnyType.Output("output"),
            ],
            hidden=[io.Hidden.unique_id, io.Hidden.prompt, io.Hidden.dynprompt],
        )

    @classmethod
    def check_lazy_status(cls, on_true=None, on_false=None, boolean=False, Purge_VRAM=False):
        if not isinstance(boolean, bool):
            boolean = boolean is not None
        if boolean and on_true is None:
            return ["on_true"]
        if not boolean and on_false is None:
            dynprompt = getattr(cls.hidden, "dynprompt", None)
            node_data = None
            if dynprompt:
                try:
                    node_data = dynprompt.get_node(cls.hidden.unique_id)
                except Exception:
                    pass
            if not node_data:
                node_data = cls.hidden.prompt.get(str(cls.hidden.unique_id))
            if not node_data:
                uid_str = str(cls.hidden.unique_id)
                for delim in (":", "."):
                    if delim in uid_str:
                        last_part = uid_str.split(delim)[-1]
                        node_data = cls.hidden.prompt.get(last_part)
                        if node_data:
                            break
            node_inputs = node_data.get("inputs", {}) if node_data else {}
            if isinstance(node_inputs.get("on_false"), list):
                return ["on_false"]
        return []

    @classmethod
    def execute(cls, on_true, on_false=None, boolean=False, Purge_VRAM=False):
        tag = f"{_LOG_PREFIX} #{cls.hidden.unique_id}"
        if Purge_VRAM:
            purge_vram()
        if not isinstance(boolean, bool):
            boolean = boolean is not None
        log.debug(tag, f"boolean={boolean}, passing {'on_true' if boolean else 'on_false'}")
        if boolean:
            return io.NodeOutput(on_true)
        else:
            return io.NodeOutput(on_false)
