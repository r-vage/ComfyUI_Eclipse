# Staged character-sheet references. Model execution remains in the workflow.

import hashlib
import json
import math
import re
from itertools import pairwise
from pathlib import Path

import torch

from .character_references import fit_reference, single_image

STAGES = ("mannequin", "wardrobe", "sheet", "dataset")
RULES_PATH = Path(__file__).resolve().parents[1] / "prompts/character_sheets.json"
DEFAULT_PATH = Path(__file__).resolve().parents[1] / ".defaults/prompts/character_sheets.json.example"
IMAGE_INPUTS = ("layout", "face", "wardrobe", "front_body", "rear_body", "front", "rear")
GENERATION_MODES = ("Preset", "Reference")
MANNEQUIN_OPTIONS = {
    "gender": ("unspecified", "female", "male", "androgynous"),
    "build": ("unspecified", "slim", "average", "full", "heavy"),
    "muscularity": ("unspecified", "very low", "low", "medium", "high", "very high"),
    "breast_size": ("unspecified", "flat", "very small", "small", "medium", "large", "very large"),
    "chest_breadth": ("unspecified", "very narrow", "narrow", "medium", "broad", "very broad"),
    "hip_width": ("unspecified", "very narrow", "narrow", "medium", "broad", "very broad"),
    "buttock_size": ("unspecified", "very small", "small", "medium", "large", "very large"),
}


def rules_bytes():
    path = RULES_PATH if RULES_PATH.exists() else DEFAULT_PATH
    if path.stat().st_size > 65536:
        raise ValueError("character_sheets.json must be at most 64 KiB.")
    return path.read_bytes()


def rules_fingerprint():
    return hashlib.sha256(rules_bytes()).hexdigest()


def load_rules():
    try:
        rules = json.loads(rules_bytes())
        if rules["schema_version"] != 1:
            raise ValueError("schema_version must be 1")
        for stage in (*STAGES, "face"):
            for key in (stage, stage + "_negative"):
                if not isinstance(rules[key], str) or not 0 < len(rules[key]) <= 12000:
                    raise ValueError(f"{key} must contain 1–12000 characters")
        for key in ("mannequin_preset", "mannequin_layout", "mannequin_details"):
            if not isinstance(rules[key], str) or not 0 < len(rules[key]) <= 12000:
                raise ValueError(f"{key} must contain 1–12000 characters")
        for name, options in MANNEQUIN_OPTIONS.items():
            for option in options[1:]:
                text = rules["mannequin_attributes"][name][option]
                if not isinstance(text, str) or not 0 < len(text) <= 1000:
                    raise ValueError(f"{name}/{option} must contain 1–1000 characters")
        return rules
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid character_sheets.json: {error}") from error


def active_roles(stage, use_front_body=True, use_rear_body=True, use_wardrobe=True, use_rear=True,
                 generation_mode="Preset"):
    if stage == "mannequin":
        if generation_mode not in GENERATION_MODES:
            raise ValueError("Mannequin generation_mode must be Preset or Reference.")
        if generation_mode == "Preset":
            return []
        return [("layout", "LAYOUT GUIDE"), *([("front_body", "FRONT BODY")] if use_front_body else []),
                *([("rear_body", "REAR BODY")] if use_rear_body else [])]
    if stage == "wardrobe":
        return [("wardrobe", "WARDROBE SOURCE")]
    if stage == "sheet":
        return [("layout", "LAYOUT GUIDE"), ("face", "FACE"),
                *([("wardrobe", "WARDROBE")] if use_wardrobe else [])]
    if stage == "dataset":
        return [("front", "FRONT CHARACTER"), ("face", "FACE"),
                *([("rear", "REAR CHARACTER")] if use_rear else [])]
    raise ValueError(f"Unknown character sheet stage: {stage}")


