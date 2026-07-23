import re

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.regex_helper import is_tags_format, smart_phrase_removal


_FILTER_TOKEN_PATTERN = re.compile(r"\*|[^*_\s]+")
_WORD_SLOT_PATTERN = r"[^\W_]+(?:[-'][^\W_]+)*"
_TOKEN_SEPARATOR_PATTERN = r"[ \t_]+"
_LEFT_TOKEN_BOUNDARY = r"(?<![\w'-])"
_RIGHT_TOKEN_BOUNDARY = r"(?![\w'-])"


def normalize_parentheses(text: str) -> str:
    # Treat escaped and unescaped prompt parentheses as equivalent.
    text = re.sub(r"\\*\(", "(", text)
    return re.sub(r"\\*\)", ")", text)


def compile_literal_token(token: str) -> str:
    escaped = re.escape(token)
    return escaped.replace(r"\(", r"\\*\(").replace(r"\)", r"\\*\)")


def compile_filter_patterns(
    raw_patterns: list[str],
) -> list[re.Pattern[str]]:
    sortable_patterns: list[tuple[int, int, re.Pattern[str]]] = []

    for raw_pattern in raw_patterns:
        tokens = _FILTER_TOKEN_PATTERN.findall(normalize_parentheses(raw_pattern))
        if not tokens:
            continue

        wildcard_count = tokens.count("*")
        token_patterns = [
            _WORD_SLOT_PATTERN if token == "*" else compile_literal_token(token)
            for token in tokens
        ]
        body = _TOKEN_SEPARATOR_PATTERN.join(token_patterns)
        regex = re.compile(
            _LEFT_TOKEN_BOUNDARY + body + _RIGHT_TOKEN_BOUNDARY,
            re.IGNORECASE,
        )
        sortable_patterns.append((len(tokens), wildcard_count, regex))

    # Prefer longer matches, then exact phrases over wildcard phrases.
    sortable_patterns.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in sortable_patterns]


def filter_tag_prompt(text: str, patterns: list[re.Pattern[str]]) -> str:
    # Remove complete tag fields so a match never leaves a partial tag.
    prompt_parts = re.split(r"([,\n])", text)
    for index in range(0, len(prompt_parts), 2):
        tag = prompt_parts[index].strip(" \t")
        if tag and any(pattern.fullmatch(tag) for pattern in patterns):
            prompt_parts[index] = ""

    return cleanup_prompt("".join(prompt_parts))


def cleanup_prompt(text: str) -> str:
    # Clean up horizontal spaces around commas, and reduce multiple commas
    # Note: we want to preserve newlines!
    text = re.sub(r"[ \t]*,[ \t]*(?:[ \t]*,[ \t]*)*", ", ", text)

    # Normalize horizontal spaces (2 or more spaces/tabs to a single space)
    text = re.sub(r"[ \t]{2,}", " ", text)

    # Clean up lines: strip leading/trailing spaces and commas on each line
    lines = []
    for line in text.split("\n"):
        line = line.strip(" \t,")
        if line:
            lines.append(line)

    return "\n".join(lines)


class RvText_FilterPrompt(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Filter Prompt [Eclipse]",
            display_name="Filter Prompt",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            inputs=[
                io.String.Input(
                    "input_string",
                    optional=True,
                    force_input=True,
                    tooltip="Tag-based or natural-language prompt from which matching words and phrases are removed.",
                ),
                io.String.Input(
                    "string",
                    multiline=True,
                    default="",
                    tooltip="Comma/newline-separated filters. Each * matches exactly one word; spaces and underscores are equivalent. Examples: *hair matches long hair, **hair matches very long hair.",
                ),
            ],
            outputs=[
                io.String.Output("string"),
            ],
        )

    @classmethod
    def execute(cls, input_string=None, string=None):
        # If nothing is connected to input_string, we just pass empty string.
        if not isinstance(input_string, str) or not input_string.strip():
            return io.NodeOutput("")

        # If nothing is in filter patterns, return input prompt exactly as is.
        if not isinstance(string, str) or not string.strip():
            return io.NodeOutput(input_string)

        # Parse filter patterns: split by lines and commas
        raw_patterns = []
        for line in string.split("\n"):
            for part in line.split(","):
                part_stripped = part.strip()
                if part_stripped:
                    raw_patterns.append(part_stripped)

        patterns = compile_filter_patterns(raw_patterns)
        if is_tags_format(input_string):
            result = filter_tag_prompt(input_string, patterns)
        else:
            result = smart_phrase_removal(input_string, patterns, "filter_prompt")

        return io.NodeOutput(result)
