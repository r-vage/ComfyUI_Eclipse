"""Shared, schema-aware workflow migration for the CLI and Eclipse node.

Only node types and their schema fields are changed. Prompt text, notes, and
custom titles are never treated as replacement targets.
"""

import json
import shutil
from pathlib import Path

BUILTINS = {
    "Integer [Eclipse]": ("PrimitiveInt", "INT", 1),
    "Boolean [Eclipse]": ("PrimitiveBoolean", "BOOLEAN", True),
    "String [Eclipse]": ("PrimitiveString", "STRING", ""),
    "String Multiline [Eclipse]": ("PrimitiveStringMultiline", "STRING", ""),
    "Show Text [Eclipse]": ("PreviewAny", "STRING", None),
}
CROP_NODE = "Image Crop by Mask [Eclipse]"
CROP_INPUT_NAMES = {"mask_expand": "mask_blur", "padding": "divisible_by"}


def _rename_crop_widget(widget):
    if isinstance(widget, dict) and widget.get("name") in CROP_INPUT_NAMES:
        widget["name"] = CROP_INPUT_NAMES[widget["name"]]
        return True
    return False


def _rename_crop_inputs(node):
    changed = False
    inputs = node.get("inputs", [])
    if isinstance(inputs, list):
        for item in inputs:
            if not isinstance(item, dict):
                continue
            changed |= _rename_crop_widget(item)
            changed |= _rename_crop_widget(item.get("widget"))
    # Includes API prompt inputs, named UI values, and object-form widgets.
    for values in (inputs, node.get("widgets_values_named"), node.get("widgets_values")):
        if not isinstance(values, dict):
            continue
        for old, new in CROP_INPUT_NAMES.items():
            if old in values:
                values.setdefault(new, values.pop(old))
                changed = True
    return changed


def backup_file(path):
    """Never overwrite an earlier pre-migration backup."""
    path = Path(path)
    backup = Path(str(path) + ".bak")
    suffix = 1
    while True:
        try:
            destination = backup.open("xb")
        except FileExistsError:
            # Exclusive creation also protects dangling symlinks and concurrent
            # migrations, unlike an exists() check followed by copy2().
            backup = Path(f"{path}.bak.{suffix}")
            suffix += 1
            continue
        try:
            with destination, path.open("rb") as source:
                shutil.copyfileobj(source, destination)
            shutil.copystat(path, backup)
        except BaseException:
            backup.unlink(missing_ok=True)
            raise
        break
    return backup


def _chain(node_type, mappings):
    chain = [node_type]
    while chain[-1] in mappings:
        target = mappings[chain[-1]]
        if target in chain:
            break
        chain.append(target)
    return chain


def _target_slot(link, node_id, slots):
    if isinstance(link, list) and len(link) >= 6 and link[3] == node_id:
        link[4] = slots[link[4]]
    elif isinstance(link, dict) and link.get("target_id") == node_id:
        link["target_slot"] = slots[link["target_slot"]]


def _convert_ui(node, graph, old, target, output_type, default):
    inputs = node.get("inputs", [])
    widgets = node.get("widgets_values", [])
    if not isinstance(inputs, list) or not isinstance(widgets, (list, dict)):
        return "unsupported input/widget serialization"
    multiline = old == "String Multiline [Eclipse]"
    preview = target == "PreviewAny"
    accepted = {"input_string", "string"} if multiline else {"source" if preview else "value"}
    if any(item.get("name") not in accepted for item in inputs):
        return "unrecognized input schema"
    if len(node.get("outputs", [])) > 1:
        return "multiple outputs require manual review"
    connected = [item for item in inputs if item.get("link") is not None]
    if multiline and len(connected) > 1:
        return "both multiline inputs are connected; the built-in has only one value input"
    value_name = "string" if multiline else "value"
    value = widgets.get(value_name, default) if isinstance(widgets, dict) else (widgets[0] if widgets else default)
    if not preview and not isinstance(value, type(default)):
        return "unexpected widget value type"

    # Check every target endpoint before dropping the optional prefix socket.
    selected = connected[0].get("name") if multiline and connected else "string"
    retained = [(i, item) for i, item in enumerate(inputs) if not multiline or item.get("name") == selected]
    slots = {old_slot: new_slot for new_slot, (old_slot, _) in enumerate(retained)}
    for link in graph.get("links", []):
        if isinstance(link, list) and len(link) >= 6:
            target_id, target_slot = link[3:5]
        elif isinstance(link, dict):
            target_id, target_slot = link.get("target_id"), link.get("target_slot")
        else:
            continue
        if target_id == node.get("id") and target_slot not in slots:
            return "a link targets an input that cannot be converted"

    node["inputs"] = [item for _, item in retained]
    for item in node["inputs"]:
        item["name"] = "source" if preview else "value"
        if multiline or "widget" in item:
            item["widget"] = {"name": item["name"]}
    for link in graph.get("links", []):
        _target_slot(link, node.get("id"), slots)
    for output in node.get("outputs", []):
        output["name"] = output_type
        output["type"] = output_type
    if preview:
        # Both preview widgets persist their display text as the first value.
        node["widgets_values"] = widgets if isinstance(widgets, list) else []
    elif isinstance(widgets, dict):
        node["widgets_values"] = {"value": value}
        if target == "PrimitiveInt":
            node["widgets_values"]["control_after_generate"] = "fixed"
    else:
        node["widgets_values"] = [value, "fixed"] if target == "PrimitiveInt" else [value]
    return None


