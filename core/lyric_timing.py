# Original lyric text and full-song timestamps are the public interchange contract.
import copy
import json
import math
import re
from difflib import SequenceMatcher

_SECTION = re.compile(
    r"^(?:(?:final|last)\s+)?(?:intro|outro|verse|chorus|pre[- ]?chorus|post[- ]?chorus|bridge|hook|refrain|interlude|instrumental|solo|break|end|transition)(?:\s+\d+)?(?:\s+reprise)?(?:\s*[:\-—–].*)?$",
    re.IGNORECASE,
)
_META = re.compile(
    r"^(?:style|caption|genre|mood|tempo|bpm|key|instrumentation|instruments|production|vocal style|vocalist|duration|title)\s*:",
    re.IGNORECASE,
)
_DELIVERY = re.compile(
    r"^(?:singer\s+[\w-]+|(?:scream(?:ing)?|shout(?:ing)?|chant(?:ing)?|whisper(?:ing)?|"
    r"spoken(?:\s+word)?|rap(?:ping)?|singing)|"
    r"(?:(?:clear|clean|distorted|harsh|soft|low|high|deep|raspy|male|female|"
    r"spoken|screamed|shouted|whispered|layered|backing)\s+)*(?:voice|vocals?))$",
    re.IGNORECASE,
)


def _caption_tag(value):
    # Bracketed performance instructions are not words to align. Unknown sung
    # asides remain intact, including ordinary parenthetical backing lyrics.
    value = value.strip()
    if _SECTION.fullmatch(value) or _META.match(value):
        return True
    parts = [part.strip() for part in re.split(r"[.;,]", value) if part.strip()]
    return bool(parts) and all(_DELIVERY.fullmatch(part) for part in parts)


def clean_lyrics(value):
    value = value.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value)
    if value.startswith("{"):
        song = json.loads(value)
        if not isinstance(song, dict) or not isinstance(song.get("lyrics"), str):
            raise ValueError("Song JSON must contain a string lyrics field.")
        value = song["lyrics"]
    lines, removed = [], []
    for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.strip()
        heading = stripped.strip("[](): ").strip()
        if (_SECTION.fullmatch(heading) or _META.match(heading)
                or (stripped.startswith("[") and stripped.endswith("]")
                    and _caption_tag(stripped[1:-1]))):
            removed.append(line)
            continue
        # Remove recognized tags before sung text; preserve unknown asides.
        line = re.sub(
            r"\[([^\]]+)\]",
            lambda m: (
                ""
                if _caption_tag(m[1])
                else m[0]
            ),
            line,
        )
        lines.append(line.strip())
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        raise ValueError(
            "No sung lyrics remain after removing section/production headings."
        )
    return cleaned, removed


def audio_data(audio):
    from numbers import Integral

    import torch

    waveform, rate = audio.get("waveform"), audio.get("sample_rate")
    if (
        not isinstance(waveform, torch.Tensor)
        or waveform.ndim != 3
        or waveform.shape[0] != 1
        or waveform.shape[1] not in (1, 2)
        or waveform.shape[2] == 0
        or not waveform.is_floating_point()
        or not torch.isfinite(waveform).all()
        or isinstance(rate, bool)
        or not isinstance(rate, Integral)
        or rate <= 0
    ):
        raise ValueError(
            "AUDIO must contain one finite mono/stereo floating waveform [1,C,T] and a positive sample_rate."
        )
    return waveform[0].detach().cpu().float(), rate


def _interval(item, duration, label):
    start, end = item.get("start"), item.get("end")
    if start is None and end is None:
        return False
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, (int, float))
        or not isinstance(end, (int, float))
        or not math.isfinite(start)
        or not math.isfinite(end)
        or not 0 <= start < end <= duration + 0.001
    ):
        raise ValueError(
            f"{label}: expected null/null or 0 <= start < end <= duration."
        )
    return True


