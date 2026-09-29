# Editable shot pools. Each allocation owns a validated snapshot; no global cache.

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

POOL_VERSION = 1
DISTANCE_IDS = ("close", "mid", "wide")
BODY_MODES = ("seated", "standing", "lying")
POSE_CATEGORIES = ("everyday", "sports")
POOL_DIRECTORY = Path(__file__).resolve().parents[1] / "prompts" / "shot_planner"
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / ".defaults" / "prompts" / "shot_planner"
POOL_FILES = ("camera.json", "poses.json", "expressions.json", "rules.json")
_MAX_FILE_BYTES = 256 * 1024
_MAX_CAMERA_KEYS = 50000
_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")


@dataclass(frozen=True)
class ShotPools:
    distances: dict
    cameras: dict
    orientations: dict
    lenses: dict
    compositions: dict
    expressions: dict
    poses: dict
    pose_categories: dict
    body_overrides: dict
    rules: dict
    snapshot: dict
    fingerprint: str


def _object(value, context, *, keys=None):
    if not isinstance(value, dict) or not value or len(value) > 128:
        raise ValueError(f"{context}: expected a non-empty object with at most 128 entries.")
    if any(not isinstance(k, str) or not _KEY_RE.fullmatch(k) for k in value):
        raise ValueError(f"{context}: IDs must be 1–80 letters, digits, underscores, dots or hyphens.")
    if keys is not None and set(value) != set(keys):
        raise ValueError(f"{context}: expected fields {', '.join(keys)}.")
    return value


def _text(value, context, *, empty=False):
    if not isinstance(value, str) or len(value) > 4000 or (not empty and not value.strip()):
        raise ValueError(f"{context}: expected {'optional' if empty else 'non-empty'} text of at most 4000 characters.")
    return value


def _text_map(value, context, *, keys=None):
    return {k: _text(v, f"{context}.{k}") for k, v in _object(value, context, keys=keys).items()}


def _options(value, allowed, context):
    if (not isinstance(value, list) or not value or len(value) > len(allowed)
            or any(not isinstance(v, str) or v not in allowed for v in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"{context}: use a non-empty list of unique values from {', '.join(allowed)}.")
    return tuple(value)


def parse_pools(snapshot: dict) -> ShotPools:
    _object(snapshot, "Shot planner files", keys=POOL_FILES)
    for filename in POOL_FILES:
        value = snapshot[filename]
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            raise ValueError(f"{filename}: schema_version must be 1.")
    camera = _object(snapshot["camera.json"], "camera.json")
    _object({k: v for k, v in camera.items() if k != "body_overrides"}, "camera.json", keys=(
        "schema_version", "distances", "cameras", "orientations", "lenses", "compositions"))
    distances = _text_map(camera["distances"], "camera.json.distances", keys=DISTANCE_IDS)
    # Distance order is a public candidate/selection contract.
    distances = {key: distances[key] for key in DISTANCE_IDS}
    cameras = {}
    for key, value in _object(camera["cameras"], "camera.json.cameras").items():
        context = f"camera.json.cameras.{key}"
        _object(value, context, keys=("family", "text", "distances"))
        family = value["family"]
        if not isinstance(family, str) or not _KEY_RE.fullmatch(family):
            raise ValueError(f"{context}.family: expected a stable camera-family ID.")
        cameras[key] = (family, _text(value["text"], context + ".text"),
                        _options(value["distances"], DISTANCE_IDS, context + ".distances"))
    orientations = _text_map(camera["orientations"], "camera.json.orientations")
    compositions = _text_map(camera["compositions"], "camera.json.compositions")
    body_overrides = {}
    if "body_overrides" in camera:
        for body, overrides in _object(camera["body_overrides"], "camera.json.body_overrides").items():
            context = f"camera.json.body_overrides.{body}"
            if body not in BODY_MODES:
                raise ValueError(f"{context}: unknown body mode.")
            _object(overrides, context)
            allowed = {"distances": distances, "cameras": cameras, "orientations": orientations}
            for section, values in overrides.items():
                if section not in allowed:
                    raise ValueError(f"{context}: use distances, cameras or orientations.")
                texts = _text_map(values, f"{context}.{section}")
                if not set(texts) <= set(allowed[section]):
                    raise ValueError(f"{context}.{section}: override references an unknown ID.")
            body_overrides[body] = overrides
    lenses = {key: _text_map(value, f"camera.json.lenses.{key}") for key, value in
              _object(camera["lenses"], "camera.json.lenses", keys=DISTANCE_IDS).items()}
    capacity = sum(sum(d in c[2] for c in cameras.values()) * len(orientations)
                   * len(lenses[d]) * len(compositions) for d in DISTANCE_IDS)
    if capacity > _MAX_CAMERA_KEYS:
        raise ValueError(f"camera.json: at most {_MAX_CAMERA_KEYS} camera combinations are supported.")
    if any(not any(d in c[2] for c in cameras.values()) for d in DISTANCE_IDS):
        raise ValueError("camera.json: every distance needs a compatible camera.")
    expressions_file = _object(snapshot["expressions.json"], "expressions.json", keys=("schema_version", "expressions"))
    expressions = _text_map(expressions_file["expressions"], "expressions.json.expressions")
    if len(expressions) < 3:
        raise ValueError("expressions.json: at least three expressions are required.")
    poses_file = _object(snapshot["poses.json"], "poses.json", keys=("schema_version", "poses"))
    poses = {}
    pose_categories = {}
    for key, value in _object(poses_file["poses"], "poses.json.poses").items():
        context = f"poses.json.poses.{key}"
        _object(value, context)
        _object({k: v for k, v in value.items() if k != "category"}, context,
                keys=("distances", "body_modes", "text"))
        category = value.get("category", "everyday")
        if category not in POSE_CATEGORIES:
            raise ValueError(f"{context}.category: use everyday or sports.")
        pose_categories[key] = category
        poses[key] = (_options(value["distances"], DISTANCE_IDS, context + ".distances"),
                      _options(value["body_modes"], BODY_MODES, context + ".body_modes"),
                      _text(value["text"], context + ".text"))
    if len(poses) < 3 or any(not any(d in p[0] for p in poses.values()) for d in DISTANCE_IDS):
        raise ValueError("poses.json: at least three poses and coverage of every distance are required.")
    rules = _object(snapshot["rules.json"], "rules.json", keys=("schema_version", "staging", "no_text", "quality", "body"))
    for key in ("staging", "no_text", "quality"):
        _text(rules[key], "rules.json." + key, empty=True)
    body_rules = _text_map(rules["body"], "rules.json.body")
    used_bodies = {body for _, bodies, _ in poses.values() for body in bodies}
    if not used_bodies <= set(body_rules) or not set(body_rules) <= set(BODY_MODES):
        raise ValueError("rules.json.body: provide a description for every body mode used by poses (seated, standing, lying).")
    # Order can affect seeded choice, so include it in the fingerprint.
    fingerprint = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False).encode()).hexdigest()
    return ShotPools(distances, cameras, orientations, lenses, compositions,
                     expressions, poses, pose_categories, body_overrides, rules, snapshot, fingerprint)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field/ID; give every entry a unique ID.")
        result[key] = value
    return result


