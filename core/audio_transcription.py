# Recognized text and observed timestamps, without a supplied lyric reference.
import copy
import math
from itertools import pairwise
from numbers import Real

from .logger import log
from .lyric_alignment import MODEL_REVISION, recognition_model
from .lyric_timing import (
    analysis_audio,
    audio_data,
    clean_lyrics,
    from_alignment,
    timing_coverage,
    validate_timing,
)

TRANSCRIPTION_REVISION = "observed-transcript-v1"


def _valid_interval(start, end, duration):
    return (type(start) in (int, float) and type(end) in (int, float)
            and math.isfinite(start) and math.isfinite(end)
            and 0 <= start < end <= duration + 0.001)


def transcript_timing(segments, duration, language):
    # Work on copies: callers may retain raw observations for diagnostics.
    segments = copy.deepcopy(segments)
    accepted, rejected, confidence = [], [], []
    for index, segment in enumerate(segments, 1):
        # Faster-whisper can return NumPy timestamps, not builtin floats.
        for item in [segment, *segment.get("words", [])]:
            for key in ("start", "end"):
                value = item.get(key)
                item[key] = float(value) if isinstance(value, Real) and not isinstance(value, bool) else None
        raw_text = " ".join(segment["text"].split())
        if not raw_text:
            continue
        if not any(character.isalnum() for character in raw_text):
            rejected.append({"segment": index, "text": raw_text,
                             "reason": "no recognized letters or numbers"})
            continue
        score = segment.get("avg_logprob", 0)
        if not math.isfinite(score) or score < -1.2:
            rejected.append({"segment": index, "text": raw_text,
                             "reason": "low recognition score"})
            continue
        # Keep the transcript and renderer's cleaned-text contract identical.
        try:
            text, removed = clean_lyrics(raw_text)
        except ValueError:
            rejected.append({"segment": index, "text": raw_text,
                             "reason": "section or production heading; no caption text"})
            continue
        segment["text"] = text
        accepted.append(segment)
        confidence.append({"segment": index, "line": len(accepted), "text": text,
                           "avg_logprob": float(score),
                           "no_speech_probability": float(segment["no_speech_prob"]) if segment.get("no_speech_prob") is not None else None,
                           "removed_headings": removed})

    # Whisper can give adjacent words a few overlapping timestamp steps.
    # Reconcile only small, positive intervals; never split a sentence evenly.
    words = [word for segment in accepted for word in segment.get("words", [])]
    normalized = []
    for previous, current in pairwise(words):
        ps, pe, cs, ce = (previous.get("start"), previous.get("end"),
                          current.get("start"), current.get("end"))
        if not (_valid_interval(ps, pe, duration) and _valid_interval(cs, ce, duration)):
            continue
        midpoint = (pe + cs) / 2
        if (0 < pe - cs <= 0.1 + 1e-9 and ps < midpoint < ce
                and previous["word"].strip().casefold() != current["word"].strip().casefold()):
            normalized.append({"previous": previous["word"], "current": current["word"],
                               "overlap": pe - cs, "boundary": midpoint})
            previous["end"] = current["start"] = midpoint

    lines, previous_end, phrase_only = [], 0.0, []
    for segment in accepted:
        for word in segment.get("words", []):
            start, end = word.get("start"), word.get("end")
            if not _valid_interval(start, end, duration) or start < previous_end:
                word["start"] = word["end"] = None
        line = from_alignment(segment["text"], {"segments": [segment]}, duration, language)["lines"][0]
        if line["start"] is None:
            start, end = segment.get("start"), segment.get("end")
            if _valid_interval(start, end, duration) and start >= previous_end:
                line.update(start=start, end=end, timing_source="recognized segment")
                phrase_only.append(len(lines) + 1)
        if line["start"] is not None:
            previous_end = line["end"]
        lines.append(line)
    data = validate_timing({"version": 1, "duration": duration,
                            "language": language, "lines": lines})
    report = {"segments": confidence, "rejected_segments": rejected,
              "normalized_word_overlaps": normalized, "phrase_only_lines": phrase_only,
              **timing_coverage(lines)}
    return data, report


def transcribe_audio(audio, language="Auto", device="auto", vocals=None):
    import comfy.model_management as mm

    mm.throw_exception_if_processing_interrupted()
    waveform, rate = audio_data(audio)
    duration = waveform.shape[-1] / rate
    source, source_name = analysis_audio(audio, vocals)
    with recognition_model(source, language, device) as (model, samples, detected_language, detection):
        log.msg("TranscribeAudio", f"Transcribing {duration:.3f}s; analysis: {source_name}.")
        segments, _ = model.transcribe_original(
            samples, language=detected_language, task="transcribe", beam_size=5,
            condition_on_previous_text=False, vad_filter=False, word_timestamps=True,
        )
        observations = []
        try:
            for segment in segments:
                mm.throw_exception_if_processing_interrupted()
                observations.append({
                    "text": segment.text, "start": segment.start, "end": segment.end,
                    "avg_logprob": segment.avg_logprob, "no_speech_prob": segment.no_speech_prob,
                    "words": [{"word": w.word, "start": w.start, "end": w.end}
                              for w in segment.words or []],
                })
        finally:
            close = getattr(segments, "close", None)
            if close:
                close()
        mm.throw_exception_if_processing_interrupted()
        data, details = transcript_timing(observations, duration, detected_language)
    warnings = [
        "Automatic transcription can miss or invent words, especially in singing, backing vocals and instrumental passages. Review the text before rendering or using it for lip sync.",
        "Recognition scores are model estimates, not measured transcription accuracy. Text is transcribed in the selected language, never translated.",
    ]
    if detection["uncertain"]:
        warnings.append("Language detection is uncertain; review or select the language explicitly.")
    if not data["lines"]:
        warnings.append("No usable transcript was recognized. Try isolated vocals or select a language; the caption renderer requires non-empty lyrics.")
    if details["phrase_only_lines"]:
        warnings.append("Some lines have only observed segment timing; they cannot highlight individual words.")
    if details["partial_lines"] or details["unaligned_lines"]:
        warnings.append("Missing, invalid or conflicting timestamps remain unresolved; review timing_json before rendering.")
    report = {"source": "automatic transcription", "analysis_source": source_name,
              "model_revision": MODEL_REVISION, "algorithm_revision": TRANSCRIPTION_REVISION,
              "language": detection, "full_audio_duration": duration,
              "warnings": warnings, **details}
    return "\n".join(line["text"] for line in data["lines"]), data, report
