#
# RvText_WildcardProcessorList - Seeded wildcard expansion with string and list outputs
#

import re
import secrets

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.logger import log
from ..core.wildcard_engine import process

_LOG_PREFIX = "Wildcard List"
_SPECIAL_SEEDS = {-1, -2, -3}
_ASSIGNMENT_RE = re.compile(
    r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"((?:\\["\\]|[^"])*)"\s*$',
    re.DOTALL,
)
_REFERENCE_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
_ESCAPE_RE = re.compile(r'\\(["\\])')


def _split_substitution_assignments(text: str) -> list[str]:
    assignments: list[str] = []
    current: list[str] = []
    in_quotes = False
    index = 0

    while index < len(text):
        char = text[index]
        if in_quotes and char == "\\" and index + 1 < len(text):
            following = text[index + 1]
            if following in {'"', "\\"}:
                current.extend((char, following))
                index += 2
                continue
        if char == '"':
            in_quotes = not in_quotes
            current.append(char)
        elif not in_quotes and char in {",", "\n", "\r"}:
            segment = "".join(current).strip()
            if segment:
                assignments.append(segment)
            current.clear()
            if char == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
                index += 1
        else:
            current.append(char)
        index += 1

    segment = "".join(current).strip()
    if segment:
        assignments.append(segment)
    return assignments


def parse_substitutions(text: str | None) -> dict[str, str]:
    if not isinstance(text, str) or not text.strip():
        return {}

    assignments = _split_substitution_assignments(text)
    substitutions: dict[str, str] = {}
    malformed = 0

    for assignment in assignments:
        match = _ASSIGNMENT_RE.fullmatch(assignment)
        if match is None:
            malformed += 1
            continue
        key, raw_value = match.groups()
        substitutions[key] = _ESCAPE_RE.sub(r"\1", raw_value)

    if malformed:
        log.warning(
            _LOG_PREFIX,
            f"Ignored {malformed} malformed substitution assignment(s)",
        )
    return substitutions


def apply_substitutions(text: str, substitutions: dict[str, str]) -> str:
    return _REFERENCE_RE.sub(
        lambda match: substitutions.get(match.group(1), match.group(0)), text
    )


def _protect_substitution_references(text: str) -> tuple[str, list[tuple[str, str]]]:
    protected: list[tuple[str, str]] = []

    def replace_reference(match: re.Match) -> str:
        token = f"\0ECLIPSE_SUBSTITUTION_{len(protected)}\0"
        protected.append((token, match.group(0)))
        return token

    return _REFERENCE_RE.sub(replace_reference, text), protected


def _resolve_backend_seed(seed: int) -> int:
    if seed in _SPECIAL_SEEDS:
        return secrets.randbelow(2**63)
    return seed


class RvText_WildcardProcessorList(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Wildcard Processor List [Eclipse]",
            display_name="Wildcard Processor List",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            description=(
                "Processes one or more newline-separated wildcard prompts as one "
                "seeded batch and returns both the original layout and a list.\n"
                "Tip: Right-click the node to toggle Wrap long lines."
            ),
            inputs=[
                io.String.Input(
                    "wildcard_text",
                    multiline=True,
                    default="Try using __wildcard__ or {option1|option2}",
                    tooltip="One or more prompts. Newlines and spacing are preserved.",
                ),
                io.String.Input(
                    "substitutions",
                    force_input=True,
                    optional=True,
                    tooltip=(
                        "Optional quoted assignments, for example:\n"
                        'S1:"a female character", '
                        'S2:"wearing blue jeans, a white shirt"\n'
                        "Use {{S1}} and {{S2}} in wildcard_text."
                    ),
                ),
                io.Combo.Input(
                    "wildcards",
                    options=["Select a Wildcard"],
                    default="Select a Wildcard",
                ),
                io.Int.Input(
                    "seed",
                    default=0,
                    min=-3,
                    max=2**64 - 1,
                    socketless=True,
                    tooltip=(
                        "Seed for the complete wildcard batch. Special values: "
                        "-1=randomize, -2=increment, -3=decrement."
                    ),
                ),
                io.Int.Input(
                    "seed_input",
                    force_input=True,
                    optional=True,
                    tooltip="Optional external seed. Overrides the local seed at queue time.",
                ),
            ],
            outputs=[
                io.String.Output("string"),
                io.String.Output("list", is_output_list=True),
            ],
        )

    @classmethod
    def execute(
        cls,
        wildcard_text,
        seed,
        substitutions=None,
        wildcards="Select a Wildcard",
        seed_input=None,
    ) -> io.NodeOutput:
        source = wildcard_text if isinstance(wildcard_text, str) else ""
        replacement_values = parse_substitutions(substitutions)
        substituted = apply_substitutions(source, replacement_values)
        protected_source, protected_references = _protect_substitution_references(
            substituted
        )
        effective_seed = seed_input if seed_input is not None else seed
        resolved_seed = _resolve_backend_seed(effective_seed)
        processed = process(
            protected_source, seed=resolved_seed, preserve_layout=True
        )
        for token, reference in protected_references:
            processed = processed.replace(token, reference)
        list_items = [line for line in processed.splitlines() if line.strip()]
        return io.NodeOutput(processed, list_items, ui={"seed": [resolved_seed]})
