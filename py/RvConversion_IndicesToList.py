from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "IndicesToList"


def _parse_indices(indices: str) -> tuple[list[int], list[str]]:
    parsed_indices: list[int] = []
    invalid_values: list[str] = []

    for value in indices.replace("\n", ",").split(","):
        value = value.strip()
        if not value:
            continue

        try:
            parsed_indices.append(int(value))
        except ValueError:
            invalid_values.append(value)

    return parsed_indices, invalid_values


class RvConversion_IndicesToList(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Indices to List [Eclipse]",
            display_name="Indices to List",
            category=CATEGORY.MAIN.value + CATEGORY.CONVERSION.value,
            description=(
                "Convert comma- or newline-separated integer indices into the "
                "LIST format used by IO Slice & Dice."
            ),
            inputs=[
                io.String.Input(
                    "indices",
                    default="0, 1, 5, 10",
                    tooltip=(
                        "Integer indices separated by commas or newlines, "
                        "for example: 0, 1, 5, 10."
                    ),
                ),
            ],
            outputs=[
                io.Custom("LIST").Output(
                    "indices",
                    tooltip="Parsed integer indices for IO Slice & Dice.",
                ),
            ],
        )

    @classmethod
    def execute(cls, indices: str):
        parsed_indices, invalid_values = _parse_indices(indices)

        if invalid_values:
            log.warning(
                _LOG_PREFIX,
                f"Ignored invalid index values: {', '.join(invalid_values)}",
            )

        return io.NodeOutput(parsed_indices)
