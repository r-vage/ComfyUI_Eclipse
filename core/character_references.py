# Character reference roles and preparation, independent of any model runtime.

import hashlib
import json
from pathlib import Path

import torch
import torch.nn.functional as F

ROLES = ("face", "body", "clothing", "rear")
MODES = ("reference", "extract", "generate", "disabled")
RULES_PATH = Path(__file__).resolve().parents[1] / "prompts/character_references.json"
DEFAULT_PATH = Path(__file__).resolve().parents[1] / ".defaults/prompts/character_references.json.example"


def rules_bytes():
    path = RULES_PATH if RULES_PATH.exists() else DEFAULT_PATH
    if path.stat().st_size > 65536:
        raise ValueError(f"{path.name}: character rules must be at most 64 KiB.")
    return path.read_bytes()


def rules_fingerprint():
    return hashlib.sha256(rules_bytes()).hexdigest()


def load_rules():
    try:
        rules = json.loads(rules_bytes())
        if rules["schema_version"] != 1:
            raise ValueError("schema_version must be 1")
        texts = [rules[key] for key in ("policy", "anchor", "negative")]
        texts += [rules["roles"][role] for role in ("anchor", *ROLES)]
        texts += [rules["prepare"][role][mode] for role in ROLES for mode in ("extract", "generate")]
        if not all(isinstance(text, str) and text.strip() and len(text) <= 8000 for text in texts):
            raise ValueError("each rule must be a nonempty string of at most 8000 characters")
        for role in ROLES:
            negative = rules["prepare"][role].get("negative", rules["negative"])
            if not isinstance(negative, str) or len(negative) > 8000:
                raise ValueError(f"prepare.{role}.negative must be text of at most 8000 characters")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"character_references.json: invalid rules: {error}") from error
    return rules


def single_image(image, name):
    if not isinstance(image, torch.Tensor) or image.ndim != 4 or image.shape[0] != 1:
        raise ValueError(f"{name}: connect exactly one IMAGE, not a batch.")
    if min(image.shape[1:3]) < 1 or image.shape[-1] not in (3, 4):
        raise ValueError(f"{name}: expected a nonempty RGB/RGBA image.")
    if image.shape[-1] == 4:
        image = image[..., :3] * image[..., 3:] + (1 - image[..., 3:])
    return image


def fit_reference(image, width, height):
    # Letterbox without stretching or cropping physique/garments. Retain precision.
    source_h, source_w = image.shape[1:3]
    scale = min(width / source_w, height / source_h)
    w, h = max(1, round(source_w * scale)), max(1, round(source_h * scale))
    resized = F.interpolate(image.movedim(-1, 1), size=(h, w), mode="bilinear", align_corners=False)
    left, top = (width - w) // 2, (height - h) // 2
    return F.pad(resized, (left, width - w - left, top, height - h - top), value=1).movedim(1, -1)


def prepare_reference(role, mode, description, instructions, image=None,
                      positive_override=None, negative_override=None):
    if role not in ROLES or mode not in MODES:
        raise ValueError("Unknown character reference role or mode.")
    source = None
    if mode in ("reference", "extract"):
        source = single_image(image, role)
    positive = negative = ""
    if positive_override is None or negative_override is None:
        rules = load_rules()
        positive = rules["prepare"][role].get(mode, "")
        negative = rules["prepare"][role].get("negative", rules["negative"])
    prompt = "\n\n".join(part.strip() for part in (
        positive, description, instructions,
    ) if part.strip())
    # None means disconnected; an empty connected string intentionally clears it.
    if positive_override is not None:
        prompt = positive_override
    if negative_override is not None:
        negative = negative_override
    spec = {"role": role, "mode": mode, "image": source}
    # Direct references and disabled branches never need a generation pass.
    return spec, prompt, negative, source


def select_reference(spec, generated=None):
    if spec["mode"] == "disabled":
        return None
    image = generated if spec["mode"] in ("extract", "generate") else spec["image"]
    return single_image(image, spec["role"])


def pack_references(width, height, identity, body_description, outfit, scene, extra_rules,
                    face=None, body=None, clothing=None, rear=None, anchor=None):
    if width % 32 or height % 32 or not 256 <= width <= 2048 or not 256 <= height <= 2048:
        raise ValueError("Character canvas dimensions must be multiples of 32 from 256 to 2048.")
    if face is None:
        raise ValueError("Enable a face reference or generate a face before assembling the character.")
    rules = load_rules()
    references = [(role, single_image(image, role)) for role, image in (
        ("anchor", anchor), ("face", face), ("body", body), ("clothing", clothing), ("rear", rear),
    ) if image is not None]
    # Native Qwen 2.1 uses the first reference size for its 64-channel output latent.
    images = [fit_reference(image, width, height) for _, image in references]
    mapping = "REFERENCE ROLES:\n\n" + "\n\n".join(
        f"<image{i}> = {rules['roles'][role]}" for i, (role, _) in enumerate(references, 1)
    )
    details = "\n".join(f"{label}: {value.strip()}" for label, value in (
        ("Character identity", identity), ("Body proportions", body_description), ("Fixed outfit", outfit),
    ) if value.strip())
    sections = [rules["policy"], mapping]
    if details:
        sections.append("CHARACTER SPECIFICATION:\n" + details)
    if extra_rules.strip():
        sections.append("ADDITIONAL INSTRUCTIONS:\n" + extra_rules.strip())
    lock = "\n\n".join(sections)
    anchor_sections = [lock, rules["anchor"]]
    if scene.strip():
        anchor_sections.append("REQUESTED SCENE AND LIGHTING:\n" + scene.strip())
    anchor_prompt = "\n\n".join(anchor_sections)
    report = f"Canvas: {width} x {height}. Encoder resolution: 0.\n" + "\n".join(
        f"<image{i}>: {role}" for i, (role, _) in enumerate(references, 1)
    )
    if body is None:
        report += "\nNo body image: physique comes from the body description/assembled character."
    if clothing is None:
        report += "\nNo clothing image: wardrobe comes from the outfit description/assembled character."
    report += "\nBody control describes visible proportions; it does not estimate or enforce measured BMI."
    return lock, anchor_prompt, rules["negative"], *images, *([None] * (5 - len(images))), report
