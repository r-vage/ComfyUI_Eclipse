"""Migrate only Shot Planner widget layouts. Dry run unless --write is given."""

import argparse
import copy
import json
import os
import stat
import tempfile
from pathlib import Path

from migration_core import backup_file

NODE_NAME = "Character Shot Planner [Eclipse]"
LAYOUT_PROPERTY = "eclipseShotPlannerWidgetVersion"
# Keep historical layouts frozen and in sync with eclipse-character-shot-planner.js.
LEGACY_ORDER = [
    "project", "batch_id", "count", "character_lock", "outfit_lock", "scene",
    "body_mode", "selection", "choices", "seed", "pose_cooldown",
    "expression_cooldown", "camera_cooldown", "operation", "pose_category",
]
EXPRESSION_ORDER = [*LEGACY_ORDER[:7], "expression", "pose_category", *LEGACY_ORDER[7:14]]
COVERAGE_ORDER = [*EXPRESSION_ORDER[:-1], "stop_when", "operation"]
PLANNER_ORDER = [*EXPRESSION_ORDER[:10], "stop_when", "operation", *EXPRESSION_ORDER[10:-1]]
WIDGET_ORDER = [*PLANNER_ORDER[:2], "mode", "manual_start", *PLANNER_ORDER[2:]]
LAYOUTS = [LEGACY_ORDER, EXPRESSION_ORDER, COVERAGE_ORDER, PLANNER_ORDER, WIDGET_ORDER]
NUMBERS = {"count", "seed", "manual_start", "pose_cooldown", "expression_cooldown", "camera_cooldown"}
OPTIONS = {
    "body_mode": ("any", "seated", "standing", "lying"),
    "selection": ("balanced", "close", "mid", "wide"), "operation": ("reserve", "preview"),
    "stop_when": ("poses", "expressions", "poses_and_expressions", "cameras", "never"),
    "mode": ("Planner", "Manual"),
}


def _fits_layout(positional, order):
    minimum = 14 if order is LEGACY_ORDER else len(order)
    return (minimum <= len(positional) <= len(order) + 2
            and all(value is None for value in positional[len(order):]))


def _recognizes_layout(positional, order):
    if not _fits_layout(positional, order):
        return False
    for name, value in zip(order, positional):
        if value is None:
            continue
        if name in OPTIONS:
            if value not in OPTIONS[name]:
                return False
        elif name in NUMBERS:
            if type(value) is not int:
                return False
        elif not isinstance(value, str):
            return False
    return True


def migrate_widget_values(node):
    """Return (replacement, review reason); never mutate the input node."""
    properties = node.get("properties") or {}
    reason = "ambiguous or unsupported widget layout; left unchanged"
    if not isinstance(properties, dict):
        return node, reason
    version = properties.get(LAYOUT_PROPERTY)
    if version == 4 and type(version) is int:
        return node, None
    values = node.get("widgets_values")
    positional = values if isinstance(values, list) else []
    named = node.get("widgets_values_named")
    if named is None:
        named = values if isinstance(values, dict) else {}
    if (not isinstance(named, dict)
            or (version is not None and (type(version) is not int or not 0 <= version <= 4))):
        return node, reason
    if positional:
        candidates = ([order for order in LAYOUTS if _recognizes_layout(positional, order)]
                      if version is None else
                      [LAYOUTS[version]] if _fits_layout(positional, LAYOUTS[version]) else [])
        if len(candidates) == 1:
            saved = dict(zip(candidates[0], positional))
        elif all(name in named for name in PLANNER_ORDER):
            saved = {}
        else:
            return node, reason
    else:
        if not all(name in named for name in LEGACY_ORDER[:14]):
            return node, reason
        saved = {}
    saved.update(named)
    for name, default in {"expression": "random", "pose_category": "all", "stop_when": "cameras",
                          "mode": "Planner", "manual_start": 1}.items():
        if saved.get(name) is None:
            saved[name] = default
    return {**node, "properties": {**properties, LAYOUT_PROPERTY: 4},
            "widgets_values": [saved.get(name) for name in WIDGET_ORDER],
            "widgets_values_named": saved}, None


def migrate_document(document):
    """Return (copy, changed node count, review messages), including subgraphs."""
    result = copy.deepcopy(document)
    changed = 0
    messages = []

    def visit(graph, location):
        nonlocal changed
        if not isinstance(graph, dict):
            return
        for node in graph.get("nodes", []):
            if not isinstance(node, dict):
                continue
            label = f"{location}/node {node.get('id', '?')}"
            if node.get("type") == NODE_NAME:
                replacement, reason = migrate_widget_values(node)
                if reason:
                    messages.append(f"{label}: {reason}.")
                elif replacement != node:
                    node.update(replacement)
                    changed += 1
            if isinstance(node.get("subgraph"), dict):
                visit(node["subgraph"], label)
        definitions = graph.get("definitions", {})
        if isinstance(definitions, dict):
            for index, subgraph in enumerate(definitions.get("subgraphs", [])):
                visit(subgraph, f"{location}/subgraph {index}")

    if isinstance(result, dict) and isinstance(result.get("nodes"), list):
        visit(result, "workflow")
    else:
        messages.append("Not a UI workflow; left unchanged (API prompts have no widget layout).")
    return result, changed, messages


def migrate_content(content):
    document, changed, messages = migrate_document(json.loads(content))
    # Keep already migrated files byte-for-byte identical, including whitespace.
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n" if changed else content,
            changed, messages)


def migrate_file(path, *, write=False):
    path = Path(path)
    content = path.read_text(encoding="utf-8")
    updated, changed, messages = migrate_content(content)
    for message in messages:
        print(f"Review [{path}]: {message}")
    if not changed:
        print(f"Unchanged: {path}")
        return 0, bool(messages)
    if not write:
        print(f"Dry run: {path}: would migrate {changed} Shot Planner node(s) to layout 4.")
        return changed, bool(messages)
    if path.is_symlink():
        raise ValueError(f"Refusing to replace a workflow symlink: {path}; pass its target file instead.")
    if path.read_text(encoding="utf-8") != content:
        raise ValueError(f"Workflow changed while being inspected: {path}; retry.")
    backup = backup_file(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(updated)
        temporary.chmod(stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Migrated: {path}: {changed} Shot Planner node(s). Backup: {backup}")
    return changed, bool(messages)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Workflow JSON file or directory (scanned recursively).")
    parser.add_argument("--write", action="store_true", help="Apply changes after making a non-overwriting backup.")
    args = parser.parse_args(argv)
    if not args.path.exists():
        parser.error(f"Path does not exist: {args.path}")
    paths = (sorted(path for path in args.path.rglob("*") if path.is_file() and path.suffix.lower() == ".json")
             if args.path.is_dir() else [args.path])
    total = 0
    review = False
    failed = False
    for path in paths:
        try:
            changed, needs_review = migrate_file(path, write=args.write)
            total += changed
            review |= needs_review
        except (OSError, ValueError, TypeError) as error:
            print(f"Error [{path}]: {error}")
            failed = True
    print(f"{'Write' if args.write else 'Dry run'} complete: {total} node(s); {len(paths)} file(s) inspected.")
    return 1 if failed else 2 if review else 0


if __name__ == "__main__":
    raise SystemExit(main())
