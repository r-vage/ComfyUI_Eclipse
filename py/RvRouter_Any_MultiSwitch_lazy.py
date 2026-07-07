from __future__ import annotations
from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "AnyMultiSwitch_Lazy"


class RvRouter_Any_MultiSwitch_lazy(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("any_type")
        return io.Schema(
            node_id="Any Multi-Switch Lazy [Eclipse]",
            display_name="Any Multi-Switch Lazy",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value,
            description=(
                "Multi-switch for ANY inputs with lazy evaluation. "
                "Only the first connected slot's upstream graph executes — "
                "all other upstream branches are skipped entirely. "
                "Inputs update automatically when inputcount changes."
            ),
            is_input_list=True,
            inputs=[
                io.Int.Input(
                    "inputcount",
                    default=2,
                    min=1,
                    max=64,
                    step=1,
                    socketless=True,
                    tooltip="Number of ANY inputs to expose. Inputs update automatically.",
                ),
                io.MatchType.Input(
                    "any_1",
                    template=type_template,
                    optional=True,
                    lazy=True,
                    tooltip="Any input #1 (highest priority). Only this branch executes if connected.",
                ),
                io.MatchType.Input(
                    "any_2",
                    template=type_template,
                    optional=True,
                    lazy=True,
                    tooltip="Any input #2 (used if #1 is empty or not connected).",
                ),
            ],
            outputs=[
                io.MatchType.Output(
                    type_template, id="*", is_output_list=True, tooltip="The selected output."
                ),
            ],
            hidden=[io.Hidden.unique_id, io.Hidden.prompt, io.Hidden.dynprompt],
        )

    @classmethod
    def check_lazy_status(cls, inputcount=2, **kwargs):
        # inputcount comes wrapped as a list since is_input_list=True
        count_val = inputcount[0] if isinstance(inputcount, list) and len(inputcount) > 0 else 2

        # Read the prompt once to know which slots actually have links.
        # We cannot rely on lazy=True for dynamically-added slots (any_3+) because
        # only schema-declared inputs carry the lazy flag. We therefore probe the
        # prompt graph ourselves and request inputs one-at-a-time in priority order.
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

        for i in range(1, max(1, count_val) + 1):
            key = f"any_{i}"
            val = kwargs.get(key)
            if val is not None:
                # This slot already has a resolved value — we have what we need.
                return []
            # val is None: either not connected or upstream not yet evaluated.
            # Only request evaluation if a link actually exists in the prompt.
            if isinstance(node_inputs.get(key), list):
                return [key]
            # No link — skip to the next slot.

        return []

    @classmethod
    def execute(cls, inputcount, **kwargs):
        tag = f"{_LOG_PREFIX} #{cls.hidden.unique_id}"

        # inputcount comes wrapped as a list since is_input_list=True
        count_val = inputcount[0] if isinstance(inputcount, list) and len(inputcount) > 0 else 2

        def is_valid_input(val):
            if val is None:
                return False
            if isinstance(val, list):
                if len(val) == 0:
                    return False
                if all(item is None for item in val):
                    return False
                # Check for list of empty strings or empty structures
                non_empty = []
                for item in val:
                    if item is None:
                        continue
                    if isinstance(item, (str, dict, list, tuple)) and len(item) == 0:
                        continue
                    non_empty.append(item)
                if len(non_empty) == 0:
                    return False
            elif isinstance(val, (str, dict, tuple)) and len(val) == 0:
                return False
            return True

        for i in range(1, max(1, count_val) + 1):
            key = f"any_{i}"
            val = kwargs.get(key)
            if is_valid_input(val):
                log.debug(tag, f"Passing slot {i} ({key})")
                return io.NodeOutput(val)

        log.debug(tag, "All slots disconnected or empty, passing None")
        return io.NodeOutput([None])
