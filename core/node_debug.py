# Bounded execution diagnostics; never materialize tensors or print user text.

import inspect
import sys
import time
from functools import wraps
from itertools import islice

from .logger import is_debug_enabled, log

_STRUCTURE_FIELDS = frozenset({
    "samples", "waveform", "sample_rate", "mode", "role", "stage", "operation",
    "width", "height", "fps", "count", "face", "body", "clothing", "rear", "anchor",
    "layout", "wardrobe", "front_body", "rear_body", "front", "font_size",
    "margin_x", "margin_y", "outline_width", "caption_transparency", "enable_glow",
    "glow_intensity", "glow_range", "glow_blur", "circle_radius", "float_distance",
    "fade_in", "fade_out", "min_display", "max_words", "max_simultaneous", "seed",
    "time_offset", "rotation_axis", "position", "font_file", "text_color",
    "highlight_color", "outline_color", "background_color", "glow_inner_color",
    "glow_outer_color", "version", "schema_version", "duration", "exhausted",
})


def _summary(value, name="", values=(), depth=0):
    if value is None or type(value) in (bool, int, float):
        return repr(value)
    if isinstance(value, str):
        return repr(value[:80]) if name in values else f"text(chars={len(value)})"
    torch = sys.modules.get("torch")
    if torch is not None and isinstance(value, torch.Tensor):
        shape = "nested" if value.is_nested else tuple(value.shape)
        return f"Tensor(shape={shape}, dtype={value.dtype}, device={value.device})"
    if type(value).__name__ == "FrameTimeline" and type(value).__module__.endswith("frame_timeline"):
        return f"FrameTimeline(frames={len(value)}, shape={value.shape}, dtype={value.dtype}, fps={value.fps})"
    if isinstance(value, (list, tuple)):
        if depth >= 2:
            return f"{type(value).__name__}(count={len(value)})"
        items = ", ".join(_summary(item, name, values, depth + 1) for item in value[:3])
        suffix = ", ..." if len(value) > 3 else ""
        return f"{type(value).__name__}(count={len(value)}, [{items}{suffix}])"
    if isinstance(value, dict):
        fields = []
        if depth < 2:
            for key in islice(value, 48):
                if isinstance(key, str) and key in _STRUCTURE_FIELDS:
                    fields.append(f"{key}={_summary(value[key], key, values, depth + 1)}")
        detail = ", " + ", ".join(fields) if fields else ""
        return f"dict(count={len(value)}{detail})"
    return f"<{type(value).__name__}>"


def debug_event(prefix, event, *, values=(), **parameters):
    # Diagnostics are optional: formatting/output failures must not change work.
    try:
        if not is_debug_enabled():
            return
        details = "; ".join(
            f"{name}={_summary(value, name, values)}" for name, value in parameters.items()
        )
        log.debug(prefix, (f"{event}; {details}" if details else event)[:3500])
    except Exception:  # noqa: BLE001 - optional diagnostics never replace execution results/errors
        return


def debug_node(prefix, *, values=()):
    # Apply below @classmethod so V3 keeps its original signature and class binding.
    def decorate(function):
        signature = inspect.signature(function)
        parts = function.__qualname__.split(".")
        operation = parts[-2].split("_", 1)[-1] if len(parts) > 1 else parts[-1]

        @wraps(function)
        def execute(*args, **kwargs):
            if not is_debug_enabled():
                return function(*args, **kwargs)
            started = time.monotonic()
            try:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                parameters = {key: value for key, value in bound.arguments.items() if key not in {"cls", "self"}}
            except TypeError:
                parameters = {}
            debug_event(prefix, f"{operation} started", values=values, **parameters)
            try:
                result = function(*args, **kwargs)
            except BaseException as error:
                debug_event(prefix, f"{operation} failed ({type(error).__name__})",
                            elapsed_seconds=round(time.monotonic() - started, 3))
                raise
            # Optional envelopes and diagnostic properties must not affect returns.
            try:
                outputs = getattr(result, "args", result) if type(result).__name__ == "NodeOutput" else result
                blocked = isinstance(outputs, tuple) and any(type(item).__name__ == "ExecutionBlocker" for item in outputs)
            except Exception:  # noqa: BLE001 - alternate V3 envelopes may not expose metadata
                outputs, blocked = result, False
            debug_event(prefix, f"{operation} {'stopped' if blocked else 'finished'}",
                        elapsed_seconds=round(time.monotonic() - started, 3), outputs=outputs)
            return result

        return execute

    return decorate


class DebugProgress:
    # Callback-driven stage progress, bounded to one update per five seconds.
    def __init__(self, prefix, stage, total=0):
        self.prefix = prefix
        self.stage = stage
        self.total = total
        self.enabled = is_debug_enabled()
        self.last_time = time.monotonic()
        self.last_count = -1
        if self.enabled:
            debug_event(prefix, f"{stage} started", total=total)

    def update(self, completed):
        if not self.enabled or completed == self.last_count:
            return
        now = time.monotonic()
        if now - self.last_time < 5 and not (self.total and completed >= self.total):
            return
        self.last_time, self.last_count = now, completed
        debug_event(self.prefix, self.stage, completed=completed, total=self.total)
