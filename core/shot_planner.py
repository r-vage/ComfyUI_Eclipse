# Persistent compatible shot planning. No image generation or external services.

import copy
import hashlib
import itertools
import json
import random
import re
from collections import Counter
from pathlib import Path

from .json_store import read_json_object, update_json_object
from .shot_planner_pools import (
    BODY_MODES,
    DISTANCE_IDS,
    POOL_VERSION,
    POSE_CATEGORIES,
    ShotPools,
    load_pools,
    parse_pools,
)

SCHEMA_VERSION = 1
MAX_SHOTS = 100
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def ledger_path(output_directory: str, project: str) -> Path:
    # Project names are labels, never paths. Keep state under the ComfyUI output root.
    if not isinstance(project, str) or not project.strip() or len(project) > 200:
        raise ValueError("Project must contain 1–200 characters.")
    root = Path(output_directory).resolve()
    directory = root / "eclipse" / "shot_ledgers"
    if not directory.resolve().is_relative_to(root):
        raise ValueError("Shot ledger directory must stay inside the output directory.")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{_digest(project.strip())[:32]}.json"
    if target.is_symlink():
        raise ValueError("Shot ledger files may not be symlinks.")
    return target


def camera_pool(pools: ShotPools | None = None) -> list[tuple[str, ...]]:
    pools = pools or load_pools()
    return [
        (distance, height, orientation, lens, composition)
        for distance in pools.distances
        for height, (_, _, compatible) in pools.cameras.items() if distance in compatible
        for orientation, lens, composition in itertools.product(
            pools.orientations, pools.lenses[distance], pools.compositions
        )
    ]


def _validate_ledger(state: dict, project: str) -> list[dict]:
    if (state.get("schema_version") != SCHEMA_VERSION
            or state.get("pool_version") != POOL_VERSION
            or state.get("project") != project
            or not isinstance(state.get("batches"), dict)):
        raise ValueError("Unrecognized shot ledger. Preserve the file and use a new project.")
    history = []
    used = set()
    try:
        for batch_id, batch in state["batches"].items():
            # Historical batches validate against their own data, never edited files.
            pools = parse_pools(batch["pool_snapshot"]) if "pool_snapshot" in batch else load_pools(shipped=True)
            valid_cameras = set(camera_pool(pools))
            if "pool_snapshot" in batch and batch.get("pool_fingerprint") != pools.fingerprint:
                raise ValueError
            if (not _ID_RE.fullmatch(batch_id) or not isinstance(batch["settings"], dict)
                    or not isinstance(batch["sets"], list)
                    or not 1 <= len(batch["sets"]) <= MAX_SHOTS):
                raise ValueError
            for shot_set in batch["sets"]:
                candidates = shot_set["candidates"]
                if (len(candidates) != 3
                        or {c["camera"][0] for c in candidates} != set(DISTANCE_IDS)
                        or len({c["pose"] for c in candidates}) != 3
                        or len({c["expression"] for c in candidates}) != 3):
                    raise ValueError
                selected = [c for c in candidates if c["status"] == "selected"]
                if len(selected) != 1:
                    raise ValueError
                for c in candidates:
                    camera = tuple(c["camera"])
                    if (camera not in valid_cameras or camera in used
                            or c["pose"] not in pools.poses or c["expression"] not in pools.expressions
                            or c["status"] not in {"selected", "skipped"}
                            or not isinstance(c["prompt"], str)
                            or not isinstance(c["shot_id"], str)
                            or type(c["seed"]) is not int or not 0 <= c["seed"] < 2**63
                            or c["body"] not in pools.poses[c["pose"]][1]
                            or camera[0] not in pools.poses[c["pose"]][0]):
                        raise ValueError
                    used.add(camera)
                history.append({**selected[0], "camera_family": pools.cameras[selected[0]["camera"][1]][0]})
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise ValueError("Malformed shot ledger; no history was replaced.") from error
    return history


