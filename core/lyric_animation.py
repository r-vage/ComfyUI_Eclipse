# Full-song floating-caption schedules. Only metadata is retained between frames.
import math
import random
import re
from dataclasses import dataclass, replace
from itertools import pairwise

FLOATING_MODES = ("floating-words", "floating-lines")


@dataclass(frozen=True)
class CaptionEvent:
    text: str
    line_index: int
    start: float
    end: float
    fade_in: float
    fade_out: float

    def opacity(self, time):
        if not self.start <= time < self.end:
            return 0.0
        incoming = min(1.0, (time - self.start) / self.fade_in) if self.fade_in else 1.0
        outgoing = min(1.0, (self.end - time) / self.fade_out) if self.fade_out else 1.0
        return min(incoming, outgoing)


def _word_phrases(line, min_display, max_words):
    # Use exact source slices. Untimed text belongs to the closest neighboring
    # timed unit by character distance, never to an invented timestamp.
    text = line["text"]
    timed = [w for w in line["words"] if w["start"] is not None]
    if not timed:
        return [(text, line["start"], line["end"])]
    boundaries = [0]
    for previous, following in pairwise(timed):
        left, right = previous["char_end"], following["char_start"]
        spaces = list(re.finditer(r"\s+", text[left:right]))
        # Split only at a word boundary. Adjacent punctuation stays attached.
        boundary = min(
            (left + m.end() for m in spaces),
            key=lambda value: abs(value - (left + right) / 2),
            default=right,
        )
        boundaries.append(boundary)
    boundaries.append(len(text))
    units = [
        (boundaries[i], boundaries[i + 1], w["start"], w["end"])
        for i, w in enumerate(timed)
    ]
    # Accepted sentence edges may extend beyond the first/last timed word.
    units[0] = (*units[0][:2], line["start"], units[0][3])
    units[-1] = (*units[-1][:3], line["end"])
    phrases = []
    first, last, start, end = units[0]
    for a, b, next_start, next_end in units[1:]:
        count = len(text[first:b].split())
        if (
            end - start < min_display
            and next_start - end <= min_display
            and count <= max_words
        ):
            last, end = b, next_end
        else:
            phrases.append((text[first:last].strip(), start, end))
            first, last, start, end = a, b, next_start, next_end
    phrases.append((text[first:last].strip(), start, end))
    return phrases


def caption_schedule(
    lines, mode, *, fade_in=0.12, fade_out=0.20, min_display=0.45,
    max_words=4, max_simultaneous=3,
):
    if mode not in FLOATING_MODES:
        raise ValueError("Expected a floating caption mode.")
    if not all(math.isfinite(v) and v >= 0 for v in (fade_in, fade_out, min_display)):
        raise ValueError("Caption fades and minimum display must be finite and non-negative.")
    if any(type(v) is not int or v < 1 for v in (max_words, max_simultaneous)):
        raise ValueError("Phrase words and simultaneous items must be positive integers.")
    events = []
    for index, line in enumerate(lines):
        if line["start"] is None or not line["text"].strip():
            continue
        phrases = (
            _word_phrases(line, min_display, max_words)
            if mode == "floating-words"
            else [(line["text"], line["start"], line["end"])]
        )
        for text, start, end in phrases:
            # Keep a readable hold between the requested fades. A following
            # phrase or line is not a reason to remove an item when slots remain.
            end = max(end, start + fade_in + min_display) + fade_out
            events.append(CaptionEvent(text, index, start, end, fade_in, fade_out))
    # Reserve a slot for every actual onset. No animation queues or delayed
    # starts: even passages faster than the preferred display remain in sync.
    for i, event in enumerate(events):
        end = event.end
        if i + max_simultaneous < len(events):
            end = min(end, events[i + max_simultaneous].start)
        span = end - event.start
        requested = fade_in + min_display + fade_out
        scale = min(1.0, span / requested) if requested else 1.0
        events[i] = replace(event, end=end, fade_in=fade_in * scale, fade_out=fade_out * scale)
    return events


@dataclass(frozen=True)
class PlacedCaption:
    event: CaptionEvent
    width: int
    height: int
    x: float
    y: float
    dx: float
    dy: float

    def position(self, time):
        phase = max(0.0, min(1.0, (time - self.event.start) / (self.event.end - self.event.start)))
        phase = phase * phase * (3 - 2 * phase)
        return self.x + self.dx * phase, self.y + self.dy * phase

    def swept_bounds(self):
        return (
            min(self.x, self.x + self.dx) - self.width / 2,
            min(self.y, self.y + self.dy) - self.height / 2,
            max(self.x, self.x + self.dx) + self.width / 2,
            max(self.y, self.y + self.dy) + self.height / 2,
        )


def place_captions(
    events, dimensions, size, *, circle_radius=20, float_distance=2,
    margin_x=40, margin_y=40, seed=0,
):
    if not all(math.isfinite(v) and v >= 0 for v in (circle_radius, float_distance, margin_x, margin_y)):
        raise ValueError("Caption radius, movement and margins must be finite and non-negative.")
    random_source = random.Random(seed)
    cx, cy = size[0] / 2, size[1] / 2
    radius = min(size) * circle_radius / 100
    distance = min(size) * float_distance / 100
    placed, active = [], []
    previous = (cx, cy)
    for event, (width, height) in zip(events, dimensions, strict=True):
        x_min, y_min = margin_x + width / 2, margin_y + height / 2
        x_max, y_max = size[0] - x_min, size[1] - y_min
        if x_min > x_max or y_min > y_max:
            raise ValueError("Floating caption does not fit inside the canvas margins.")

        def constrain(x, y, x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max):
            length = math.hypot(x - cx, y - cy)
            if length > radius:
                scale = radius / length
                x, y = cx + (x - cx) * scale, cy + (y - cy) * scale
            # Clamping toward the centered margin rectangle also stays in disk.
            return min(x_max, max(x_min, x)), min(y_max, max(y_min, y))

        active = [item for item in active if item.event.end > event.start]
        candidates = []
        angle = random_source.uniform(0, math.tau)
        dx, dy = distance * math.cos(angle), distance * math.sin(angle)
        for attempt in range(40):
            angle = random_source.uniform(0, math.tau)
            reach = radius * math.sqrt(random_source.random())
            base = previous if attempt < 30 else (cx, cy)
            x, y = constrain(base[0] + reach * math.cos(angle), base[1] + reach * math.sin(angle))
            end_x, end_y = constrain(x + dx, y + dy)
            candidate = PlacedCaption(event, width, height, x, y, end_x - x, end_y - y)
            bounds = candidate.swept_bounds()
            overlap = 0.0
            for other in active:
                box = other.swept_bounds()
                # Small breathing room also reduces collisions during drift.
                overlap += max(0, min(bounds[2], box[2]) - max(bounds[0], box[0]) + 6) * max(
                    0, min(bounds[3], box[3]) - max(bounds[1], box[1]) + 6
                )
            nearby = math.hypot(x - previous[0], y - previous[1])
            candidates.append(((overlap, nearby), candidate))
        best = min(candidates, key=lambda value: value[0])[1]
        previous = best.position(best.event.end)
        placed.append(best)
        active.append(best)
    return placed
