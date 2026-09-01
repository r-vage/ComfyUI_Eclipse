from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.prompt_styler import (
    apply_prompt_style,
    convert_spaces_to_underscores,
)
from ..core.styles import get_all_styles, invalidate_styles_cache

_convert_spaces_to_underscores = convert_spaces_to_underscores

# Module-level storage for styles by mode
_styles_by_mode = {}
_names_by_mode = {}


def _reload_styles():
    # Reload styles from disk. Call this when style files are modified.
    global _styles_by_mode, _names_by_mode
    invalidate_styles_cache()
    _styles_by_mode, _names_by_mode = get_all_styles()
    total = sum(len(v) for v in _names_by_mode.values())
    return {
        "success": True,
        "total_styles": total,
        "tag_based": len(_names_by_mode.get("tag_based", [])),
        "natural_language": len(_names_by_mode.get("natural_language", [])),
        "custom": len(_names_by_mode.get("custom", [])),
    }


def _get_default_styles():
    # Load styles and return default style list for the dropdown
    global _styles_by_mode, _names_by_mode
    _styles_by_mode, _names_by_mode = get_all_styles()
    default_mode = "tag_based"
    default_styles = _names_by_mode.get(default_mode, [])
    if not default_styles:
        default_styles = ["No styles found"]
    return default_styles


class RvText_PromptStyler(io.ComfyNode):
    # Load and apply prompt styles from JSON or CSV files.
    # Replaces {prompt} placeholder with your positive prompt and combines negative prompts.
    # Style files should be placed in the 'templates/styles' folder of ComfyUI_Eclipse.

    @classmethod
    def define_schema(cls):
        default_styles = _get_default_styles()
        return io.Schema(
            node_id="Prompt Styler [Eclipse]",
            display_name="Prompt Styler",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            inputs=[
                io.String.Input(
                    "text_positive",
                    default="",
                    force_input=True,
                    tooltip="Positive prompt text. Will replace {prompt} in the style template.",
                ),
                io.Combo.Input(
                    "style_mode",
                    options=["tag_based", "natural_language", "custom"],
                    default="tag_based",
                    tooltip="Select style format: tag_based uses comma-separated tags, natural_language uses flowing sentences, custom shows user-added style files.",
                ),
                io.Combo.Input(
                    "style",
                    options=default_styles,
                    tooltip="Select style to apply (contains both positive and negative prompts).",
                ),
                io.Int.Input(
                    "index",
                    default=0,
                    min=-3,
                    max=999999,
                    step=1,
                    tooltip="Style index: 0+ = fixed position, -1 = random, -2 = increment, -3 = decrement. Use navigation buttons to control.",
                ),
                io.Boolean.Input(
                    "spaces_to_underscores",
                    default=False,
                    label_on="yes",
                    label_off="no",
                    tooltip="Convert spaces to underscores in tag-like segments (comma-separated parts with few words).",
                ),
                io.Int.Input(
                    "max_words_to_combine",
                    default=3,
                    min=2,
                    max=10,
                    step=1,
                    tooltip="Maximum words in a segment to apply underscore conversion. Segments with more words are treated as natural language and left unchanged.",
                ),
                io.Boolean.Input(
                    "apply_to_positive",
                    default=True,
                    label_on="yes",
                    label_off="no",
                    tooltip="Apply style to positive prompt.",
                ),
                io.Boolean.Input(
                    "apply_to_negative",
                    default=True,
                    label_on="yes",
                    label_off="no",
                    tooltip="Apply style to negative prompt.",
                ),
                io.Boolean.Input(
                    "log_prompt",
                    default=False,
                    label_on="yes",
                    label_off="no",
                    tooltip="Log the styled prompts to console.",
                ),
                io.String.Input(
                    "text_negative",
                    default="",
                    force_input=True,
                    optional=True,
                    tooltip="Negative prompt text. Will be combined with the style's negative prompt.",
                ),
            ],
            outputs=[
                io.String.Output("text_positive"),
                io.String.Output("text_negative"),
            ],
        )

    @classmethod
    def execute(
        cls,
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
    ) -> io.NodeOutput:
        return io.NodeOutput(
            *apply_prompt_style(
                style_mode=style_mode,
                style=style,
                index=index,
                spaces_to_underscores=spaces_to_underscores,
                max_words_to_combine=max_words_to_combine,
                text_positive=text_positive,
                apply_to_positive=apply_to_positive,
                apply_to_negative=apply_to_negative,
                log_prompt=log_prompt,
                text_negative=text_negative,
            )
        )