def validate_timing(value, expected_text=None):
    data = json.loads(value) if isinstance(value, str) else copy.deepcopy(value)
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("lines"), list)
    ):
        raise ValueError(
            "Timing JSON requires version: 1, duration, and a lines array."
        )
    duration = data.get("duration")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
    ):
        raise ValueError("Timing duration must be finite positive seconds.")
    for line in data["lines"]:
        if not isinstance(line, dict) or not isinstance(line.get("text"), str):
            raise TypeError("Each timing line requires text.")
        aligned = _interval(line, duration, "Line")
        words = line.setdefault("words", [])
        if not isinstance(words, list):
            raise TypeError("words must be an array.")
        char_end, word_end = 0, line["start"] if aligned else 0
        for word in words:
            if not isinstance(word, dict):
                raise TypeError("Each word must be an object.")
            a, b = word.get("char_start"), word.get("char_end")
            if (
                type(a) is not int
                or type(b) is not int
                or not char_end <= a < b <= len(line["text"])
                or word.get("text") != line["text"][a:b]
            ):
                raise ValueError(
                    "Word character spans must match the original line text in order."
                )
            char_end = b
            if _interval(word, duration, "Word"):
                if word["start"] < word_end - 0.001 or (
                    aligned and word["end"] > line["end"] + 0.001
                ):
                    raise ValueError(
                        "Word timings must be ordered and inside their line."
                    )
                word_end = word["end"]
    extras = data.get("extra_occurrences", [])
    if not isinstance(extras, list):
        raise TypeError("extra_occurrences must be an array.")
    if extras:
        for line in extras:
            if not isinstance(line, dict):
                raise TypeError("Each extra occurrence must be an object.")
            index = line.get("source_line")
            if (type(index) is not int or not 1 <= index <= len(data["lines"])
                    or line.get("text") != data["lines"][index - 1]["text"]
                    or not line["text"].strip()):
                raise ValueError("Extra occurrences must reference an identical supplied source line.")
        # Reuse all word/character validation without allowing nested extras.
        data["extra_occurrences"] = validate_timing(
            {"version": 1, "duration": duration, "lines": extras})["lines"]
    transcribed = data.get("transcribed_passages", [])
    if not isinstance(transcribed, list):
        raise TypeError("transcribed_passages must be an array.")
    if transcribed:
        for line in transcribed:
            if (not isinstance(line, dict) or not isinstance(line.get("text"), str)
                    or not line["text"].strip() or line.get("text_source") != "speech recognition"):
                raise ValueError("Transcribed passages require text and text_source: speech recognition.")
        data["transcribed_passages"] = validate_timing(
            {"version": 1, "duration": duration, "lines": transcribed})["lines"]
    previous = 0.0
    for line in caption_lines(data):
        if line["start"] is not None:
            if line["start"] < previous - 0.001:
                raise ValueError("Timing lines must be non-overlapping in chronological order.")
            previous = line["end"]
    if expected_text is not None and [
        x["text"] for x in data["lines"]
    ] != expected_text.split("\n"):
        raise ValueError(
            "Corrected timing lines must match the cleaned lyrics, including blank lines."
        )
    return data


def from_alignment(text, result, duration, language):
    # Map exact non-whitespace characters, without replacing punctuation or wording.
    original = [(i, c) for i, c in enumerate(text) if not c.isspace()]
    raw = [w for s in (result or {}).get("segments", []) for w in s.get("words", [])]
    emitted = "".join("".join(c for c in w["word"] if not c.isspace()) for w in raw)
    matcher = SequenceMatcher(
        None, emitted, "".join(c for _, c in original), autojunk=False
    )
    mapping = {}
    for a, b, count in matcher.get_matching_blocks():
        mapping.update((a + k, original[b + k][0]) for k in range(count))
    matched, cursor, last_end = [], 0, 0.0
    for w in raw:
        count = sum(not c.isspace() for c in w["word"])
        positions = [mapping.get(i) for i in range(cursor, cursor + count)]
        cursor += count
        if not positions or any(p is None for p in positions):
            continue
        a, b = positions[0], positions[-1] + 1
        if "\n" in text[a:b] or "".join(text[a:b].split()) != "".join(
            w["word"].split()
        ):
            continue
        start, end = w.get("start"), w.get("end")
        valid = (
            isinstance(start, (int, float))
            and isinstance(end, (int, float))
            and math.isfinite(start)
            and math.isfinite(end)
            and last_end <= start < end <= duration + 0.001
        )
        matched.append((a, b, start if valid else None, end if valid else None))
        if valid:
            last_end = end
    lines, offset = [], 0
    for line in text.split("\n"):
        words = [
            {
                "text": line[a - offset : b - offset],
                "char_start": a - offset,
                "char_end": b - offset,
                "start": s,
                "end": e,
            }
            for a, b, s, e in matched
            if offset <= a < b <= offset + len(line)
        ]
        covered = {
            i
            for w in words
            if w["start"] is not None
            for i in range(w["char_start"], w["char_end"])
        }
        # Missing text is explicit, with null timestamps; no interpolation.
        missing = [
            i for i, c in enumerate(line) if not c.isspace() and i not in covered
        ]
        timed = [word for word in words if word["start"] is not None]
        lines.append(
            {
                "text": line,
                "start": timed[0]["start"] if timed else None,
                "end": timed[-1]["end"] if timed else None,
                "words": words,
                "unaligned": [
                    {"char_start": m.start(), "char_end": m.end(), "text": m.group()}
                    for m in re.finditer(r"\S+", line)
                    if any(i in missing for i in range(m.start(), m.end()))
                ],
            }
        )
        offset += len(line) + 1
    return validate_timing(
        {"version": 1, "duration": duration, "language": language, "lines": lines}
    )