def _convert_api(node, old, target, default):
    inputs = node.get("inputs", {})
    if not isinstance(inputs, dict):
        return "unsupported API inputs"
    if old == "String Multiline [Eclipse]":
        prefix = inputs.get("input_string", "")
        body = inputs.get("string", "")
        if isinstance(prefix, list) and isinstance(body, list):
            return "both multiline inputs are connected; the built-in has only one value input"
        value = prefix if isinstance(prefix, list) else body if isinstance(body, list) else " ".join(
            part for part in (prefix, body) if isinstance(part, str) and part
        )
        node["inputs"] = {"value": value}
    elif target != "PreviewAny":
        inputs.setdefault("value", default)
    return None


def migrate_content(content, mappings):
    """Return (JSON text, replacement counts, review messages); unchanged is exact."""
    document = json.loads(content)
    replacements = {}
    messages = []
    renamed_widgets = {}
    graphs = []
    crop_nodes = {}
    changed_crops = set()

    def record_crop(node):
        if id(node) not in changed_crops:
            changed_crops.add(id(node))
            replacements[CROP_NODE] = replacements.get(CROP_NODE, 0) + 1

    def migrate_node(node, graph=None):
        key = "type" if graph is not None else "class_type"
        original = node.get(key)
        if not isinstance(original, str):
            return
        if original == CROP_NODE:
            if graph is not None:
                crop_nodes.setdefault(id(graph), {})[str(node.get("id"))] = node
            if _rename_crop_inputs(node):
                record_crop(node)
        chain = _chain(original, mappings)
        target = chain[-1]
        source = next((name for name in chain if name in BUILTINS and BUILTINS[name][0] == target), None)
        if target == "Universal Block Swap [Eclipse]":
            messages.append(f"Node {node.get('id', '?')}: Universal Block Swap retained; no direct built-in replacement.")
        if target == original:
            return
        if source:
            _, output_type, default = BUILTINS[source]
            reason = (
                _convert_ui(node, graph, source, target, output_type, default)
                if graph is not None else _convert_api(node, source, target, default)
            )
            if reason:
                messages.append(f"Node {node.get('id', '?')} ({original}) retained: {reason}.")
                return
            if source == "String Multiline [Eclipse]" and graph is not None:
                renamed_widgets.setdefault(graph.get("id"), set()).add(str(node.get("id")))
                for widget in graph.get("widgets", []):
                    if str(widget.get("id")) == str(node.get("id")) and widget.get("name") == "string":
                        widget["name"] = "value"
        node[key] = target
        properties = node.get("properties", {})
        if isinstance(properties, dict):
            if properties.get("Node name for S&R") in chain:
                properties["Node name for S&R"] = target
            if source:
                properties["cnr_id"] = "comfy-core"
                properties.pop("ver", None)
        replacements[original] = replacements.get(original, 0) + 1

    def migrate_graph(graph):
        graphs.append(graph)
        for node in graph.get("nodes", []):
            if isinstance(node, dict):
                migrate_node(node, graph)
                if isinstance(node.get("subgraph"), dict):
                    migrate_graph(node["subgraph"])
        definitions = graph.get("definitions", {})
        if isinstance(definitions, dict):
            for subgraph in definitions.get("subgraphs", []):
                if isinstance(subgraph, dict):
                    migrate_graph(subgraph)

    if isinstance(document, dict) and isinstance(document.get("nodes"), list):
        migrate_graph(document)
    elif isinstance(document, dict) and document and all(
        isinstance(node, dict) and "class_type" in node for node in document.values()
    ):
        for node in document.values():
            migrate_node(node)
    else:
        messages.append("Not a supported ComfyUI workflow or API prompt; left unchanged.")
    # Legacy subgraph hosts store exposed widget names outside the definition.
    for graph in graphs:
        for node in graph.get("nodes", []):
            renamed = renamed_widgets.get(node.get("type"), set())
            for widget in node.get("properties", {}).get("proxyWidgets", []):
                if isinstance(widget, list) and len(widget) == 2 and str(widget[0]) in renamed and widget[1] == "string":
                    widget[1] = "value"
    # Preserve promoted crop controls in modern definitions and embedded graphs.
    definitions = {graph["id"]: graph for graph in graphs if "id" in graph}
    for graph in graphs:
        crops = crop_nodes.get(id(graph), {})
        for widget in graph.get("widgets", []):
            crop = crops.get(str(widget.get("id")))
            if crop is not None and _rename_crop_widget(widget):
                record_crop(crop)
        for node in graph.get("nodes", []):
            inner = node.get("subgraph") or definitions.get(node.get("type"))
            inner_crops = crop_nodes.get(id(inner), {})
            for proxy in node.get("properties", {}).get("proxyWidgets", []):
                if not isinstance(proxy, list) or len(proxy) != 2:
                    continue
                crop = inner_crops.get(str(proxy[0]))
                if crop is not None and proxy[1] in CROP_INPUT_NAMES:
                    proxy[1] = CROP_INPUT_NAMES[proxy[1]]
                    record_crop(crop)
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n" if replacements else content, replacements, messages)