def _prompt(settings: dict, camera: tuple, pose: str, expression: str, body: str, pools: ShotPools) -> str:
    distance, height, orientation, lens, composition = camera
    overrides = pools.body_overrides.get(body, {})
    camera_rules = [overrides.get("distances", {}).get(distance, pools.distances[distance]),
                    overrides.get("cameras", {}).get(height, pools.cameras[height][1]),
                    overrides.get("orientations", {}).get(orientation, pools.orientations[orientation]),
                    pools.lenses[distance][lens],
                    pools.compositions[composition]]
    pose_rules = [pools.poses[pose][2], pools.expressions[expression]]
    if distance == "close":
        pose_rules.append(pools.rules["body"][body])
    sections = [
        ("CHARACTER AND REFERENCE LOCK", settings["character_lock"]),
        ("FIXED WARDROBE", settings["outfit_lock"]),
        ("CAMERA AND FRAMING", "\n".join(f"- {rule}" for rule in camera_rules)),
        ("POSE AND EXPRESSION", "\n".join(f"- {rule}" for rule in pose_rules)),
        ("SCENE AND LIGHTING", settings["scene"]),
        ("ANATOMY AND STAGING", pools.rules["staging"]),
        ("TEXT AND SURFACE RULES", pools.rules["no_text"]),
        ("STYLE AND QUALITY", pools.rules["quality"]),
        ("FINAL OUTPUT", ("One image of the same character in the requested shot. "
         "Preserve identity, physique and wardrobe while changing only the specified shot attributes. "
         "No contact sheet, panels, inset portraits or duplicate people.")),
    ]
    return "\n\n".join(f"{title}:\n{value.strip()}" for title, value in sections if value.strip())


def _assign_poses(options: dict[str, list[str]]) -> dict[str, str] | None:
    # Match constrained distances first, backtracking instead of a greedy draw.
    order = sorted(options, key=lambda d: len(options[d]))
    assignment = {}

    def assign(position):
        if position == len(order):
            return True
        distance = order[position]
        for pose in options[distance]:
            if pose in assignment.values():
                continue
            assignment[distance] = pose
            if assign(position + 1):
                return True
            del assignment[distance]
        return False

    return assignment if assign(0) else None


