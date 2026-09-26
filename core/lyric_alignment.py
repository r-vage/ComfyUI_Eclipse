# Offline stable-ts/faster-whisper alignment against a pinned, verified model.
import gc
import hashlib
import json
import math
import threading
from bisect import bisect_right
from collections import OrderedDict, deque
from contextlib import contextmanager
from difflib import SequenceMatcher
from functools import lru_cache
from itertools import pairwise
from numbers import Real
from pathlib import Path

import folder_paths

from .logger import log
from .lyric_timing import audio_data, from_alignment, timing_coverage

ALGORITHM_REVISION = "chronological-window-alignment-v10"
CACHE_MAX_ENTRIES = 8
CACHE_MAX_BYTES = 16 * 1024 * 1024
_ALIGNMENT_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()

MODEL_REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
MODEL_HASHES = {
    "model.bin": (
        "sha256",
        "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1",
    ),
    "config.json": ("git", "75336feae814999bae6ccccdecf177639ffc6f9d"),
    "tokenizer.json": ("git", "3a5e2ba63acdcac9a19ba56cf9bd27f185bfff61"),
    "vocabulary.json": ("git", "0adcd01e7c237205d593b707e66dd5d7bc785d2d"),
    "preprocessor_config.json": ("git", "931c77a740890c46365c7ae0c9d350ba3cca908f"),
}
# Whisper large-v3 language codes. Importing the optional runtime is unnecessary for schema discovery.
LANGUAGES = [
    "en",
    "zh",
    "de",
    "es",
    "ru",
    "ko",
    "fr",
    "ja",
    "pt",
    "tr",
    "pl",
    "ca",
    "nl",
    "ar",
    "sv",
    "it",
    "id",
    "hi",
    "fi",
    "vi",
    "he",
    "uk",
    "el",
    "ms",
    "cs",
    "ro",
    "da",
    "hu",
    "ta",
    "no",
    "th",
    "ur",
    "hr",
    "bg",
    "lt",
    "la",
    "mi",
    "ml",
    "cy",
    "sk",
    "te",
    "fa",
    "lv",
    "bn",
    "sr",
    "az",
    "sl",
    "kn",
    "et",
    "mk",
    "br",
    "eu",
    "is",
    "hy",
    "ne",
    "mn",
    "bs",
    "kk",
    "sq",
    "sw",
    "gl",
    "mr",
    "pa",
    "si",
    "km",
    "sn",
    "yo",
    "so",
    "af",
    "oc",
    "ka",
    "be",
    "tg",
    "sd",
    "gu",
    "am",
    "yi",
    "lo",
    "uz",
    "fo",
    "ht",
    "ps",
    "tk",
    "nn",
    "mt",
    "sa",
    "lb",
    "my",
    "bo",
    "tl",
    "mg",
    "as",
    "tt",
    "haw",
    "ln",
    "ha",
    "ba",
    "jw",
    "su",
    "yue",
]


def model_path():
    return Path(folder_paths.models_dir) / "whisper" / "large-v3"


