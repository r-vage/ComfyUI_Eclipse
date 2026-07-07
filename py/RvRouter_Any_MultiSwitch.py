from __future__ import annotations
from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "AnyMultiSwitch"


class RvRouter_Any_MultiSwitch(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        type_template = io.MatchType.Template("any_type")
        return io.Schema(
            node_id="Any Multi-Switch [Eclipse]",
            display_name="Any Multi-Switch",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value,
            description="Multi-switch for ANY inputs. Returns the first non-None input. Inputs update automatically when inputcount changes.",
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
                    tooltip="Any input #1 (highest priority). Leave empty to bypass.",
                ),
                io.MatchType.Input(
                    "any_2",
                    template=type_template,
                    optional=True,
                    tooltip="Any input #2 (used if #1 is empty).",
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

        # All inputs are None/empty — pass through None
        log.debug(tag, "All slots disconnected or empty, passing None")
        return io.NodeOutput([None])
