import re

from comfy_api.latest import io #type: ignore
from ..core import CATEGORY
from ..core.regex_helper import is_tags_format

# Match balanced outer parens: ((text)) or (text:1.5)
RE_EXPLICIT_WEIGHT = re.compile(r'^(.*?):(\d+\.?\d*)\s*$')
# Match bracket groups containing commas: ((a:2, b, c))
RE_WEIGHT_GROUP = re.compile(r'(\(+)((?:[^()]*,)+[^()]*)(\)+)')

def _strip_weight_markers(tag):
    # Strip matched outer ( ) and [ ] layers, explicit :weight, quotes, unmatched edge parens
    s = tag.strip()
    while len(s) >= 2:
        if s[0] == '(' and s[-1] == ')':
            s = s[1:-1].strip()
        elif s[0] == '[' and s[-1] == ']':
            s = s[1:-1].strip()
        else:
            break
    s = RE_EXPLICIT_WEIGHT.sub(r'\1', s)
    s = s.strip("'\"")
    # Strip remaining unmatched parens/brackets at edges (from comma-split within weighted groups)
    while s and s[0] in '([':
        s = s[1:]
    while s and s[-1] in ')]':
        s = s[:-1]
    return s.strip()

def _normalize_tag_weight(tag, max_weight=1.4):
    # Cap emphasis weights to max_weight, converting to explicit format if needed
    s = tag.strip()
    if not s:
        return s
    depth = 0
    inner = s
    while len(inner) >= 2 and inner[0] == '(' and inner[-1] == ')':
        depth += 1
        inner = inner[1:-1].strip()
    if depth == 0:
        return s
    m = RE_EXPLICIT_WEIGHT.match(inner)
    if m:
        base = m.group(1).strip()
        w = min(float(m.group(2)), max_weight)
        if w <= 1.0:
            return base
        return f"({base}:{round(w, 2)})"
    # Paren-depth weight: each layer = 1.1x — always convert to explicit format
    w = min(round(1.1 ** depth, 2), max_weight)
    if w <= 1.0:
        return inner
    return f"({inner}:{w})"

def _dedup_key(tag, weight_aware):
    # Build a comparison key for a tag
    base = _strip_weight_markers(tag) if weight_aware else tag
    return base.lower().replace("_", " ")

def _expand_weight_groups(text):
    # Expand bracket groups with commas into individual weighted tags.
    # ((artist signature:2, artist name:2, text, writing)) becomes:
    # (artist signature:2), (artist name:2), ((text)), ((writing))
    # Each item preserves its weight - explicit :N stays, others get bracket depth.
    def _replacer(m):
        open_b = m.group(1)
        inner = m.group(2)
        close_b = m.group(3)
        depth = min(len(open_b), len(close_b))
        parts = [p.strip() for p in inner.split(',') if p.strip()]
        expanded = []
        for part in parts:
            part = part.strip("'\"").strip()
            if not part:
                continue
            wm = RE_EXPLICIT_WEIGHT.match(part)
            if wm:
                # Has explicit :weight — keep as single-layer (base:weight)
                expanded.append(f"({wm.group(1).strip()}:{wm.group(2)})")
            else:
                # No explicit weight — wrap in original bracket depth
                expanded.append('(' * depth + part + ')' * depth)
        return ', '.join(expanded)
    return RE_WEIGHT_GROUP.sub(_replacer, text)

def _dedup_tags(tags, weight_aware):
    # Deduplicate a list of tags, keeping first occurrence
    seen = set()
    unique = []
    for tag in tags:
        if not tag:
            continue
        key = _dedup_key(tag, weight_aware)
        if key not in seen:
            seen.add(key)
            unique.append(tag)
    return unique

class RvText_DeDuplicate(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="String DeDuplicate [Eclipse]",
            display_name="String DeDuplicate",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            description="Combine multiple strings and remove duplicate entries (case-insensitive, underscore-normalized). Handles both tag format (comma-separated) and prose (line-based). Empty inputs are ignored.",
            inputs=[
                io.Int.Input("inputcount", default=2, min=2, max=20, step=1, socketless=True, tooltip="Number of string inputs."),
                io.Boolean.Input("dedup_inputs", default=False, label_on="Yes", label_off="No", tooltip="Deduplicate within each input before combining."),
                io.Combo.Input("weight_handling", options=["None", "Remove Weights", "Normalize"], default="None", tooltip="None=keep weights as-is. Remove Weights=strip all emphasis markers. Normalize=cap weights at 1.4."),
                io.String.Input("string_1", optional=True, force_input=True, tooltip="String input #1."),
                io.String.Input("string_2", optional=True, force_input=True, tooltip="String input #2."),
            ],
            outputs=[
                io.String.Output("text"),
            ],
        )

    @classmethod
    def execute(cls, inputcount=2, dedup_inputs=False, weight_handling="None", **kwargs):
        weight_aware = weight_handling != "None"

        # Collect all non-empty string inputs
        parts_list = []
        for i in range(1, max(2, inputcount) + 1):
            val = kwargs.get(f"string_{i}")
            if val is None:
                continue
            val = val.strip() if isinstance(val, str) else ""
            if val:
                parts_list.append(val)

        if not parts_list:
            return io.NodeOutput("")

        is_tags = is_tags_format(parts_list[0])
        sep = "," if is_tags else "\n"
        out_joiner = ", " if is_tags else "\n"

        # Optionally dedup within each input first
        if dedup_inputs and len(parts_list) > 1:
            deduped = []
            for inp in parts_list:
                if is_tags:
                    inp = _expand_weight_groups(inp)
                tags = [t.strip() for t in inp.split(sep)]
                tags = _dedup_tags(tags, weight_aware)
                deduped.append(out_joiner.join(tags))
            parts_list = deduped

        # Combine all inputs and dedup globally
        combined = (", " if is_tags else "\n").join(parts_list)
        if is_tags:
            combined = _expand_weight_groups(combined)
        all_tags = [t.strip() for t in combined.split(sep)]
        unique = _dedup_tags(all_tags, weight_aware)

        # Apply weight handling to output
        if weight_handling == "Remove Weights":
            unique = [_strip_weight_markers(t) for t in unique]
        elif weight_handling == "Normalize":
            unique = [_normalize_tag_weight(t) for t in unique]

        return io.NodeOutput(out_joiner.join(unique))