def model_identity():
    root = model_path()
    return tuple(
        (name, p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
        for name in MODEL_HASHES
        if (p := root / name).is_file()
    )


@lru_cache(maxsize=2)
def _verify(root, identity):
    if len(identity) != len(MODEL_HASHES):
        raise ValueError(
            "Missing large-v3 files in models/whisper/large-v3. See Readme/Lyric_Captions.md."
        )
    for name, (kind, expected) in MODEL_HASHES.items():
        path = Path(root) / name
        digest = hashlib.sha256() if kind == "sha256" else hashlib.sha1()
        if kind == "git":
            digest.update(f"blob {path.stat().st_size}\0".encode())
        with path.open("rb") as stream:
            while chunk := stream.read(8 * 1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError(
                f"Unverified large-v3 artifact: {name}. Use the documented pinned Systran revision."
            )
    return root


def detect_language(model, samples):
    from faster_whisper.vad import get_speech_timestamps

    chunks = get_speech_timestamps(samples, sampling_rate=16000)
    if not chunks and max(abs(samples), default=0) < 0.0001:
        raise ValueError(
            "VAD detection failed on the analyzed audio (zero coverage). Select a manual language; no English fallback was applied. This does not establish absence of vocals."
        )
    # Sample vocal candidates across the song instead of assuming the intro is sung.
    vocal_samples = sum(chunk["end"] - chunk["start"] for chunk in chunks)
    offsets = []
    # A continuous sung region can be minutes long, so sample inside it too.
    for fraction in (0, 0.25, 0.5, 0.75):
        remaining = int(vocal_samples * fraction)
        for chunk in chunks:
            length = chunk["end"] - chunk["start"]
            if remaining < length:
                offsets.append(chunk["start"] + remaining)
                break
            remaining -= length
    if vocal_samples < len(samples) * 0.05:
        # Speech VAD often misses sung vocals. Inspect later music windows too.
        offsets.extend(
            int(max(0, len(samples) - 30 * 16000) * f) for f in (0.2, 0.5, 0.8, 1.0)
        )
    votes, observations = {}, []
    for start in sorted(set(offsets)):
        sample = samples[start : min(len(samples), start + 30 * 16000)]
        language, confidence, _ = model.detect_language(sample)
        votes[language] = votes.get(language, 0) + confidence
        observations.append(
            {
                "offset": start / 16000,
                "language": language,
                "confidence": float(confidence),
            }
        )
    chosen = max(votes, key=votes.get)
    confidence = sum(
        x["confidence"] for x in observations if x["language"] == chosen
    ) / len(observations)
    uncertain = confidence < 0.7 or len(votes) > 1
    return chosen, {
        "mode": "Auto",
        "detected_language": chosen,
        "confidence": confidence,
        "uncertain": uncertain,
        "uncertainty_reasons": (["low aggregate language score"] if confidence < 0.7 else [])
        + (["language disagreement across samples"] if len(votes) > 1 else []),
        "vad_status": "detection failed on analyzed audio; not evidence of absent vocals" if not vocal_samples else "regions detected",
        "samples": observations,
        "vad_coverage": vocal_samples / len(samples),
    }


def _normalize_lyric(value):
    return "".join(c for c in value.casefold() if c.isalnum())


def _line_candidates(chars, words, min_precision=0.65):
    # Compare each continuous word window independently. A whole-transcript
    # longest-block match can assign the first chorus to a later repetition and
    # discard everything between them. Keep alternatives for the order solver.
    matcher = SequenceMatcher(None, chars, autojunk=False)
    candidates, best_coverage = {}, 0.0
    minimum = max(min(4, len(chars)), math.ceil(len(chars) * 0.65))
    maximum = len(chars) / min_precision
    for first in range(len(words)):
        heard, owners = "", []
        for last in range(first, len(words)):
            word = words[last]
            if (word["end"] - words[first]["start"] > 30
                    or (last > first and (word["start"] - words[last - 1]["end"] > 3
                                         or word["start"] < words[last - 1]["end"] - 1e-9))):
                break
            normalized = word["normalized"]
            heard += normalized
            owners.extend([last] * len(normalized))
            if len(heard) > maximum:
                break
            if len(heard) < minimum:
                continue
            matcher.set_seq2(heard)
            if matcher.quick_ratio() < 0.65:
                continue
            blocks = [block for block in matcher.get_matching_blocks() if block.size]
            count = sum(block.size for block in blocks)
            coverage = count / len(chars)
            best_coverage = max(best_coverage, coverage)
            precision = count / len(heard)
            if count < minimum or precision < min_precision:
                continue
            lo = owners[blocks[0].b]
            hi = owners[blocks[-1].b + blocks[-1].size - 1]
            # Quality rewards support for the whole line and penalizes unrelated
            # recognized words. Extra words never improve a candidate's score.
            quality = count - 0.25 * (len(heard) - count)
            key = (lo, hi)
            candidate = {"start": words[lo]["start"], "end": words[hi]["end"],
                         "coverage": coverage, "precision": precision, "matched_characters": count, "quality": quality}
            if key not in candidates or quality > candidates[key]["quality"]:
                candidates[key] = candidate
    return list(candidates.values()), best_coverage


def _recognition_words(segments, duration, *, sentences=False):
    words = []
    for segment in segments:
        score = segment.get("avg_logprob", 0)
        if not isinstance(score, Real) or not math.isfinite(score) or score < -1.2:
            continue
        units = [segment] if sentences else segment.get("words", [])
        for word in units:
            start, end = word.get("start"), word.get("end")
            if not (isinstance(start, Real) and isinstance(end, Real)
                    and not isinstance(start, bool) and not isinstance(end, bool)
                    and math.isfinite(start) and math.isfinite(end)
                    and 0 <= start < end <= duration):
                continue
            chars = _normalize_lyric(word["text"] if sentences else word["word"])
            if chars:
                words.append(dict(word, start=float(start), end=float(end), normalized=chars))
    words.sort(key=lambda word: (word["start"], word["end"]))
    if not sentences:
        for previous, current in pairwise(words):
            overlap = previous["end"] - current["start"]
            midpoint = (previous["end"] + current["start"]) / 2
            if (0 < overlap <= 0.1 + 1e-9
                    and previous["normalized"] != current["normalized"]
                    and previous["start"] < midpoint < current["end"]):
                previous["end"] = current["start"] = midpoint
    return words


def lyric_anchors(text, segments, duration, *, sentences=False):
    # Reference matching preserves supplied wording. Optional transcription is
    # handled separately, after every supported reference occurrence is fixed.
    words = _recognition_words(segments, duration, sentences=sentences)
    # A frontier holds the best ordered path available by each ending time.
    # Skip transitions preserve unresolved lines; matching never reuses audio.
    frontier = [(0.0, 0.0, None)]
    choices, diagnostics, candidate_cache = {}, {}, {}
    for index, line in enumerate(text.split("\n"), 1):
        chars = _normalize_lyric(line)
        if not chars:
            continue
        if chars not in candidate_cache:
            candidate_cache[chars] = _line_candidates(chars, words, 0.75 if sentences else 0.65)
        candidates, coverage = candidate_cache[chars]
        choices[index] = candidates
        diagnostics[index] = {"line": index, "text": line,
                              "reason": "insufficient continuous vocal-text match (possible recognition gap)",
                              "coverage": coverage}
        endings = [state[0] for state in frontier]
        expanded = list(frontier)
        for candidate in candidates:
            previous = frontier[bisect_right(endings, candidate["start"]) - 1]
            anchor = {k: candidate[k] for k in ("start", "end", "coverage", "precision", "matched_characters")}
            anchor.update(text=line, line=index)
            expanded.append((candidate["end"], previous[1] + candidate["quality"], (anchor, previous[2])))
        frontier = []
        for state in sorted(expanded, key=lambda state: (state[0], -state[1])):
            if not frontier or state[1] > frontier[-1][1] + 1e-9:
                frontier.append(state)
    anchors, path = [], frontier[-1][2]
    while path is not None:
        anchor, path = path
        anchors.append(anchor)
    anchors.reverse()
    accepted = {anchor["line"] for anchor in anchors}
    rejected = []
    for index, detail in diagnostics.items():
        if index not in accepted:
            if choices[index]:
                detail["reason"] = "recognized candidates conflict with lyric order or another occurrence"
                detail["candidate_windows"] = [
                    {k: candidate[k] for k in ("start", "end", "coverage", "precision", "matched_characters")}
                    for candidate in sorted(choices[index], key=lambda candidate: -candidate["quality"])[:3]
                ]
            rejected.append(detail)
    return anchors, rejected


def _transcribe_window(model, samples, language, start=0, end=None, *, word_timestamps=True):
    import comfy.model_management as mm

    first = int(start * 16000)
    last = len(samples) if end is None else min(len(samples), int(end * 16000))
    offset = first / 16000
    mm.throw_exception_if_processing_interrupted()
    segments, _ = model.transcribe_original(
        samples[first:last], language=language, task="transcribe", beam_size=5,
        condition_on_previous_text=False, vad_filter=False, word_timestamps=word_timestamps,
    )
    observations = []
    try:
        for segment in segments:
            mm.throw_exception_if_processing_interrupted()
            observation = {"avg_logprob": segment.avg_logprob,
                           "no_speech_prob": getattr(segment, "no_speech_prob", None),
                           "compression_ratio": getattr(segment, "compression_ratio", None)}
            if word_timestamps:
                observation["words"] = [
                    {"word": w.word, "start": float(w.start) + offset, "end": float(w.end) + offset,
                     "probability": getattr(w, "probability", None)}
                    for w in segment.words or []
                ]
            else:
                # Decoder sentence timestamps are independent of word extraction.
                # Faster-whisper rewrites segment bounds from words when word
                # timestamps are enabled, so retaining those would not suffice.
                observation.update(text=segment.text, start=float(segment.start) + offset,
                                   end=float(segment.end) + offset)
            observations.append(observation)
    finally:
        close = getattr(segments, "close", None)
        if close:
            close()
    return observations


def _recover_sentence_boundaries(model, samples, text, language, anchors, rejected, duration):
    anchors = sorted(anchors, key=lambda a: a["line"])
    if not rejected:
        return anchors, rejected, {"performed": False, "checked_lines": [], "recovered_lines": []}
    observations = _transcribe_window(model, samples, language, word_timestamps=False)
    lines = text.split("\n")
    boundaries = [{"line": 0, "end": 0.0}, *anchors,
                  {"line": len(lines) + 1, "start": duration}]
    recovered, additions = set(), []
    for previous, following in pairwise(boundaries):
        missing = [r for r in rejected if previous["line"] < r["line"] < following["line"]]
        if not missing:
            continue
        start, end = previous["end"], following["start"]
        first, last = missing[0]["line"], missing[-1]["line"]
        # Preserve already supported neighbors. Decoder bounds may overlap them
        # by a few timestamp steps; trim at most 0.5 s at either boundary, never
        # move a sentence from a different occurrence into this interval.
        local = [dict(o, start=max(start, o["start"]), end=min(end, o["end"]))
                 for o in observations if start - 0.5 <= o["start"] < o["end"] <= end + 0.5]
        candidates, _ = lyric_anchors("\n".join(lines[first - 1:last]), local, duration, sentences=True)
        for candidate in candidates:
            candidate.update(line=candidate["line"] + first - 1, boundary_source="sentence timestamps")
            additions.append(candidate)
            recovered.add(candidate["line"])
    return (sorted([*anchors, *additions], key=lambda a: a["line"]),
            [r for r in rejected if r["line"] not in recovered],
            {"performed": True, "checked_lines": [r["line"] for r in rejected], "recovered_lines": sorted(recovered),
             "source": "independent decoder sentence timestamps; word timestamp extraction disabled"})


def _context_windows(start, end, duration):
    # Include neighboring context and overlap long-gap crops so phrases crossing
    # a crop edge can be heard in full. Keep the fractional recording tail.
    if end - start <= 30:
        first = max(0.0, min(float(math.floor((start + end) / 2 - 15)), duration - 30))
        first = max(first, end - 30)
        yield first, min(duration, first + 30)
        return
    first = max(0.0, min(float(math.floor(start)) - 3, duration - 30))
    while True:
        last = min(duration, first + 30)
        yield first, last
        if last >= end:
            return
        first = min(first + 24, max(0.0, float(math.floor(end - 27))), duration - 30)


def _context_recovery(model, samples, fallback_samples, text, language, anchors, rejected,
                      duration, remaining, observations):
    # Decode with neighboring lyrics for context, but admit timestamps only in
    # the unresolved interval. Reuse each decoded window for nearby gaps.
    lines, retries = text.split("\n"), []
    sources = [(samples, "analysis audio")]
    if fallback_samples is not None:
        sources.append((fallback_samples, "original mix fallback"))
    for source_samples, source_name in sources:
        decoded = []
        reserved = remaining // 2 if source_name == "analysis audio" and fallback_samples is not None else 0
        boundaries = [{"line": 0, "end": 0.0}, *sorted(anchors, key=lambda a: a["line"]),
                      {"line": len(lines) + 1, "start": duration}]
        pending = deque(
            (previous, following, iter(_context_windows(previous["end"], following["start"], duration)))
            for previous, following in pairwise(boundaries)
            if previous["end"] < following["start"]
        )
        # Give every unresolved section a turn before spending more of the
        # bounded budget on one long gap. Reserve the existing fallback share.
        while pending:
            previous, following, windows = pending.popleft()
            missing = [r for r in rejected if previous["line"] < r["line"] < following["line"]]
            start, end = previous["end"], following["start"]
            window = next(windows, None) if missing else None
            if window is None:
                continue
            pending.append((previous, following, windows))
            window_start, window_end = window
            needed_start, needed_end = max(start, window_start), min(end, window_end)
            cached = next((item for item in decoded if item[0] <= needed_start and needed_end <= item[1]), None)
            if cached is None:
                if remaining <= reserved:
                    break
                remaining -= 1
                heard = _transcribe_window(model, source_samples, language, window_start, window_end)
                cached = (window_start, window_end, heard)
                decoded.append(cached)
                observations.append(heard)
            window_start, window_end, heard = cached
            # Newly accepted lines split the original gap. Match each remaining
            # interval independently so overlapping crops cannot reuse a chorus
            # or overwrite the timing of a supported neighbor.
            inner = [previous, *sorted(
                (a for a in anchors if previous["line"] < a["line"] < following["line"]),
                key=lambda a: a["line"]), following]
            accepted = []
            words = _recognition_words(heard, duration)
            for before, after in pairwise(inner):
                unresolved = [r for r in missing if before["line"] < r["line"] < after["line"]]
                lo, hi = before["end"], after["start"]
                if not unresolved or hi <= window_start or lo >= window_end:
                    continue
                local = [{"words": [w for w in words if lo <= w["start"] < w["end"] <= hi]}]
                first, last = unresolved[0]["line"], unresolved[-1]["line"]
                candidates, diagnostics = lyric_anchors("\n".join(lines[first - 1:last]), local, duration)
                for candidate in candidates:
                    candidate.update(line=candidate["line"] + first - 1, recovery_source=source_name)
                    anchors.append(candidate)
                    accepted.append(candidate["line"])
                for detail in diagnostics:
                    for item in rejected:
                        if item["line"] == detail["line"] + first - 1:
                            item["context_rejection"] = {**detail, "line": item["line"], "source": source_name}
            rejected = [r for r in rejected if r["line"] not in accepted]
            retries.append({"kind": "context recovery", "source": source_name,
                            "start": window_start, "end": window_end,
                            "allowed_start": start, "allowed_end": end,
                            "lines": [r["line"] for r in missing], "accepted_lines": accepted})
    return sorted(anchors, key=lambda a: a["line"]), rejected, retries, remaining


def _unused_occurrences(text, observations, anchors, rejected, duration):
    # Work with each independent decode separately; combining overlapping
    # decodes would duplicate words and create false timestamp conflicts.
    lines = text.split("\n")
    groups = {}
    for index, line in enumerate(lines, 1):
        chars = _normalize_lyric(line)
        if chars:
            groups.setdefault(chars, []).append(index)
    occupied = list(anchors)
    additions, repetitions = [], []
    unresolved = {item["line"] for item in rejected}
    proposals = []
    for chars, indices in groups.items():
        candidates = {}
        for heard in observations:
            words = [w for w in _recognition_words(heard, duration)
                     if not any(w["start"] < a["end"] and a["start"] < w["end"] for a in occupied)]
            choices, _ = _line_candidates(chars, words, 0.8)
            for candidate in choices:
                if candidate["coverage"] < 0.8 or any(
                        candidate["start"] < a["end"] and a["start"] < candidate["end"] for a in occupied):
                    continue
                key = candidate["start"], candidate["end"]
                if key not in candidates or candidate["quality"] > candidates[key]["quality"]:
                    candidates[key] = candidate
        for candidate in candidates.values():
            proposals.append((chars, indices, candidate))
    proposals.sort(key=lambda item: (-item[2]["coverage"], -item[2]["precision"],
                                    -item[2]["matched_characters"], item[2]["start"]))
    selected = {}
    for chars, indices, candidate in proposals:
        if any(candidate["start"] < a["end"] and a["start"] < candidate["end"] for a in occupied):
            continue
        remaining = [index for index in indices if index in unresolved]
        chosen = selected.setdefault(chars, [])
        extra = len(chosen) >= len(remaining)
        if extra and (candidate["coverage"] < 1 or candidate["precision"] < 1
                      or len(chars) < 12 or len(lines[indices[0] - 1].split()) < 3):
            continue
        chosen.append(candidate)
        occupied.append(candidate)
    for chars, candidates in selected.items():
        indices = groups[chars]
        remaining = [index for index in indices if index in unresolved]
        # Remaining identical reference lines receive distinct occurrences in
        # sung order, regardless of candidate score ordering above.
        for position, candidate in enumerate(sorted(candidates, key=lambda a: a["start"])):
            if position < len(remaining):
                index = remaining[position]
                additions.append(dict(candidate, line=index, text=lines[index - 1],
                                      recovery_source="unused recognized occurrence"))
                unresolved.remove(index)
            else:
                index = min(indices, key=lambda i: min(
                    (abs(a["start"] - candidate["start"]) for a in anchors if a["line"] == i),
                    default=float("inf")))
                repetitions.append(dict(candidate, line=index, text=lines[index - 1],
                                        recovery_source="extra exact repetition"))
    return additions, repetitions, [item for item in rejected if item["line"] in unresolved]


def _align_windows(model, samples, anchors, language, duration, fallback_samples=None):
    import comfy.model_management as mm

    mm.throw_exception_if_processing_interrupted()
    if fallback_samples is not None:
        result = [None] * len(anchors)
        for source, fallback in ((samples, False), (fallback_samples, True)):
            indices = [i for i, anchor in enumerate(anchors)
                       if (anchor.get("recovery_source") == "original mix fallback") == fallback]
            if indices:
                aligned = _align_windows(model, source, [anchors[i] for i in indices], language, duration)
                for index, line in zip(indices, aligned):
                    result[index] = line
        return result
    # Recovered repetitions can be grouped by lyric text rather than song time.
    # Stable-ts requires chronological windows; restore caller order afterward
    # so each aligned phrase keeps its source lyric and audio-source mapping.
    ordered = sorted(enumerate(anchors), key=lambda item: item[1]["start"])
    result = model.align_words(
        samples, [{k: anchor[k] for k in ("text", "start", "end")} for _, anchor in ordered],
        language=language, normalize_text=False, regroup=False,
        verbose=None, suppress_silence=True,
    )
    aligned = result.to_dict()["segments"] if result is not None else [{"words": []} for _ in anchors]
    if len(aligned) != len(anchors):
        raise ValueError("Alignment changed the vocal-window count; supply corrected timing JSON.")
    lines = [None] * len(anchors)
    for (index, anchor), segment in zip(ordered, aligned):
        for word in segment.get("words", []):
            if (word.get("start") is None or word.get("end") is None
                    or word["start"] < anchor["start"] - 0.001 or word["end"] > anchor["end"] + 0.001):
                word["start"] = word["end"] = None
        line = from_alignment(anchor["text"], {"segments": [segment]}, duration, language)["lines"][0]
        if line["start"] is not None:
            # Word-level silence suppression often clips a sung tail. The ASR
            # window supplies an observed ending, without inventing word timings
            # or extending captions toward the next phrase across a silent gap.
            line["end"] = max(line["end"], anchor["end"])
        lines[index] = line
    return lines


def _timed_characters(line):
    return sum(w["char_end"] - w["char_start"] for w in line["words"] if w["start"] is not None)


def _suspect_boundary(line):
    return (line["start"] is None
            or any(w["start"] is not None and w["end"] - w["start"] > 2.5 for w in line["words"][:2])
            or any(w["char_start"] == 0 or w["char_end"] == len(line["text"]) for w in line.get("unaligned", [])))


def _transcription_gaps(anchors, rejected, duration, line_count):
    boundaries = [{"line": 0, "end": 0.0}, *sorted(anchors, key=lambda a: a["start"]),
                  {"line": line_count + 1, "start": duration}]
    return [(before["end"], after["start"]) for before, after in pairwise(boundaries)
            if before["end"] < after["start"] and any(
                before["line"] < item["line"] < after["line"] for item in rejected)]


def _transcribed_gaps(observations, anchors, repetitions, rejected, duration, language, line_count):
    # Only fill intervals with unresolved reference lines. Never overwrite a
    # supported phrase, split its wording, or assign missing lyrics by guesswork.
    gaps = _transcription_gaps(anchors, rejected, duration, line_count)
    occupied = [*anchors, *repetitions]
    passages, discarded = [], []
    for index, segment in enumerate(observations, 1):
        reason = None
        for key, maximum in (("no_speech_prob", 0.6), ("compression_ratio", 2.4)):
            value = segment.get(key)
            if value is not None and (not isinstance(value, Real) or not math.isfinite(value)
                                      or value > maximum):
                reason = "speech recognition flagged silence or repetitive output"
        if reason:
            discarded.append({"segment": index, "reason": reason})
            continue
        runs, run, previous_gap = [], [], None
        # Use one full-song decode; overlapping retry transcripts must not be
        # stitched into invented phrases. Word times are observed, never spread
        # across decoder sentences or the space between reference anchors.
        for word in _recognition_words([segment], duration):
            gap = next((g for g in gaps if g[0] <= word["start"] < word["end"] <= g[1]), None)
            probability = word.get("probability")
            usable = (gap is not None and word["end"] - word["start"] <= 3
                      and (probability is None or (isinstance(probability, Real)
                           and math.isfinite(probability) and 0.5 <= probability <= 1))
                      and not any(word["start"] < a["end"] and a["start"] < word["end"]
                                  for a in occupied))
            split = run and (not usable or gap != previous_gap
                             or word["start"] - run[-1]["end"] > 1.5
                             or word["start"] < run[-1]["end"]
                             or word["end"] - run[0]["start"] > 8
                             or sum(len(w["word"]) for w in run) + len(word["word"]) > 80)
            if split:
                runs.append(run)
                run = []
            if usable:
                run.append(word)
                previous_gap = gap
        if run:
            runs.append(run)
        for words in runs:
            text = " ".join("".join(w["word"] for w in words).split())
            line = from_alignment(text, {"segments": [{"words": words}]}, duration, language)["lines"][0]
            if line["start"] is None or any(
                    line["start"] < a["end"] and a["start"] < line["end"] for a in occupied):
                continue
            line["text_source"] = "speech recognition"
            passages.append(line)
            occupied.append(line)
    return passages, discarded


def anchored_alignment(model, samples, text, language, fallback_samples=None, *, transcribe_unmatched=False):
    duration = len(samples) / 16000
    observations = [_transcribe_window(model, samples, language)]
    anchors, rejected = lyric_anchors(text, observations[0], duration)
    retries = []
    # Retry only short interior gaps bounded by reliable neighboring lines. Keep
    # a fixed inference budget; never force unrecognized text into a silent gap.
    remaining = 12
    lines = text.split("\n")
    initial = list(anchors)
    recovered = set()
    for previous, following in pairwise(initial):
        missing = [r for r in rejected if previous["line"] < r["line"] < following["line"]]
        start, end = previous["end"], following["start"]
        if not missing or not 0 < end - start <= 30 or remaining <= 6:
            continue
        remaining -= 1
        first, last = missing[0]["line"], missing[-1]["line"]
        candidate_text = "\n".join(lines[first - 1:last])
        candidates, _ = lyric_anchors(candidate_text, _transcribe_window(model, samples, language, start, end), duration)
        accepted = []
        for candidate in candidates:
            candidate["line"] += first - 1
            # lyric_anchors already applies the same continuous-match threshold
            # as the full-song pass; local retries do not lower it.
            if start <= candidate["start"] < candidate["end"] <= end:
                anchors.append(candidate)
                recovered.add(candidate["line"])
                accepted.append(candidate["line"])
        retries.append({"kind": "missed section", "start": start, "end": end,
                        "lines": [r["line"] for r in missing], "accepted_lines": accepted})
    rejected = [r for r in rejected if r["line"] not in recovered]
    anchors, rejected, context_retries, remaining = _context_recovery(
        model, samples, fallback_samples, text, language, anchors, rejected, duration, remaining, observations)
    retries.extend(context_retries)
    anchors, rejected, sentence_check = _recover_sentence_boundaries(
        model, samples, text, language, anchors, rejected, duration)
    additions, repetitions, rejected = _unused_occurrences(text, observations, anchors, rejected, duration)
    anchors = sorted([*anchors, *additions], key=lambda a: a["start"])
    data = from_alignment(text, None, duration, language)
    aligned = _align_windows(model, samples, anchors, language, duration, fallback_samples) if anchors else []
    # Refine early first words and clipped edge words using a fresh ASR pass in
    # the neighboring interval. Require strong text evidence and no loss of
    # usable original-word coverage; a retry never substitutes recognized text.
    for index, (anchor, line) in enumerate(zip(anchors, aligned)):
        start = anchors[index - 1]["end"] if index else max(0, anchor["start"] - 2)
        end = anchors[index + 1]["start"] if index + 1 < len(anchors) else min(duration, anchor["end"] + 2)
        # Do not let a boundary repair consume a separately observed repeat.
        for extra in repetitions:
            if extra["end"] <= anchor["start"]:
                start = max(start, extra["end"])
            elif extra["start"] >= anchor["end"]:
                end = min(end, extra["start"])
        if (anchor.get("recovery_source") or not remaining or not _suspect_boundary(line)
                or not 0 < end - start <= 30):
            continue
        remaining -= 1
        candidates, _ = lyric_anchors(anchor["text"], _transcribe_window(model, samples, language, start, end), duration)
        accepted = False
        if candidates and candidates[0]["coverage"] >= 0.8:
            candidate = dict(candidates[0], line=anchor["line"], recovery_source=anchor.get("recovery_source", "analysis audio"))
            # A retry for a missing tail must not drag an already timed opening
            # word back into preceding silence. Missing opening words may still
            # be recovered earlier; otherwise preserve the known onset floor.
            leading = next((w for w in line["words"] if w["char_start"] == 0 and w["start"] is not None), None)
            if leading:
                candidate["start"] = max(candidate["start"], leading["start"])
            if start <= candidate["start"] < candidate["end"] <= end:
                refined = _align_windows(model, samples, [candidate], language, duration)[0]
                if refined["start"] is not None and _timed_characters(refined) >= _timed_characters(line):
                    anchors[index], aligned[index] = candidate, refined
                    accepted = True
        retries.append({"kind": "suspect boundary", "start": start, "end": end,
                        "lines": [anchor["line"]], "accepted_lines": [anchor["line"]] if accepted else []})
    failed, phrase_only = [], []
    for anchor, line in zip(anchors, aligned):
        word_alignment_failed = line["start"] is None
        if word_alignment_failed or line.get("unaligned"):
            failed.append({"line": anchor["line"], "text": anchor["text"],
                           "reason": "forced word alignment yielded no usable timings; recognized phrase bounds retained"
                           if word_alignment_failed else "forced word alignment left unresolved words",
                           "unaligned": line.get("unaligned", [])})
        if word_alignment_failed:
            # The recognized phrase already has an ordered, supported interval.
            # Keep it visible even when word refinement fails; no word times or
            # highlights are invented. Both display modes share this timing.
            line.update(start=anchor["start"], end=anchor["end"],
                        timing_source=anchor.get("boundary_source", "recognized phrase"))
            phrase_only.append(anchor["line"])
        data["lines"][anchor["line"] - 1] = line
    if repetitions:
        extra_lines = _align_windows(model, samples, repetitions, language, duration)
        data["extra_occurrences"] = []
        for anchor, line in zip(repetitions, extra_lines):
            if line["start"] is None:
                line.update(start=anchor["start"], end=anchor["end"], timing_source="recognized phrase")
            line["source_line"] = anchor["line"]
            data["extra_occurrences"].append(line)
    transcription = {"enabled": transcribe_unmatched, "passages": [], "discarded_segments": [], "sources": []}
    if transcribe_unmatched:
        passages, discarded = _transcribed_gaps(
            observations[0], anchors, repetitions, rejected, duration, language, len(lines))
        for line in passages:
            line["audio_source"] = "analysis audio"
        discarded = [dict(item, source="analysis audio") for item in discarded]
        transcription["sources"].append({"source": "analysis audio", "accepted_passages": len(passages)})
        if fallback_samples is not None and _transcription_gaps(anchors, rejected, duration, len(lines)):
            # A failed stem transcript must not prevent the original mix from
            # supplying changed sung words. Decode it once with full-song
            # context, using the same confidence and word-timing requirements.
            mix_observations = _transcribe_window(model, fallback_samples, language)
            candidates, mix_discarded = _transcribed_gaps(
                mix_observations, anchors, repetitions, rejected, duration, language, len(lines))
            mix_additions, conflicts = [], 0
            for line in candidates:
                # Keep an accepted stem phrase intact. Select whole independently
                # recognized runs instead of splicing conflicting transcripts.
                if any(line["start"] < p["end"] and p["start"] < line["end"] for p in passages):
                    conflicts += 1
                    continue
                line["audio_source"] = "original mix fallback"
                mix_additions.append(line)
            passages = sorted([*passages, *mix_additions], key=lambda line: line["start"])
            discarded.extend(dict(item, source="original mix fallback") for item in mix_discarded)
            transcription["sources"].append({"source": "original mix fallback", "accepted_passages": len(mix_additions),
                                             "overlapping_passages_skipped": conflicts})
        data["transcribed_passages"] = passages
        transcription.update(passages=[{key: line[key] for key in ("text", "start", "end", "audio_source")} for line in passages],
                             discarded_segments=discarded,
                             source="full-song speech recognition; observed word timestamps")
    return data, {"method": "supported lyric occurrences with bounded context recovery and independent sentence checks",
                  "transcription_fallback": transcription,
                  "recovered_occurrences": additions, "extra_occurrences": repetitions,
                  "vocal_windows": anchors, "unanchored_lines": rejected, "sentence_boundary_check": sentence_check,
                  "word_alignment_failures": failed, "phrase_only_lines": phrase_only, "local_retries": retries,
                  "caption_end_source": "observed lyric-window end; forced word timings remain unchanged",
                  "text_match_coverage": len(anchors) / max(1, sum(bool(x.strip()) for x in text.split("\n"))),
                  "coverage_note": "Accepted lyric-window fraction measures text matching, not timestamp accuracy. Unmatched text is unresolved, not proven absent."}


@contextmanager
def recognition_model(audio, language="Auto", device="auto"):
    # Shared offline model lifecycle for alignment and standalone transcription.
    import comfy.model_management as mm

    mm.throw_exception_if_processing_interrupted()
    try:
        import stable_whisper
        import torch
        import torchaudio.functional as AF
    except ImportError as exc:
        raise RuntimeError(
            "Install Eclipse requirements (stable-ts and faster-whisper) for audio recognition; corrected timing bypasses inference."
        ) from exc
    waveform, rate = audio_data(audio)
    samples = AF.resample(waveform.mean(0), rate, 16000).numpy()
    root = _verify(str(model_path()), model_identity())
    if language != "Auto" and language not in LANGUAGES:
        raise ValueError("Unsupported Whisper language code.")
    if device not in ("auto", "cpu", "cuda"):
        raise ValueError("Recognition device must be auto, cpu or cuda.")
    selected_device = (
        ("cuda" if torch.cuda.is_available() else "cpu") if device == "auto" else device
    )
    model = None
    try:
        model = stable_whisper.load_faster_whisper(
            root,
            device=selected_device,
            compute_type="float16" if selected_device == "cuda" else "int8",
            local_files_only=True,
        )
        mm.throw_exception_if_processing_interrupted()
        if language == "Auto":
            language, detection = detect_language(model, samples)
        else:
            detection = {
                "mode": "manual",
                "selected_language": language,
                "uncertain": False,
            }
        mm.throw_exception_if_processing_interrupted()
        yield model, samples, language, detection
    finally:
        try:
            if model is not None:
                model.model.unload_model()
        finally:
            del model
            gc.collect()


def align_lyrics(audio, text, language="Auto", device="auto", fallback_audio=None, *, transcribe_unmatched=False):
    with recognition_model(audio, language, device) as (model, samples, detected_language, detection):
        duration = len(samples) / 16000
        log.msg("AlignLyrics", f"Finding vocal-text matches across the full {duration:.3f}s song, then aligning within those windows.")
        fallback_samples = None
        if fallback_audio is not None:
            import torchaudio.functional as AF

            waveform, rate = audio_data(fallback_audio)
            fallback_samples = AF.resample(waveform.mean(0), rate, 16000).numpy()
            if abs(len(fallback_samples) / 16000 - duration) > 0.05 + 1e-9:
                raise ValueError("Fallback audio must cover the same complete song.")
        data, anchoring = anchored_alignment(model, samples, text, detected_language, fallback_samples,
                                            **({"transcribe_unmatched": True} if transcribe_unmatched else {}))
        warnings = [
            "Singing alignment is approximate. Review sustained notes, repetitions, instrumental gaps, and mixed-language passages. Auto selects one predominant language; text is never translated."
        ]
        if detection["uncertain"]:
            warnings.append(
                "Language uncertainty: " + "; ".join(detection["uncertainty_reasons"]) + ". Review samples or choose a manual override."
            )
        if detection.get("vad_coverage") == 0:
            warnings.append("VAD detection failed on the analyzed audio (zero coverage); this does not establish absence of vocals.")
        report = {
            "language": detection,
            "model_revision": MODEL_REVISION,
            "algorithm_revision": ALGORITHM_REVISION,
            "warnings": warnings,
            "full_audio_duration": duration,
            "alignment": anchoring,
        }
        warnings.append("Vocal windows come from speech recognition on the analyzed audio. Missed or hallucinated vocals and repeated passages still require review; unsupported lines are left unaligned.")
        report.update(timing_coverage(data["lines"]))
        if anchoring["sentence_boundary_check"]["recovered_lines"]:
            warnings.append("Some lines were recovered with independent sentence timestamps. These can be coarser than word timing; review the recovered lines listed in sentence_boundary_check.")
        if anchoring["extra_occurrences"]:
            warnings.append("Extra exact recognized repetitions reuse supplied wording and are stored separately in extra_occurrences. Review or remove unwanted repetitions in corrected timing JSON.")
        if anchoring["phrase_only_lines"]:
            warnings.append("Recognized phrase bounds keep lines visible when word alignment fails; those lines have no timed word highlighting. Review phrase-only lines in the alignment report.")
        if transcribe_unmatched:
            warnings.append("Unmatched passages may use speech recognition wording. Review transcribed_passages in timing JSON: singing recognition can mishear or invent words. Matched supplied lyrics are preserved.")
        if report["partial_lines"] or report["unaligned_lines"]:
            warnings.append("Captions use valid word onsets when available and recognized phrase bounds otherwise, retaining the observed phrase ending. Unresolved words remain visible without highlighting. Unmatched lines are omitted. Review or supply corrected timing JSON.")
        return data, report


def cached_alignment(audio, text, language="Auto", device="auto", analysis_source="original mix", fallback_audio=None,
                     *, transcribe_unmatched=False):
    # Store serialized timing/report data only. Hash all channels at the supplied
    # sample rate, before mono conversion/resampling; never retain audio or models.
    import comfy.model_management as mm

    mm.throw_exception_if_processing_interrupted()
    digest = hashlib.sha256()
    for source in (audio, fallback_audio):
        if source is None:
            digest.update(b"no fallback")
            continue
        waveform, rate = audio_data(source)
        digest.update(str((rate, tuple(waveform.shape))).encode())
        for channel in waveform:
            for chunk in channel.split(1024 * 1024):
                digest.update(chunk.contiguous().numpy().tobytes())
    identity = (digest.hexdigest(), text, language, device, str(model_path()),
                model_identity(), MODEL_REVISION, ALGORITHM_REVISION, transcribe_unmatched)
    key = hashlib.sha256(repr(identity).encode()).hexdigest()
    with _CACHE_LOCK:
        payload = _ALIGNMENT_CACHE.get(key)
        if payload is not None:
            _ALIGNMENT_CACHE.move_to_end(key)
    hit = payload is not None
    if not hit:
        options = {"fallback_audio": fallback_audio} if fallback_audio is not None else {}
        if transcribe_unmatched:
            options["transcribe_unmatched"] = True
        data, report = align_lyrics(audio, text, language, device, **options)
        mm.throw_exception_if_processing_interrupted()
        payload = json.dumps([data, report], ensure_ascii=False, allow_nan=False).encode()
        # An entirely unresolved attempt has no reusable timing result.
        if any(line["start"] is not None for line in [*data["lines"], *data.get("transcribed_passages", [])]) and len(payload) <= CACHE_MAX_BYTES:
            with _CACHE_LOCK:
                _ALIGNMENT_CACHE[key] = payload
                _ALIGNMENT_CACHE.move_to_end(key)
                while len(_ALIGNMENT_CACHE) > CACHE_MAX_ENTRIES or sum(map(len, _ALIGNMENT_CACHE.values())) > CACHE_MAX_BYTES:
                    _ALIGNMENT_CACHE.popitem(last=False)
    if hit:
        mm.throw_exception_if_processing_interrupted()
    data, report = json.loads(payload)
    report.update(cache_hit=hit, analysis_source=analysis_source, algorithm_revision=ALGORITHM_REVISION)
    return data, report
