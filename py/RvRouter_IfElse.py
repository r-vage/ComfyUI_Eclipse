from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY, purge_vram
from ..core.logger import log

_LOG_PREFIX = "IfAElseB"


class RvRouter_IfElse(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("any_type")
        return io.Schema(
            node_id="IF A Else B [Eclipse]",
            display_name="IF A Else B",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value,
            inputs=[
                io.MatchType.Input(
                    "on_true",
                    template=type_template,
                    lazy=True,
                    tooltip="Value to return if boolean is True.",
                ),
                io.MatchType.Input(
                    "on_false",
                    template=type_template,
                    lazy=True,
                    optional=True,
                    tooltip="Value to return if boolean is False. Unconnected returns None.",
                ),
                io.Boolean.Input(
                    "boolean",
                    default=False,
                    tooltip="Condition to select on_true or on_false.",
                ),
                io.Boolean.Input(
                    "Purge_VRAM",
                    default=False,
                    tooltip="If True, purges VRAM before switching.",
                ),
            ],
            outputs=[
                io.MatchType.Output(
                    type_template, id="output", tooltip="The output value."
                ),
            ],
            hidden=[io.Hidden.unique_id, io.Hidden.prompt, io.Hidden.dynprompt],
        )

    @classmethod
    def check_lazy_status(
        cls, on_true=None, on_false=None, boolean=True, Purge_VRAM=False
    ):
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
                node_data = cls.hidden.prompt.get(cls.hidden.unique_id)
            if not node_data:
                uid_str = cls.hidden.unique_id
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
        # Robust boolean handling: when a non-bool value is connected (e.g. AnyType),
        # treat None as False and any non-None value as True.
        # This avoids Python truthiness pitfalls where 0, "", [] are falsy but valid data.
        if not isinstance(boolean, bool):
            boolean = boolean is not None
        log.debug(
            tag, f"boolean={boolean}, passing {'on_true' if boolean else 'on_false'}"
        )
        if boolean:
            return io.NodeOutput(on_true)
        else:
            return io.NodeOutput(on_false)
