# Render Lyric Captions [Eclipse] — file-backed captioned VIDEO, SRT and report.
import json
from pathlib import Path

from comfy_api.latest import io

from ..core import CATEGORY
from ..core.fonts import default_caption_font, get_font_list, get_font_path
from ..core.frame_timeline import TIMELINE_TYPE
from ..core.logger import log
from ..core.lyric_alignment import (
    ALGORITHM_REVISION,
    LANGUAGES,
    cached_alignment,
    model_identity,
)
from ..core.lyric_animation import ANIMATED_MODES, ROTATION_AXES
from ..core.lyric_render import render_video, validate_background
from ..core.lyric_timing import (
    analysis_audio,
    audio_data,
    caption_lines,
    clean_lyrics,
    shifted_lines,
    srt_text,
    timing_coverage,
    trim_audio,
    validate_timing,
)

_MISSING = object()


def _single(value, name):
    # ComfyUI wraps all inputs for list-aware nodes, including lazy placeholders.
    while isinstance(value, (list, tuple)):
        if len(value) != 1:
            raise ValueError(f"{name} requires exactly one value; only background accepts a sequence.")
        value = value[0]
    return value


class RvVideo_RenderLyricCaptions(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        background_type = io.MatchType.Template("background", allowed_types=[io.Image, io.Video, io.Custom(TIMELINE_TYPE)])
        return io.Schema(
            is_input_list=True,
            node_id="Render Lyric Captions [Eclipse]",
            display_name="Render Lyric Captions",
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            description="Align full-song lyrics using optional isolated vocals and render with the original soundtrack. Trim audio and captions together. Wire VIDEO through Preview Video for review, then to Save Video.",
            inputs=[
                io.Audio.Input("audio"),
                io.String.Input("lyrics", force_input=True, tooltip="Full-song plain lyrics or YuE2/MiniMax song JSON."),
                io.Combo.Input("language", options=["Auto"] + LANGUAGES, default="en"),
                io.Combo.Input("device", options=["auto", "cpu", "cuda"], default="auto"),
                io.Combo.Input(
                    "font_file", options=get_font_list(), default=default_caption_font()
                ),
                io.Int.Input("font_size", default=80, min=8, max=512),
                io.Combo.Input(
                    "mode", options=["whole-line", "active-word", *ANIMATED_MODES], default="floating-words",
                    tooltip="Words modes group fast words into phrases; lines modes use complete lines. Rotating captions turn in place at a speed set by lyric timing.",
                ),
                io.Int.Input("width", default=720, min=16, max=4096, step=2),
                io.Int.Input("height", default=1280, min=16, max=4096, step=2),
                io.Float.Input("fps", default=24, min=1, max=120),
                io.Combo.Input(
                    "position",
                    options=[
                        v + "_" + h
                        for v in ("top", "center", "bottom")
                        for h in ("left", "center", "right")
                    ],
                    default="bottom_center",
                ),
                io.Int.Input("margin_x", default=40, min=0, max=2048),
                io.Int.Input("margin_y", default=40, min=0, max=2048),
                io.String.Input("text_color", default="#ffffff"),
                io.String.Input("highlight_color", default="#ffff00"),
                io.String.Input("outline_color", default="#616161"),
                io.Int.Input("outline_width", default=3, min=0, max=32),
                io.String.Input("background_color", default="#000000"),
                io.Float.Input("caption_transparency", display_name="Caption transparency (%)",
                               default=0, min=0, max=100, step=1,
                               tooltip="Fade text, outline, highlight and glow together. 100% hides captions; background and audio are unchanged."),
                io.Boolean.Input("enable_glow", display_name="Enable glow", default=False),
                io.Int.Input("glow_intensity", default=5, min=2, max=20, step=1,
                             tooltip="Glow buildup iterations (more = denser glow)."),
                io.Int.Input("glow_range", default=25, min=1, max=500, step=1,
                             tooltip="Maximum glow expansion distance in pixels."),
                io.Int.Input("glow_blur", default=15, min=0, max=500, step=1,
                             tooltip="Blur radius applied to each glow step."),
                io.String.Input("glow_inner_color", default="#2ec0ff"),
                io.String.Input("glow_outer_color", default="#006eff"),
                io.Float.Input(
                    "trim_start",
                    force_input=True,
                    min=0,
                    max=86400,
                    step=0.01,
                    tooltip="Trim the full soundtrack and captions together, in seconds from the start of the song.",
                ),
                io.Float.Input("duration", force_input=True, min=0, max=86400, step=0.01, tooltip="Connect seconds explicitly. 0 renders the remainder of the song; the end is clamped to available samples."),
                io.Float.Input(
                    "timing_adjustment",
                    default=0,
                    min=-86400,
                    max=86400,
                    step=0.01,
                    tooltip="Positive values delay captions.",
                ),
                io.Float.Input("circle_radius", default=30, min=0, max=50, step=0.5,
                               tooltip="Floating caption centers stay inside this circle, centered on the video. Radius is a percentage of the shorter canvas dimension."),
                io.Float.Input("float_distance", default=1.5, min=0, max=20, step=0.1,
                               tooltip="Maximum gentle movement as a percentage of the shorter canvas dimension."),
                io.Float.Input("fade_in", default=0.5, min=0, max=2, step=0.01,
                               tooltip="Fade-in seconds, added before the minimum readable hold. Shortened when floating slots or the next rotating caption need room."),
                io.Float.Input("fade_out", default=0.5, min=0, max=2, step=0.01,
                               tooltip="Fade-out seconds after the sung span and minimum readable hold. Floating items can overlap while slots remain; rotating items finish by the next onset."),
                io.Float.Input("min_display", default=0.7, min=0, max=3, step=0.01,
                               tooltip="Minimum seconds at full animation opacity, excluding fades. Also groups fast words. Reduced when floating slots or the next rotating caption need room; sung onsets never move."),
                io.Int.Input("max_words", default=4, min=1, max=12,
                             tooltip="Maximum words to combine per phrase. Indivisible timed spans and attached untimed text stay intact."),
                io.Int.Input("max_simultaneous", default=5, min=1, max=8,
                             tooltip="Maximum visible floating items, including outgoing fades."),
                io.Int.Input("seed", default=42, min=0, max=0xFFFFFFFFFFFFFFFF,
                             control_after_generate=False, tooltip="Repeatable full-song positions, also preserved when trimming."),
                io.Audio.Input("vocals", optional=True, lazy=True, tooltip="Full-song isolated vocals starting at zero. Duration must match audio within 50 ms; never shifted or stretched."),
                io.String.Input("corrected_timing", optional=True, force_input=True, tooltip="Full-song timing JSON matching cleaned lyrics. Bypasses alignment and lazy vocal separation."),
                io.MatchType.Input(
                    "background", template=background_type, optional=True,
                    display_name="background image / video",
                    tooltip="Optional IMAGE, image batch/list, exact frame timeline, or one VIDEO. Timelines require matching caption FPS and stream exact pixels. Images play in order; VIDEO uses timestamps. Short backgrounds hold their last frame. Background audio is ignored.",
                ),
                io.Combo.Input("rotation_axis", options=list(ROTATION_AXES), default="Turning sign",
                               tooltip="Rotating modes: Turning sign turns around the vertical axis; Flipping card turns around the horizontal axis. Speed follows lyric timing automatically."),
                io.Combo.Input("unmatched_passages", options=["omit", "transcribe"], default="omit", optional=True,
                               tooltip="Keep matched lyrics. Transcribe uses recognized words only in gaps with unmatched lyrics; review added passages in timing JSON. Omit preserves the supplied-lyrics-only behavior."),
            ],
            outputs=[
                io.Video.Output("video"),
                io.String.Output("srt"),
                io.String.Output("report"),
                io.String.Output("timing_json"),
                io.String.Output("cleaned_lyrics"),
            ],
        )

    @classmethod
    def fingerprint_inputs(cls, font_file="", corrected_timing="", **kwargs):
        font_file = _single(font_file, "font_file")
        corrected_timing = _single(corrected_timing, "corrected_timing")
        if not isinstance(font_file, str):
            return None  # Linked widget values are resolved only during execution.
        path = Path(get_font_path(font_file))
        font = (path.stat().st_size, path.stat().st_mtime_ns) if path.is_file() else "missing"
        # Linked corrections are resolved at execution. Do not inspect model files
        # for a correction (or a link that might supply one).
        identity = model_identity() if isinstance(corrected_timing, str) and not corrected_timing.strip() else ()
        return font, identity, ALGORITHM_REVISION

    @classmethod
    def check_lazy_status(cls, vocals=_MISSING, corrected_timing=None, **kwargs):
        vocals = _single(vocals, "vocals")
        corrected_timing = _single(corrected_timing, "corrected_timing")
        return ["vocals"] if not (corrected_timing or "").strip() and vocals is None else []

    @classmethod
    def execute(
        cls, audio, lyrics, trim_start, duration, language="en", device="auto", vocals=None,
        corrected_timing="", timing_adjustment=0, background=None, unmatched_passages="omit", **settings,
    ):
        audio, lyrics, trim_start, duration, language, device, vocals, corrected_timing, timing_adjustment = (
            _single(value, name) for name, value in (
                ("audio", audio), ("lyrics", lyrics), ("trim_start", trim_start),
                ("duration", duration), ("language", language), ("device", device),
                ("vocals", vocals), ("corrected_timing", corrected_timing),
                ("timing_adjustment", timing_adjustment),
            )
        )
        settings = {name: _single(value, name) for name, value in settings.items()}
        unmatched_passages = _single(unmatched_passages, "unmatched_passages")
        if unmatched_passages not in ("omit", "transcribe"):
            raise ValueError("Unmatched passages must be omit or transcribe.")
        validate_background(background)
        text, removed = clean_lyrics(lyrics)
        waveform, rate = audio_data(audio)
        full_duration = waveform.shape[-1] / rate
        clipped, actual_start = trim_audio(audio, trim_start, duration)
        if (corrected_timing or "").strip():
            data = validate_timing(corrected_timing, text)
            if abs(data["duration"] - full_duration) > 0.05:
                raise ValueError("Corrected timing duration must match the full audio, before trimming.")
            report = {"source": "corrected timing; inference bypassed", "analysis_source": "corrected timing (no audio analysis)",
                      "algorithm_revision": ALGORITHM_REVISION, "cache_hit": False,
                      "language": data.get("language"), "warnings": []}
        else:
            source, source_name = analysis_audio(audio, vocals)
            data, report = cached_alignment(
                source, text, language, device, source_name,
                **({"fallback_audio": audio} if vocals is not None else {}),
                **({"transcribe_unmatched": True} if unmatched_passages == "transcribe" else {}),
            )
        # A resampled stem may differ by a sample, or within the allowed 50 ms.
        # Clamp timings at the original soundtrack end without shifting/stretching.
        data["duration"] = full_duration
        for line in [*data["lines"], *data.get("extra_occurrences", []), *data.get("transcribed_passages", [])]:
            for item in [line, *line["words"]]:
                if item["start"] is not None:
                    item["end"] = min(item["end"], full_duration)
                    if item["start"] >= item["end"]:
                        item["start"] = item["end"] = None
        data = validate_timing(data, text)
        clip_duration = clipped["waveform"].shape[-1] / rate
        lines = shifted_lines(data, clip_duration, actual_start, timing_adjustment)
        log.msg("RenderLyricCaptions", f"Full audio: {full_duration:.3f}s; output: {clip_duration:.3f}s; sample-boundary start: {actual_start:.6f}s; analysis: {report['analysis_source']}; cache hit: {report['cache_hit']}.")
        animated = settings.get("mode", "floating-words") in ANIMATED_MODES
        # Schedule against untouched full-song times. SRT alone uses clipped
        # intervals; even an outgoing fade from before the trim keeps its phase.
        video, warnings = render_video(
            clipped, caption_lines(data) if animated else lines,
            time_offset=actual_start - timing_adjustment if animated else 0,
            background=background, **settings,
        )
        coverage = timing_coverage(data["lines"])
        if coverage["partial_lines"]:
            warnings.append("Partially timed lines preserve their supported caption bounds; unresolved words are not highlighted.")
        if coverage["unaligned_lines"]:
            warnings.append("Unresolved lines are omitted from captions, not classified as absent from the recording. Supply corrected timing JSON to restore them.")
        report.update(coverage)
        report["extra_occurrences"] = [
            {key: line[key] for key in ("source_line", "text", "start", "end")}
            for line in data.get("extra_occurrences", [])
        ]
        report["transcribed_passages"] = [
            {key: line[key] for key in ("text", "start", "end")}
            for line in data.get("transcribed_passages", [])
        ]
        report.update(duration=clip_duration, full_audio_duration=full_duration,
                      trim_start=actual_start, requested_trim_start=trim_start,
                      requested_duration=duration, timing_adjustment=timing_adjustment,
                      caption_duration=sum(line["end"] - line["start"] for line in lines),
                      caption_lines=len(lines), unaligned_lines_skipped=coverage["unaligned_lines"],
                      removed_heading_count=len(removed))
        report.setdefault("warnings", []).extend(warnings)
        return io.NodeOutput(video, srt_text(lines), json.dumps(report, ensure_ascii=False, indent=2),
                             json.dumps(data, ensure_ascii=False, indent=2), text)
