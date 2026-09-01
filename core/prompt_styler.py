from .logger import log
from .styles import find_template_by_name, get_all_styles

_LOG_PREFIX = "Style Loader"


def convert_spaces_to_underscores(text: str, max_words: int) -> str:
    # Convert spaces to underscores in comma-separated segments that have
    # at most max_words words. Longer segments are treated as prose.
    if not text:
        return text

    converted_segments = []
    for segment in text.split(","):
        stripped = segment.strip()
        if not stripped:
            converted_segments.append(segment)
            continue

        if len(stripped.split()) <= max_words:
            leading_space = segment[: len(segment) - len(segment.lstrip())]
            trailing_space = segment[len(segment.rstrip()) :]
            converted = stripped.replace(" ", "_")
            converted_segments.append(f"{leading_space}{converted}{trailing_space}")
        else:
            converted_segments.append(segment)

    return ",".join(converted_segments)


def apply_prompt_style(
    *,
    style_mode: str,
    style: str,
    index: int,
    spaces_to_underscores: bool,
    max_words_to_combine: int,
    text_positive: str,
    apply_to_positive: bool,
    apply_to_negative: bool,
    log_prompt: bool,
    text_negative: str = "",
) -> tuple[str, str]:
    # Always fetch fresh styles from the cached core module (handles hot reload).
    styles_by_mode, _ = get_all_styles()
    mode_styles = styles_by_mode.get(style_mode, [])

    if index >= 0 and mode_styles:
        actual_index = index % len(mode_styles)
        template = mode_styles[actual_index]
        style = template.get("name", style)
        if log_prompt:
            log.debug(
                _LOG_PREFIX,
                f"Using index {index} (wrapped to {actual_index}) - style: {style} (mode: {style_mode})",
            )
    else:
        template = find_template_by_name(mode_styles, style)

    if template:
        prompt_template = template["prompt"]
        if "{prompt}" in prompt_template:
            parts = prompt_template.split("{prompt}")
            prefix = parts[0].strip()
            suffix = parts[1].strip() if len(parts) > 1 else ""
            suffix = suffix.lstrip("., ")
            text_positive_clean = text_positive.strip().rstrip(".,;: ")
            prefix_sep = ", " if style_mode == "tag_based" else " "

            if prefix and suffix:
                text_positive_styled = (
                    f"{prefix}{prefix_sep}{text_positive_clean}, {suffix}"
                )
            elif prefix:
                text_positive_styled = f"{prefix}{prefix_sep}{text_positive_clean}"
            elif suffix:
                text_positive_styled = f"{text_positive_clean}, {suffix}"
            else:
                text_positive_styled = text_positive_clean
        else:
            text_positive_styled = prompt_template

        json_negative_prompt = template.get("negative_prompt", "")
        text_negative_styled = (
            f"{json_negative_prompt}, {text_negative}"
            if json_negative_prompt and text_negative
            else json_negative_prompt or text_negative
        )
    else:
        text_positive_styled = text_positive
        text_negative_styled = text_negative
        if log_prompt:
            log.warning(_LOG_PREFIX, f"Style '{style}' not found")

    if not apply_to_positive:
        text_positive_styled = text_positive
        if log_prompt:
            log.debug(_LOG_PREFIX, "apply_to_positive: disabled")

    if not apply_to_negative:
        text_negative_styled = text_negative
        if log_prompt:
            log.debug(_LOG_PREFIX, "apply_to_negative: disabled")

    if spaces_to_underscores:
        if apply_to_positive:
            text_positive_styled = convert_spaces_to_underscores(
                text_positive_styled, max_words_to_combine
            )
        if apply_to_negative:
            text_negative_styled = convert_spaces_to_underscores(
                text_negative_styled, max_words_to_combine
            )
        if log_prompt:
            log.debug(
                _LOG_PREFIX,
                f"Applied spaces_to_underscores (max_words: {max_words_to_combine})",
            )

    if log_prompt:
        log.msg(_LOG_PREFIX, f"style: {style}")
        log.msg(_LOG_PREFIX, f"text_positive: {text_positive}")
        log.msg(_LOG_PREFIX, f"text_negative: {text_negative}")
        log.msg(_LOG_PREFIX, f"text_positive_styled: {text_positive_styled}")
        log.msg(_LOG_PREFIX, f"text_negative_styled: {text_negative_styled}")

    return text_positive_styled, text_negative_styled
