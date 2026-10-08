# Read only the A1111 parameters written for Civitai; never traverse a workflow.

import json
import math
import re

from .image_metadata import IMV_CIVITAI_SAMPLER_MAP, INV_CIVITAI_SCHEDULER_MAP

MAX_PARAMETERS = 65536


def _fields(text):
    # Commas inside Hashes JSON or quoted values do not delimit parameter fields.
    start, depth, quoted, escaped = 0, 0, False, False
    parts = []
    for index, char in enumerate(text):
        if escaped:
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif not quoted:
            if char in "[{":
                depth += 1
            elif char in "]}":
                depth = max(0, depth - 1)
            elif char == "," and depth == 0:
                parts.append(text[start:index])
                start = index + 1
    parts.append(text[start:])
    return {key.strip(): value.strip() for part in parts if ":" in part
            for key, value in [part.split(":", 1)]}


def parse_video_parameters(value):
    if not isinstance(value, str) or not value:
        return {"raw": "", "data": None, "status": "No A1111 parameters"}
    truncated = len(value) > MAX_PARAMETERS
    raw = value[:MAX_PARAMETERS]
    for _ in range(2):
        if raw.startswith('"') and raw.endswith('"'):
            try:
                decoded = json.loads(raw)
            except (ValueError, TypeError):
                break
            if isinstance(decoded, str):
                raw = decoded
                continue
        break
    match = list(re.finditer(r"(?:^|\n)Steps:\s*", raw))
    if not match:
        return {"raw": raw, "data": None, "status": "Unrecognized A1111 parameters" + (" (truncated)" if truncated else "")}
    boundary = match[-1].start()
    prompt = raw[:boundary]
    details = _fields(raw[boundary:].lstrip("\n"))
    positive, separator, negative = prompt.partition("\nNegative prompt:")
    data = {"text_pos": positive.strip(), "text_neg": negative.strip() if separator else ""}
    errors = []
    for label, key, cast in [("Steps", "steps", int), ("CFG scale", "cfg", float),
                             ("Seed", "seed", int), ("Denoising strength", "denoise", float),
                             ("Clip skip", "clip_skip", int)]:
        if details.get(label, "") == "":
            continue
        try:
            result = cast(details[label])
            if isinstance(result, float) and not math.isfinite(result):
                raise ValueError("nonfinite parameter")
            data[key] = -abs(result) if key == "clip_skip" else result
        except ValueError:
            errors.append(label)
    size = re.fullmatch(r"(\d+)\s*x\s*(\d+)", details.get("Size", ""))
    if size:
        data.update(width=int(size[1]), height=int(size[2]))
    sampler = details.get("Sampler", "")
    scheduler = details.get("Schedule type", "")
    for suffix, normalized in sorted(INV_CIVITAI_SCHEDULER_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        if sampler.lower().endswith(" " + suffix.lower()):
            sampler = sampler[:-(len(suffix) + 1)]
            scheduler = scheduler or normalized
            break
    if scheduler and sampler.endswith("_" + scheduler):
        sampler = sampler[:-(len(scheduler) + 1)]
    if sampler:
        data["sampler_name"] = IMV_CIVITAI_SAMPLER_MAP.get(sampler, sampler)
    if scheduler:
        data["scheduler"] = INV_CIVITAI_SCHEDULER_MAP.get(scheduler, scheduler)
    status = "Partial: " + ", ".join(errors) if errors else "A1111 prompts and sampling settings"
    if truncated:
        status += " (source text truncated)"
    return {"raw": raw, "data": data, "status": status}


def display_parameters(parameters):
    # JSON numbers cannot safely carry arbitrary 64-bit generation seeds.
    result = dict(parameters)
    if result.get("data"):
        result["data"] = {key: str(value) if isinstance(value, int) and abs(value) > 2**53 - 1 else value
                          for key, value in result["data"].items()}
    return result
