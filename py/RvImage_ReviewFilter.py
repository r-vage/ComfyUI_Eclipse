# Route aligned per-image JSON reviews without changing image pixels or dimensions.

import json
import re

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.image_helpers import flatten_images


def _unique_object(pairs):
    # Conflicting duplicate fields must not turn an uncertain report into a reject.
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def _parse_report(report) -> tuple[str, list[str]]:
    if not isinstance(report, str):
        return "review", ["Invalid report: expected a JSON string."]
    text = report.strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (ValueError, RecursionError):
        return "review", ["Invalid JSON report; manual review required."]
    if not isinstance(data, dict):
        return "review", ["Invalid report: expected one JSON object."]
    verdict = data.get("verdict")
    if not isinstance(verdict, str) or verdict not in ("pass", "review", "reject"):
        return "review", ["Missing or unknown verdict; manual review required."]
    reasons = data.get("reasons")
    if (
        not isinstance(reasons, list)
        or any(not isinstance(reason, str) or not reason.strip() for reason in reasons)
        or (verdict != "pass" and not reasons)
    ):
        return "review", ["Incomplete report: reasons must be nonempty strings; review/reject needs a reason."]
    return verdict, [" ".join(reason.split()) for reason in reasons]


class RvImage_ReviewFilter(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Review Filter [Eclipse]",
            display_name="Image Review Filter",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_BATCH.value,
            description=(
                "Split images using one aligned JSON report per image. Only a valid reject "
                "verdict removes an image. Uncertain or invalid reports stay kept as review. "
                "Preserves input pixels, dimensions and order; empty groups stay empty."
            ),
            is_input_list=True,
            inputs=[
                io.Image.Input("images", tooltip="Image batch or list, in the same order as reports."),
                io.String.Input(
                    "reports", force_input=True,
                    tooltip='One JSON string per image: {"verdict":"pass|review|reject","reasons":["..."]}.',
                ),
            ],
            outputs=[
                io.Image.Output("kept", is_output_list=True, tooltip="Pass and review images."),
                io.Image.Output("rejected", is_output_list=True, tooltip="Valid reject images only."),
                io.String.Output("report", tooltip="Original image numbers, preview positions, reasons and totals."),
            ],
        )

    @classmethod
    def execute(cls, images, reports):
        frames = flatten_images(images)
        reports = list(reports) if isinstance(reports, (list, tuple)) else [reports]
        if len(frames) != len(reports):
            raise ValueError(
                f"Image Review Filter: expected exactly one report per image; "
                f"received {len(frames)} images and {len(reports)} reports. "
                "Connect images and text from the same SmartLLM execution; alignment cannot be guessed."
            )

        kept, rejected, lines = [], [], []
        counts = {"pass": 0, "review": 0, "reject": 0}
        for number, (frame, raw_report) in enumerate(zip(frames, reports), 1):
            verdict, reasons = _parse_report(raw_report)
            counts[verdict] += 1
            destination = rejected if verdict == "reject" else kept
            destination.append(frame)
            preview = "Rejected" if verdict == "reject" else "Kept"
            flag = " [MANUAL REVIEW]" if verdict == "review" else ""
            explanation = "; ".join(reasons) if reasons else "No defects reported."
            lines.append(f"{number}. {verdict.upper()}{flag} → {preview} #{len(destination)}: {explanation}")

        totals = (
            f"Totals: {len(frames)} images | Kept: {len(kept)} "
            f"(pass: {counts['pass']}, review: {counts['review']}) | Rejected: {len(rejected)}"
        )
        report = "\n".join([totals, "", *lines]) if lines else totals
        return io.NodeOutput(kept, rejected, report)
