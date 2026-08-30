from typing import Any

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.danbooru_maintenance import apply_reviewed_batches


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple)):
        raise TypeError("Expected a string or a list of strings")
    values: list[str] = []
    for item in value:
        if isinstance(item, str):
            values.append(item)
        elif isinstance(item, (list, tuple)):
            values.extend(_string_list(item))
        else:
            raise TypeError("Expected a string or a list of strings")
    return values


class RvText_DanbooruCategoryApply(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Danbooru Category Apply [Eclipse]",
            display_name="Danbooru Category Apply",
            category=CATEGORY.MAIN.value + CATEGORY.DANBOORU.value,
            description=(
                "Validate the second SmartLLM pass and atomically append "
                "manifest-matched tag-category assignments to Eclipse Prompt Forge, "
                "recovering independently valid objects from a malformed envelope "
                "while leaving unusable tags pending for a later pass."
            ),
            inputs=[
                io.String.Input(
                    "reviewed_text",
                    force_input=True,
                    tooltip="Connect Smart LM Loader #2 text output.",
                ),
                io.String.Input(
                    "batch_token",
                    force_input=True,
                    tooltip="Connect the matching maintenance-node batch tokens.",
                ),
            ],
            outputs=[
                io.String.Output(
                    "categorized_text",
                    is_output_list=True,
                    tooltip="Validated [category] tag lines for each accepted batch.",
                ),
                io.String.Output("report", tooltip="Validation/application report."),
                io.Int.Output("remaining", tooltip="General tags still pending."),
            ],
            is_input_list=True,
            is_output_node=True,
            not_idempotent=True,
        )

    @classmethod
    def execute(
        cls,
        reviewed_text: Any,
        batch_token: Any,
    ) -> io.NodeOutput:
        validated, report, remaining = apply_reviewed_batches(
            _string_list(reviewed_text),
            _string_list(batch_token),
            apply_changes=True,
        )
        return io.NodeOutput(validated, report, remaining)