def _read_snapshot(directory: Path, suffix="") -> dict:
    snapshot = {}
    for name in POOL_FILES:
        path = directory / (name + suffix)
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError(f"{name}: pool files may not be symlinks.")
            with path.open("rb") as handle:
                raw = handle.read(_MAX_FILE_BYTES + 1)
            if len(raw) > _MAX_FILE_BYTES:
                raise ValueError(f"{name}: file exceeds 256 KiB.")
            snapshot[name] = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            detail = f"line {error.lineno}, column {error.colno}" if isinstance(error, json.JSONDecodeError) else type(error).__name__
            raise ValueError(f"Cannot load {name} ({detail}). Fix or restore this file in prompts/shot_planner.") from error
        except ValueError as error:
            raise ValueError(f"{name}: {error}") from error
    return snapshot


def ensure_pool_files() -> None:
    # Seed the directory once. Never repair/replace a partially edited user folder.
    if POOL_DIRECTORY.exists():
        return
    snapshot = _read_snapshot(DEFAULT_DIRECTORY, ".example")
    parse_pools(snapshot)
    POOL_DIRECTORY.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".shot-planner-", dir=POOL_DIRECTORY.parent) as staging:
        for name in POOL_FILES:
            (Path(staging) / name).write_bytes((DEFAULT_DIRECTORY / (name + ".example")).read_bytes())
        try:
            os.rename(staging, POOL_DIRECTORY)
        except OSError:
            if not POOL_DIRECTORY.is_dir():
                raise


def load_pools(*, shipped=False, initialize=True) -> ShotPools:
    if shipped:
        return parse_pools(_read_snapshot(DEFAULT_DIRECTORY, ".example"))
    if initialize:
        ensure_pool_files()
    if POOL_DIRECTORY.is_symlink():
        raise ValueError("The shot planner folder may not be a symlink.")
    return parse_pools(_read_snapshot(POOL_DIRECTORY))


def pool_summary() -> dict:
    pools = load_pools(initialize=False)
    return {"success": True, "fingerprint": pools.fingerprint,
            "directory": "prompts/shot_planner", "files": list(POOL_FILES),
            "poses": len(pools.poses), "expressions": len(pools.expressions),
            "cameras": len(pools.cameras)}
