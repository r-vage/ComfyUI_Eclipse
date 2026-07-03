from __future__ import annotations
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "AnyMultiSwitch"

class RvRouter_Any_MultiSwitch(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Any Multi-Switch [Eclipse]",
            display_name="Any Multi-Switch",
            category=CATEGORY.MAIN.value + CATEGORY.ROUTER.value,
            description="Multi-switch for ANY inputs. Returns the first non-None input. Inputs update automatically when inputcount changes.",
            inputs=[
                io.Int.Input("inputcount", default=2, min=1, max=64, step=1, socketless=True, tooltip="Number of ANY inputs to expose. Inputs update automatically."),
                io.AnyType.Input("any_1", optional=True, tooltip="Any input #1 (highest priority). Leave empty to bypass."),
                io.AnyType.Input("any_2", optional=True, tooltip="Any input #2 (used if #1 is empty)."),
            ],
            outputs=[
                io.AnyType.Output("*"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, inputcount, **kwargs):
        tag = f"{_LOG_PREFIX} #{cls.hidden.unique_id}"

        # First pass: return the first connected input that has real data.
        # Skip None (disconnected/muted), empty strings, empty dicts, and empty tuples.
        for i in range(1, max(1, inputcount) + 1):
            key = f"any_{i}"
            val = kwargs.get(key)
            if val is None or val == "":
                continue
            if isinstance(val, (dict, tuple, list)) and len(val) == 0:
                log.debug(tag, f"Skipping slot {i} ({key}): empty {type(val).__name__}")
                continue
            log.debug(tag, f"Passing slot {i} ({key})")
            return io.NodeOutput(val)

        # Second pass: return empty string if any slot had one
        for i in range(1, max(1, inputcount) + 1):
            key = f"any_{i}"
            val = kwargs.get(key)
            if val == "":
                log.debug(tag, f"All slots empty, returning empty string from slot {i}")
                return io.NodeOutput(val)

        # All inputs are None/empty — pass through None
        log.debug(tag, "All slots disconnected or empty, passing None")
        return io.NodeOutput(None)