def _allocate(state: dict, batch_id: str, settings: dict, pools: ShotPools) -> dict:
    history = _validate_ledger(state, state["project"])
    used = {tuple(c["camera"]) for b in state["batches"].values()
            for s in b["sets"] for c in s["candidates"]}
    available = [camera for camera in camera_pool(pools) if camera not in used]
    rng = random.Random(int(_digest([state["project"], batch_id, settings["seed"]]), 16))
    sets = []
    for index in range(settings["count"]):
        recent_poses = {c["pose"] for c in history[-settings["pose_cooldown"]:]}
        recent_expressions = {c["expression"] for c in history[-settings["expression_cooldown"]:]}
        recent_families = {c["camera_family"]
                           for c in history[-settings["camera_cooldown"]:]}
        if not settings["pose_cooldown"]:
            recent_poses.clear()
        if not settings["expression_cooldown"]:
            recent_expressions.clear()
        if not settings["camera_cooldown"]:
            recent_families.clear()
        orientation_counts = Counter(c["camera"][2] for c in history)
        composition_counts = Counter(c["camera"][4] for c in history)
        pose_options = {
            distance: [p for p, (distances, bodies, _) in pools.poses.items()
                       if distance in distances and p not in recent_poses
                       and (settings["body_mode"] == "any" or settings["body_mode"] in bodies)
                       and (settings.get("pose_category", "all") == "all"
                            or settings["pose_category"] == pools.pose_categories[p])]
            for distance in pools.distances
        }
        for options in pose_options.values():
            rng.shuffle(options)
        pose_assignment = _assign_poses(pose_options)
        if pose_assignment is None:
            raise ValueError(f"Cannot plan shot {index + 1}: no three distinct compatible poses "
                             f"for body_mode={settings['body_mode']}, "
                             f"pose_category={settings.get('pose_category', 'all')} "
                             "under the current history/cooldowns. Check poses.json compatibility, "
                             "broaden the filters or reduce pose cooldown. No part of this batch was saved.")
        candidates = []
        for distance in pools.distances:
            cameras = [c for c in available if c[0] == distance
                       and pools.cameras[c[1]][0] not in recent_families]
            expressions = [e for e in pools.expressions if e not in recent_expressions]
            if not cameras or not expressions:
                raise ValueError(
                    f"Cannot plan shot {index + 1} ({distance}): compatible camera, pose or "
                    "expression pool exhausted under the current history/cooldowns. "
                    "No part of this batch was saved. Reduce cooldowns or use a new project."
                )
            rng.shuffle(cameras)
            camera = min(cameras, key=lambda c: (orientation_counts[c[2]], composition_counts[c[4]]))
            pose, expression = pose_assignment[distance], rng.choice(expressions)
            bodies = pools.poses[pose][1]
            body = rng.choice(bodies) if settings["body_mode"] == "any" else settings["body_mode"]
            shot_id = f"{batch_id}/{index + 1:04d}/{distance}"
            candidates.append({
                "shot_id": shot_id, "camera": list(camera), "pose": pose,
                "expression": expression, "body": body, "status": "skipped",
                "seed": int(_digest([settings["seed"], state["project"], shot_id])[:15], 16),
                "camera_family": pools.cameras[camera[1]][0],
                "prompt": _prompt(settings, camera, pose, expression, body, pools),
            })
            available.remove(camera)
            recent_expressions.add(expression)
        counts = Counter(c["camera"][0] for c in history)
        choice = settings["choices"][index] if settings["choices"] else settings["selection"]
        if choice == "balanced":
            choice = min(pools.distances, key=lambda d: counts[d])
        selected = next(c for c in candidates if c["camera"][0] == choice)
        selected["status"] = "selected"
        history.append(selected)
        sets.append({"candidates": candidates})
    return {"settings": settings, "sets": sets,
            "pool_snapshot": pools.snapshot, "pool_fingerprint": pools.fingerprint}