class RvText_PromptStylerV2(io.ComfyNode):
    # Compact Prompt Styler variant with cosmetic feature chips backed by
    # ordinary serialized Boolean inputs.

    @classmethod
    def define_schema(cls):
        default_styles = _get_default_styles()
        return io.Schema(
            node_id="Prompt Styler v2 [Eclipse]",
            display_name="Prompt Styler v2",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            inputs=[
                io.String.Input(
                    "text_positive",
                    default="",
                    force_input=True,
                    tooltip="Positive prompt text. Will replace {prompt} in the style template.",
                ),
                io.Combo.Input(
                    "style_mode",
                    options=["tag_based", "natural_language", "custom"],
                    default="tag_based",
                    tooltip="Select style format: tag_based uses comma-separated tags, natural_language uses flowing sentences, custom shows user-added style files.",
                ),
                io.Combo.Input(
                    "style",
                    options=default_styles,
                    tooltip="Select style to apply (contains both positive and negative prompts).",
                ),
                io.Int.Input(
                    "index",
                    default=0,
                    min=-3,
                    max=999999,
                    step=1,
                    tooltip="Style index: 0+ = fixed position, -1 = random, -2 = increment, -3 = decrement. Use navigation buttons to control.",
                ),
                io.String.Input(
                    "features",
                    default="",
                    socketless=True,
                    tooltip="Feature-chip placeholder. The serialized Boolean inputs remain authoritative.",
                ),
                io.Boolean.Input(
                    "spaces_to_underscores",
                    default=False,
                    label_on="yes",
                    label_off="no",
                    socketless=True,
                    tooltip="Convert spaces to underscores in tag-like segments (comma-separated parts with few words).",
                ),
                io.Int.Input(
                    "max_words_to_combine",
                    default=3,
                    min=2,
                    max=10,
                    step=1,
                    tooltip="Maximum words in a segment to apply underscore conversion. Segments with more words are treated as natural language and left unchanged.",
                ),
                io.Boolean.Input(
                    "apply_to_positive",
                    default=True,
                    label_on="yes",
                    label_off="no",
                    socketless=True,
                    tooltip="Apply style to positive prompt.",
                ),
                io.Boolean.Input(
                    "apply_to_negative",
                    default=True,
                    label_on="yes",
                    label_off="no",
                    socketless=True,
                    tooltip="Apply style to negative prompt.",
                ),
                io.Boolean.Input(
                    "log_prompt",
                    default=False,
                    label_on="yes",
                    label_off="no",
                    socketless=True,
                    tooltip="Log the styled prompts to console.",
                ),
                io.String.Input(
                    "text_negative",
                    default="",
                    force_input=True,
                    optional=True,
                    tooltip="Negative prompt text. Will be combined with the style's negative prompt.",
                ),
            ],
            outputs=[
                io.String.Output("text_positive"),
                io.String.Output("text_negative"),
            ],
        )

    @classmethod
    def execute(
        cls,
        style_mode: str,
        style: str,
        index: int,
        features: str,
        spaces_to_underscores: bool,
        max_words_to_combine: int,
        text_positive: str,
        apply_to_positive: bool,
        apply_to_negative: bool,
        log_prompt: bool,
        text_negative: str = "",
    ) -> io.NodeOutput:
        del features
        return io.NodeOutput(
            *apply_prompt_style(
                style_mode=style_mode,
                style=style,
                index=index,
                spaces_to_underscores=spaces_to_underscores,
                max_words_to_combine=max_words_to_combine,
                text_positive=text_positive,
                apply_to_positive=apply_to_positive,
                apply_to_negative=apply_to_negative,
                log_prompt=log_prompt,
                text_negative=text_negative,
            )
        )
