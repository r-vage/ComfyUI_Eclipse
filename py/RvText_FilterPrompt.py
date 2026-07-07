import re
from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY


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
                    tooltip="The input prompt string from which matches should be filtered/removed.",
                ),
                io.String.Input(
                    "string",
                    multiline=True,
                    default="",
                    tooltip="Enter values or wildcard patterns (e.g. *looking at*) to remove from the input string. Split multiple patterns by lines or commas.",
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

        # Start with original string to preserve original formatting (including underscores)
        current_orig = input_string
        # Convert underscores to spaces and convert to lowercase in the normalized copy.
        # Since replacing "_" with " " keeps length identical, indices will align 1:1.
        current_norm = current_orig.replace("_", " ").lower()

        for pattern in raw_patterns:
            # 1. Normalize the pattern: replace _ with space, lowercase
            pat_norm = pattern.replace("_", " ").lower()

            # 2. Support parentheses with or without preceding backslashes (strip any number of backslashes before them)
            pat_norm = re.sub(r"\\*\(", "(", pat_norm)
            pat_norm = re.sub(r"\\*\)", ")", pat_norm)

            # Determine if we should add word boundaries at the start and end.
            # We check if the first/last characters of the normalized pattern are alphanumeric or underscore.
            starts_with_word = bool(pat_norm and (pat_norm[0].isalnum() or pat_norm[0] == '_'))
            ends_with_word = bool(pat_norm and (pat_norm[-1].isalnum() or pat_norm[-1] == '_'))

            # 3. Escape for regex matching
            rx_pat = re.escape(pat_norm)

            # 4. Convert wildcard * to [^,\n]*
            rx_pat = rx_pat.replace(r"\*", r"[^,\n]*")

            # 5. Make backslashes before parentheses optional in matching
            rx_pat = rx_pat.replace(r"\(", r"\\?\(").replace(r"\)", r"\\?\)")

            # Prepend/append \b if appropriate
            if starts_with_word:
                rx_pat = r"\b" + rx_pat
            if ends_with_word:
                rx_pat = rx_pat + r"\b"

            try:
                # Compile regex
                rx = re.compile(rx_pat)

                # Iteratively find and remove all matches from both original and normalized copies
                while True:
                    match = rx.search(current_norm)
                    if not match:
                        break
                    start, end = match.span()
                    current_orig = current_orig[:start] + current_orig[end:]
                    current_norm = current_norm[:start] + current_norm[end:]
            except Exception:
                # If regex compilation/search fails for a pattern, skip it
                continue

        # Clean up double commas, trailing/leading whitespace, and extra spaces caused by replacement
        result = cleanup_prompt(current_orig)
        return io.NodeOutput(result)