def plan_shots(path: Path, *, project: str, batch_id: str, count: int = 40,
               character_lock: str = "The same character as the reference images",
               outfit_lock: str = "", scene: str = "Featureless white background and light floor",
               body_mode: str = "any", selection: str = "balanced", choices: str = "",
               seed: int = 0, pose_cooldown: int = 3, expression_cooldown: int = 3,
               camera_cooldown: int = 2, operation: str = "reserve", pose_category: str = "all") -> dict:
    if not isinstance(project, str) or not project.strip() or len(project) > 200:
        raise ValueError("Project must contain 1–200 characters.")
    project = project.strip()
    if not isinstance(batch_id, str) or not _ID_RE.fullmatch(batch_id):
        raise ValueError("Batch ID must be 1–80 letters, digits, underscores, dots or hyphens, starting with a letter or digit.")
    if type(count) is not int or not 1 <= count <= MAX_SHOTS:
        raise ValueError(f"Shot count must be 1–{MAX_SHOTS}.")
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError("Seed must be an unsigned 64-bit integer.")
    if body_mode not in {"any", *BODY_MODES} or selection not in {"balanced", *DISTANCE_IDS}:
        raise ValueError("Unknown body mode or selection.")
    if pose_category not in {"all", *POSE_CATEGORIES}:
        raise ValueError("Pose category must be all, everyday or sports.")
    if operation not in {"preview", "reserve"}:
        raise ValueError("Operation must be preview or reserve.")
    for cooldown in (pose_cooldown, expression_cooldown, camera_cooldown):
        if type(cooldown) is not int or not 0 <= cooldown <= 20:
            raise ValueError("Cooldowns must be 0–20 selected shots.")
    for value in (character_lock, outfit_lock, scene, choices):
        if not isinstance(value, str) or len(value) > 10000:
            raise ValueError("Prompt fields must be text of at most 10000 characters each.")
    if not character_lock.strip():
        raise ValueError("Character lock must not be empty.")
    selections = [s.lower() for s in re.split(r"[\s,]+", choices.strip()) if s]
    if selections and (len(selections) != count or any(s not in DISTANCE_IDS for s in selections)):
        raise ValueError("Manual choices require exactly one close, mid or wide value per shot.")
    settings = {
        "count": count, "character_lock": character_lock.strip(), "outfit_lock": outfit_lock.strip(),
        "scene": scene.strip(), "body_mode": body_mode, "selection": selection, "choices": selections,
        "seed": seed, "pose_cooldown": pose_cooldown, "expression_cooldown": expression_cooldown,
        "camera_cooldown": camera_cooldown,
    }
    # An omitted filter retains the original settings shape, so old batches replay.
    if pose_category != "all":
        settings["pose_category"] = pose_category
    initial = {"schema_version": SCHEMA_VERSION, "pool_version": POOL_VERSION,
               "project": project, "batches": {}}
    result = None
    resolved_id = batch_id

    def allocate(state):
        nonlocal result, resolved_id
        _validate_ledger(state, project)
        # Resolve under the ledger lock so concurrent retries share one reservation.
        # Keep the requested ID stable: each settings variant remains replayable.
        resolved_id = batch_id
        revision = 1
        existing = state["batches"].get(resolved_id)
        while existing is not None and existing["settings"] != settings:
            revision += 1
            suffix = f"-{revision}"
            resolved_id = batch_id + suffix
            if len(resolved_id) > 80:
                # Preserve the input limit without conflating long, similar IDs.
                stem = batch_id[:80 - len(suffix) - 13]
                resolved_id = f"{stem}-{_digest(batch_id)[:12]}{suffix}"
            existing = state["batches"].get(resolved_id)
        if existing is not None:
            result = existing
        else:
            result = _allocate(state, resolved_id, settings, load_pools())
            state["batches"][resolved_id] = result

    if operation == "reserve":
        update_json_object(path, allocate, default=initial, private=True)
    else:
        allocate(read_json_object(path, default=initial))
    plan = {"schema_version": SCHEMA_VERSION, "project": project, "batch_id": resolved_id,
            "requested_batch_id": batch_id,
            "ledger": str(path), "operation": operation, **copy.deepcopy(result)}
    return plan


def selected_shots(plan: dict, start: int = 0, count: int = 0) -> list[dict]:
    if not isinstance(plan, dict) or plan.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Expected a Character Shot Planner plan.")
    sets = plan["sets"]
    if (type(start) is not int or type(count) is not int
            or start < 0 or start >= len(sets) or count < 0 or start + count > len(sets)):
        raise ValueError("Shot plan slice is outside the planned range; indices are zero-based.")
    return [next(c for c in s["candidates"] if c["status"] == "selected")
            for s in sets[start:start + count if count else None]]


def plan_report(plan: dict) -> str:
    lines = [f"Project: {plan['project']} | Batch: {plan['batch_id']} | {plan['operation']}",
             f"Ledger: {plan['ledger']}",
             f"Pool snapshot: {plan.get('pool_fingerprint', 'original defaults (legacy batch)')}",
             "Three candidates per shot; * = selected. Skipped cameras are reserved too.",
             "Preview does not reserve history. Prompts do not guarantee image quality."]
    requested = plan.get("requested_batch_id", plan["batch_id"])
    if requested != plan["batch_id"]:
        lines.insert(1, f"Requested ID: {requested} | Automatically resolved to {plan['batch_id']} "
                     "because earlier IDs have different settings. Same settings replay this batch.")
    for i, shot_set in enumerate(plan["sets"], 1):
        lines.append(f"\nShot {i}")
        for c in shot_set["candidates"]:
            lines.append(f"{'*' if c['status'] == 'selected' else '-'} {' / '.join(c['camera'])} | "
                         f"{c['pose']} | {c['expression']} | seed {c['seed']}\n{c['prompt']}")
    return "\n".join(lines)
