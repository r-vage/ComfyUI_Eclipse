#
# RvText_WildcardProcessor - Processes text with wildcard syntax
#
# Supports two processing modes:
# - populate: Expands all wildcards and options. Seed controls output - change seed for new output, fix seed for consistent output
# - fixed: Uses populated_text as-is, ignoring wildcards
#
# Special seed values (from eclipse-seed.js extension):
#   -1: Randomize each time (generates new random seed)
#   -2: Increment from last seed
#   -3: Decrement from last seed


import os

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.logger import log
from ..core.wildcard_engine import process, wildcard_load

_LOG_PREFIX = "Wildcard"


def _load_wildcard_path(path=None):
    if path is None:
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "wildcards"
        )
    wildcard_load(path)
    log.msg(_LOG_PREFIX, f"Loaded wildcards from: {path}")


class RvText_WildcardProcessor(io.ComfyNode):
    # A wildcard text processor that expands wildcard patterns and options.

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Wildcard Processor [Eclipse]",
            display_name="Wildcard Processor",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            inputs=[
                io.String.Input(
                    "wildcard_text",
                    multiline=True,
                    default="Try using __wildcard__ or {option1|option2}",
                    tooltip="Enter a prompt using wildcard syntax.",
                ),
                io.String.Input(
                    "populated_text",
                    multiline=True,
                    default="",
                    tooltip="The actual value processed from 'wildcard_text'. In 'populate' mode, this is auto-updated. In 'fixed' mode, you can edit this value.",
                ),
                io.Combo.Input(
                    "mode",
                    options=["populate", "fixed"],
                    default="populate",
                    tooltip="populate: Auto-processes wildcard_text based on seed. Change seed for new output, fix seed for consistent output.\nfixed: Uses populated_text as-is, you can edit it",
                ),
                io.Int.Input(
                    "seed",
                    default=0,
                    min=-3,
                    max=2**64 - 1,
                    socketless=True,
                    tooltip="Seed controls wildcard expansion in populate mode.\nSpecial values: -1=randomize each time, -2=increment from last, -3=decrement from last",
                ),
                io.Combo.Input(
                    "wildcards", options=["Select a Wildcard"], optional=True
                ),
                io.Int.Input(
                    "seed_input",
                    force_input=True,
                    optional=True,
                    tooltip="Optional external seed. When connected, it overrides the local seed at queue time.",
                ),
            ],
            outputs=[
                io.String.Output("processed_text"),
            ],
        )

    @classmethod
    def execute(
        cls,
        wildcard_text,
        populated_text,
        mode,
        seed,
        wildcards="Select a Wildcard",
        seed_input=None,
    ) -> io.NodeOutput:

        try:
            effective_seed = seed_input if seed_input is not None else seed

            # The server-side prompt handler (onprompt_populate_wildcards) already processed
            # wildcards and updated populated_text before execution in populate mode.
            # So we just use populated_text directly.

            # In "populate" mode: populated_text was already processed by server handler
            # In "fixed" mode: populated_text contains manually edited text
            result = populated_text

            # Add selected wildcard if not "Select a Wildcard"
            if wildcards and wildcards != "Select a Wildcard":
                if result and not result.endswith("\n"):
                    result += "\n"
                result += wildcards
                # Process the added wildcard
                result = process(result, seed=effective_seed)

            return io.NodeOutput(
                result, ui={"text": [result], "seed": [effective_seed]}
            )

        except Exception as e:  # noqa: BLE001 - keep node execution failure-safe
            log.error(_LOG_PREFIX, f"Error in execute: {e}")
            return io.NodeOutput(
                populated_text,
                ui={"text": [populated_text], "seed": [effective_seed]},
            )


# Ensure wildcard engine is initialized on import
_wildcard_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "wildcards"
)
if os.path.exists(_wildcard_path):
    _load_wildcard_path(_wildcard_path)
