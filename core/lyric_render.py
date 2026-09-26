# Incremental PyAV encoding; output ownership follows ComfyUI's cached VIDEO.
import math
import os
import tempfile
import unicodedata
from contextlib import closing
from fractions import Fraction

import av
import folder_paths
import torch
from comfy_api.latest import Input
from PIL import Image, ImageColor, ImageDraw, ImageFont

from .common import make_comfy_progress
from .fonts import caption_font, default_caption_font
from .frame_timeline import FrameTimeline
from .image_helpers import flatten_images, tensor2pil
from .lyric_animation import (
    ANIMATED_MODES,
    ROTATING_MODES,
    ROTATION_AXES,
    caption_schedule,
    place_captions,
)
from .lyric_appearance import CaptionAppearance, CaptionRaster
from .lyric_timing import audio_data
from .video_helpers import TemporaryVideo


def _remove(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass



def text_direction(text):
    for c in text:
        direction = unicodedata.bidirectional(c)
        if direction in ("R", "AL"):
            return "rtl"
        if direction == "L":
            return "ltr"
    return "ltr"


def caption_layer(
    line,
    size,
    font,
    font_size,
    position,
    margin_x,
    margin_y,
    text_color,
    highlight_color,
    outline_color,
    outline_width,
    active_word=None,
):
    text = line["text"]
    direction = text_direction(text)
    fonts = {font_size: font}
    fitted, bbox = floating_layout(text, size, fonts, font_size, margin_x, margin_y, outline_width)
    font = fonts[fitted]
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (
        margin_x
        if position.endswith("left")
        else size[0] - margin_x - width
        if position.endswith("right")
        else (size[0] - width) / 2
    )
    y = (
        margin_y
        if position.startswith("top")
        else size[1] - margin_y - height
        if position.startswith("bottom")
        else (size[1] - height) / 2
    )
    origin = (x - bbox[0], y - bbox[1])
    layer = Image.new("RGBA", size)
    draw = ImageDraw.Draw(layer)
    kwargs = {
        "font": font,
        "direction": direction,
        "stroke_width": outline_width,
        "stroke_fill": outline_color,
    }
    draw.text(origin, text, fill=text_color, **kwargs)
    if active_word is not None:
        # Draw the full shaped line in both colors, then clip to the active span.
        # Pure RTL reverses advance coordinates; mixed bidi uses whole-line mode.
        prefix = font.getlength(text[: active_word["char_start"]], direction=direction)
        end = font.getlength(text[: active_word["char_end"]], direction=direction)
        if direction == "rtl":
            total = font.getlength(text, direction=direction)
            prefix, end = total - end, total - prefix
        bright = Image.new("RGBA", size)
        ImageDraw.Draw(bright).text(origin, text, fill=highlight_color, **kwargs)
        box = (
            max(0, math.floor(origin[0] + prefix)),
            0,
            min(size[0], math.ceil(origin[0] + end)),
            size[1],
        )
        layer.paste(bright.crop(box), box[:2])
    return layer


def floating_layout(text, size, fonts, font_size, margin_x, margin_y, outline_width):
    # Measure the stroked glyph bounds, including accents and RTL bearings.
    available = (size[0] - 2 * margin_x, size[1] - 2 * margin_y)
    if min(available) <= 0:
        raise ValueError("Canvas margins leave no room for captions.")
    fitted = font_size
    while True:
        if fitted not in fonts:
            fonts[fitted] = ImageFont.truetype(
                fonts[font_size].path, fitted, layout_engine=ImageFont.Layout.RAQM
            )
        bbox = fonts[fitted].getbbox(text, direction=text_direction(text), stroke_width=outline_width)
        width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
        scale = min(available[0] / max(1, width), available[1] / max(1, height))
        if scale >= 1:
            return fitted, bbox
        if fitted == 1:
            raise ValueError("Floating caption outline does not fit the canvas margins.")
        fitted = max(1, min(fitted - 1, int(fitted * scale)))


def floating_layer(text, font, bbox, text_color, outline_color, outline_width, padding=0):
    # Effects get their own padded raster without changing the measured text box.
    bbox = (bbox[0] - padding, bbox[1] - padding, bbox[2] + padding, bbox[3] + padding)
    layer = Image.new("RGBA", (bbox[2] - bbox[0], bbox[3] - bbox[1]))
    ImageDraw.Draw(layer).text(
        (-bbox[0], -bbox[1]), text, font=font, direction=text_direction(text),
        fill=text_color, stroke_width=outline_width, stroke_fill=outline_color,
    )
    return layer


class FloatingOverlays:
    def __init__(self, placements, layouts, fonts, colors, outline_width, appearance=None):
        self.placements = placements
        self.layouts = layouts
        self.fonts = fonts
        self.colors = colors
        self.outline_width = outline_width
        self.appearance = appearance or CaptionAppearance()
        self.cursor = 0
        self.sprites = {}

    def composite(self, frame, time):
        # Raster memory is limited to currently visible items. A trim can skip
        # thousands of earlier events without ever rasterizing those captions.
        for index in list(self.sprites):
            if self.placements[index].event.end <= time:
                del self.sprites[index]
        while self.cursor < len(self.placements) and self.placements[self.cursor].event.start <= time:
            index = self.cursor
            self.cursor += 1
            item = self.placements[index]
            if item.event.end <= time:
                continue
            fitted, bbox = self.layouts[index]
            text = floating_layer(
                item.event.text, self.fonts[fitted], bbox,
                self.colors[0], self.colors[2], self.outline_width,
                self.appearance.padding,
            )
            self.sprites[index] = CaptionRaster(text, self.appearance)
        for index, sprite in self.sprites.items():
            item = self.placements[index]
            opacity = item.event.opacity(time)
            if opacity <= 0:
                continue
            x, y = item.position(time)
            pad = self.appearance.padding
            sprite.composite(frame, (round(x - item.width / 2) - pad,
                                     round(y - item.height / 2) - pad), opacity)


class RotatingOverlays:
    def __init__(self, events, layouts, fonts, colors, outline_width, appearance,
                 size, position, margin_x, margin_y, axis):
        self.events, self.layouts, self.fonts = events, layouts, fonts
        self.colors, self.outline_width, self.appearance = colors, outline_width, appearance
        self.axis = axis
        # One full-song envelope anchors the rotation center. Different words,
        # line lengths and trims cannot move that center, including at corners.
        # Match fitting's one-pixel allowance for bicubic support and rounding.
        width = max((b[2] - b[0] for _, b in layouts), default=0) * 8 / 7 + 2
        height = max((b[3] - b[1] for _, b in layouts), default=0) * 8 / 7 + 2
        self.center = (
            margin_x + width / 2 if position.endswith("left") else
            size[0] - margin_x - width / 2 if position.endswith("right") else size[0] / 2,
            margin_y + height / 2 if position.startswith("top") else
            size[1] - margin_y - height / 2 if position.startswith("bottom") else size[1] / 2,
        )
        self.cursor = 0
        self.raster = None

    def composite(self, frame, time):
        while self.cursor < len(self.events) and self.events[self.cursor].end <= time:
            self.cursor += 1
            self.raster = None
        if self.cursor >= len(self.events):
            return
        event = self.events[self.cursor]
        if event.start > time:
            return
        opacity = event.opacity(time)
        if opacity <= 0:
            return
        if self.raster is None:
            fitted, bbox = self.layouts[self.cursor]
            text = floating_layer(event.text, self.fonts[fitted], bbox,
                                  self.colors[0], self.colors[2], self.outline_width,
                                  self.appearance.padding)
            self.raster = CaptionRaster(text, self.appearance)
        result = self.raster.rotated(event.rotation_angle(time), self.axis)
        if result is not None:
            sprite, (left, top) = result
            sprite.composite(frame, (round(self.center[0]) + left, round(self.center[1]) + top), opacity)


def _background_items(background):
    if isinstance(background, (list, tuple)):
        if not background:
            raise ValueError("Background image lists must not be empty.")
        for item in background:
            yield from _background_items(item)
    else:
        yield background


def validate_background(background):
    # Validate list structure and tensor metadata without copying pixels or
    # constructing a per-frame list. MatchType leaves runtime validation to us.
    if background is None:
        return None
    video = None
    images = False
    timeline = None
    for item in _background_items(background):
        if isinstance(item, FrameTimeline):
            if timeline is not None:
                raise ValueError("Background accepts only one frame timeline.")
            timeline = item
        elif isinstance(item, Input.Video):
            if video is not None:
                raise ValueError("Background accepts only one VIDEO object.")
            video = item
        elif isinstance(item, torch.Tensor):
            if (item.ndim not in (3, 4) or any(d == 0 for d in item.shape)
                    or item.shape[-1] not in (1, 3, 4) or not item.is_floating_point()):
                raise ValueError("Background IMAGE must be a non-empty floating HWC or BHWC tensor with 1, 3 or 4 channels.")
            images = True
        else:
            raise TypeError("Background requires IMAGE tensors or one VIDEO object.")
        if video is not None and images:
            raise ValueError("Background cannot mix IMAGE and VIDEO inputs.")
        if timeline is not None and (video is not None or images):
            raise ValueError("Background cannot mix a timeline with IMAGE or VIDEO inputs.")
    return video


def _background_images(background):
    # Slice first: flatten_images then produces just one shared-storage view,
    # even for a very long batch. Differing image sizes need no concatenation.
    for item in _background_items(background):
        if isinstance(item, FrameTimeline):
            with closing(iter(item)) as frames:
                for frame in frames:
                    yield frame.unsqueeze(0)
        elif item.ndim == 4:
            for index in range(item.shape[0]):
                yield flatten_images(item[index:index + 1])[0]
        else:
            yield flatten_images(item)[0]


def background_frames(background, size, color, fps, count):
    import comfy.model_management as mm

    video = validate_background(background)
    for item in _background_items(background):
        if isinstance(item, FrameTimeline) and item.fps != fps:
            raise ValueError("Caption FPS must match the exact frame timeline.")
    if video is None:
        images = iter(()) if background is None else _background_images(background)
        last = Image.new("RGB", size, color)
        try:
            for _ in range(count):
                mm.throw_exception_if_processing_interrupted()
                image = next(images, None)
                if image is not None:
                    if not torch.isfinite(image).all():
                        raise ValueError("Background IMAGE pixels must be finite.")
                    # The shared converter squeezes singleton axes. Expand spatial
                    # singletons as views so narrow images keep their orientation.
                    image = image.detach().expand(1, max(2, image.shape[1]), max(2, image.shape[2]), -1)
                    last = tensor2pil(image).convert("RGB").resize(size)
                yield last
        finally:
            if hasattr(images, "close"):
                images.close()
        return
    # Use the public VIDEO exporter to honor upstream trim/crop/rotation views.
    # File-backed exporters remux/encode incrementally. Only the needed excerpt
    # is materialized, and this intermediate never becomes a cached output.
    from comfy_api.latest import Types

    mm.throw_exception_if_processing_interrupted()
    source_duration = video.get_duration()
    if not math.isfinite(source_duration) or source_duration <= 0:
        raise ValueError("Background video has no usable duration.")
    duration = min(count / fps, source_duration)
    trimmed = video.as_trimmed(0, duration, strict_duration=False)
    if trimmed is None:
        raise ValueError("Background video has no usable duration.")
    with tempfile.TemporaryDirectory(
        prefix="EclipseLyricsBackground_", dir=folder_paths.get_temp_directory()
    ) as directory:
        path = os.path.join(directory, "background.mp4")
        trimmed.save_to(
            path, format=Types.VideoContainer.MP4, codec=Types.VideoCodec.H264
        )
        with av.open(path) as source:
            stream = source.streams.video[0]
            origin = float((stream.start_time or 0) * stream.time_base)
            decoded = iter(source.decode(video=0))
            next_frame = next(decoded, None)
            last = None
            for index in range(count):
                mm.throw_exception_if_processing_interrupted()
                target = index / fps
                while (
                    next_frame is not None and (next_frame.time or 0) - origin <= target
                ):
                    mm.throw_exception_if_processing_interrupted()
                    last = next_frame.to_image().convert("RGB").resize(size)
                    next_frame = next(decoded, None)
                if last is None:
                    if next_frame is None:
                        raise ValueError(
                            "Background video contains no decodable frames."
                        )
                    last = next_frame.to_image().convert("RGB").resize(size)
                yield last


def render_video(
    audio,
    lines,
    *,
    font_file=None,
    font_size=80,
    width=720,
    height=1280,
    fps=24,
    mode="floating-words",
    position="bottom_center",
    margin_x=40,
    margin_y=40,
    text_color="#ffffff",
    highlight_color="#ffff00",
    outline_color="#616161",
    outline_width=3,
    background_color="#000000",
    caption_transparency=0,
    enable_glow=False,
    glow_intensity=5,
    glow_range=25,
    glow_blur=15,
    glow_inner_color="#2ec0ff",
    glow_outer_color="#006eff",
    background=None,
    circle_radius=30,
    float_distance=1.5,
    fade_in=0.5,
    fade_out=0.5,
    min_display=0.7,
    max_words=4,
    max_simultaneous=5,
    seed=42,
    time_offset=0,
    rotation_axis="Turning sign",
):
    import comfy.model_management as mm

    appearance = CaptionAppearance(caption_transparency, enable_glow, glow_intensity,
                                   glow_range, glow_blur, glow_inner_color, glow_outer_color)
    if mode not in ("whole-line", "active-word", *ANIMATED_MODES):
        raise ValueError("Select a supported caption mode.")
    if mode in ROTATING_MODES and rotation_axis not in ROTATION_AXES:
        raise ValueError("Select Turning sign or Flipping card for the rotation axis.")
    waveform, rate = audio_data(audio)
    duration = waveform.shape[-1] / rate
    if (
        not math.isfinite(fps)
        or fps <= 0
        or width < 16
        or height < 16
        or width % 2
        or height % 2
    ):
        raise ValueError(
            "Use a positive fps and even video dimensions of at least 16 pixels."
        )
    size = (width, height)
    # Keep the existing preallocation limit for excessive effects separate from
    # text fitting. Accepted glow may cross margins and clip at the video edge.
    if appearance.enable_glow and min(width - 2 * (margin_x + appearance.padding),
                                      height - 2 * (margin_y + appearance.padding)) <= 0:
        raise ValueError("Glow exceeds the canvas resource limit; reduce glow range/blur or margins.")
    font = caption_font(default_caption_font() if font_file is None else font_file,
                        font_size, "\n".join(x["text"] for x in lines))
    colors = [
        ImageColor.getrgb(c)
        for c in (text_color, highlight_color, outline_color, background_color)
    ]
    floating = rotating = None
    if mode in ANIMATED_MODES:
        events = caption_schedule(
            lines, mode, fade_in=fade_in, fade_out=fade_out,
            min_display=min_display, max_words=max_words, max_simultaneous=max_simultaneous,
        )
        fonts = {font_size: font}
        if mode in ROTATING_MODES:
            # Reserve the maximum perspective expansion around the stationary
            # full-song text/outline envelope, including pixel rounding.
            fitting_size = tuple(math.floor((side - 2 * (margin + 1)) * 7 / 8)
                                 for side, margin in zip(size, (margin_x, margin_y)))
            layouts = [floating_layout(e.text, fitting_size, fonts, font_size, 0, 0,
                                       outline_width) for e in events]
            rotating = RotatingOverlays(events, layouts, fonts, colors, outline_width, appearance,
                                       size, position, margin_x, margin_y, rotation_axis)
        else:
            layouts = [
                floating_layout(event.text, size, fonts, font_size, margin_x, margin_y, outline_width)
                for event in events
            ]
            placements = place_captions(
                events, [(bbox[2] - bbox[0], bbox[3] - bbox[1]) for _, bbox in layouts],
                size, circle_radius=circle_radius, float_distance=float_distance,
                margin_x=margin_x, margin_y=margin_y, seed=seed,
            )
            floating = FloatingOverlays(placements, layouts, fonts, colors, outline_width, appearance)
    count = math.ceil(duration * fps)
    warnings = []
    if mode == "active-word" and any(not x["words"] for x in lines):
        warnings.append("Lines without usable word timings remain visible without word highlighting.")
    mixed = {
        i
        for i, line in enumerate(lines)
        if {"L"} <= {unicodedata.bidirectional(c) for c in line["text"]}
        and any(unicodedata.bidirectional(c) in ("R", "AL") for c in line["text"])
    }
    if mixed and mode == "active-word":
        warnings.append(
            "Mixed bidirectional lines use whole-line captions to preserve shaping and visual order."
        )
    fixed = FixedOverlays(lines, mode, mixed, size, font, font_size, position,
                          margin_x, margin_y, colors, outline_width, appearance)
    os.makedirs(folder_paths.get_temp_directory(), exist_ok=True)
    fd, path = tempfile.mkstemp(
        prefix="EclipseLyrics_", suffix=".mp4", dir=folder_paths.get_temp_directory()
    )
    os.close(fd)
    backgrounds = background_frames(
        background, size, colors[3], fps, count
    )
    try:
        with av.open(path, "w", options={"movflags": "+faststart"}) as output:
            video = output.add_stream(
                "libx264", rate=Fraction(str(fps)).limit_denominator(10000)
            )
            video.width, video.height, video.pix_fmt = width, height, "yuv420p"
            video.options = {"crf": "20", "preset": "veryfast"}
            sound = output.add_stream("aac", rate=rate)
            sound.layout = "mono" if waveform.shape[0] == 1 else "stereo"
            progress = make_comfy_progress(count + 1)
            audio_cursor = 0
            for index, bg in enumerate(backgrounds):
                mm.throw_exception_if_processing_interrupted()
                t = index / fps + time_offset
                frame = bg.convert("RGBA")
                if rotating is not None:
                    rotating.composite(frame, t)
                elif floating is not None:
                    floating.composite(frame, t)
                else:
                    fixed.composite(frame, t)
                vf = av.VideoFrame.from_image(frame.convert("RGB"))
                vf.pts = index
                for packet in video.encode(vf):
                    output.mux(packet)
                audio_end = min(waveform.shape[-1], round((index + 1) / fps * rate))
                if audio_end > audio_cursor:
                    af = av.AudioFrame.from_ndarray(
                        waveform[:, audio_cursor:audio_end].contiguous().numpy(),
                        format="fltp",
                        layout=sound.layout.name,
                    )
                    af.sample_rate, af.pts, af.time_base = (
                        rate,
                        audio_cursor,
                        Fraction(1, rate),
                    )
                    for packet in sound.encode(af):
                        output.mux(packet)
                    audio_cursor = audio_end
                progress.update(1)
            for stream in (video, sound):
                for packet in stream.encode():
                    output.mux(packet)
        progress.update(1)
        return TemporaryVideo(path), warnings
    except BaseException:
        _remove(path)
        raise
    finally:
        backgrounds.close()
        fixed.raster = None
        if rotating is not None:
            rotating.raster = None
        if floating is not None:
            floating.sprites.clear()


class FixedOverlays:
    def __init__(self, lines, mode, mixed, size, font, font_size, position,
                 margin_x, margin_y, colors, outline_width, appearance):
        self.lines, self.mode, self.mixed = lines, mode, mixed
        self.appearance = appearance
        self.args = (size, font, font_size, position,
                     margin_x, margin_y,
                     *colors[:3], outline_width)
        self.line_index = 0
        self.key = None
        self.raster = None
        self.bounds = None

    def composite(self, frame, time):
        while self.line_index < len(self.lines) and (
            self.lines[self.line_index]["end"] is None or self.lines[self.line_index]["end"] <= time
        ):
            self.line_index += 1
        line = self.lines[self.line_index] if self.line_index < len(self.lines) else None
        if line is None or line["start"] > time:
            self.raster = self.key = self.bounds = None
            return
        active = (
            next((w for w in line["words"] if w["start"] is not None and w["start"] <= time < w["end"]), None)
            if self.mode == "active-word" and self.line_index not in self.mixed else None
        )
        key = (self.line_index, active["char_start"] if active else None)
        if key != self.key:
            layer = caption_layer(line, *self.args, active_word=active)
            if self.key is None or self.key[0] != self.line_index:
                bounds = layer.getbbox()
                if bounds is None:
                    self.raster = None
                    return
                pad = self.appearance.padding
                self.bounds = (bounds[0] - pad, bounds[1] - pad, bounds[2] + pad, bounds[3] + pad)
                self.raster = CaptionRaster(layer.crop(self.bounds), self.appearance)
            else:
                # A highlight changes color, never shape. Reuse the line's glow.
                self.raster.text = layer.crop(self.bounds)
            self.key = key
        self.raster.composite(frame, self.bounds[:2])