def timing_coverage(lines):
    # Derive coverage from character spans, including corrected JSON without
    # an unaligned field. Line-only corrections intentionally have no words.
    partial, unaligned = [], []
    for index, line in enumerate(lines, 1):
        if not line["text"].strip():
            continue
        if line["start"] is None:
            unaligned.append(index)
            continue
        if not line["words"]:
            continue
        covered = {
            i
            for word in line["words"]
            if word["start"] is not None
            for i in range(word["char_start"], word["char_end"])
        }
        missing = [
            {"text": match.group(), "char_start": match.start(), "char_end": match.end()}
            for match in re.finditer(r"\S+", line["text"])
            if any(i not in covered for i in range(match.start(), match.end()))
        ]
        if missing:
            partial.append({"line": index, "unaligned": missing})
    return {"partial_lines": partial, "unaligned_lines": unaligned}


def caption_lines(data):
    # Public lyric array stays in source order; rendering uses sung order.
    lines = [dict(line, source_line=index) for index, line in enumerate(data["lines"], 1)]
    lines.extend(data.get("extra_occurrences", []))
    # Give additional phrases distinct rendering identities without claiming
    # that they correspond one-to-one to any of the unresolved source lines.
    lines.extend(dict(line, source_line=len(data["lines"]) + index)
                 for index, line in enumerate(data.get("transcribed_passages", []), 1))
    return sorted(lines, key=lambda line: (line["start"] is None, line["start"] or 0))


def shifted_lines(data, duration, trim_start=0, timing_adjustment=0):
    if (
        not all(math.isfinite(v) for v in (trim_start, timing_adjustment))
        or trim_start < 0
    ):
        raise ValueError(
            "Trim start must be non-negative and timing adjustment finite."
        )
    out = []
    for source in caption_lines(data):
        if source.get("start") is None:
            continue
        line = copy.deepcopy(source)
        delta = timing_adjustment - trim_start
        line["start"], line["end"] = (
            max(0, source["start"] + delta),
            min(duration, source["end"] + delta),
        )
        if line["start"] >= line["end"]:
            continue
        line["words"] = [
            dict(
                w, start=max(0, w["start"] + delta), end=min(duration, w["end"] + delta)
            )
            for w in source["words"]
            if w["start"] is not None
            and w["end"] + delta > 0
            and w["start"] + delta < duration
        ]
        out.append(line)
    return out


def srt_text(lines):
    def stamp(t):
        ms = round(t * 1000)
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

    return "\n\n".join(
        f"{i}\n{stamp(x['start'])} --> {stamp(x['end'])}\n{x['text']}"
        for i, x in enumerate(sorted(lines, key=lambda line: line["start"]), 1)
    ) + ("\n" if lines else "")


def trim_audio(audio, trim_start=0, duration=0):
    waveform, rate = audio_data(audio)
    if not all(math.isfinite(v) and v >= 0 for v in (trim_start, duration)):
        raise ValueError("Trim start and duration must be finite non-negative seconds.")
    length = waveform.shape[-1]
    if trim_start >= length / rate:
        raise ValueError("Trim start must be before the end of the full audio.")
    start = int(trim_start * rate)
    end = length if duration == 0 else min(length, start + int(duration * rate))
    if end <= start:
        raise ValueError("Caption duration must contain at least one audio sample.")
    return {"waveform": waveform[:, start:end].unsqueeze(0), "sample_rate": rate}, start / rate


def analysis_audio(audio, vocals=None):
    waveform, rate = audio_data(audio)
    if vocals is None:
        return audio, "original mix"
    stem, stem_rate = audio_data(vocals)
    if abs(stem.shape[-1] / stem_rate - waveform.shape[-1] / rate) > 0.05 + 1e-9:
        raise ValueError("Vocals must represent the complete song starting at zero, within 50 ms of the full audio duration. Stems are never shifted or stretched.")
    return vocals, "connected vocals (complete song starting at zero)"