def pack_sheet(stage, width, height, details="", positive_override=None, negative_override=None,
               use_front_body=True, use_rear_body=True, use_wardrobe=True, use_rear=True,
               generation_mode="Preset", gender="unspecified", build="average", muscularity="unspecified",
               breast_size="unspecified", chest_breadth="unspecified", hip_width="unspecified",
               buttock_size="unspecified", **images):
    if width % 32 or height % 32 or not 256 <= width <= 2048 or not 256 <= height <= 2048:
        raise ValueError("Canvas dimensions must be multiples of 32 from 256 to 2048.")
    roles = active_roles(stage, use_front_body, use_rear_body, use_wardrobe, use_rear, generation_mode)
    required = {"mannequin": (), "wardrobe": ("wardrobe",),
                "sheet": ("layout", "face"), "dataset": ("front", "face")}[stage]
    for name in required:
        if images.get(name) is None:
            raise ValueError(f"{stage}: connect the {name} image before continuing.")
    if stage == "sheet" and (not use_wardrobe or images.get("wardrobe") is None) and not details.strip():
        raise ValueError("No wardrobe image: describe the complete outfit in details.")
    if stage == "mannequin" and (generation_mode == "Preset" or images.get("layout") is None):
        # Native Qwen takes output dimensions from its first reference. A plain
        # canvas fixes size without supplying a mannequin's anatomy or pose.
        images = {**images, "layout": torch.full((1, height, width, 3), 0.94, dtype=torch.float32)}
        roles = [("layout", "OUTPUT CANVAS (blank; size only, no pose or anatomy)"), *roles[1:]]
    selected = [(name, label, single_image(images[name], name)) for name, label in roles
                if images.get(name) is not None]
    rules = load_rules() if stage == "mannequin" or positive_override is None or negative_override is None else {}
    task_key = "mannequin_preset" if stage == "mannequin" and generation_mode == "Preset" else stage
    task = rules.get(task_key, "") if positive_override is None else positive_override
    negative = rules.get(stage + "_negative", "") if negative_override is None else negative_override
    if stage == "mannequin":
        selections = {"gender": gender}
        if generation_mode == "Preset":
            selections.update(build=build, muscularity=muscularity, breast_size=breast_size,
                              chest_breadth=chest_breadth, hip_width=hip_width, buttock_size=buttock_size)
        task += "\n\n" + rules["mannequin_layout"]
        for name, value in selections.items():
            if value not in MANNEQUIN_OPTIONS[name]:
                raise ValueError(f"Mannequin {name} must be one of {MANNEQUIN_OPTIONS[name]}.")
            if value != "unspecified":
                task += "\n\n" + rules["mannequin_attributes"][name][value]
    mapping = "REFERENCE ROLES — ACTUAL ENCODER INPUTS:\n" + "\n".join(
        f"<image{i}> = {label}." for i, (_, label, _) in enumerate(selected, 1))
    missing = [label for name, label in roles if images.get(name) is None]
    if stage == "sheet" and not use_wardrobe:
        missing.append("WARDROBE")
    if missing:
        mapping += "\nNot supplied: " + ", ".join(missing) + ". Use the written specification where needed."
    prompt = mapping + "\n\n" + task
    if details.strip():
        prompt += ("\n\n" + rules["mannequin_details"] + " " if stage == "mannequin"
                   else "\n\nWRITTEN SPECIFICATION:\n") + details.strip()
    # Resolve role names throughout task text, not only in the opening map.
    # One regex pass avoids replacing parts of words or rewriting inserted labels.
    labels = {label.partition(" (")[0]: f"<image{i}>"
              for i, (_, label, _) in enumerate(selected, 1)}
    pattern = r"(?<!\w)(?:" + "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True)) + r")(?!\w)"
    task_text = prompt[len(mapping):]
    prompt = mapping + re.sub(pattern, lambda match: labels[match.group()], task_text)
    if stage == "mannequin":
        # Keep the inspectable role map in report, not as a document heading in
        # the image prompt. Qwen's edit guidance uses continuous, direct prose.
        assignments = []
        for i, (name, label, _) in enumerate(selected, 1):
            if name == "layout":
                purpose = "sets canvas size only" if label.startswith("OUTPUT CANVAS") else "supplies panel framing only"
            else:
                purpose = f"supplies visible {'front' if name == 'front_body' else 'rear'} body proportions"
            assignments.append(f"<image{i}> {purpose}.")
        prompt = " ".join((prompt[len(mapping):].strip() + " " + " ".join(assignments)).split())
        # Absent roles are prose, never references to non-existent image slots.
        for role, description in (("FRONT BODY", "a frontal physique reference"),
                                  ("REAR BODY", "a rear physique reference"),
                                  ("LAYOUT GUIDE", "a layout reference"),
                                  ("OUTPUT CANVAS", "the output canvas")):
            if role not in labels:
                prompt = prompt.replace(role, description)
        if len(selected) == 1:
            prompt = prompt.replace("<image1>", "the supplied image")
    elif stage == "sheet":
        # Lead with the edit operation; retain the resolved reference legend
        # after it for inspection instead of making it the opening instruction.
        prompt = prompt[len(mapping):].strip() + "\n\n" + mapping
    negative = re.sub(pattern, lambda match: labels[match.group()], negative)
    fitted = []
    for index, (_, _, image) in enumerate(selected):
        if stage in ("sheet", "mannequin") and index > 0:
            # Only the first reference sets the output canvas. Preserve the
            # supporting image's aspect ratio without landscape letterboxing:
            # body contours should occupy the reference, not a narrow padded strip.
            source_h, source_w = image.shape[1:3]
            scale = min(1.0, 1024 / max(source_w, source_h))
            if scale < 1.0:
                image = fit_reference(image, max(1, round(source_w * scale)),
                                      max(1, round(source_h * scale)))
            fitted.append(image)
        else:
            fitted.append(fit_reference(image, width, height))
    report = f"Stage: {stage}. Canvas: {width} x {height}; encoder resolution=0.\n" + mapping
    report += "\nReference sizes: " + ", ".join(
        f"<image{i}> {image.shape[2]} x {image.shape[1]}"
        for i, image in enumerate(fitted, 1))
    if stage == "mannequin":
        report += f"\nGeneration mode: {generation_mode}.\nGender/presentation: {gender}"
    return prompt, negative, *fitted, *([None] * (3 - len(fitted))), report


