# Bounded pixel readers for native VIDEO objects. Timing never uses average FPS.

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from fractions import Fraction

import numpy as np
import torch
from comfy_api.latest import InputImpl
from comfy_api.latest._util import normalize_crop_rect

from .video_loader import (
    _normalize_component_audio,
    _open,
    _pixels,
    _video_stream,
    extract_audio,
    scan_video,
)
from .video_timing import VideoTiming, check_media_interrupt, join_timing


@dataclass
class VideoFrameSource:
    width: int
    height: int
    timing: VideoTiming
    frames: Callable[[], Iterator]
    audio: Callable[[], dict | None]
    has_audio: bool


def video_frame_source(video):
    if isinstance(video, InputImpl.VideoFromList):
        parts = [video_frame_source(item) for item in video.videos]
        if len({(item.width, item.height) for item in parts}) != 1:
            raise ValueError("VIDEO clips must have matching dimensions before upscaling")
        timing = join_timing((index, item.timing) for index, item in enumerate(parts))
        has_audio = video.complete_audio is not None or any(item.has_audio for item in parts)

        def frames():
            for item in parts:
                yield from item.frames()

        def audio():
            if video.complete_audio is not None:
                return _normalize_component_audio(video.complete_audio, timing.duration)
            if not has_audio:
                return None
            rate = 48000
            waveform = torch.zeros((1, 2, round(timing.duration * rate)))
            position = Fraction()
            for item in parts:
                start = round(position * rate)
                position += item.timing.duration
                end = round(position * rate)
                part = item.audio()
                if part is not None:
                    count = min(end - start, part["waveform"].shape[-1])
                    waveform[..., start:start + count] = part["waveform"][..., :count]
                del part
            return {"waveform": waveform, "sample_rate": rate}

        return VideoFrameSource(parts[0].width, parts[0].height, timing, frames, audio, has_audio)

    if isinstance(video, InputImpl.VideoFromFile):
        source = video.get_stream_source()
        scan = scan_video(source)
        start_time, duration = video.get_active_trim_window()
        start = int(start_time / scan.time_base) * scan.time_base
        end = int((start_time + duration) / scan.time_base) * scan.time_base if duration else None
        indices = [index for index, moment in enumerate(scan.times)
                   if moment >= start and (end is None or moment < end)]
        if not indices:
            raise ValueError("VIDEO trim contains no frames")
        first, stop = indices[0], indices[-1] + 1
        durations = list(scan.durations[first:stop])
        if end is not None:
            durations[-1] = min(durations[-1], end - scan.times[stop - 1])
        timing = VideoTiming(tuple(durations))
        # ComfyUI exposes trim publicly, but its file crop has no public getter.
        # Qualify the known native representation instead of calling get_components,
        # which would allocate the entire movie. Unknown representations fail below.
        crop = getattr(video, "_VideoFromFile__crop", None)
        rect = None
        crop_dimensions = (scan.width, scan.height)
        with _open(source) as container:
            has_audio = bool(container.streams.audio)
            if crop is not None:
                # Native crop coordinates address rotated decoded pixels, before
                # any display-aspect correction. Match that native view exactly.
                first_frame = next(container.decode(_video_stream(container)))
                crop_dimensions = (first_frame.width, first_frame.height)
                if round(first_frame.rotation / 90) % 2:
                    crop_dimensions = crop_dimensions[::-1]
                rect = normalize_crop_rect(*crop, *crop_dimensions)
        width, height = rect[2:] if rect else crop_dimensions

        def frames():
            # Keep the owning VIDEO alive until its final source read completes.
            source = video.get_stream_source()
            with _open(source) as container:
                stream = _video_stream(container)
                stream.codec_context.thread_count = 2
                for index, frame in enumerate(container.decode(stream)):
                    check_media_interrupt()
                    if index >= stop:
                        break
                    if index < first:
                        continue
                    if crop is not None:
                        array = frame.to_ndarray(format="rgb24")
                        if frame.rotation:
                            array = np.rot90(array, k=round(frame.rotation / 90) % 4)
                        if rect:
                            x, y, w, h = rect
                            array = array[y:y + h, x:x + w]
                        array = np.ascontiguousarray(array)
                    else:
                        array = _pixels(frame, stream)
                    yield array

        def audio():
            return extract_audio(video.get_stream_source(), scan.times[first], timing.duration) if has_audio else None

        return VideoFrameSource(width, height, timing, frames, audio, has_audio)

    if isinstance(video, InputImpl.VideoFromComponents):
        components = video.get_components()
        images = components.images
        if components.alpha is not None or video.get_color_space() not in ("sRGB", "auto"):
            raise ValueError("Video upscaling requires SDR RGB; convert HDR or composite alpha first")
        if images.ndim != 4 or images.shape[-1] != 3 or not len(images):
            raise ValueError("VIDEO components must contain a nonempty RGB image batch")
        timing = VideoTiming.constant(len(images), components.frame_rate)
        return VideoFrameSource(
            images.shape[2], images.shape[1], timing, lambda: iter(images),
            lambda: _normalize_component_audio(components.audio, timing.duration),
            components.audio is not None,
        )
    raise ValueError("Unsupported VIDEO implementation; use native file, component or concatenated VIDEO")