def split_sheet(sheet, front_end=1 / 3, rear_end=2 / 3, gutter=0.004, mode="manual"):
    sheet = single_image(sheet, "character sheet")
    if mode not in ("auto", "manual"):
        raise ValueError("Split mode must be auto or manual.")
    if mode == "auto":
        # Derive thirds from this image, ignoring saved manual boundaries.
        front_end, rear_end = 1 / 3, 2 / 3
    if not all(math.isfinite(v) for v in (front_end, rear_end, gutter)):
        raise ValueError("Panel boundaries and gutter must be finite.")
    if not 0 < front_end < rear_end < 1 or not 0 <= gutter < 0.1:
        raise ValueError("Use 0 < front_end < rear_end < 1 and a gutter below 0.1.")
    width = sheet.shape[2]
    edges = (0, round(width * front_end), round(width * rear_end), width)
    margin = round(width * gutter)
    panels = []
    for left, right in pairwise(edges):
        if right - left <= 2 * margin:
            raise ValueError("Panel is too narrow for the gutter; reduce gutter or adjust boundaries.")
        panels.append(sheet[:, :, left + margin:right - margin, :].clone())
    report = f"Single-image outputs: front, rear, portrait. Mode: {mode}. Source {width} x {sheet.shape[1]}. "
    report += f"Boundaries at x={edges[1]}, {edges[2]}; {margin}px trimmed from each panel edge."
    return *panels, report
